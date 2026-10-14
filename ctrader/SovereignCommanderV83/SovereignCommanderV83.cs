// SovereignCommander V83: rebuild of the user's V82 "DynamicConclave" M5 gold pullback bot.
// The V82 signal is kept (pullback to the fast EMA, M5 9/21 EMA stack, H1 200 EMA filter);
// the risk and execution layers are replaced with the ones validated in MicroLotBreakoutBot:
//   - every distance in ATR, never in hardcoded dollars or % of price
//   - stop-slippage floor: no stop tighter than ExpectedStopSlippage / MaxSlippageShare
//   - structural stop is never clamped tighter; a too-wide stop skips the trade
//   - %-equity risk cap, daily loss count AND cash cap rebuilt from History, equity floor
//   - market-range orders (slippage cap), spread / quote / news checks, DoubleTick stops
//   - chandelier trail from the extreme since entry, breakeven, missing-stop repair
//   - CONFIG at start and RUN SUMMARY at stop, so a run explains itself
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum V83Direction { Both, LongOnly, ShortOnly }
    public enum V83Sizing { FixedLots, RiskPercent }
    public enum V83StopTrigger { SingleTick, DoubleTick }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class SovereignCommander_V83 : Robot
    {
        // ---------- 1. Risk ----------
        [Parameter("Trade Direction", Group = "1. Risk", DefaultValue = V83Direction.Both)]
        public V83Direction Direction { get; set; }

        [Parameter("Sizing Mode", Group = "1. Risk", DefaultValue = V83Sizing.FixedLots)]
        public V83Sizing SizingMode { get; set; }

        [Parameter("Fixed Lots", Group = "1. Risk", DefaultValue = 0.01, MinValue = 0.01, Step = 0.01)]
        public double FixedLots { get; set; }

        [Parameter("Risk % (RiskPercent mode)", Group = "1. Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 5)]
        public double RiskPercent { get; set; }

        [Parameter("Max Risk % per Trade (skip above)", Group = "1. Risk", DefaultValue = 3.0, MinValue = 0.5, MaxValue = 10)]
        public double MaxRiskPercent { get; set; }

        [Parameter("Max Losing Trades per Day", Group = "1. Risk", DefaultValue = 2, MinValue = 1, MaxValue = 5)]
        public int MaxDailyLosses { get; set; }

        [Parameter("Max Daily Loss % of Balance", Group = "1. Risk", DefaultValue = 6.0, MinValue = 1, MaxValue = 20)]
        public double MaxDailyLossPercent { get; set; }

        [Parameter("Equity Floor $ (0 = off)", Group = "1. Risk", DefaultValue = 120, MinValue = 0)]
        public double EquityFloor { get; set; }

        [Parameter("Commission $ per Lot (round turn)", Group = "1. Risk", DefaultValue = 8.0, MinValue = 0)]
        public double CommissionPerLot { get; set; }

        // ---------- 2. Sessions (UTC, HHMM) ----------
        [Parameter("Session A Start (HHMM)", Group = "2. Sessions UTC", DefaultValue = 800, MinValue = 0, MaxValue = 2359)]
        public int SessionAStart { get; set; }

        [Parameter("Session A End (HHMM)", Group = "2. Sessions UTC", DefaultValue = 1100, MinValue = 0, MaxValue = 2359)]
        public int SessionAEnd { get; set; }

        [Parameter("Session B Start (HHMM)", Group = "2. Sessions UTC", DefaultValue = 1400, MinValue = 0, MaxValue = 2359)]
        public int SessionBStart { get; set; }

        [Parameter("Session B End (HHMM)", Group = "2. Sessions UTC", DefaultValue = 1630, MinValue = 0, MaxValue = 2359)]
        public int SessionBEnd { get; set; }

        [Parameter("Enable Asian Session 0100-0600", Group = "2. Sessions UTC", DefaultValue = false)]
        public bool EnableAsian { get; set; }

        [Parameter("News Blackout (yyyy-MM-dd HH:mm;...)", Group = "2. Sessions UTC", DefaultValue = "")]
        public string NewsTimes { get; set; }

        [Parameter("News Blackout +/- Minutes", Group = "2. Sessions UTC", DefaultValue = 30, MinValue = 0)]
        public int NewsWindowMinutes { get; set; }

        // ---------- 3. Signal (V82 logic, ATR-scaled) ----------
        [Parameter("ATR Period", Group = "3. Signal", DefaultValue = 14, MinValue = 2)]
        public int AtrPeriod { get; set; }

        [Parameter("Fast EMA", Group = "3. Signal", DefaultValue = 9, MinValue = 2)]
        public int FastEmaPeriod { get; set; }

        [Parameter("Slow EMA", Group = "3. Signal", DefaultValue = 21, MinValue = 3)]
        public int SlowEmaPeriod { get; set; }

        [Parameter("H1 Trend EMA", Group = "3. Signal", DefaultValue = 200, MinValue = 10)]
        public int H1EmaPeriod { get; set; }

        [Parameter("H1 EMA Slope Bars (0 = off)", Group = "3. Signal", DefaultValue = 10, MinValue = 0)]
        public int H1SlopeBars { get; set; }

        [Parameter("Pullback Band (x ATR)", Group = "3. Signal", DefaultValue = 0.25, MinValue = 0, Step = 0.05)]
        public double PullbackBandAtr { get; set; }

        [Parameter("Swing Lookback Bars", Group = "3. Signal", DefaultValue = 10, MinValue = 2)]
        public int SwingLookback { get; set; }

        // ---------- 4. Stops & exits ----------
        [Parameter("Stop Buffer beyond Swing (x ATR)", Group = "4. Stops", DefaultValue = 0.5, MinValue = 0, Step = 0.1)]
        public double StopBufferAtr { get; set; }

        [Parameter("Min Stop (x ATR)", Group = "4. Stops", DefaultValue = 1.5, MinValue = 0.5, Step = 0.1)]
        public double MinStopAtr { get; set; }

        [Parameter("Max Stop (x ATR, skip above)", Group = "4. Stops", DefaultValue = 4.0, MinValue = 1, Step = 0.1)]
        public double MaxStopAtr { get; set; }

        [Parameter("Expected Stop Slippage $", Group = "4. Stops", DefaultValue = 0.8, MinValue = 0, Step = 0.05)]
        public double ExpectedStopSlippage { get; set; }

        [Parameter("Max Slippage Share of Risk", Group = "4. Stops", DefaultValue = 0.20, MinValue = 0.05, MaxValue = 1, Step = 0.05)]
        public double MaxSlippageShare { get; set; }

        [Parameter("Take Profit (R, 0 = trail only)", Group = "4. Stops", DefaultValue = 1.5, MinValue = 0, Step = 0.25)]
        public double TakeProfitR { get; set; }

        [Parameter("Breakeven at (R, 0 = off)", Group = "4. Stops", DefaultValue = 0, MinValue = 0, Step = 0.25)]
        public double BreakevenR { get; set; }

        [Parameter("Trail Start (R)", Group = "4. Stops", DefaultValue = 1.5, MinValue = 0, Step = 0.25)]
        public double TrailStartR { get; set; }

        [Parameter("Chandelier Trail (x ATR)", Group = "4. Stops", DefaultValue = 2.5, MinValue = 0.5, Step = 0.1)]
        public double TrailAtr { get; set; }

        [Parameter("Stop Trigger", Group = "4. Stops", DefaultValue = V83StopTrigger.DoubleTick)]
        public V83StopTrigger StopTrigger { get; set; }

        // ---------- 5. Execution ----------
        [Parameter("Max Spread (pips)", Group = "5. Execution", DefaultValue = 6, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Max Entry Slippage (pips)", Group = "5. Execution", DefaultValue = 3, MinValue = 0)]
        public double MaxEntrySlippagePips { get; set; }

        [Parameter("Order Retries (transient errors)", Group = "5. Execution", DefaultValue = 2, MinValue = 0, MaxValue = 5)]
        public int MaxRetries { get; set; }

        [Parameter("Label", Group = "5. Execution", DefaultValue = "SovV83")]
        public string BotLabel { get; set; }

        // ---------- 6. Friday ----------
        [Parameter("Friday No New Trades (HHMM)", Group = "6. Friday", DefaultValue = 1600, MinValue = 0, MaxValue = 2359)]
        public int FridayNoNew { get; set; }

        [Parameter("Friday Flatten (HHMM)", Group = "6. Friday", DefaultValue = 2000, MinValue = 0, MaxValue = 2359)]
        public int FridayFlatten { get; set; }

        private AverageTrueRange _atr;
        private ExponentialMovingAverage _fast, _slow, _h1Ema;
        private Bars _h1;
        private readonly List<DateTime> _news = new List<DateTime>();
        private readonly Dictionary<long, double> _initialRisk = new Dictionary<long, double>();
        private readonly Dictionary<string, int> _skips = new Dictionary<string, int>();
        private Signal _pending;
        private bool _floorBreached;
        private readonly List<double> _riskSkips = new List<double>();
        private bool _prevBarSignal;
        private int _signals, _fills, _rejects, _trailMoves, _beMoves, _stopRepairs;

        private class Signal
        {
            public TradeType Side;
            public double Stop;
            public int Attempts;
            public DateTime BarTime;
        }

        protected override void OnStart()
        {
            _atr = Indicators.AverageTrueRange(Bars, AtrPeriod, MovingAverageType.Exponential);
            _fast = Indicators.ExponentialMovingAverage(Bars.ClosePrices, FastEmaPeriod);
            _slow = Indicators.ExponentialMovingAverage(Bars.ClosePrices, SlowEmaPeriod);
            _h1 = MarketData.GetBars(TimeFrame.Hour);
            _h1Ema = Indicators.ExponentialMovingAverage(_h1.ClosePrices, H1EmaPeriod);

            ParseNews();
            Positions.Closed += OnPositionClosed;

            // Positions opened before a restart: rebuild initial risk from their stops.
            foreach (var p in OurPositions())
                if (p.StopLoss.HasValue)
                    _initialRisk[p.Id] = Math.Abs(p.EntryPrice - p.StopLoss.Value);

            Print("CONFIG symbol={0} tf={1} pipSize={2} pipValue/unit={3} minUnits={4} sizing={5} lots={6} risk%={7} maxRisk%={8}",
                SymbolName, TimeFrame, Symbol.PipSize, Symbol.PipValue, Symbol.VolumeInUnitsMin, SizingMode, FixedLots, RiskPercent, MaxRiskPercent);
            Print("CONFIG stop: min {0}xATR, max {1}xATR, slippage floor ${2:F2} ({3:F2}/{4:P0}), BE {5}R, trail {6}xATR from {7}R, TP {8}R, trigger {9}",
                MinStopAtr, MaxStopAtr, CostFloor(), ExpectedStopSlippage, MaxSlippageShare, BreakevenR, TrailAtr, TrailStartR, TakeProfitR, StopTrigger);
            Print("CONFIG sessions {0:D4}-{1:D4}, {2:D4}-{3:D4}, asian={4}; news events={5}; spread<={6} pips; floor ${7}",
                SessionAStart, SessionAEnd, SessionBStart, SessionBEnd, EnableAsian, _news.Count, MaxSpreadPips, EquityFloor);

            if (TimeFrame != TimeFrame.Minute5)
                Print("WARNING: V82 logic was designed for M5; this chart is {0}.", TimeFrame);
            if (CommissionPerLot <= 0)
                Print("WARNING: commission is 0; backtests will overstate profit.");
            if (MaxStopAtr <= MinStopAtr)
                Print("WARNING: Max Stop ({0}) <= Min Stop ({1}) x ATR: every trade will be skipped.", MaxStopAtr, MinStopAtr);
        }

        protected override void OnTick()
        {
            CheckEquityFloor();
            if (_floorBreached)
                return;

            FridayFlattenIfDue();
            ManageOpenPositions();

            if (_pending != null)
                TryExecute(_pending);
        }

        protected override void OnBar()
        {
            if (_floorBreached)
                return;

            _pending = null; // a signal lives for one bar only
            TrailOpenPositions();

            Signal s = Evaluate();
            // Count each setup once (first bar of a run of signal bars) and only inside a session,
            // so the summary reflects real opportunities, not the same setup re-firing every bar.
            bool fresh = s != null && !_prevBarSignal;
            _prevBarSignal = s != null;
            if (s != null && !InSession(Server.Time.Hour * 100 + Server.Time.Minute))
                s = null;
            else if (s != null && !fresh)
                s = null;
            if (s != null)
            {
                _signals++;
                _pending = s;
                TryExecute(s);
            }
        }

        protected override void OnError(Error error)
        {
            // API docs: "Called if there is an error executing a trade operation". Logged only;
            // every order call below also checks its own TradeResult and decides retry vs drop.
            Print("ERROR {0}", error.Code);
        }

        protected override void OnStop()
        {
            var ours = History.Where(t => t.SymbolName == SymbolName && t.Label == BotLabel).ToList();
            double net = ours.Sum(t => t.NetProfit);
            int wins = ours.Count(t => t.NetProfit > 0);
            Print("RUN SUMMARY trades={0} wins={1} ({2:P1}) net=${3:F2} signals={4} fills={5} rejects={6} BE={7} trailMoves={8} stopRepairs={9}",
                ours.Count, wins, ours.Count > 0 ? (double)wins / ours.Count : 0, net, _signals, _fills, _rejects, _beMoves, _trailMoves, _stopRepairs);
            foreach (var kv in _skips.OrderByDescending(k => k.Value))
                Print("RUN SUMMARY skipped: {0} x{1}", kv.Key, kv.Value);
            if (_riskSkips.Count > 0)
            {
                var r = _riskSkips.OrderBy(x => x).ToList();
                Func<double, double> q = f => r[Math.Min(r.Count - 1, (int)(f * r.Count))];
                Print("RUN SUMMARY risk-skipped setups: n={0} risk$ min {1:F2} / median {2:F2} / p75 {3:F2} / p90 {4:F2}; equity needed at {5}% for median: ${6:F0}, for p75: ${7:F0}",
                    r.Count, r[0], q(0.5), q(0.75), q(0.9), MaxRiskPercent, q(0.5) * 100 / MaxRiskPercent, q(0.75) * 100 / MaxRiskPercent);
            }
        }

        // ================= Signal =================

        private Signal Evaluate()
        {
            if (OurPositions().Any())
                return null; // V82 rule kept: one position at a time

            int i = Bars.Count - 2; // last closed bar
            if (i < Math.Max(SwingLookback, SlowEmaPeriod) + 2 || _h1.Count < H1EmaPeriod + H1SlopeBars + 2)
                return null;

            double atr = _atr.Result[i];
            if (double.IsNaN(atr) || atr <= 0)
                return null;

            double h1Close = _h1.ClosePrices.Last(1);
            double h1Ema = _h1Ema.Result.Last(1);
            double h1EmaPast = _h1Ema.Result.Last(1 + H1SlopeBars);
            bool h1Up = h1Close > h1Ema && (H1SlopeBars == 0 || h1Ema > h1EmaPast);
            bool h1Down = h1Close < h1Ema && (H1SlopeBars == 0 || h1Ema < h1EmaPast);

            double o = Bars.OpenPrices[i], c = Bars.ClosePrices[i], lo = Bars.LowPrices[i], hi = Bars.HighPrices[i];
            double fast = _fast.Result[i], slow = _slow.Result[i];
            double band = PullbackBandAtr * atr;

            bool longSig = Direction != V83Direction.ShortOnly && h1Up && fast > slow
                           && lo <= fast + band && c > fast && c > o;
            bool shortSig = Direction != V83Direction.LongOnly && h1Down && fast < slow
                            && hi >= fast - band && c < fast && c < o;
            if (!longSig && !shortSig)
                return null;

            var side = longSig ? TradeType.Buy : TradeType.Sell;
            double swing = longSig
                ? Enumerable.Range(i - SwingLookback + 1, SwingLookback).Min(k => Bars.LowPrices[k]) - StopBufferAtr * atr
                : Enumerable.Range(i - SwingLookback + 1, SwingLookback).Max(k => Bars.HighPrices[k]) + StopBufferAtr * atr;

            return new Signal { Side = side, Stop = swing, BarTime = Bars.OpenTimes[i + 1] };
        }

        // ================= Execution =================

        private void TryExecute(Signal s)
        {
            string why;
            if (!CanTrade(out why))
            {
                Skip(why);
                _pending = null;
                return;
            }

            if (!IsQuoteValid() || Symbol.Spread / Symbol.PipSize > MaxSpreadPips)
            {
                // Spread or quote problem is transient: keep the signal for the rest of this bar.
                if (s.Attempts == 0) Skip("spread/quote (waiting)");
                s.Attempts = Math.Max(s.Attempts, 1);
                return;
            }

            double atr = _atr.Result.Last(1);
            bool isBuy = s.Side == TradeType.Buy;
            double entry = isBuy ? Symbol.Ask : Symbol.Bid;

            // Stop already breached by price: the structure is gone.
            if (isBuy ? s.Stop >= Symbol.Bid : s.Stop <= Symbol.Ask)
            {
                Skip("price through structural stop");
                _pending = null;
                return;
            }

            double dist = Math.Abs(entry - s.Stop);
            double minDist = Math.Max(MinStopAtr * atr, CostFloor());
            if (dist > MaxStopAtr * atr)
            {
                Skip("structural stop wider than max");
                _pending = null;
                return;
            }
            dist = Math.Max(dist, minDist); // only ever widen, never tighten inside structure

            double units = VolumeFor(dist, out why);
            if (units <= 0)
            {
                Skip(why);
                _pending = null;
                return;
            }

            double margin = Symbol.GetEstimatedMargin(s.Side, units);
            if (margin > Account.FreeMargin * 0.8)
            {
                Skip("insufficient free margin");
                _pending = null;
                return;
            }

            double slPips = dist / Symbol.PipSize;
            double? tpPips = TakeProfitR > 0 ? slPips * TakeProfitR : (double?)null;
            var trigger = StopTrigger == V83StopTrigger.DoubleTick ? StopTriggerMethod.DoubleTrade : StopTriggerMethod.Trade;

            var r = ExecuteMarketRangeOrder(s.Side, SymbolName, units, MaxEntrySlippagePips, entry, BotLabel,
                slPips, tpPips, "V83", false, trigger);

            if (r.IsSuccessful && r.Position != null)
            {
                _fills++;
                _pending = null;
                _initialRisk[r.Position.Id] = dist;
                Print("FILLED {0} {1} units @ {2} SL {3} risk ${4:F2} ({5:F2}% eq) atr {6:F2}",
                    s.Side, units, r.Position.EntryPrice, r.Position.StopLoss, CashFor(dist, units),
                    CashFor(dist, units) / Account.Equity * 100, atr);
                if (!r.Position.StopLoss.HasValue)
                    RepairStop(r.Position, dist);
                return;
            }

            _rejects++;
            s.Attempts++;
            bool transient = r.Error == ErrorCode.TechnicalError || r.Error == ErrorCode.Timeout
                             || r.Error == ErrorCode.Disconnected;
            Print("ORDER FAILED {0} attempt {1}: {2}", s.Side, s.Attempts, r.Error);
            if (!transient || s.Attempts > MaxRetries)
                _pending = null; // non-transient (no money, bad volume, slippage exceeded...) drops the signal
        }

        private bool CanTrade(out string reason)
        {
            reason = null;
            DateTime now = Server.Time;
            int hhmm = now.Hour * 100 + now.Minute;

            if (!InSession(hhmm)) { reason = "outside session"; return false; }
            if (now.DayOfWeek == DayOfWeek.Friday && hhmm >= FridayNoNew) { reason = "friday cutoff"; return false; }
            if (_news.Any(n => Math.Abs((now - n).TotalMinutes) <= NewsWindowMinutes)) { reason = "news blackout"; return false; }

            // Daily limits from History: survives restarts, counts only this bot, uses NET P/L.
            var today = History.Where(t => t.SymbolName == SymbolName && t.Label == BotLabel
                                           && t.ClosingTime.Date == now.Date).ToList();
            if (today.Count(t => t.NetProfit < 0) >= MaxDailyLosses) { reason = "daily loss count"; return false; }
            double dayNet = today.Sum(t => t.NetProfit);
            if (-dayNet >= Account.Balance * MaxDailyLossPercent / 100.0) { reason = "daily loss cash"; return false; }
            return true;
        }

        private bool InSession(int hhmm)
        {
            bool a = hhmm >= SessionAStart && hhmm < SessionAEnd;
            bool b = hhmm >= SessionBStart && hhmm < SessionBEnd;
            bool asia = EnableAsian && hhmm >= 100 && hhmm < 600;
            return a || b || asia;
        }

        private double VolumeFor(double dist, out string reason)
        {
            reason = null;
            double cap = Account.Equity * MaxRiskPercent / 100.0;
            double units;

            if (SizingMode == V83Sizing.FixedLots)
            {
                units = Symbol.NormalizeVolumeInUnits(Symbol.QuantityToVolumeInUnits(FixedLots), RoundingMode.Down);
            }
            else
            {
                double perUnit = CashFor(dist, 1);
                double raw = Account.Equity * RiskPercent / 100.0 / perUnit;
                // NormalizeVolumeInUnits lifts sub-minimum volumes to the minimum (seen in backtests),
                // so the minimum is checked on the raw value and the cap decides.
                units = raw < Symbol.VolumeInUnitsMin
                    ? Symbol.VolumeInUnitsMin
                    : Symbol.NormalizeVolumeInUnits(raw, RoundingMode.Down);
            }

            if (units < Symbol.VolumeInUnitsMin) { reason = "volume below broker minimum"; return 0; }
            double risk = CashFor(dist, units) + CommissionPerLot * Symbol.VolumeInUnitsToQuantity(units);
            if (risk > cap) { _riskSkips.Add(risk); reason = "risk above max % (skip, not clamp)"; return 0; }
            return units;
        }

        // ================= Position management =================

        private void ManageOpenPositions()
        {
            foreach (var p in OurPositions().ToList())
            {
                double risk;
                if (!_initialRisk.TryGetValue(p.Id, out risk) || risk <= 0)
                    continue;

                if (!p.StopLoss.HasValue)
                {
                    RepairStop(p, risk);
                    continue;
                }

                // Breakeven: entry plus round-trip commission so a BE exit is not a net loss.
                if (BreakevenR > 0 && Progress(p) >= BreakevenR * risk)
                {
                    bool isBuy = p.TradeType == TradeType.Buy;
                    double costDist = CommissionPerLot * Symbol.VolumeInUnitsToQuantity(p.VolumeInUnits)
                                      / Math.Max(1e-9, CashFor(1, p.VolumeInUnits));
                    double be = Math.Round(isBuy ? p.EntryPrice + costDist : p.EntryPrice - costDist, Symbol.Digits);
                    bool improves = isBuy ? be > p.StopLoss.Value + Symbol.PipSize : be < p.StopLoss.Value - Symbol.PipSize;
                    bool valid = isBuy ? be < Symbol.Bid - Symbol.Spread : be > Symbol.Ask + Symbol.Spread;
                    bool beyondTp = p.TakeProfit.HasValue && (isBuy ? be >= p.TakeProfit.Value : be <= p.TakeProfit.Value);
                    if (improves && valid && !beyondTp && ModifySl(p, be))
                        _beMoves++;
                }
            }
        }

        // Chandelier from the extreme since entry, on closed bars only (fewer modify calls, no
        // reaction to a single spike tick).
        private void TrailOpenPositions()
        {
            double atr = _atr.Result.Last(1);
            if (double.IsNaN(atr) || atr <= 0)
                return;

            foreach (var p in OurPositions().ToList())
            {
                double risk;
                if (!_initialRisk.TryGetValue(p.Id, out risk) || risk <= 0 || !p.StopLoss.HasValue)
                    continue;
                if (Progress(p) < TrailStartR * risk)
                    continue;

                bool isBuy = p.TradeType == TradeType.Buy;
                double extreme = isBuy ? double.MinValue : double.MaxValue;
                // bar k counts if it closed after the entry time (OpenTimes[k + 1] is its close)
                for (int k = Bars.Count - 2; k >= 0 && Bars.OpenTimes[k + 1] > p.EntryTime; k--)
                    extreme = isBuy ? Math.Max(extreme, Bars.HighPrices[k]) : Math.Min(extreme, Bars.LowPrices[k]);
                if (extreme == double.MinValue || extreme == double.MaxValue)
                    continue;

                double sl = Math.Round(isBuy ? extreme - TrailAtr * atr : extreme + TrailAtr * atr, Symbol.Digits);
                bool improves = isBuy ? sl > p.StopLoss.Value + Symbol.PipSize : sl < p.StopLoss.Value - Symbol.PipSize;
                bool valid = isBuy ? sl < Symbol.Bid - Symbol.Spread : sl > Symbol.Ask + Symbol.Spread;
                if (improves && valid && ModifySl(p, sl))
                    _trailMoves++;
            }
        }

        private double Progress(Position p)
        {
            return p.TradeType == TradeType.Buy ? Symbol.Bid - p.EntryPrice : p.EntryPrice - Symbol.Ask;
        }

        private bool ModifySl(Position p, double price)
        {
            var r = p.ModifyStopLossPrice(price);
            if (!r.IsSuccessful)
                Print("MODIFY SL FAILED pos {0} -> {1}: {2}", p.Id, price, r.Error);
            return r.IsSuccessful;
        }

        private void RepairStop(Position p, double dist)
        {
            bool isBuy = p.TradeType == TradeType.Buy;
            double sl = Math.Round(isBuy ? p.EntryPrice - dist : p.EntryPrice + dist, Symbol.Digits);
            bool valid = isBuy ? sl < Symbol.Bid : sl > Symbol.Ask;
            _stopRepairs++;
            if (valid && ModifySl(p, sl))
            {
                Print("STOP REPAIRED pos {0} at {1}", p.Id, sl);
                return;
            }
            Print("STOP REPAIR FAILED pos {0}: closing at market.", p.Id);
            var r = ClosePosition(p);
            if (!r.IsSuccessful)
                Print("CLOSE FAILED pos {0}: {1} (will retry next tick)", p.Id, r.Error);
        }

        private void FridayFlattenIfDue()
        {
            DateTime now = Server.Time;
            if (now.DayOfWeek != DayOfWeek.Friday || now.Hour * 100 + now.Minute < FridayFlatten)
                return;
            foreach (var p in OurPositions().ToList())
            {
                var r = ClosePosition(p);
                if (!r.IsSuccessful)
                    Print("FRIDAY CLOSE FAILED pos {0}: {1} (retrying next tick)", p.Id, r.Error);
            }
        }

        private void CheckEquityFloor()
        {
            if (_floorBreached || EquityFloor <= 0 || Account.Equity >= EquityFloor)
                return;
            _floorBreached = true;
            Print("EQUITY FLOOR: equity {0:F2} < {1:F2}. Flattening and stopping.", Account.Equity, EquityFloor);
            foreach (var p in OurPositions().ToList())
                ClosePosition(p);
            Stop();
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            var p = args.Position;
            if (p.SymbolName != SymbolName || p.Label != BotLabel)
                return;
            double risk;
            _initialRisk.TryGetValue(p.Id, out risk);
            _initialRisk.Remove(p.Id);
            double r = risk > 0 ? p.NetProfit / CashFor(risk, p.VolumeInUnits) : 0;
            Print("CLOSED {0} pos {1} net ${2:F2} ({3:F2}R) reason {4}", p.TradeType, p.Id, p.NetProfit, r, args.Reason);
        }

        // ================= Helpers =================

        // Smallest stop at which typical stop slippage stays under MaxSlippageShare of risk.
        private double CostFloor()
        {
            return MaxSlippageShare > 0 ? ExpectedStopSlippage / MaxSlippageShare : 0;
        }

        private double CashFor(double priceDistance, double units)
        {
            return priceDistance / Symbol.PipSize * Symbol.PipValue * units;
        }

        private bool IsQuoteValid()
        {
            double bid = Symbol.Bid, ask = Symbol.Ask;
            return !double.IsNaN(bid) && !double.IsNaN(ask) && bid > 0 && ask > 0 && ask >= bid;
        }

        private IEnumerable<Position> OurPositions()
        {
            return Positions.Where(p => p.SymbolName == SymbolName && p.Label == BotLabel);
        }

        private void Skip(string reason)
        {
            int n;
            _skips.TryGetValue(reason, out n);
            _skips[reason] = n + 1;
        }

        private void ParseNews()
        {
            if (string.IsNullOrWhiteSpace(NewsTimes))
                return;
            foreach (var part in NewsTimes.Split(new[] { ';', ',' }, StringSplitOptions.RemoveEmptyEntries))
            {
                DateTime t;
                if (DateTime.TryParseExact(part.Trim(), "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture,
                        DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out t))
                    _news.Add(t);
                else
                    Print("WARNING: cannot parse news time '{0}' (use yyyy-MM-dd HH:mm, UTC).", part.Trim());
            }
        }
    }
}
