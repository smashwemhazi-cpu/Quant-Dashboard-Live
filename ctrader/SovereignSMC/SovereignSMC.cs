// SovereignSMC: Smart Money Concepts entry engine for XAUUSD on a small, fixed-0.01-lot account.
//
// Why it exists: the V83 log printed the dollar risk of every skipped setup. With a market
// entry at the pullback close, the median M5 swing stop cost $14.56 (needs ~$485 equity at 3%).
// SMC fixes the geometry rather than the risk rule: wait for liquidity to be swept, for a
// displacement that breaks structure (CHoCH), and then buy BACK at the fair value gap with a
// LIMIT order. The stop still sits behind the sweep wick (a structural stop), but the entry is
// much closer to it, so the same structure costs a fraction of the dollars. Limit entries also
// pay no entry slippage.
//
// Setup (long; shorts are the mirror image), evaluated on closed bars:
//   1. Bias      H1 close above H1 EMA, EMA rising (optional).
//   2. Liquidity confirmed pivot lows (fractal strength N) inside the lookback, plus the Asian
//                range low of the day (optional).
//   3. Sweep     a bar wicks below a pool and closes back above it.
//   4. CHoCH     within N bars, a close above the last pivot high before the sweep, on a bar
//                whose body is >= DisplacementAtr x ATR. A close well below the sweep low
//                instead invalidates the setup (breakdown, not a sweep).
//   5. FVG       the largest bullish 3-bar gap (Low[k] > High[k-2]) in the displacement leg.
//   6. Order     limit at the FVG (EntryDepth: 0 = top edge, 0.5 = midpoint, 1 = bottom edge),
//                stop behind the sweep low + buffer, never tighter than the slippage floor.
//                Too expensive at the midpoint -> retry at the bottom edge -> otherwise skip.
//   7. Target    the external high before the sweep (opposite liquidity) when it gives
//                MinRR..MaxRR, or a fixed R multiple.
//   8. Expiry    the order expires after N bars, and is cancelled if price reaches the target
//                or breaks the stop before filling (the setup played out without us).
// Risk layers reused from MicroLotBreakoutBot: %-equity cap (skip, never clamp), slippage
// floor, daily loss count and cash from History, equity floor, sessions, news blackout,
// spread/quote checks, DoubleTick stops, end-of-day and Friday flatten, and full diagnostics.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum SmcDirection { Both, LongOnly, ShortOnly }
    public enum SmcTargetMode { Liquidity, FixedR }
    public enum SmcStopTrigger { SingleTick, DoubleTick }
    public enum SmcStopMode { SweepOnly, FvgOnly, Auto }
    public enum SmcFvgPick { Latest, Largest }
    public enum SmcLiquidity { AllPivots, MajorOnly }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class SovereignSMC : Robot
    {
        // ---------- 1. Risk ----------
        [Parameter("Trade Direction", Group = "1. Risk", DefaultValue = SmcDirection.Both)]
        public SmcDirection Direction { get; set; }

        [Parameter("Fixed Lots", Group = "1. Risk", DefaultValue = 0.01, MinValue = 0.01, Step = 0.01)]
        public double FixedLots { get; set; }

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
        [Parameter("Killzone A Start (HHMM)", Group = "2. Sessions UTC", DefaultValue = 800, MinValue = 0, MaxValue = 2359)]
        public int SessionAStart { get; set; }

        [Parameter("Killzone A End (HHMM)", Group = "2. Sessions UTC", DefaultValue = 1100, MinValue = 0, MaxValue = 2359)]
        public int SessionAEnd { get; set; }

        [Parameter("Killzone B Start (HHMM)", Group = "2. Sessions UTC", DefaultValue = 1400, MinValue = 0, MaxValue = 2359)]
        public int SessionBStart { get; set; }

        [Parameter("Killzone B End (HHMM)", Group = "2. Sessions UTC", DefaultValue = 1630, MinValue = 0, MaxValue = 2359)]
        public int SessionBEnd { get; set; }

        [Parameter("Daily Flatten (HHMM, 0 = off)", Group = "2. Sessions UTC", DefaultValue = 2030, MinValue = 0, MaxValue = 2359)]
        public int DailyFlatten { get; set; }

        [Parameter("Friday No New Orders (HHMM)", Group = "2. Sessions UTC", DefaultValue = 1600, MinValue = 0, MaxValue = 2359)]
        public int FridayNoNew { get; set; }

        [Parameter("Friday Flatten (HHMM)", Group = "2. Sessions UTC", DefaultValue = 1930, MinValue = 0, MaxValue = 2359)]
        public int FridayFlatten { get; set; }

        [Parameter("News Blackout (yyyy-MM-dd HH:mm;...)", Group = "2. Sessions UTC", DefaultValue = "")]
        public string NewsTimes { get; set; }

        [Parameter("News Blackout +/- Minutes", Group = "2. Sessions UTC", DefaultValue = 30, MinValue = 0)]
        public int NewsWindowMinutes { get; set; }

        // ---------- 3. Structure ----------
        [Parameter("ATR Period", Group = "3. Structure", DefaultValue = 14, MinValue = 2)]
        public int AtrPeriod { get; set; }

        [Parameter("Pivot Strength (bars each side)", Group = "3. Structure", DefaultValue = 2, MinValue = 1, MaxValue = 10)]
        public int PivotStrength { get; set; }

        [Parameter("Liquidity Lookback (bars)", Group = "3. Structure", DefaultValue = 48, MinValue = 10)]
        public int LiquidityLookback { get; set; }

        [Parameter("Use Asian Range as Liquidity", Group = "3. Structure", DefaultValue = true)]
        public bool UseAsianRange { get; set; }

        // AllPivots: any confirmed M-timeframe swing plus Asian range and previous-day high/low.
        // MajorOnly: only the Asian range and previous-day high/low (the pools SMC treats as
        // significant liquidity). Fewer setups, each with more resting stops behind it.
        [Parameter("Liquidity Pools", Group = "3. Structure", DefaultValue = SmcLiquidity.AllPivots)]
        public SmcLiquidity LiquidityMode { get; set; }

        [Parameter("FVG Pick", Group = "3. Structure", DefaultValue = SmcFvgPick.Latest)]
        public SmcFvgPick FvgPick { get; set; }

        [Parameter("Max Chase (fraction of target already run)", Group = "4. Orders", DefaultValue = 0.5, MinValue = 0.1, MaxValue = 1, Step = 0.1)]
        public double MaxChase { get; set; }

        [Parameter("Asian Range End (HHMM)", Group = "3. Structure", DefaultValue = 700, MinValue = 100, MaxValue = 1200)]
        public int AsianEnd { get; set; }

        [Parameter("CHoCH Window (bars after sweep)", Group = "3. Structure", DefaultValue = 12, MinValue = 2)]
        public int ChochWindow { get; set; }

        [Parameter("Displacement Body (x ATR)", Group = "3. Structure", DefaultValue = 0.8, MinValue = 0, Step = 0.1)]
        public double DisplacementAtr { get; set; }

        [Parameter("Min FVG Size (x ATR)", Group = "3. Structure", DefaultValue = 0.15, MinValue = 0, Step = 0.05)]
        public double MinFvgAtr { get; set; }

        [Parameter("Invalidate beyond Sweep (x ATR)", Group = "3. Structure", DefaultValue = 0.5, MinValue = 0, Step = 0.1)]
        public double InvalidateAtr { get; set; }

        [Parameter("Require H1 Bias", Group = "3. Structure", DefaultValue = true)]
        public bool RequireH1Bias { get; set; }

        [Parameter("H1 Bias EMA", Group = "3. Structure", DefaultValue = 200, MinValue = 10)]
        public int H1EmaPeriod { get; set; }

        [Parameter("H1 EMA Slope Bars (0 = off)", Group = "3. Structure", DefaultValue = 10, MinValue = 0)]
        public int H1SlopeBars { get; set; }

        // ---------- 4. Orders & exits ----------
        [Parameter("Entry Depth in FVG (0 top .. 1 bottom)", Group = "4. Orders", DefaultValue = 0.5, MinValue = 0, MaxValue = 1, Step = 0.25)]
        public double EntryDepth { get; set; }

        [Parameter("Stop Buffer beyond Sweep (x ATR)", Group = "4. Orders", DefaultValue = 0.2, MinValue = 0, Step = 0.05)]
        public double StopBufferAtr { get; set; }

        [Parameter("Expected Stop Slippage $", Group = "4. Orders", DefaultValue = 0.8, MinValue = 0, Step = 0.05)]
        public double ExpectedStopSlippage { get; set; }

        [Parameter("Max Slippage Share of Risk", Group = "4. Orders", DefaultValue = 0.20, MinValue = 0.05, MaxValue = 1, Step = 0.05)]
        public double MaxSlippageShare { get; set; }

        [Parameter("Target Mode", Group = "4. Orders", DefaultValue = SmcTargetMode.Liquidity)]
        public SmcTargetMode TargetMode { get; set; }

        [Parameter("Min R:R (skip below)", Group = "4. Orders", DefaultValue = 1.5, MinValue = 0.5, Step = 0.25)]
        public double MinRR { get; set; }

        [Parameter("Max R:R (target cap) / Fixed R", Group = "4. Orders", DefaultValue = 3.0, MinValue = 1, Step = 0.25)]
        public double MaxRR { get; set; }

        [Parameter("Order Expiry (bars)", Group = "4. Orders", DefaultValue = 12, MinValue = 1)]
        public int OrderExpiryBars { get; set; }

        [Parameter("Breakeven v2 at R (0 = off)", Group = "4. Orders", DefaultValue = 0, MinValue = 0, Step = 0.25)]
        public double BreakevenAtR { get; set; }

        [Parameter("Stop Trigger", Group = "4. Orders", DefaultValue = SmcStopTrigger.DoubleTick)]
        public SmcStopTrigger StopTrigger { get; set; }

        // Sweep: stop behind the sweep wick (widest, most robust). Fvg: stop behind the far edge of
        // the gap (the same level whose close-through cancels the order). Auto tries the sweep stop
        // first and falls back to the gap stop only when the sweep stop is unaffordable.
        [Parameter("Stop Mode", Group = "4. Orders", DefaultValue = SmcStopMode.Auto)]
        public SmcStopMode StopMode { get; set; }

        [Parameter("Max Spread (pips)", Group = "4. Orders", DefaultValue = 6, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Label", Group = "4. Orders", DefaultValue = "SovSMC")]
        public string BotLabel { get; set; }

        [Parameter("Verbose Setup Log", Group = "4. Orders", DefaultValue = false)]
        public bool Verbose { get; set; }

        private class Setup
        {
            public bool IsBuy;
            public double Pool;        // swept liquidity level
            public string PoolKind;
            public double SweepExtreme;
            public int SweepIndex;
            public double ChochLevel;
            public double Target;      // opposite (external) liquidity
        }

        private class OrderPlan
        {
            public bool IsBuy;
            public double Entry, Stop, Tp, Risk;
            public double FvgTop, FvgBottom;
            public string StopKind;
        }

        // Bump on every change so the log proves which code produced a run.
        private const string BuildTag = "SMC-2026-10-14e";

        private AverageTrueRange _atr;
        private Bars _h1, _d1;
        private ExponentialMovingAverage _h1Ema;
        private readonly List<DateTime> _news = new List<DateTime>();
        private readonly Dictionary<string, int> _funnel = new Dictionary<string, int>();
        private readonly Dictionary<long, double> _initialRisk = new Dictionary<long, double>();
        private readonly HashSet<long> _beDone = new HashSet<long>();
        private readonly List<double> _riskSkips = new List<double>();
        private readonly List<double> _stopSlips = new List<double>();
        private Setup _long, _short;
        private OrderPlan _livePlan;
        private int _lastSweepLongBar = -1, _lastSweepShortBar = -1;
        private double _tfMinutes = 5;
        private bool _floorBreached;

        protected override void OnStart()
        {
            _atr = Indicators.AverageTrueRange(Bars, AtrPeriod, MovingAverageType.Exponential);
            _h1 = MarketData.GetBars(TimeFrame.Hour);
            _d1 = MarketData.GetBars(TimeFrame.Daily);
            _h1Ema = Indicators.ExponentialMovingAverage(_h1.ClosePrices, H1EmaPeriod);
            _tfMinutes = EstimateTfMinutes();
            ParseNews();

            Positions.Closed += OnPositionClosed;
            PendingOrders.Filled += OnOrderFilled;
            PendingOrders.Cancelled += OnOrderCancelled;

            foreach (var p in OurPositions())
                if (p.StopLoss.HasValue)
                    _initialRisk[p.Id] = Math.Abs(p.EntryPrice - p.StopLoss.Value);

            double units = Units();
            double cap = Account.Equity * MaxRiskPercent / 100.0;
            Print("CONFIG build " + BuildTag + " (if this line is missing or shows an older tag, cTrader is running an old build: press Build)");
            Print("CONFIG symbol={0} tf={1} ({2} min) pipSize={3} pipValue/unit={4} units={5} equity={6:F2}",
                SymbolName, TimeFrame, _tfMinutes, Symbol.PipSize, Symbol.PipValue, units, Account.Equity);
            Print("CONFIG risk cap ${0:F2} ({1}%) = {2:F2} price at {3} lots; slippage floor {4:F2} price; affordable stop window {4:F2}..{2:F2}",
                cap, MaxRiskPercent, units > 0 ? PriceFor(cap - Commission(units), units) : 0, FixedLots, CostFloor());
            Print("CONFIG structure: pools " + LiquidityMode + " (+asian, prev-day H/L), fvg pick " + FvgPick + ", max chase " + MaxChase + ", pivots {0}, liquidity {1} bars, asian={2} (to {3:D4}), CHoCH {4} bars, displacement {5}xATR, FVG>={6}xATR, H1 bias={7} EMA{8}",
                PivotStrength, LiquidityLookback, UseAsianRange, AsianEnd, ChochWindow, DisplacementAtr, MinFvgAtr, RequireH1Bias, H1EmaPeriod);
            Print("CONFIG orders: depth {0}, stop mode " + StopMode + ", stop buffer {1}xATR, target {2} RR {3}..{4}, expiry {5} bars, BE {6}R, trigger {7}; killzones {8:D4}-{9:D4}, {10:D4}-{11:D4}; news {12}",
                EntryDepth, StopBufferAtr, TargetMode, MinRR, MaxRR, OrderExpiryBars, BreakevenAtR, StopTrigger,
                SessionAStart, SessionAEnd, SessionBStart, SessionBEnd, _news.Count);
            if (units > 0 && PriceFor(cap - Commission(units), units) < CostFloor())
                Print("WARNING: the risk cap is smaller than the slippage floor: no trade can pass. Raise equity or Max Risk %.");
            else if (units > 0 && PriceFor(cap - Commission(units), units) < CostFloor() * 1.25)
                Print("WARNING: affordable stop window is only {0:F2} wide ({1:F2}..{2:F2}). Expect very few or no orders. " +
                      "Equity for a 50% wider window: ${3:F0}. In backtests set Starting capital to your real balance.",
                    PriceFor(cap - Commission(units), units) - CostFloor(), CostFloor(), PriceFor(cap - Commission(units), units),
                    (CashFor(CostFloor() * 1.5, units) + Commission(units)) * 100 / MaxRiskPercent);
            if (CommissionPerLot <= 0)
                Print("WARNING: commission is 0; backtests will overstate profit.");
            if (BreakevenAtR > 0)
                Print("WARNING: breakeven at {0}R is on. In every run so far it turned +1R trades into losses after stop slippage (default 0).", BreakevenAtR);
        }

        protected override void OnTick()
        {
            CheckEquityFloor();
            if (_floorBreached)
                return;
            FlattenIfDue();
            ManageBreakeven();
            CancelIfPlayedOut();
        }

        protected override void OnBar()
        {
            if (_floorBreached)
                return;

            int i = Bars.Count - 2;
            CancelIfFvgClosedThrough(i);
            if (i < LiquidityLookback + PivotStrength * 2 + 5)
                return;
            double atr = _atr.Result[i];
            if (double.IsNaN(atr) || atr <= 0)
                return;

            // Track setups on every bar (structure forms outside killzones too); only orders are gated.
            if (Direction != SmcDirection.ShortOnly)
                _long = Advance(_long, true, i, atr);
            if (Direction != SmcDirection.LongOnly)
                _short = Advance(_short, false, i, atr);
        }

        protected override void OnError(Error error)
        {
            Print("ERROR {0}", error.Code);
        }

        protected override void OnStop()
        {
            var ours = History.Where(t => t.SymbolName == SymbolName && t.Label == BotLabel).ToList();
            double gp = ours.Where(t => t.NetProfit > 0).Sum(t => t.NetProfit);
            double gl = -ours.Where(t => t.NetProfit < 0).Sum(t => t.NetProfit);
            Print("RUN SUMMARY trades={0} wins={1} net=${2:F2} PF={3:F2} avgWin={4:F2} avgLoss={5:F2}",
                ours.Count, ours.Count(t => t.NetProfit > 0), gp - gl, gl > 0 ? gp / gl : 0,
                ours.Any(t => t.NetProfit > 0) ? ours.Where(t => t.NetProfit > 0).Average(t => t.NetProfit) : 0,
                ours.Any(t => t.NetProfit < 0) ? ours.Where(t => t.NetProfit < 0).Average(t => t.NetProfit) : 0);
            string[] order = { "sweep", "choch", "invalidated", "choch timeout", "no fvg", "fvg", "no room (RR)", "risk too high",
                               "stale: move already ran", "blocked: h1 bias", "blocked: session", "blocked: daily limit", "blocked: news", "blocked: busy", "blocked: spread",
                               "blocked: friday", "placed", "placed: sweep stop", "placed: fvg stop", "place failed", "filled", "expired", "missed: target hit before fill", "cancelled: stop broken before fill", "cancelled: fvg closed through",
                               "filled without stop (gapped)", "stop widened to floor after gap fill", "cancelled: other" };
            Print("RUN SUMMARY funnel: " + string.Join(" | ", order.Where(k => _funnel.ContainsKey(k)).Select(k => k + " " + _funnel[k])));
            int placed;
            _funnel.TryGetValue("placed", out placed);
            var blockers = _funnel.Where(k => k.Key == "risk too high" || k.Key == "no room (RR)" || k.Key == "no fvg" || k.Key.StartsWith("blocked"))
                                  .OrderByDescending(k => k.Value).ToList();
            if (placed == 0)
                Print("VERDICT: no orders placed. Main blocker: {0}.",
                    blockers.Count > 0 ? blockers[0].Key + " x" + blockers[0].Value : (_funnel.ContainsKey("sweep") ? "no CHoCH/FVG completed" : "no sweeps detected"));
            if (_riskSkips.Count > 0)
            {
                var r = _riskSkips.OrderBy(x => x).ToList();
                Print("RUN SUMMARY risk-skipped: n={0} risk$ median {1:F2} p75 {2:F2}; equity needed at {3}% for median ${4:F0}",
                    r.Count, Q(r, 0.5), Q(r, 0.75), MaxRiskPercent, Q(r, 0.5) * 100 / MaxRiskPercent);
            }
            if (_stopSlips.Count > 0)
                Print("RUN SUMMARY stop slippage: n={0} total ${1:F2} median ${2:F2}", _stopSlips.Count, _stopSlips.Sum(), Q(_stopSlips.OrderBy(x => x).ToList(), 0.5));
        }

        // ================= Setup state machine =================

        private Setup Advance(Setup s, bool isBuy, int i, double atr)
        {
            if (s != null)
            {
                // Deeper extreme after the sweep: the sweep is still forming.
                if (Beyond(Ext(i, isBuy), s.SweepExtreme, isBuy))
                    s.SweepExtreme = Ext(i, isBuy);

                // A close decisively through the pool is a real breakdown, not a liquidity grab.
                double invalidation = isBuy ? s.Pool - InvalidateAtr * atr : s.Pool + InvalidateAtr * atr;
                if (Beyond(Bars.ClosePrices[i], invalidation, isBuy))
                {
                    Count("invalidated");
                    s = null;
                }
                else if (i - s.SweepIndex > ChochWindow)
                {
                    Count("choch timeout");
                    s = null;
                }
                else if (IsDisplacementBreak(s, i, atr))
                {
                    Count("choch");
                    Log("CHOCH {0} level {1:F2} at bar {2}", isBuy ? "long" : "short", s.ChochLevel, Bars.OpenTimes[i]);
                    TryPlace(s, i, atr);
                    s = null;
                }
            }

            if (s == null)
            {
                var fresh = DetectSweep(isBuy, i, atr);
                if (fresh != null)
                    s = fresh;
            }
            return s;
        }

        private Setup DetectSweep(bool isBuy, int i, double atr)
        {
            if (isBuy ? _lastSweepLongBar == i : _lastSweepShortBar == i)
                return null;

            // Candidate pools: confirmed pivots before this bar, inside the lookback.
            var pools = new List<KeyValuePair<double, string>>();
            int newestPivot = i - 1 - PivotStrength;
            if (LiquidityMode == SmcLiquidity.AllPivots)
                for (int j = newestPivot; j >= i - LiquidityLookback && j >= PivotStrength; j--)
                    if (IsPivot(j, isBuy))
                        pools.Add(new KeyValuePair<double, string>(Ext(j, isBuy), "pivot"));
            if (_d1 != null && _d1.Count >= 2)
                pools.Add(new KeyValuePair<double, string>(isBuy ? _d1.LowPrices.Last(1) : _d1.HighPrices.Last(1), isBuy ? "pdl" : "pdh"));
            if (UseAsianRange)
            {
                double asian;
                if (AsianLevel(isBuy, i, out asian))
                    pools.Add(new KeyValuePair<double, string>(asian, "asian"));
            }
            if (pools.Count == 0)
                return null;

            double wick = Ext(i, isBuy), close = Bars.ClosePrices[i];
            // Swept = wick beyond the pool, close back inside, and the pool was still untouched
            // before this bar (already-taken liquidity is stale). Prefer the most extreme pool.
            var swept = pools.Where(p => Beyond(wick, p.Key, isBuy) && !Beyond(close, p.Key, isBuy) && !IsStale(p, isBuy, i))
                             .OrderBy(p => isBuy ? p.Key : -p.Key).ToList();
            if (swept.Count == 0)
                return null;
            var pool = swept[0];

            // CHoCH level = most recent opposite pivot before the sweep; target = external extreme.
            double choch = double.NaN;
            for (int j = i - 1 - PivotStrength; j >= i - LiquidityLookback && j >= PivotStrength; j--)
                if (IsPivot(j, !isBuy)) { choch = Ext(j, !isBuy); break; }
            if (double.IsNaN(choch))
                return null;
            double target = isBuy
                ? Enumerable.Range(i - LiquidityLookback, LiquidityLookback).Max(k => Bars.HighPrices[k])
                : Enumerable.Range(i - LiquidityLookback, LiquidityLookback).Min(k => Bars.LowPrices[k]);

            if (isBuy) _lastSweepLongBar = i; else _lastSweepShortBar = i;
            Count("sweep");
            Log("SWEEP {0} {1} pool {2:F2} wick {3:F2} close {4:F2} choch {5:F2} target {6:F2}",
                isBuy ? "long" : "short", pool.Value, pool.Key, wick, close, choch, target);
            return new Setup
            {
                IsBuy = isBuy, Pool = pool.Key, PoolKind = pool.Value, SweepExtreme = wick,
                SweepIndex = i, ChochLevel = choch, Target = target
            };
        }

        private bool IsDisplacementBreak(Setup s, int i, double atr)
        {
            double c = Bars.ClosePrices[i], o = Bars.OpenPrices[i];
            bool breaks = s.IsBuy ? c > s.ChochLevel : c < s.ChochLevel;
            bool body = Math.Abs(c - o) >= DisplacementAtr * atr && (s.IsBuy ? c > o : c < o);
            return breaks && body;
        }

        // ================= Order planning =================

        private void TryPlace(Setup s, int i, double atr)
        {
            // FVG in the leg from the sweep to the CHoCH bar: the latest qualifying gap (closest to
            // price, created by the displacement) or the largest one.
            double minGap = Math.Max(MinFvgAtr * atr, Symbol.PipSize);
            double bestSize = 0, top = 0, bottom = 0;
            for (int k = Math.Max(s.SweepIndex, 2); k <= i; k++)
            {
                double lo = s.IsBuy ? Bars.HighPrices[k - 2] : Bars.HighPrices[k];
                double hi = s.IsBuy ? Bars.LowPrices[k] : Bars.LowPrices[k - 2];
                double size = hi - lo;
                bool take = FvgPick == SmcFvgPick.Latest ? size >= minGap : size > bestSize;
                if (take) { bestSize = size; top = hi; bottom = lo; }
            }
            if (bestSize < minGap)
            {
                Count("no fvg");
                return;
            }
            Count("fvg");

            double floor = CostFloor();
            double sweepStop = s.IsBuy ? s.SweepExtreme - StopBufferAtr * atr - Symbol.Spread
                                       : s.SweepExtreme + StopBufferAtr * atr + Symbol.Spread;
            double fvgStop = s.IsBuy ? bottom - StopBufferAtr * atr - Symbol.Spread
                                     : top + StopBufferAtr * atr + Symbol.Spread;
            var attempts = new List<Tuple<double, double, string>>();
            if (StopMode != SmcStopMode.FvgOnly)
            {
                attempts.Add(Tuple.Create(EntryDepth, sweepStop, "sweep"));
                if (EntryDepth < 1.0) attempts.Add(Tuple.Create(1.0, sweepStop, "sweep"));
            }
            if (StopMode != SmcStopMode.SweepOnly)
                attempts.Add(Tuple.Create(EntryDepth, fvgStop, "fvg"));
            double units = Units();
            double cap = Account.Equity * MaxRiskPercent / 100.0;
            OrderPlan plan = null;
            double lastRisk = 0;

            // Preferred depth and stop first, then cheaper variants (see Stop Mode).
            foreach (var a in attempts)
            {
                double depth = a.Item1;
                double entry = s.IsBuy ? top - depth * (top - bottom) : bottom + depth * (top - bottom);
                double sl = a.Item2;
                if (s.IsBuy ? sl >= entry : sl <= entry)
                    continue;
                if (Math.Abs(entry - sl) < floor)
                    sl = s.IsBuy ? entry - floor : entry + floor; // widen only
                double dist = Math.Abs(entry - sl);
                lastRisk = CashFor(dist, units) + Commission(units);
                if (lastRisk > cap)
                    continue;

                double tpDist = TargetMode == SmcTargetMode.FixedR
                    ? MaxRR * dist
                    : Math.Min(Math.Abs(s.Target - entry), MaxRR * dist);
                if (TargetMode == SmcTargetMode.Liquidity && (s.IsBuy ? s.Target <= entry : s.Target >= entry))
                    tpDist = 0;
                if (tpDist < MinRR * dist)
                {
                    Count("no room (RR)");
                    return;
                }
                plan = new OrderPlan
                {
                    IsBuy = s.IsBuy, Entry = Round(entry), Stop = Round(sl),
                    Tp = Round(s.IsBuy ? entry + tpDist : entry - tpDist), Risk = dist,
                    FvgTop = top, FvgBottom = bottom, StopKind = a.Item3
                };
                break;
            }
            if (plan == null)
            {
                Count("risk too high");
                _riskSkips.Add(lastRisk);
                return;
            }

            if (!H1Allows(s.IsBuy))
            {
                Count("blocked: h1 bias");
                return;
            }

            string why;
            if (!CanOpen(out why))
            {
                Count("blocked: " + why);
                return;
            }

            // The limit must sit on the correct side of the market; otherwise the move already
            // came back through the gap and the entry is no longer a discount.
            if (s.IsBuy ? plan.Entry >= Symbol.Ask : plan.Entry <= Symbol.Bid)
            {
                Count("no room (RR)");
                return;
            }

            // Run-1 M5 log: 9 of 15 orders were cancelled within a minute because price was
            // already at the target when they were placed. If the move has already run more than
            // MaxChase of the way to the target, the retrace entry is stale.
            double ran = s.IsBuy ? Symbol.Bid - plan.Entry : plan.Entry - Symbol.Ask;
            if (ran >= MaxChase * Math.Abs(plan.Tp - plan.Entry))
            {
                Count("stale: move already ran");
                return;
            }

            var side = s.IsBuy ? TradeType.Buy : TradeType.Sell;
            var trigger = StopTrigger == SmcStopTrigger.DoubleTick ? StopTriggerMethod.DoubleTrade : StopTriggerMethod.Trade;
            DateTime expiry = Server.Time.AddMinutes(OrderExpiryBars * _tfMinutes);
            var r = PlaceLimitOrder(side, SymbolName, units, plan.Entry, BotLabel, plan.Stop, plan.Tp,
                ProtectionType.Absolute, expiry, s.PoolKind, false, trigger);
            if (!r.IsSuccessful)
            {
                Count("place failed");
                Print("ORDER FAILED {0} @ {1}: {2}", side, plan.Entry, r.Error);
                return;
            }
            _livePlan = plan;
            Count("placed");
            Count("placed: " + plan.StopKind + " stop");
            Print("ORDER {13}-stop {0} limit {1} SL {2} TP {3} | risk ${4:F2} ({5:F2}% eq) RR {6:F2} | pool {7} {8:F2} sweep {9:F2} FVG {10:F2}-{11:F2} atr {12:F2}",
                side, plan.Entry, plan.Stop, plan.Tp, CashFor(plan.Risk, units) + Commission(units),
                (CashFor(plan.Risk, units) + Commission(units)) / Account.Equity * 100, Math.Abs(plan.Tp - plan.Entry) / plan.Risk,
                s.PoolKind, s.Pool, s.SweepExtreme, bottom, top, atr, plan.StopKind);
        }

        private bool CanOpen(out string why)
        {
            why = null;
            DateTime now = Server.Time;
            int hhmm = now.Hour * 100 + now.Minute;
            bool inZone = (hhmm >= SessionAStart && hhmm < SessionAEnd) || (hhmm >= SessionBStart && hhmm < SessionBEnd);
            if (!inZone) { why = "session"; return false; }
            if (now.DayOfWeek == DayOfWeek.Friday && hhmm >= FridayNoNew) { why = "friday"; return false; }
            if (_news.Any(n => Math.Abs((now - n).TotalMinutes) <= NewsWindowMinutes)) { why = "news"; return false; }
            if (OurPositions().Any() || OurOrders().Any()) { why = "busy"; return false; }
            if (!IsQuoteValid() || Symbol.Spread / Symbol.PipSize > MaxSpreadPips) { why = "spread"; return false; }

            var today = History.Where(t => t.SymbolName == SymbolName && t.Label == BotLabel && t.ClosingTime.Date == now.Date).ToList();
            if (today.Count(t => t.NetProfit < 0) >= MaxDailyLosses || -today.Sum(t => t.NetProfit) >= Account.Balance * MaxDailyLossPercent / 100.0)
            {
                why = "daily limit";
                return false;
            }
            return true;
        }

        // ================= Live management =================

        private void CancelIfPlayedOut()
        {
            if (_livePlan == null)
                return;
            foreach (var o in OurOrders().ToList())
            {
                bool reachedTarget = _livePlan.IsBuy ? Symbol.Bid >= _livePlan.Tp : Symbol.Ask <= _livePlan.Tp;
                bool brokeStop = _livePlan.IsBuy ? Symbol.Bid <= _livePlan.Stop : Symbol.Ask >= _livePlan.Stop;
                if (!reachedTarget && !brokeStop)
                    continue;
                var r = CancelPendingOrder(o);
                if (r.IsSuccessful)
                    Count(reachedTarget ? "missed: target hit before fill" : "cancelled: stop broken before fill");
                else
                    Print("CANCEL FAILED order {0}: {1}", o.Id, r.Error);
            }
        }

        // SMC rule: a candle CLOSING through the far edge of the gap means the imbalance failed.
        // Filling after that is buying into momentum against the setup.
        private void CancelIfFvgClosedThrough(int i)
        {
            if (_livePlan == null || i < 0)
                return;
            double c = Bars.ClosePrices[i];
            bool failed = _livePlan.IsBuy ? c < _livePlan.FvgBottom : c > _livePlan.FvgTop;
            if (!failed)
                return;
            foreach (var o in OurOrders().ToList())
            {
                var r = CancelPendingOrder(o);
                if (r.IsSuccessful)
                    Count("cancelled: fvg closed through");
                else
                    Print("CANCEL FAILED order {0}: {1}", o.Id, r.Error);
            }
        }

        private void ManageBreakeven()
        {
            if (BreakevenAtR <= 0)
                return;
            foreach (var p in OurPositions().ToList())
            {
                double risk;
                if (_beDone.Contains(p.Id) || !_initialRisk.TryGetValue(p.Id, out risk) || !p.StopLoss.HasValue)
                    continue;
                bool isBuy = p.TradeType == TradeType.Buy;
                double progress = isBuy ? Symbol.Bid - p.EntryPrice : p.EntryPrice - Symbol.Ask;
                if (progress < BreakevenAtR * risk)
                    continue;
                double cost = PriceFor(Commission(p.VolumeInUnits), p.VolumeInUnits);
                double be = Round(isBuy ? p.EntryPrice + cost : p.EntryPrice - cost);
                bool valid = isBuy ? be < Symbol.Bid - Symbol.Spread : be > Symbol.Ask + Symbol.Spread;
                if (!valid)
                    continue;
                var r = p.ModifyStopLossPrice(be);
                _beDone.Add(p.Id); // one attempt; the original stop remains if it fails
                if (!r.IsSuccessful)
                    Print("BREAKEVEN FAILED pos {0}: {1}", p.Id, r.Error);
            }
        }

        private void FlattenIfDue()
        {
            DateTime now = Server.Time;
            int hhmm = now.Hour * 100 + now.Minute;
            bool friday = now.DayOfWeek == DayOfWeek.Friday && hhmm >= FridayFlatten;
            bool daily = DailyFlatten > 0 && hhmm >= DailyFlatten;
            if (!friday && !daily)
                return;
            foreach (var o in OurOrders().ToList())
            {
                var r = CancelPendingOrder(o);
                if (!r.IsSuccessful) Print("CANCEL FAILED order {0}: {1}", o.Id, r.Error);
            }
            foreach (var p in OurPositions().ToList())
            {
                var r = ClosePosition(p);
                if (!r.IsSuccessful) Print("FLATTEN FAILED pos {0}: {1} (retrying next tick)", p.Id, r.Error);
            }
        }

        private void CheckEquityFloor()
        {
            if (_floorBreached || EquityFloor <= 0 || Account.Equity >= EquityFloor)
                return;
            _floorBreached = true;
            Print("EQUITY FLOOR: equity {0:F2} < {1:F2}. Flattening and stopping.", Account.Equity, EquityFloor);
            foreach (var o in OurOrders().ToList()) CancelPendingOrder(o);
            foreach (var p in OurPositions().ToList()) ClosePosition(p);
            Stop();
        }

        private void OnOrderFilled(PendingOrderFilledEventArgs args)
        {
            var p = args.Position;
            if (p == null || p.SymbolName != SymbolName || p.Label != BotLabel)
                return;
            Count("filled");
            bool widened = false;
            // A limit can fill at a better price than requested when price gaps through it
            // (log 05/10/2026: sell limit 4159.87 filled at 4162.82, leaving the stop only 1.05
            // away). That silently breaks the slippage floor, so widen the stop back to the floor;
            // the cash risk is then the floor amount, which the risk cap already approved.
            if (p.StopLoss.HasValue && Math.Abs(p.EntryPrice - p.StopLoss.Value) < CostFloor())
            {
                bool isBuy = p.TradeType == TradeType.Buy;
                double sl = Round(isBuy ? p.EntryPrice - CostFloor() : p.EntryPrice + CostFloor());
                bool valid = isBuy ? sl < Symbol.Bid : sl > Symbol.Ask;
                if (valid)
                {
                    var m = p.ModifyStopLossPrice(sl);
                    if (m.IsSuccessful)
                    {
                        widened = true;
                        Count("stop widened to floor after gap fill");
                    }
                    else
                        Print("STOP WIDEN FAILED pos {0}: {1} (keeping tighter stop)", p.Id, m.Error);
                }
            }
            if (widened)
                _initialRisk[p.Id] = CostFloor();
            else if (p.StopLoss.HasValue)
                _initialRisk[p.Id] = Math.Abs(p.EntryPrice - p.StopLoss.Value);
            Print("FILLED {0} @ {1} SL {2} TP {3}", p.TradeType, p.EntryPrice, p.StopLoss, p.TakeProfit);
            if (!p.StopLoss.HasValue)
            {
                Count("filled without stop (gapped)");
                Print("Filled without a stop (price gapped through the stop level): closing immediately.");
                var r = ClosePosition(p);
                if (!r.IsSuccessful) Print("CLOSE FAILED pos {0}: {1}", p.Id, r.Error);
            }
        }

        private void OnOrderCancelled(PendingOrderCancelledEventArgs args)
        {
            var o = args.PendingOrder;
            if (o == null || o.SymbolName != SymbolName || o.Label != BotLabel)
                return;
            if (args.Reason == PendingOrderCancellationReason.Expired)
                Count("expired");
            else if (args.Reason != PendingOrderCancellationReason.Cancelled)
                Count("cancelled: other");
            Log("ORDER {0} {1} @ {2}", args.Reason, o.TradeType, o.TargetPrice);
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            var p = args.Position;
            if (p.SymbolName != SymbolName || p.Label != BotLabel)
                return;
            double risk;
            _initialRisk.TryGetValue(p.Id, out risk);
            _initialRisk.Remove(p.Id);
            _beDone.Remove(p.Id);

            if (args.Reason == PositionCloseReason.StopLoss && p.StopLoss.HasValue)
            {
                var t = History.FirstOrDefault(h => h.PositionId == p.Id);
                if (t != null)
                {
                    double slip = p.TradeType == TradeType.Buy ? p.StopLoss.Value - t.ClosingPrice : t.ClosingPrice - p.StopLoss.Value;
                    if (slip > 0) _stopSlips.Add(CashFor(slip, p.VolumeInUnits));
                }
            }
            double denom = risk > 0 ? CashFor(risk, p.VolumeInUnits) : 0;
            Print("CLOSED {0} pos {1} net ${2:F2} ({3}) reason {4} equity {5:F2}", p.TradeType, p.Id, p.NetProfit,
                denom > 0 ? (p.NetProfit / denom).ToString("F2", CultureInfo.InvariantCulture) + "R" : "n/a", args.Reason, Account.Equity);
        }

        // ================= Helpers =================

        private bool IsPivot(int j, bool low)
        {
            if (j - PivotStrength < 0 || j + PivotStrength >= Bars.Count - 1)
                return false;
            double v = Ext(j, low);
            for (int k = 1; k <= PivotStrength; k++)
                if (!Beyond(v, Ext(j - k, low), low) || !Beyond(v, Ext(j + k, low), low))
                    return false;
            return true;
        }

        // Asian range low (long) / high (short) for the current day, once the range has closed.
        private bool AsianLevel(bool isBuy, int i, out double level)
        {
            level = 0;
            DateTime t = Bars.OpenTimes[i];
            if (t.Hour * 100 + t.Minute < AsianEnd)
                return false;
            DateTime end = t.Date.AddHours(AsianEnd / 100).AddMinutes(AsianEnd % 100);
            bool any = false;
            for (int k = i - 1; k >= 0 && Bars.OpenTimes[k] >= t.Date; k--)
            {
                if (Bars.OpenTimes[k] >= end) continue;
                double v = Ext(k, isBuy);
                if (!any || Beyond(v, level, isBuy)) level = v;
                any = true;
            }
            return any;
        }

        private bool IsStale(KeyValuePair<double, string> pool, bool isBuy, int i)
        {
            DateTime formed = pool.Value == "pdl" || pool.Value == "pdh"
                ? _d1.OpenTimes.LastValue
                : PoolTime(pool, isBuy, i);
            for (int k = i - 1; k >= 0 && Bars.OpenTimes[k] > formed; k--)
                if (Beyond(Ext(k, isBuy), pool.Key, isBuy))
                    return true;
            return false;
        }

        // Time the pool was formed: pivots are found by value; the latest bar at that value counts.
        private DateTime PoolTime(KeyValuePair<double, string> pool, bool isBuy, int i)
        {
            for (int k = i - 1; k >= Math.Max(0, i - LiquidityLookback - 300); k--)
                if (Math.Abs(Ext(k, isBuy) - pool.Key) < Symbol.TickSize / 2)
                    return Bars.OpenTimes[k];
            return DateTime.MinValue;
        }

        private double Ext(int k, bool lowSide) { return lowSide ? Bars.LowPrices[k] : Bars.HighPrices[k]; }
        private static bool Beyond(double a, double b, bool lowSide) { return lowSide ? a < b : a > b; }

        private bool H1Allows(bool isBuy)
        {
            if (!RequireH1Bias)
                return true;
            if (_h1.Count < H1EmaPeriod + H1SlopeBars + 2)
                return false;
            double c = _h1.ClosePrices.Last(1), e = _h1Ema.Result.Last(1), past = _h1Ema.Result.Last(1 + H1SlopeBars);
            return isBuy ? c > e && (H1SlopeBars == 0 || e > past) : c < e && (H1SlopeBars == 0 || e < past);
        }

        private double Units()
        {
            double u = Symbol.NormalizeVolumeInUnits(Symbol.QuantityToVolumeInUnits(FixedLots), RoundingMode.Down);
            return u >= Symbol.VolumeInUnitsMin ? u : 0;
        }

        private double CostFloor() { return MaxSlippageShare > 0 ? ExpectedStopSlippage / MaxSlippageShare : 0; }
        private double CashFor(double dist, double units) { return dist / Symbol.PipSize * Symbol.PipValue * units; }
        private double PriceFor(double cash, double units) { return units > 0 ? cash / (Symbol.PipValue * units) * Symbol.PipSize : 0; }
        private double Commission(double units) { return CommissionPerLot * Symbol.VolumeInUnitsToQuantity(units); }
        private double Round(double price) { return Math.Round(price, Symbol.Digits); }

        private bool IsQuoteValid()
        {
            double bid = Symbol.Bid, ask = Symbol.Ask;
            return !double.IsNaN(bid) && !double.IsNaN(ask) && bid > 0 && ask > 0 && ask >= bid;
        }

        private IEnumerable<Position> OurPositions() { return Positions.Where(p => p.SymbolName == SymbolName && p.Label == BotLabel); }
        private IEnumerable<PendingOrder> OurOrders() { return PendingOrders.Where(o => o.SymbolName == SymbolName && o.Label == BotLabel); }

        private double EstimateTfMinutes()
        {
            var diffs = new List<double>();
            for (int k = Math.Max(1, Bars.Count - 50); k < Bars.Count; k++)
                diffs.Add((Bars.OpenTimes[k] - Bars.OpenTimes[k - 1]).TotalMinutes);
            return diffs.Count > 0 ? diffs.Where(d => d > 0).DefaultIfEmpty(5).Min() : 5;
        }

        private static double Q(List<double> sorted, double f) { return sorted[Math.Min(sorted.Count - 1, (int)(f * sorted.Count))]; }

        private void Count(string key)
        {
            int n;
            _funnel.TryGetValue(key, out n);
            _funnel[key] = n + 1;
        }

        private void Log(string format, params object[] args)
        {
            if (Verbose) Print(format, args);
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
