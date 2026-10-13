// =====================================================================================
//  MicroLotBreakoutBot  -  cTrader Automate cBot for SMALL accounts at a FIXED minimum lot
// -------------------------------------------------------------------------------------
//  Built for a ~$200 account that can only trade 0.01 lot (1 oz on XAUUSD). At a fixed
//  lot, "position sizing" is impossible - the only risk lever is the STOP DISTANCE.
//  So the bot only trades setups whose natural stop is small enough:
//
//  Strategy : Volatility-compression breakout (Crabel-style NR box) in the direction of
//             the higher-timeframe trend, during liquid London / New York hours only.
//  Entry    : Stop-limit order beyond a tight N-bar box (slippage-capped, auto-expiring).
//  Stop     : Opposite side of the box (structural, small by construction).
//  Exit     : Fixed R target, breakeven at +1R, flat before the daily rollover - no swap,
//             no weekend gap.
//  Risk gate: Trade is skipped unless min-lot loss <= Max Risk % of equity. Daily trade
//             cap, daily loss cap, intraday loss-streak stop, hard equity floor.
//  Report   : On start, prints a FEASIBILITY report measured on YOUR broker's data:
//             how many setups were affordable at your account size.
//
//  Verified against cTrader.Automate 1.0.21 (cAlgo.API). Attach to an M5 or M15 chart.
//  Research code - backtest and demo-test before risking capital.
// =====================================================================================
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum MicroDirection
    {
        Both,
        LongOnly,
        ShortOnly
    }

    public enum MicroTrendTimeFrame
    {
        Minute30,
        Hour,
        Hour4
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class MicroLotBreakoutBot : Robot
    {
        // ------------------------------------------------------------------ General
        [Parameter("Label", Group = "General", DefaultValue = "MLB")]
        public string BotLabel { get; set; }

        [Parameter("Direction", Group = "General", DefaultValue = MicroDirection.Both)]
        public MicroDirection Direction { get; set; }

        [Parameter("Fixed Lots", Group = "General", DefaultValue = 0.01, MinValue = 0.01, Step = 0.01)]
        public double FixedLots { get; set; }

        // ------------------------------------------------------------------ Trend filter
        [Parameter("Trend Timeframe", Group = "Trend", DefaultValue = MicroTrendTimeFrame.Hour)]
        public MicroTrendTimeFrame TrendTimeFrame { get; set; }

        [Parameter("Trend EMA", Group = "Trend", DefaultValue = 50, MinValue = 10, MaxValue = 200, Step = 10)]
        public int TrendEmaPeriod { get; set; }

        [Parameter("Trend EMA Slope (bars)", Group = "Trend", DefaultValue = 5, MinValue = 1, MaxValue = 30, Step = 1)]
        public int TrendSlopeBars { get; set; }

        // ------------------------------------------------------------------ Setup
        [Parameter("Box Bars", Group = "Setup", DefaultValue = 12, MinValue = 4, MaxValue = 48, Step = 2)]
        public int BoxBars { get; set; }

        [Parameter("ATR Period", Group = "Setup", DefaultValue = 14, MinValue = 5, MaxValue = 50, Step = 1)]
        public int AtrPeriod { get; set; }

        [Parameter("Box Max Height (ATR x)", Group = "Setup", DefaultValue = 2.5, MinValue = 1.0, MaxValue = 6.0, Step = 0.25)]
        public double BoxMaxAtr { get; set; }

        [Parameter("Box Min Height (ATR x)", Group = "Setup", DefaultValue = 0.8, MinValue = 0.2, MaxValue = 3.0, Step = 0.1)]
        public double BoxMinAtr { get; set; }

        [Parameter("Breakout Buffer (ATR x)", Group = "Setup", DefaultValue = 0.1, MinValue = 0.0, MaxValue = 1.0, Step = 0.05)]
        public double BufferAtr { get; set; }

        [Parameter("Order Expiry (bars)", Group = "Setup", DefaultValue = 6, MinValue = 1, MaxValue = 48, Step = 1)]
        public int OrderExpiryBars { get; set; }

        // ------------------------------------------------------------------ Exits
        [Parameter("Reward (R)", Group = "Exits", DefaultValue = 2.0, MinValue = 1.0, MaxValue = 5.0, Step = 0.25)]
        public double RewardR { get; set; }

        [Parameter("Breakeven At (R, 0=off)", Group = "Exits", DefaultValue = 1.0, MinValue = 0.0, MaxValue = 3.0, Step = 0.25)]
        public double BreakevenR { get; set; }

        [Parameter("Flat Time (UTC hhmm)", Group = "Exits", DefaultValue = 2030, MinValue = 0, MaxValue = 2359)]
        public int FlatTimeUtc { get; set; }

        [Parameter("Friday Flat Time (UTC hhmm)", Group = "Exits", DefaultValue = 1930, MinValue = 0, MaxValue = 2359)]
        public int FridayFlatTimeUtc { get; set; }

        // ------------------------------------------------------------------ Risk
        [Parameter("Max Risk Per Trade %", Group = "Risk", DefaultValue = 3.0, MinValue = 0.5, MaxValue = 10.0, Step = 0.25)]
        public double MaxRiskPercent { get; set; }

        [Parameter("Max Risk Per Trade $ (0=off)", Group = "Risk", DefaultValue = 0, MinValue = 0, Step = 1)]
        public double MaxRiskCash { get; set; }

        [Parameter("Max Trades Per Day", Group = "Risk", DefaultValue = 2, MinValue = 1, MaxValue = 10, Step = 1)]
        public int MaxTradesPerDay { get; set; }

        [Parameter("Max Daily Loss %", Group = "Risk", DefaultValue = 6.0, MinValue = 1.0, MaxValue = 20.0, Step = 0.5)]
        public double MaxDailyLossPercent { get; set; }

        [Parameter("Max Losses In A Row (per day)", Group = "Risk", DefaultValue = 2, MinValue = 1, MaxValue = 10, Step = 1)]
        public int MaxDailyLossStreak { get; set; }

        [Parameter("Equity Floor $ (stops bot)", Group = "Risk", DefaultValue = 120, MinValue = 0, Step = 5)]
        public double EquityFloor { get; set; }

        // ------------------------------------------------------------------ Execution
        [Parameter("Stop-Limit Range (pips)", Group = "Execution", DefaultValue = 5, MinValue = 0, Step = 1)]
        public double StopLimitRangePips { get; set; }

        [Parameter("Max Spread (pips)", Group = "Execution", DefaultValue = 6, MinValue = 0.1, Step = 0.5)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Max Spread (fraction of risk)", Group = "Execution", DefaultValue = 0.15, MinValue = 0.01, MaxValue = 0.5, Step = 0.01)]
        public double MaxSpreadOfRisk { get; set; }

        [Parameter("Stale Quote (sec)", Group = "Execution", DefaultValue = 60, MinValue = 5, Step = 5)]
        public int StaleQuoteSeconds { get; set; }

        // ------------------------------------------------------------------ Sessions (UTC)
        [Parameter("Session 1 Start (UTC hhmm)", Group = "Sessions", DefaultValue = 700, MinValue = 0, MaxValue = 2359)]
        public int Session1Start { get; set; }

        [Parameter("Session 1 End (UTC hhmm)", Group = "Sessions", DefaultValue = 1030, MinValue = 0, MaxValue = 2359)]
        public int Session1End { get; set; }

        [Parameter("Session 2 Start (UTC hhmm)", Group = "Sessions", DefaultValue = 1230, MinValue = 0, MaxValue = 2359)]
        public int Session2Start { get; set; }

        [Parameter("Session 2 End (UTC hhmm)", Group = "Sessions", DefaultValue = 1530, MinValue = 0, MaxValue = 2359)]
        public int Session2End { get; set; }

        // ------------------------------------------------------------------ News
        [Parameter("News Times UTC (yyyy-MM-dd HH:mm,...)", Group = "News", DefaultValue = "")]
        public string NewsTimes { get; set; }

        [Parameter("News Window (+/- min)", Group = "News", DefaultValue = 30, MinValue = 0, MaxValue = 240, Step = 5)]
        public int NewsWindowMinutes { get; set; }

        // ------------------------------------------------------------------ Report
        [Parameter("Feasibility Report Bars", Group = "Report", DefaultValue = 3000, MinValue = 200, Step = 100)]
        public int FeasibilityBars { get; set; }

        // ------------------------------------------------------------------ State
        private AverageTrueRange _atr;
        private Bars _trendBars;
        private ExponentialMovingAverage _trendEma;
        private double _volume;
        private double _barMinutes;
        private DateTime _lastTickTime = DateTime.MinValue;
        private bool _floorBreached;
        private readonly List<DateTime> _news = new List<DateTime>();
        private readonly HashSet<long> _breakevenDone = new HashSet<long>();

        // ================================================================== Lifecycle
        protected override void OnStart()
        {
            if (TimeFrame != TimeFrame.Minute && TimeFrame != TimeFrame.Minute5 &&
                TimeFrame != TimeFrame.Minute15 && TimeFrame != TimeFrame.Minute30)
            {
                Print("MicroLotBreakoutBot is designed for M5/M15 charts (current: {0}). Stopping.", TimeFrame);
                Stop();
                return;
            }

            _volume = ResolveVolume();
            if (_volume <= 0)
            {
                Stop();
                return;
            }

            _atr = Indicators.AverageTrueRange(AtrPeriod, MovingAverageType.Simple);
            _trendBars = MarketData.GetBars(MapTimeFrame(TrendTimeFrame));
            _trendEma = Indicators.ExponentialMovingAverage(_trendBars.ClosePrices, TrendEmaPeriod);
            _barMinutes = EstimateBarMinutes();

            ParseNewsTimes();
            PendingOrders.Filled += OnOrderFilled;
            Positions.Closed += OnPositionClosed;

            Print("Started on {0} {1}. Volume={2} units ({3} lots). PipSize={4} PipValue={5}",
                SymbolName, TimeFrame, _volume, Symbol.VolumeInUnitsToQuantity(_volume), Symbol.PipSize, Symbol.PipValue);

            PrintFeasibilityReport();
        }

        protected override void OnStop()
        {
            PendingOrders.Filled -= OnOrderFilled;
            Positions.Closed -= OnPositionClosed;
        }

        protected override void OnTick()
        {
            try
            {
                if (!IsQuoteValid())
                    return;
                _lastTickTime = Server.Time;

                CheckEquityFloor();
                ManageBreakeven();
            }
            catch (Exception ex)
            {
                Print("OnTick error: {0}", ex.Message);
            }
        }

        protected override void OnBar()
        {
            try
            {
                int i = Bars.Count - 2; // last closed bar
                if (i < Math.Max(BoxBars, AtrPeriod) + 2 || _floorBreached)
                    return;

                HandleClock();

                string reason;
                if (!CanOpenNew(out reason))
                    return;

                TryPlaceSetup(i);
            }
            catch (Exception ex)
            {
                Print("OnBar error: {0}", ex);
            }
        }

        // ================================================================== Setup
        private void TryPlaceSetup(int i)
        {
            double atr = _atr.Result[i];
            if (double.IsNaN(atr) || atr <= 0)
                return;

            double hi = double.MinValue, lo = double.MaxValue;
            for (int j = i - BoxBars + 1; j <= i; j++)
            {
                hi = Math.Max(hi, Bars.HighPrices[j]);
                lo = Math.Min(lo, Bars.LowPrices[j]);
            }
            double height = hi - lo;
            if (height > BoxMaxAtr * atr || height < BoxMinAtr * atr)
                return; // not a compression box

            int trend = TrendBias();
            TradeType side;
            if (trend > 0 && Direction != MicroDirection.ShortOnly)
                side = TradeType.Buy;
            else if (trend < 0 && Direction != MicroDirection.LongOnly)
                side = TradeType.Sell;
            else
                return;

            double buffer = BufferAtr * atr;
            double spread = Symbol.Spread;
            double entry, sl, tp;
            // Bars are Bid-based: buy stops trigger on Ask and short stops are hit on Ask,
            // so the spread is added on those sides.
            if (side == TradeType.Buy)
            {
                entry = hi + buffer + spread;
                sl = lo - buffer;
                tp = entry + RewardR * (entry - sl);
                if (entry <= Symbol.Ask)
                    return;
            }
            else
            {
                entry = lo - buffer;
                sl = hi + buffer + spread;
                tp = entry - RewardR * (sl - entry);
                if (entry >= Symbol.Bid)
                    return;
            }

            entry = Math.Round(entry, Symbol.Digits);
            sl = Math.Round(sl, Symbol.Digits);
            tp = Math.Round(tp, Symbol.Digits);

            double riskPrice = Math.Abs(entry - sl);
            double riskCash = CashForPriceMove(riskPrice);
            double cap = RiskCapCash();
            if (riskCash > cap)
            {
                Print("SKIP {0}: box setup risks ${1:F2} at {2} lots > cap ${3:F2} ({4}% of equity).",
                    side, riskCash, Symbol.VolumeInUnitsToQuantity(_volume), cap, MaxRiskPercent);
                return;
            }

            if (spread / Symbol.PipSize > MaxSpreadPips || spread > MaxSpreadOfRisk * riskPrice)
            {
                Print("SKIP {0}: spread {1:F1} pips too wide for a {2:F1}-pip stop.",
                    side, spread / Symbol.PipSize, riskPrice / Symbol.PipSize);
                return;
            }
            if ((Server.Time - _lastTickTime).TotalSeconds > StaleQuoteSeconds)
            {
                Print("SKIP {0}: quotes are stale (last tick {1:HH:mm:ss}).", side, _lastTickTime);
                return;
            }

            DateTime expiry = Server.Time.AddMinutes(OrderExpiryBars * _barMinutes);
            DateTime sessionEnd = CurrentSessionEnd(Server.Time);
            if (sessionEnd > Server.Time && sessionEnd < expiry)
                expiry = sessionEnd;

            TradeResult r = PlaceStopLimitOrder(side, SymbolName, _volume, entry, StopLimitRangePips, BotLabel,
                sl, tp, ProtectionType.Absolute, expiry);

            if (r.IsSuccessful)
                Print("ORDER {0} stop-limit @ {1} | SL {2} | TP {3} | risk ${4:F2} ({5:F1}% eq) | box {6:F2} = {7:F2} ATR | expires {8:HH:mm}",
                    side, entry, sl, tp, riskCash, riskCash / Account.Equity * 100.0, height, height / atr, expiry);
            else
                Print("Order placement failed: {0} (setup re-evaluated next bar).", r.Error);
        }

        // +1 = uptrend, -1 = downtrend, 0 = no clear trend. Uses the last CLOSED trend bar.
        private int TrendBias()
        {
            int t = _trendBars.Count - 2;
            if (t < TrendEmaPeriod + TrendSlopeBars)
                return 0;

            double ema = _trendEma.Result[t];
            double emaPrev = _trendEma.Result[t - TrendSlopeBars];
            double close = _trendBars.ClosePrices[t];
            if (double.IsNaN(ema) || double.IsNaN(emaPrev))
                return 0;

            if (close > ema && ema > emaPrev)
                return 1;
            if (close < ema && ema < emaPrev)
                return -1;
            return 0;
        }

        // ================================================================== Guards
        private bool CanOpenNew(out string reason)
        {
            DateTime now = Server.Time;
            reason = null;

            if (!InSession(now)) { reason = "outside session"; return false; }
            if (PastFlatTime(now)) { reason = "past flat time"; return false; }
            if (InNewsWindow(now)) { reason = "news window"; return false; }
            if (OurPositions().Any() || OurOrders().Any()) { reason = "already engaged"; return false; }

            DateTime dayStart = now.Date;
            int tradesToday = OurHistory().Count(t => t.EntryTime >= dayStart);
            if (tradesToday >= MaxTradesPerDay) { reason = "daily trade cap"; return false; }

            var closedToday = OurHistory().Where(t => t.ClosingTime >= dayStart).OrderByDescending(t => t.ClosingTime).ToList();
            double dayPnl = closedToday.Sum(t => t.NetProfit);
            if (dayPnl <= -Account.Balance * MaxDailyLossPercent / 100.0)
            {
                reason = "daily loss cap";
                return false;
            }

            int streak = 0;
            foreach (HistoricalTrade t in closedToday)
            {
                if (t.NetProfit < 0) streak++;
                else break;
            }
            if (streak >= MaxDailyLossStreak) { reason = "loss streak today"; return false; }

            return true;
        }

        private double RiskCapCash()
        {
            double cap = Account.Equity * MaxRiskPercent / 100.0;
            if (MaxRiskCash > 0)
                cap = Math.Min(cap, MaxRiskCash);
            return cap;
        }

        private void CheckEquityFloor()
        {
            if (_floorBreached || EquityFloor <= 0 || Account.Equity >= EquityFloor)
                return;

            _floorBreached = true;
            Print("EQUITY FLOOR: equity {0:F2} < {1:F2}. Flattening and stopping the bot.", Account.Equity, EquityFloor);
            CancelOurOrders();
            CloseOurPositions("equity floor");
            Stop();
        }

        // ================================================================== Clock / position management
        private void HandleClock()
        {
            DateTime now = Server.Time;

            if (PastFlatTime(now))
            {
                CancelOurOrders();
                CloseOurPositions("flat time");
                return;
            }
            if (!InSession(now) || InNewsWindow(now))
                CancelOurOrders();
        }

        private void ManageBreakeven()
        {
            if (BreakevenR <= 0)
                return;

            foreach (Position pos in OurPositions().ToArray())
            {
                if (!pos.StopLoss.HasValue)
                {
                    Print("Position {0} has no stop - closing for safety.", pos.Id);
                    ClosePosition(pos);
                    continue;
                }
                if (_breakevenDone.Contains(pos.Id))
                    continue;

                double initialRisk = pos.TakeProfit.HasValue
                    ? Math.Abs(pos.TakeProfit.Value - pos.EntryPrice) / RewardR
                    : Math.Abs(pos.EntryPrice - pos.StopLoss.Value);
                if (initialRisk <= 0)
                    continue;

                bool isBuy = pos.TradeType == TradeType.Buy;
                double favourable = isBuy ? Symbol.Bid - pos.EntryPrice : pos.EntryPrice - Symbol.Ask;
                if (favourable < BreakevenR * initialRisk)
                    continue;

                _breakevenDone.Add(pos.Id);
                double be = Math.Round(pos.EntryPrice, Symbol.Digits);
                bool improves = isBuy ? be > pos.StopLoss.Value : be < pos.StopLoss.Value;
                if (!improves)
                    continue;

                TradeResult r = pos.ModifyStopLossPrice(be);
                if (r.IsSuccessful)
                    Print("Breakeven: {0} stop -> {1}", pos.Id, be);
                else
                    Print("Breakeven move failed for {0}: {1} (original stop remains).", pos.Id, r.Error);
            }
        }

        private void OnOrderFilled(PendingOrderFilledEventArgs args)
        {
            Position p = args.Position;
            if (p == null || !IsOurs(p))
                return;

            Print("FILLED {0} @ {1} | SL {2} | TP {3}", p.TradeType, p.EntryPrice, p.StopLoss, p.TakeProfit);
            if (!p.StopLoss.HasValue)
            {
                Print("Filled without a stop - closing immediately.");
                ClosePosition(p);
            }
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            Position p = args.Position;
            if (!IsOurs(p))
                return;
            _breakevenDone.Remove(p.Id);
            Print("CLOSED {0} | reason={1} | net={2:F2} | equity={3:F2}", p.TradeType, args.Reason, p.NetProfit, Account.Equity);
        }

        private void CancelOurOrders()
        {
            foreach (PendingOrder o in OurOrders().ToArray())
            {
                TradeResult r = CancelPendingOrder(o);
                if (!r.IsSuccessful)
                    Print("Cancel failed for order {0}: {1}", o.Id, r.Error);
            }
        }

        private void CloseOurPositions(string why)
        {
            foreach (Position p in OurPositions().ToArray())
            {
                TradeResult r = ClosePosition(p);
                if (r.IsSuccessful)
                    Print("Closed {0} ({1}).", p.Id, why);
                else
                    Print("Close failed for {0} ({1}): {2} - server-side stop remains.", p.Id, why, r.Error);
            }
        }

        // ================================================================== Feasibility report
        // Measured on this broker's own history so the account-size verdict isn't a guess.
        private void PrintFeasibilityReport()
        {
            int last = Bars.Count - 2;
            int first = Math.Max(Math.Max(BoxBars, AtrPeriod) + 1, last - FeasibilityBars);
            if (last - first < 100)
            {
                Print("FEASIBILITY: not enough history loaded for a report.");
                return;
            }

            double spread = IsQuoteValid() ? Symbol.Spread : 0;
            double cap = RiskCapCash();
            var atrs = new List<double>();
            var setupRisks = new List<double>();
            int affordable = 0;

            for (int j = first; j <= last; j++)
            {
                double atr = _atr.Result[j];
                if (double.IsNaN(atr) || atr <= 0)
                    continue;
                if (!InSession(Bars.OpenTimes[j]))
                    continue;
                atrs.Add(atr);

                double hi = double.MinValue, lo = double.MaxValue;
                for (int k = j - BoxBars + 1; k <= j; k++)
                {
                    hi = Math.Max(hi, Bars.HighPrices[k]);
                    lo = Math.Min(lo, Bars.LowPrices[k]);
                }
                double h = hi - lo;
                if (h > BoxMaxAtr * atr || h < BoxMinAtr * atr)
                    continue;

                double risk = CashForPriceMove(h + 2 * BufferAtr * atr + spread);
                setupRisks.Add(risk);
                if (risk <= cap)
                    affordable++;
            }

            if (atrs.Count == 0)
            {
                Print("FEASIBILITY: no in-session bars in the sample.");
                return;
            }

            double medAtr = Median(atrs);
            Print("FEASIBILITY | sample {0} in-session {1} bars | {2} lots = ${3:F2} per 1.00 price move | risk cap ${4:F2} ({5}% of ${6:F2})",
                atrs.Count, TimeFrame, Symbol.VolumeInUnitsToQuantity(_volume), CashForPriceMove(1.0), cap, MaxRiskPercent, Account.Equity);
            Print("FEASIBILITY | median ATR({0}) = {1:F2} -> a 1-ATR stop costs ${2:F2} ({3:F1}% of equity)",
                AtrPeriod, medAtr, CashForPriceMove(medAtr), CashForPriceMove(medAtr) / Account.Equity * 100.0);

            if (setupRisks.Count == 0)
            {
                Print("FEASIBILITY | no compression boxes found with current settings.");
                return;
            }

            double pct = 100.0 * affordable / setupRisks.Count;
            Print("FEASIBILITY | compression setups: {0} | affordable: {1} ({2:F0}%) | median setup risk ${3:F2}",
                setupRisks.Count, affordable, pct, Median(setupRisks));

            if (pct < 10)
                Print("VERDICT: under 10% of setups fit a ${0:F2} risk cap - {1} on {2} is NOT suitable for this account. " +
                      "Try a calmer symbol or a lower timeframe, or accept that the bot will rarely trade.", cap, SymbolName, TimeFrame);
            else if (pct < 40)
                Print("VERDICT: only some setups fit - expect few trades. Backtest before going further.");
            else
                Print("VERDICT: most setups fit the risk cap - account size is workable for this symbol/timeframe.");
        }

        // ================================================================== Helpers
        private double CashForPriceMove(double priceDistance)
        {
            return priceDistance / Symbol.PipSize * Symbol.PipValue * _volume;
        }

        private double ResolveVolume()
        {
            double units = Symbol.NormalizeVolumeInUnits(Symbol.QuantityToVolumeInUnits(FixedLots), RoundingMode.Down);
            if (units < Symbol.VolumeInUnitsMin)
            {
                Print("Fixed Lots {0} is below the broker minimum ({1} units).", FixedLots, Symbol.VolumeInUnitsMin);
                return 0;
            }
            return units;
        }

        private bool IsQuoteValid()
        {
            double bid = Symbol.Bid, ask = Symbol.Ask;
            return !double.IsNaN(bid) && !double.IsNaN(ask) && bid > 0 && ask > 0 && ask >= bid;
        }

        private IEnumerable<Position> OurPositions()
        {
            return Positions.Where(IsOurs);
        }

        private bool IsOurs(Position p)
        {
            return p.SymbolName == SymbolName && p.Label == BotLabel;
        }

        private IEnumerable<PendingOrder> OurOrders()
        {
            return PendingOrders.Where(o => o.SymbolName == SymbolName && o.Label == BotLabel);
        }

        private IEnumerable<HistoricalTrade> OurHistory()
        {
            return History.Where(t => t.SymbolName == SymbolName && t.Label == BotLabel);
        }

        private static int Minutes(int hhmm)
        {
            return (hhmm / 100) * 60 + hhmm % 100;
        }

        private bool InSession(DateTime t)
        {
            if (t.DayOfWeek == DayOfWeek.Saturday || t.DayOfWeek == DayOfWeek.Sunday)
                return false;
            int m = t.Hour * 60 + t.Minute;
            return (m >= Minutes(Session1Start) && m < Minutes(Session1End)) ||
                   (m >= Minutes(Session2Start) && m < Minutes(Session2End));
        }

        private DateTime CurrentSessionEnd(DateTime t)
        {
            int m = t.Hour * 60 + t.Minute;
            if (m >= Minutes(Session1Start) && m < Minutes(Session1End))
                return t.Date.AddMinutes(Minutes(Session1End));
            if (m >= Minutes(Session2Start) && m < Minutes(Session2End))
                return t.Date.AddMinutes(Minutes(Session2End));
            return t;
        }

        private bool PastFlatTime(DateTime t)
        {
            int flat = t.DayOfWeek == DayOfWeek.Friday ? FridayFlatTimeUtc : FlatTimeUtc;
            return t.Hour * 60 + t.Minute >= Minutes(flat);
        }

        private bool InNewsWindow(DateTime t)
        {
            foreach (DateTime n in _news)
                if (Math.Abs((t - n).TotalMinutes) <= NewsWindowMinutes)
                    return true;
            return false;
        }

        private void ParseNewsTimes()
        {
            if (string.IsNullOrWhiteSpace(NewsTimes))
                return;
            foreach (string raw in NewsTimes.Split(new[] { ',', ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                DateTime d;
                if (DateTime.TryParseExact(raw.Trim(), "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out d))
                    _news.Add(d);
                else
                    Print("Ignoring invalid news time '{0}' (expected yyyy-MM-dd HH:mm, UTC).", raw);
            }
            Print("{0} news time(s) loaded.", _news.Count);
        }

        private double EstimateBarMinutes()
        {
            double best = double.MaxValue;
            for (int j = Math.Max(1, Bars.Count - 50); j < Bars.Count; j++)
            {
                double m = (Bars.OpenTimes[j] - Bars.OpenTimes[j - 1]).TotalMinutes;
                if (m > 0 && m < best)
                    best = m;
            }
            return best == double.MaxValue ? 5 : best;
        }

        private static TimeFrame MapTimeFrame(MicroTrendTimeFrame tf)
        {
            switch (tf)
            {
                case MicroTrendTimeFrame.Minute30: return TimeFrame.Minute30;
                case MicroTrendTimeFrame.Hour4: return TimeFrame.Hour4;
                default: return TimeFrame.Hour;
            }
        }

        private static double Median(List<double> values)
        {
            var sorted = values.OrderBy(v => v).ToList();
            return sorted[sorted.Count / 2];
        }
    }
}
