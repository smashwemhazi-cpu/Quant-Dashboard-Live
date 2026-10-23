// Silver (XAGUSD) trend scalper for small accounts. Attach to an M5 chart.  -- v2
//
// Strategy (unchanged from v1): London-session pullback-to-EMA20 scalps in the direction of a
// rising/falling H1 EMA50, swing stop with min/max bounds, fixed R take profit, breakeven, time
// stop, flat before the broker's daily close. See CHANGES block for what v2 adds.
//
// CHANGES in v2 (each new behaviour has a parameter; "off" defaults keep v1 results comparable)
//  Fixes
//   1. A breakeven scratch (net = -commission) counted as a LOSS, so with "Stop Day After 1 Loss"
//      every scratched trade ended the day. Losses are now trades worse than -LossThresholdR.
//   2. Daily loss limit was measured in nominal 1% R, but a forced-min-lot trade risks up to 2%.
//      The limit now sums each trade's actual R (net / risk money taken at entry).
//   3. Breakeven had one attempt; a rejected modify left the trade at full risk. Now up to 3
//      attempts, 10 s apart, and the stop can lock BreakEvenLockPips to cover costs.
//   4. Naked-position guard: if a position from this bot has no stop (SL attach rejected),
//      the stop is re-applied from the stored R; if that fails the position is closed.
//   5. The default blackout list held only FOMC (18:00-19:00 UTC) - outside the 07-12 entry
//      window, so it never blocked anything. The real risk is holding a late-morning entry into
//      US data at 12:30/13:30 UTC (CPI, NFP, PCE). New "Flatten Before US Data" closes open
//      positions at a fixed UTC time; the blackout now also flattens open positions.
//  Additions
//   6. Costs in sizing: RoundTurnCostPips is added to the stop distance when sizing and to the
//      spread filter, so 1R really is 1% after commission on 6-pip stops.
//   7. Optional ATR trailing stop after TrailStartR (off by default: TP 1.5R makes it moot unless
//      you run a larger TP).
//   8. Equity circuit breakers: max drawdown from equity peak (persisted in LocalStorage across
//      restarts) and weekly loss limit in R. Breaker trips -> no new entries, positions kept.
//   9. Optional filters (off by default): gold H1 trend confirmation, M5 ATR band, skip dates.
//  10. Richer RLOG2 line (hour, spread and slippage at entry, stop pips, costs) for JARVIS
//      Bot Lab analysis. RLOG v1 fields are still the first columns.
//  11. Feed watchdog: warns when no tick has arrived for N seconds during the session.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class SilverScalperBot : Robot
    {
        [Parameter("Label", DefaultValue = "SilverScalp", Group = "General")]
        public string BotLabel { get; set; }

        [Parameter("Allow Longs", DefaultValue = true, Group = "General")]
        public bool AllowLongs { get; set; }

        [Parameter("Allow Shorts", DefaultValue = true, Group = "General")]
        public bool AllowShorts { get; set; }

        [Parameter("H1 Trend EMA", DefaultValue = 50, MinValue = 5, Group = "Trend")]
        public int HtfEmaPeriod { get; set; }

        [Parameter("H1 Slope Bars", DefaultValue = 3, MinValue = 1, Group = "Trend")]
        public int HtfSlopeBars { get; set; }

        // Gold leads silver; requiring gold's H1 trend to agree filters chop. Symbol names differ
        // by broker. Blank = off.
        [Parameter("Confirm Symbol (blank = off)", DefaultValue = "", Group = "Trend")]
        public string ConfirmSymbol { get; set; }

        [Parameter("Pullback EMA", DefaultValue = 20, MinValue = 3, Group = "Entry")]
        public int FastEmaPeriod { get; set; }

        [Parameter("Pullback Lookback (bars)", DefaultValue = 3, MinValue = 1, Group = "Entry")]
        public int PullbackBars { get; set; }

        [Parameter("Swing Lookback (bars)", DefaultValue = 5, MinValue = 2, Group = "Entry")]
        public int SwingLookback { get; set; }

        [Parameter("Stop Buffer (pips)", DefaultValue = 2, MinValue = 0, Group = "Exits")]
        public double StopBufferPips { get; set; }

        [Parameter("Min Stop (pips)", DefaultValue = 6, MinValue = 1, Group = "Exits")]
        public double MinStopPips { get; set; }

        [Parameter("Max Stop (pips)", DefaultValue = 20, MinValue = 2, Group = "Exits")]
        public double MaxStopPips { get; set; }

        [Parameter("Min Stop (x ATR, 0 = use pips)", DefaultValue = 0, MinValue = 0, Step = 0.1, Group = "Exits")]
        public double MinStopAtr { get; set; }

        [Parameter("Max Stop (x ATR, 0 = use pips)", DefaultValue = 0, MinValue = 0, Step = 0.1, Group = "Exits")]
        public double MaxStopAtr { get; set; }

        [Parameter("ATR Period", DefaultValue = 14, MinValue = 2, Group = "Exits")]
        public int AtrPeriod { get; set; }

        [Parameter("Take Profit (R)", DefaultValue = 1.5, MinValue = 0.5, Step = 0.1, Group = "Exits")]
        public double TakeProfitR { get; set; }

        [Parameter("Breakeven At (R, 0 = off)", DefaultValue = 1.0, MinValue = 0, Step = 0.1, Group = "Exits")]
        public double BreakEvenAtR { get; set; }

        [Parameter("Breakeven Lock (pips beyond entry)", DefaultValue = 0, MinValue = 0, Step = 0.1, Group = "Exits")]
        public double BreakEvenLockPips { get; set; }

        [Parameter("Trail Start (R, 0 = off)", DefaultValue = 0, MinValue = 0, Step = 0.1, Group = "Exits")]
        public double TrailStartR { get; set; }

        [Parameter("Trail Distance (x ATR)", DefaultValue = 1.5, MinValue = 0.1, Step = 0.1, Group = "Exits")]
        public double TrailAtrMult { get; set; }

        [Parameter("Max Minutes In Trade", DefaultValue = 180, MinValue = 5, Group = "Exits")]
        public int MaxMinutesInTrade { get; set; }

        [Parameter("Risk % per Trade", DefaultValue = 1.0, MinValue = 0.05, MaxValue = 5, Step = 0.05, Group = "Risk")]
        public double RiskPercent { get; set; }

        [Parameter("Max Risk % if Min Lot Forced", DefaultValue = 2.0, MinValue = 0.05, MaxValue = 10, Step = 0.05, Group = "Risk")]
        public double MaxRiskPercent { get; set; }

        [Parameter("Max Trades Per Day", DefaultValue = 6, MinValue = 1, Group = "Risk")]
        public int MaxTradesPerDay { get; set; }

        [Parameter("Daily Loss Limit (R)", DefaultValue = 3.0, MinValue = 0.5, Step = 0.5, Group = "Risk")]
        public double DailyLossLimitR { get; set; }

        [Parameter("Stop Day After N Losses", DefaultValue = 1, MinValue = 1, Group = "Risk")]
        public int StopDayAfterLosses { get; set; }

        // A trade only counts as a loss below -this many R; breakeven scratches do not.
        [Parameter("Loss Threshold (R)", DefaultValue = 0.25, MinValue = 0, Step = 0.05, Group = "Risk")]
        public double LossThresholdR { get; set; }

        [Parameter("Weekly Loss Limit (R, 0 = off)", DefaultValue = 6, MinValue = 0, Step = 0.5, Group = "Risk")]
        public double WeeklyLossLimitR { get; set; }

        [Parameter("Max Drawdown from Peak % (0 = off)", DefaultValue = 15, MinValue = 0, MaxValue = 90, Group = "Risk")]
        public double MaxDrawdownPct { get; set; }

        // The equity peak survives restarts on purpose. Set true for ONE start after reviewing a
        // breaker trip (or after a deposit/withdrawal) to restart the peak at current equity.
        [Parameter("Reset Drawdown Peak On Start", DefaultValue = false, Group = "Risk")]
        public bool ResetPeakOnStart { get; set; }

        // Commission + typical exit slippage, expressed in pips of this symbol, round turn.
        [Parameter("Round-turn Cost (pips)", DefaultValue = 0, MinValue = 0, Step = 0.1, Group = "Execution")]
        public double RoundTurnCostPips { get; set; }

        [Parameter("Max Slippage (pips)", DefaultValue = 2, MinValue = 0, Group = "Execution")]
        public double MaxSlippagePips { get; set; }

        [Parameter("Max Spread (pips)", DefaultValue = 4, MinValue = 0, Group = "Execution")]
        public double MaxSpreadPips { get; set; }

        [Parameter("Max Spread (% of stop)", DefaultValue = 20, MinValue = 1, MaxValue = 100, Group = "Execution")]
        public double MaxSpreadPctOfStop { get; set; }

        [Parameter("Feed Watchdog (seconds, 0 = off)", DefaultValue = 120, MinValue = 0, Group = "Execution")]
        public int FeedWatchdogSeconds { get; set; }

        [Parameter("London Entry From Hour UTC", DefaultValue = 7, MinValue = 0, MaxValue = 23, Group = "Filters")]
        public int LondonFromHour { get; set; }

        [Parameter("London Entry To Hour UTC", DefaultValue = 12, MinValue = 1, MaxValue = 24, Group = "Filters")]
        public int LondonToHour { get; set; }

        [Parameter("Flat By Hour UTC", DefaultValue = 20, MinValue = 1, MaxValue = 23, Group = "Filters")]
        public int FlatByHour { get; set; }

        // US releases are 12:30 UTC (US summer time) / 13:30 UTC (winter). Positions opened late
        // in the London window are otherwise still open then. Blank = off.
        [Parameter("Flatten Before US Data (UTC HH:mm, blank = off)", DefaultValue = "12:25", Group = "Filters")]
        public string FlattenBeforeUsDataUtc { get; set; }

        [Parameter("News Blackout (minutes each side)", DefaultValue = 60, MinValue = 0, Group = "Filters")]
        public int BlackoutMinutes { get; set; }

        // Add CPI / NFP / PCE / FOMC here (JARVIS can generate this string from its calendar).
        [Parameter("Events UTC (yyyy-MM-dd HH:mm; ...)", Group = "Filters",
            DefaultValue = "2026-01-28 19:00;2026-03-18 18:00;2026-04-29 18:00;2026-06-17 18:00;2026-07-29 18:00;2026-09-16 18:00;2026-10-28 18:00;2026-12-09 19:00")]
        public string EventsUtc { get; set; }

        [Parameter("Skip Dates (yyyy-MM-dd; ...)", DefaultValue = "", Group = "Filters")]
        public string SkipDates { get; set; }

        [Parameter("Min M5 ATR (pips, 0 = off)", DefaultValue = 0, MinValue = 0, Step = 0.5, Group = "Filters")]
        public double MinAtrPips { get; set; }

        [Parameter("Max M5 ATR (pips, 0 = off)", DefaultValue = 0, MinValue = 0, Step = 0.5, Group = "Filters")]
        public double MaxAtrPips { get; set; }

        private Bars _h1, _confirmH1;
        private ExponentialMovingAverage _htfEma, _fastEma, _confirmEma;
        private AverageTrueRange _atr;
        private List<DateTime> _events;
        private HashSet<DateTime> _skipDates;
        private TimeSpan? _usDataCut;
        private DateTime _lastTick = DateTime.MinValue;
        private bool _feedWarned, _breakerTripped;
        private double _peakEquity;
        private readonly Dictionary<string, int> _stats = new Dictionary<string, int>();
        private readonly Dictionary<int, double[]> _excursion = new Dictionary<int, double[]>();
        private readonly Dictionary<int, BeState> _breakeven = new Dictionary<int, BeState>();
        private readonly Dictionary<int, EntryInfo> _entryInfo = new Dictionary<int, EntryInfo>();
        private readonly Dictionary<int, DateTime> _lastTrail = new Dictionary<int, DateTime>();
        private const string CommentPrefix = "SCALP";
        private const string PeakKey = "peakEquity";

        private class BeState { public int Attempts; public DateTime Last; public bool Done; }
        private class EntryInfo { public double SpreadPips, SlipPips, StopPips; public int Hour; }

        protected override void OnStart()
        {
            if (TimeFrame != TimeFrame.Minute5)
                Print("NOTE: designed for M5; running on {0}.", TimeFrame);

            _h1 = LoadH1(SymbolName);
            _htfEma = Indicators.ExponentialMovingAverage(_h1.ClosePrices, HtfEmaPeriod);
            _fastEma = Indicators.ExponentialMovingAverage(Bars.ClosePrices, FastEmaPeriod);
            _atr = Indicators.AverageTrueRange(Bars, AtrPeriod, MovingAverageType.WilderSmoothing);
            if (!string.IsNullOrWhiteSpace(ConfirmSymbol))
            {
                try
                {
                    _confirmH1 = LoadH1(ConfirmSymbol.Trim());
                    _confirmEma = Indicators.ExponentialMovingAverage(_confirmH1.ClosePrices, HtfEmaPeriod);
                }
                catch (Exception e)
                {
                    Print("Confirm symbol '{0}' unavailable ({1}) - filter disabled.", ConfirmSymbol, e.Message);
                    _confirmH1 = null;
                }
            }
            _events = ParseEvents(EventsUtc);
            _skipDates = ParseDates(SkipDates);
            _usDataCut = ParseTime(FlattenBeforeUsDataUtc);

            _peakEquity = ResetPeakOnStart ? Account.Equity : Math.Max(Account.Equity, ReadPeak());
            SavePeak(_peakEquity);
            Positions.Closed += OnPositionClosed;
            Server.Disconnected += () => Print("Disconnected - entries paused.");
            Server.Connected += () => { Print("Reconnected."); _feedWarned = false; };

            double maxFit = Account.Equity * MaxRiskPercent / 100.0 / (Symbol.VolumeInUnitsMin * Symbol.PipValue) - RoundTurnCostPips;
            Print("Started on {0} {1}. Min volume {2} units fits stops up to {3:F1} pips at {4}% (equity {5:F2}); " +
                  "stop range {6}-{7} pips. Broker commission {8} ({9}) - set Round-turn Cost accordingly.",
                  SymbolName, TimeFrame, Symbol.VolumeInUnitsMin, maxFit, MaxRiskPercent, Account.Equity,
                  MinStopPips, Math.Min(MaxStopPips, maxFit), Symbol.Commission, Symbol.CommissionType);
            Print("RLOG,id,kind,side,stop,entryUtc,exitUtc,hours,R,mfeR,maeR,exit");
            Print("RLOG2,id,side,entryUtc,exitUtc,hours,R,mfeR,maeR,exit,hour,stopPips,spreadPips,slipPips,costPips,net");
        }

        protected override void OnStop()
        {
            Print("Summary: " + (_stats.Count == 0 ? "no signals"
                : string.Join(", ", _stats.OrderBy(kv => kv.Key).Select(kv => kv.Key + "=" + kv.Value))));
        }

        protected override void OnTick()
        {
            _lastTick = Server.Time;
            if (!QuotesValid())
                return;

            if (Account.Equity > _peakEquity)
            {
                _peakEquity = Account.Equity;
                SavePeak(_peakEquity);
            }

            foreach (var p in MyPositions())
            {
                double r;
                if (!ReadR(p, out r))
                    continue;
                if (!p.StopLoss.HasValue)
                {
                    GuardNakedPosition(p, r);
                    continue;
                }
                bool sell = p.TradeType == TradeType.Sell;
                double fav = sell ? p.EntryPrice - Symbol.Ask : Symbol.Bid - p.EntryPrice;
                double[] ex;
                if (!_excursion.TryGetValue(p.Id, out ex))
                    _excursion[p.Id] = ex = new double[2];
                ex[0] = Math.Max(ex[0], fav / r);
                ex[1] = Math.Max(ex[1], -fav / r);

                if (BreakEvenAtR > 0 && fav >= BreakEvenAtR * r)
                    TryBreakeven(p, sell);
                if (TrailStartR > 0 && fav >= TrailStartR * r)
                    Trail(p, sell);
            }
        }

        protected override void OnBar()
        {
            var now = Server.Time;
            bool usCut = _usDataCut.HasValue && now.TimeOfDay >= _usDataCut.Value && now.Hour < FlatByHour;
            bool inEvent = InEventWindow(now);
            foreach (var p in MyPositions())
            {
                string why = null;
                if (now.Hour >= FlatByHour) why = "flat-by-" + FlatByHour;
                else if (usCut && p.EntryTime.TimeOfDay < _usDataCut.Value) why = "us-data-cut";
                else if (inEvent) why = "event-blackout";
                else if ((now - p.EntryTime).TotalMinutes >= MaxMinutesInTrade) why = "time-stop";
                if (why == null)
                    continue;
                var res = ClosePosition(p);
                Print("{0} close on {1}: {2}", why, p.Id, res.IsSuccessful ? "closed" : res.Error.ToString());
            }

            if (FeedWatchdogSeconds > 0 && _lastTick != DateTime.MinValue && !_feedWarned
                && (now - _lastTick).TotalSeconds > FeedWatchdogSeconds && InSession(now))
            {
                _feedWarned = true;
                Print("WARNING: no ticks for {0:F0}s - check connection/feed.", (now - _lastTick).TotalSeconds);
            }
            CheckEntry();
        }

        protected override void OnError(Error error)
        {
            Print("Trade operation error: {0}", error.Code);
        }

        private void CheckEntry()
        {
            if (MyPositions().Length > 0 || !Server.IsConnected || !QuotesValid())
                return;
            if (Bars.Count < Math.Max(FastEmaPeriod, SwingLookback) + PullbackBars + 3)
                return;

            int dir = Bias(_h1, _htfEma);
            if (dir == 0 || (dir > 0 && !AllowLongs) || (dir < 0 && !AllowShorts))
                return;
            if (!PullbackSignal(dir))
                return;
            Count("signal");

            DateTime now = Server.Time;
            if (!InSession(now))
            {
                Count("skip_session");
                return;
            }
            if (_usDataCut.HasValue && now.TimeOfDay >= _usDataCut.Value)
            {
                Count("skip_usDataCut");
                return;
            }
            if (_skipDates.Contains(now.Date))
            {
                Count("skip_date");
                return;
            }
            if (InEventWindow(now))
            {
                Count("skip_news");
                return;
            }
            if (_confirmH1 != null && Bias(_confirmH1, _confirmEma) != dir)
            {
                Count("skip_confirm");
                return;
            }
            string block = BreakerCheck() ?? DayGuard();
            if (block != null)
            {
                Count("skip_" + block);
                return;
            }

            double atrPips = _atr.Result.Last(1) / Symbol.PipSize;
            if ((MinAtrPips > 0 && atrPips < MinAtrPips) || (MaxAtrPips > 0 && atrPips > MaxAtrPips))
            {
                Count("skip_atrBand");
                return;
            }

            var side = dir > 0 ? TradeType.Buy : TradeType.Sell;
            double refPrice = dir > 0 ? Symbol.Ask : Symbol.Bid;
            double swing = dir > 0 ? Lowest(SwingLookback) : Highest(SwingLookback);
            // Bars are bid prices; a short's stop fills on the ask, so add the spread to keep the buffer.
            double stopPrice = swing - dir * StopBufferPips * Symbol.PipSize;
            if (dir < 0)
                stopPrice += Symbol.Spread;
            double stopPips = (refPrice - stopPrice) * dir / Symbol.PipSize;
            double minStop = MinStopAtr > 0 && atrPips > 0 ? MinStopAtr * atrPips : MinStopPips;
            double maxStop = MaxStopAtr > 0 && atrPips > 0 ? MaxStopAtr * atrPips : MaxStopPips;
            if (stopPips < minStop)
                stopPips = minStop;
            if (stopPips > maxStop)
            {
                Count("skip_stopTooWide");
                return;
            }

            double spread = Symbol.Spread / Symbol.PipSize;
            if (spread > MaxSpreadPips || spread + RoundTurnCostPips > stopPips * MaxSpreadPctOfStop / 100.0)
            {
                Count("skip_spread");
                return;
            }

            double volume = SizePosition(stopPips);
            if (volume <= 0)
            {
                Count("skip_minVolumeRisk");
                return;
            }

            double tpPips = stopPips * TakeProfitR;
            string comment = string.Format(CultureInfo.InvariantCulture, "{0}|R={1:F6}|V={2:F2}",
                CommentPrefix, stopPips * Symbol.PipSize, volume);
            var result = ExecuteMarketRangeOrder(side, SymbolName, volume, MaxSlippagePips, refPrice,
                BotLabel, stopPips, tpPips, comment);
            if (result.IsSuccessful && result.Position != null)
            {
                Count("taken");
                double slip = (result.Position.EntryPrice - refPrice) * dir / Symbol.PipSize;
                _entryInfo[result.Position.Id] = new EntryInfo { SpreadPips = spread, SlipPips = slip, StopPips = stopPips, Hour = now.Hour };
                double risk = volume * (stopPips + RoundTurnCostPips) * Symbol.PipValue;
                Print("{0} {1} units @ {2} | stop {3:F1} pips, TP {4:F1} pips | risk {5:F2} ({6:F2}%) | spread {7:F1} | slip {8:F1}",
                    side, volume, result.Position.EntryPrice, stopPips, tpPips, risk, risk / Account.Equity * 100, spread, slip);
                if (!result.Position.StopLoss.HasValue)
                    GuardNakedPosition(result.Position, stopPips * Symbol.PipSize);
            }
            else
            {
                Count("entryFailed");
                Print("Entry failed: {0}", result.Error);
            }
        }

        // +1 / -1 / 0 from the last completed H1 bar of `bars`: close vs EMA and EMA slope.
        private int Bias(Bars bars, ExponentialMovingAverage ema)
        {
            int k = bars.OpenTimes.GetIndexByTime(Bars.OpenTimes.Last(0)) - 1;
            if (k < HtfEmaPeriod + HtfSlopeBars)
                return 0;
            double e = ema.Result[k], slope = e - ema.Result[k - HtfSlopeBars];
            double close = bars.ClosePrices[k];
            if (close > e && slope > 0) return 1;
            if (close < e && slope < 0) return -1;
            return 0;
        }

        private bool PullbackSignal(int dir)
        {
            double o = Bars.OpenPrices.Last(1), c = Bars.ClosePrices.Last(1), ema = _fastEma.Result.Last(1);
            bool touched = false;
            for (int i = 2; i <= PullbackBars + 1; i++)
            {
                double e = _fastEma.Result.Last(i);
                if (dir > 0 ? Bars.LowPrices.Last(i) <= e : Bars.HighPrices.Last(i) >= e)
                    touched = true;
            }
            if (!touched)
                return false;
            return dir > 0
                ? c > o && c > ema && c > Bars.HighPrices.Last(2)
                : c < o && c < ema && c < Bars.LowPrices.Last(2);
        }

        private void TryBreakeven(Position p, bool sell)
        {
            BeState s;
            if (!_breakeven.TryGetValue(p.Id, out s))
                _breakeven[p.Id] = s = new BeState();
            if (s.Done || s.Attempts >= 3 || (Server.Time - s.Last).TotalSeconds < 10)
                return;
            double target = p.EntryPrice + (sell ? -1 : 1) * BreakEvenLockPips * Symbol.PipSize;
            double tol = Symbol.PipSize * 0.5;
            // never loosen: only move if the current stop is still on the losing side of target
            if (sell ? p.StopLoss.Value <= target + tol : p.StopLoss.Value >= target - tol)
            {
                s.Done = true;
                return;
            }
            s.Attempts++;
            s.Last = Server.Time;
            var res = p.ModifyStopLossPrice(target);
            if (res.IsSuccessful)
                s.Done = true;
            else
                Print("Breakeven attempt {0}/3 failed on {1}: {2}", s.Attempts, p.Id, res.Error);
        }

        private void Trail(Position p, bool sell)
        {
            double dist = TrailAtrMult * _atr.Result.Last(1);
            if (dist <= 0)
                return;
            double candidate = sell ? Symbol.Ask + dist : Symbol.Bid - dist;
            double cur = p.StopLoss.Value;
            // only tighten, and only by at least half a pip to avoid modify spam
            DateTime last;
            if (_lastTrail.TryGetValue(p.Id, out last) && (Server.Time - last).TotalSeconds < 5)
                return;
            if (sell ? candidate < cur - Symbol.PipSize * 0.5 : candidate > cur + Symbol.PipSize * 0.5)
            {
                _lastTrail[p.Id] = Server.Time;
                var res = p.ModifyStopLossPrice(candidate);
                if (!res.IsSuccessful)
                    Print("Trail modify failed on {0}: {1}", p.Id, res.Error);
            }
        }

        private void GuardNakedPosition(Position p, double r)
        {
            double sl = p.TradeType == TradeType.Buy ? p.EntryPrice - r : p.EntryPrice + r;
            var res = p.ModifyStopLossPrice(sl);
            if (res.IsSuccessful)
            {
                Print("Re-applied missing stop on {0} at {1}.", p.Id, sl);
                return;
            }
            Print("CRITICAL: position {0} has no stop and SL could not be set ({1}) - closing.", p.Id, res.Error);
            ClosePosition(p);
        }

        private double Lowest(int n)
        {
            double v = double.MaxValue;
            for (int i = 1; i <= n; i++) v = Math.Min(v, Bars.LowPrices.Last(i));
            return v;
        }

        private double Highest(int n)
        {
            double v = double.MinValue;
            for (int i = 1; i <= n; i++) v = Math.Max(v, Bars.HighPrices.Last(i));
            return v;
        }

        // Closed trades of this bot in R (net / risk money at entry), newest first.
        private List<(DateTime Closed, double R)> ClosedR(DateTime since)
        {
            return History.Where(t => t.Label == BotLabel && t.SymbolName == SymbolName && t.EntryTime >= since)
                .GroupBy(t => t.PositionId)
                .Select(g =>
                {
                    var first = g.First();
                    double r = 0, vol = 0;
                    ParseComment(first.Comment, out r, out vol);
                    double riskMoney = r > 0 && vol > 0
                        ? (r / Symbol.PipSize + RoundTurnCostPips) * Symbol.PipValue * vol : 0;
                    double net = g.Sum(t => t.NetProfit);
                    return (Closed: g.Max(t => t.ClosingTime), R: riskMoney > 0 ? net / riskMoney : (net < 0 ? -1.0 : 0.0));
                })
                .OrderByDescending(x => x.Closed).ToList();
        }

        private string DayGuard()
        {
            var trades = ClosedR(Server.Time.Date);
            if (trades.Count >= MaxTradesPerDay)
                return "maxTradesDay";
            if (trades.Sum(x => x.R) <= -DailyLossLimitR)
                return "dailyLoss";
            int streak = 0;
            foreach (var x in trades)
            {
                if (x.R < -LossThresholdR) streak++;
                else break;
            }
            return streak >= StopDayAfterLosses ? "lossStreakDay" : null;
        }

        private string BreakerCheck()
        {
            if (_breakerTripped)
                return "breaker";
            if (MaxDrawdownPct > 0 && _peakEquity > 0 && (_peakEquity - Account.Equity) / _peakEquity * 100 >= MaxDrawdownPct)
            {
                _breakerTripped = true;
                Print("BREAKER: equity {0:F2} is {1:F1}% below peak {2:F2}. No new entries; review, then restart with Reset Drawdown Peak On Start.",
                    Account.Equity, (_peakEquity - Account.Equity) / _peakEquity * 100, _peakEquity);
                return "breaker";
            }
            if (WeeklyLossLimitR > 0)
            {
                var today = Server.Time.Date;
                var monday = today.AddDays(-(((int)today.DayOfWeek + 6) % 7));
                if (ClosedR(monday).Sum(x => x.R) <= -WeeklyLossLimitR)
                    return "weeklyLoss";
            }
            return null;
        }

        // Raw size is compared to the minimum BEFORE normalising: NormalizeVolumeInUnits lifts
        // sub-minimum volumes to the minimum, which would bypass the risk cap.
        private double SizePosition(double stopPips)
        {
            double perUnit = (stopPips + RoundTurnCostPips) * Symbol.PipValue;
            if (perUnit <= 0 || double.IsNaN(perUnit))
                return 0;
            double raw = Account.Equity * RiskPercent / 100.0 / perUnit;
            double volume;
            if (raw >= Symbol.VolumeInUnitsMin)
                volume = Symbol.NormalizeVolumeInUnits(raw, RoundingMode.Down);
            else if (Symbol.VolumeInUnitsMin * perUnit <= Account.Equity * MaxRiskPercent / 100.0)
                volume = Symbol.VolumeInUnitsMin;
            else
                return 0;
            volume = Math.Min(volume, Symbol.VolumeInUnitsMax);
            double cap = Account.Equity * Math.Max(RiskPercent, MaxRiskPercent) / 100.0;
            return volume * perUnit > cap * 1.0001 ? 0 : volume;
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            var p = args.Position;
            if (p.Label != BotLabel || p.SymbolName != SymbolName)
                return;
            double r, vol;
            if (!ParseComment(p.Comment, out r, out vol))
                return;
            double riskMoney = (r / Symbol.PipSize + RoundTurnCostPips) * Symbol.PipValue * vol;
            var deals = History.Where(t => t.PositionId == p.Id).ToList();
            double net = deals.Count > 0 ? deals.Sum(t => t.NetProfit) : p.NetProfit;
            double[] ex;
            _excursion.TryGetValue(p.Id, out ex);
            EntryInfo ei;
            _entryInfo.TryGetValue(p.Id, out ei);
            _excursion.Remove(p.Id);
            _breakeven.Remove(p.Id);
            _entryInfo.Remove(p.Id);
            _lastTrail.Remove(p.Id);
            double hours = (Server.Time - p.EntryTime).TotalHours;
            double rr = riskMoney > 0 ? net / riskMoney : 0;
            double mfe = ex != null ? ex[0] : 0, mae = ex != null ? ex[1] : 0;
            var ci = CultureInfo.InvariantCulture;
            Print(string.Format(ci, "RLOG,{0},scalp,{1},normal,{2:yyyy-MM-dd HH:mm},{3:yyyy-MM-dd HH:mm},{4:F2},{5:F2},{6:F2},{7:F2},{8}",
                p.Id, p.TradeType, p.EntryTime, Server.Time, hours, rr, mfe, mae, args.Reason));
            Print(string.Format(ci, "RLOG2,{0},{1},{2:yyyy-MM-dd HH:mm},{3:yyyy-MM-dd HH:mm},{4:F2},{5:F2},{6:F2},{7:F2},{8},{9},{10:F1},{11:F1},{12:F1},{13:F1},{14:F2}",
                p.Id, p.TradeType, p.EntryTime, Server.Time, hours, rr, mfe, mae, args.Reason,
                ei != null ? ei.Hour : p.EntryTime.Hour, ei != null ? ei.StopPips : r / Symbol.PipSize,
                ei != null ? ei.SpreadPips : double.NaN, ei != null ? ei.SlipPips : double.NaN, RoundTurnCostPips, net));
        }

        private static bool ParseComment(string comment, out double r, out double vol)
        {
            r = 0;
            vol = 0;
            if (string.IsNullOrEmpty(comment) || !comment.StartsWith(CommentPrefix))
                return false;
            foreach (var part in comment.Split('|'))
            {
                if (part.StartsWith("R="))
                    double.TryParse(part.Substring(2), NumberStyles.Float, CultureInfo.InvariantCulture, out r);
                else if (part.StartsWith("V="))
                    double.TryParse(part.Substring(2), NumberStyles.Float, CultureInfo.InvariantCulture, out vol);
            }
            return r > 0;
        }

        private static bool ReadR(Position p, out double r)
        {
            double vol;
            return ParseComment(p.Comment, out r, out vol);
        }

        private Bars LoadH1(string symbol)
        {
            var bars = MarketData.GetBars(TimeFrame.Hour, symbol);
            for (int i = 0; bars.Count < HtfEmaPeriod + HtfSlopeBars + 10 && i < 20; i++)
                if (bars.LoadMoreHistory() <= 0) break;
            return bars;
        }

        private bool InSession(DateTime now)
        {
            return now.Hour >= LondonFromHour && now.Hour < LondonToHour;
        }

        private bool InEventWindow(DateTime now)
        {
            var window = TimeSpan.FromMinutes(BlackoutMinutes);
            return BlackoutMinutes > 0 && _events.Any(e => now >= e - window && now <= e + window);
        }

        private Position[] MyPositions()
        {
            return Positions.FindAll(BotLabel, SymbolName);
        }

        private bool QuotesValid()
        {
            return !double.IsNaN(Symbol.Bid) && !double.IsNaN(Symbol.Ask) && Symbol.Bid > 0 && Symbol.Ask >= Symbol.Bid;
        }

        private void Count(string key)
        {
            int n;
            _stats.TryGetValue(key, out n);
            _stats[key] = n + 1;
        }

        private double ReadPeak()
        {
            try
            {
                double v;
                var s = LocalStorage.GetString(PeakStorageKey());
                return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out v) ? v : 0;
            }
            catch (Exception e)
            {
                Print("LocalStorage read failed ({0}); drawdown peak starts at current equity.", e.Message);
                return 0;
            }
        }

        private void SavePeak(double v)
        {
            try
            {
                LocalStorage.SetString(PeakStorageKey(), v.ToString("R", CultureInfo.InvariantCulture));
            }
            catch (Exception)
            {
                // non-fatal: the breaker still works in-session
            }
        }

        private string PeakStorageKey()
        {
            return BotLabel + "." + Account.Number + "." + PeakKey;
        }

        private static TimeSpan? ParseTime(string s)
        {
            TimeSpan t;
            if (string.IsNullOrWhiteSpace(s))
                return null;
            return TimeSpan.TryParseExact(s.Trim(), "hh\\:mm", CultureInfo.InvariantCulture, out t) ? t : (TimeSpan?)null;
        }

        private HashSet<DateTime> ParseDates(string list)
        {
            var set = new HashSet<DateTime>();
            foreach (var s in (list ?? "").Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                DateTime d;
                if (DateTime.TryParseExact(s.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out d))
                    set.Add(d.Date);
                else
                    Print("Ignoring invalid skip date '{0}'.", s);
            }
            return set;
        }

        private List<DateTime> ParseEvents(string list)
        {
            var events = new List<DateTime>();
            foreach (var s in (list ?? "").Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                DateTime t;
                if (DateTime.TryParseExact(s.Trim(), "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture,
                        DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out t))
                    events.Add(t);
                else
                    Print("Ignoring invalid event time '{0}'.", s);
            }
            return events;
        }
    }
}
