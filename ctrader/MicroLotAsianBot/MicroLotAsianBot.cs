// =====================================================================================
//  MicroLotBreakoutBot  -  cTrader Automate cBot for SMALL accounts at a FIXED minimum lot
// -------------------------------------------------------------------------------------
//  Built for a ~$200 account that can only trade 0.01 lot (1 oz on XAUUSD). At a fixed
//  lot the only risk lever is the STOP DISTANCE, so every trade's stop is bounded by a
//  cash cap, and stops are never allowed inside market noise (>= Min Stop ATR).
//
//  Engine 1 - Breakout (trend days):
//      Volatility-compression box (Crabel-style) broken in the direction of the H1 trend.
//      Stop-limit entry beyond the box, structural stop (capped), fixed R target.
//  Engine 2 - Range fade (range days, optional):
//      Only when the H1 trend is FLAT. Price closes outside a Bollinger band, then closes
//      back inside -> fade toward the middle band. Stop beyond the extreme (capped).
//  The two engines are mutually exclusive by regime, so adding the fade raises trade
//  count without diluting the breakout logic.
//
//  Shared: London / New York hours only, flat before rollover (no swap, no weekend gap),
//  cost hurdle (target must pay N x spread+commission), daily trade cap, daily loss cap,
//  intraday loss-streak stop, equity floor, spread/stale-quote guards, news blackout.
//  Diagnostics: start-up FEASIBILITY report + end-of-run REJECTION summary.
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

    public enum MicroSizingMode
    {
        // Always trade Fixed Lots.
        FixedLots,
        // Trade as many minimum-lot steps as the risk budget allows (step-up compounding).
        RiskPercent
    }

    public enum MicroEntryMode
    {
        // Original behaviour: one market / stop-limit order per setup.
        Market,
        // Several limit orders spread across a price zone, one shared structural stop.
        LimitLadder
    }

    public enum MicroStopTrigger
    {
        // Stop fires on the first price beyond it (cTrader default).
        SingleTick,
        // Stop fires only after two consecutive prices beyond it: ignores one-tick spikes.
        DoubleTick
    }

    public enum MicroStopMode
    {
        // Use the structural stop; skip the trade if it costs more than the risk cap.
        Structural,
        // Tighten the structural stop to the risk cap, provided it stays >= Min Stop ATR.
        CappedAtRisk
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class MicroLotAsianBot : Robot
    {
        // ------------------------------------------------------------------ General
        [Parameter("Label", Group = "General", DefaultValue = "MLBASIA")]
        public string BotLabel { get; set; }

        [Parameter("Direction", Group = "General", DefaultValue = MicroDirection.Both)]
        public MicroDirection Direction { get; set; }

        [Parameter("Fixed Lots", Group = "General", DefaultValue = 0.01, MinValue = 0.01, Step = 0.01)]
        public double FixedLots { get; set; }

        [Parameter("Sizing Mode", Group = "General", DefaultValue = MicroSizingMode.RiskPercent)]
        public MicroSizingMode SizingMode { get; set; }

        // ------------------------------------------------------------------ Trend filter
        [Parameter("Trend Timeframe", Group = "Trend", DefaultValue = MicroTrendTimeFrame.Hour)]
        public MicroTrendTimeFrame TrendTimeFrame { get; set; }

        [Parameter("Trend EMA", Group = "Trend", DefaultValue = 50, MinValue = 10, MaxValue = 200, Step = 10)]
        public int TrendEmaPeriod { get; set; }

        [Parameter("Trend EMA Slope (bars)", Group = "Trend", DefaultValue = 5, MinValue = 1, MaxValue = 30, Step = 1)]
        public int TrendSlopeBars { get; set; }

        [Parameter("Flat Trend Slope (trend ATR x)", Group = "Trend", DefaultValue = 0.3, MinValue = 0.05, MaxValue = 2.0, Step = 0.05)]
        public double FlatSlopeAtr { get; set; }

        // ------------------------------------------------------------------ Breakout engine
        [Parameter("Use Breakout Engine", Group = "Breakout", DefaultValue = true)]
        public bool UseBreakout { get; set; }

        [Parameter("Box Bars", Group = "Breakout", DefaultValue = 12, MinValue = 4, MaxValue = 48, Step = 2)]
        public int BoxBars { get; set; }

        [Parameter("ATR Period", Group = "Breakout", DefaultValue = 14, MinValue = 5, MaxValue = 50, Step = 1)]
        public int AtrPeriod { get; set; }

        [Parameter("Box Max Height (ATR x)", Group = "Breakout", DefaultValue = 3.0, MinValue = 1.0, MaxValue = 6.0, Step = 0.25)]
        public double BoxMaxAtr { get; set; }

        [Parameter("Box Min Height (ATR x)", Group = "Breakout", DefaultValue = 0.8, MinValue = 0.2, MaxValue = 3.0, Step = 0.1)]
        public double BoxMinAtr { get; set; }

        [Parameter("Breakout Buffer (ATR x)", Group = "Breakout", DefaultValue = 0.1, MinValue = 0.0, MaxValue = 1.0, Step = 0.05)]
        public double BufferAtr { get; set; }

        [Parameter("Order Expiry (bars)", Group = "Breakout", DefaultValue = 6, MinValue = 1, MaxValue = 48, Step = 1)]
        public int OrderExpiryBars { get; set; }

        [Parameter("Breakout Reward (R)", Group = "Breakout", DefaultValue = 2.0, MinValue = 1.0, MaxValue = 5.0, Step = 0.25)]
        public double RewardR { get; set; }

        // ------------------------------------------------------------------ Range-fade engine
        [Parameter("Use Range Fade Engine", Group = "Range Fade", DefaultValue = true)]
        public bool UseRangeFade { get; set; }

        [Parameter("Bollinger Period", Group = "Range Fade", DefaultValue = 20, MinValue = 10, MaxValue = 50, Step = 1)]
        public int BbPeriod { get; set; }

        [Parameter("Bollinger Std Dev", Group = "Range Fade", DefaultValue = 2.0, MinValue = 1.0, MaxValue = 3.5, Step = 0.1)]
        public double BbStdDev { get; set; }

        [Parameter("Fade Stop Buffer (ATR x)", Group = "Range Fade", DefaultValue = 0.25, MinValue = 0.0, MaxValue = 2.0, Step = 0.05)]
        public double FadeStopBufferAtr { get; set; }

        [Parameter("Fade Min Reward/Risk", Group = "Range Fade", DefaultValue = 1.0, MinValue = 0.3, MaxValue = 3.0, Step = 0.1)]
        public double FadeMinRewardRisk { get; set; }

        [Parameter("Fade Target (fraction to mid band)", Group = "Range Fade", DefaultValue = 1.0, MinValue = 0.3, MaxValue = 1.0, Step = 0.05)]
        public double FadeTargetFraction { get; set; }

        // ------------------------------------------------------------------ Limit ladder
        [Parameter("Entry Mode", Group = "Ladder", DefaultValue = MicroEntryMode.LimitLadder)]
        public MicroEntryMode EntryMode { get; set; }

        [Parameter("Ladder Orders", Group = "Ladder", DefaultValue = 3, MinValue = 1, MaxValue = 10, Step = 1)]
        public int LadderOrders { get; set; }

        [Parameter("Ladder Depth (ATR x)", Group = "Ladder", DefaultValue = 1.0, MinValue = 0.2, MaxValue = 4.0, Step = 0.1)]
        public double LadderDepthAtr { get; set; }

        [Parameter("Ladder Stop Beyond Zone (ATR x)", Group = "Ladder", DefaultValue = 0.75, MinValue = 0.2, MaxValue = 3.0, Step = 0.05)]
        public double LadderStopAtr { get; set; }

        [Parameter("Ladder Min Order Stop (ATR x)", Group = "Ladder", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 3.0, Step = 0.05)]
        public double LadderMinStopAtr { get; set; }

        [Parameter("Ladder Expiry (bars)", Group = "Ladder", DefaultValue = 3, MinValue = 1, MaxValue = 24, Step = 1)]
        public int LadderExpiryBars { get; set; }

        [Parameter("Trend Pullback Ladder", Group = "Ladder", DefaultValue = true)]
        public bool UseTrendLadder { get; set; }

        // ------------------------------------------------------------------ Stops & exits
        [Parameter("Stop Mode", Group = "Stops", DefaultValue = MicroStopMode.CappedAtRisk)]
        public MicroStopMode StopMode { get; set; }

        [Parameter("Min Stop (ATR x)", Group = "Stops", DefaultValue = 1.5, MinValue = 0.3, MaxValue = 4.0, Step = 0.1)]
        public double MinStopAtr { get; set; }

        [Parameter("Stop Trigger", Group = "Stops", DefaultValue = MicroStopTrigger.DoubleTick)]
        public MicroStopTrigger StopTrigger { get; set; }

        [Parameter("Breakeven At (R, 0=off)", Group = "Stops", DefaultValue = 1.0, MinValue = 0.0, MaxValue = 3.0, Step = 0.25)]
        public double BreakevenR { get; set; }

        [Parameter("Flat Time (UTC hhmm)", Group = "Stops", DefaultValue = 2030, MinValue = 0, MaxValue = 2359)]
        public int FlatTimeUtc { get; set; }

        [Parameter("Friday Flat Time (UTC hhmm)", Group = "Stops", DefaultValue = 1930, MinValue = 0, MaxValue = 2359)]
        public int FridayFlatTimeUtc { get; set; }

        // ------------------------------------------------------------------ Costs
        [Parameter("Commission $ per Lot (round turn)", Group = "Costs", DefaultValue = 8, MinValue = 0, Step = 0.5)]
        public double CommissionPerLot { get; set; }

        [Parameter("Expected Stop Slippage (price)", Group = "Costs", DefaultValue = 0.8, MinValue = 0, Step = 0.05)]
        public double ExpectedStopSlippage { get; set; }

        [Parameter("Max Slippage Share of Risk", Group = "Costs", DefaultValue = 0.20, MinValue = 0.05, MaxValue = 1.0, Step = 0.05)]
        public double MaxSlippageShare { get; set; }

        [Parameter("Cost Hurdle (target / cost x)", Group = "Costs", DefaultValue = 3.0, MinValue = 1.0, MaxValue = 10.0, Step = 0.5)]
        public double CostHurdle { get; set; }

        // ------------------------------------------------------------------ Risk
        [Parameter("Max Risk Per Trade %", Group = "Risk", DefaultValue = 3.0, MinValue = 0.5, MaxValue = 10.0, Step = 0.25)]
        public double MaxRiskPercent { get; set; }

        [Parameter("Max Risk Per Trade $ (0=off)", Group = "Risk", DefaultValue = 0, MinValue = 0, Step = 1)]
        public double MaxRiskCash { get; set; }

        [Parameter("Max Trades Per Day", Group = "Risk", DefaultValue = 3, MinValue = 1, MaxValue = 20, Step = 1)]
        public int MaxTradesPerDay { get; set; }

        [Parameter("Max Daily Loss %", Group = "Risk", DefaultValue = 6.0, MinValue = 1.0, MaxValue = 20.0, Step = 0.5)]
        public double MaxDailyLossPercent { get; set; }

        [Parameter("Max Losses In A Row (per day)", Group = "Risk", DefaultValue = 2, MinValue = 1, MaxValue = 10, Step = 1)]
        public int MaxDailyLossStreak { get; set; }

        [Parameter("Equity Floor $ (stops bot)", Group = "Risk", DefaultValue = 120, MinValue = 0, Step = 5)]
        public double EquityFloor { get; set; }

        // 2025-26 run: equity fell to $129.92 in April 2025; 3% of that ($3.90) is below the $4
        // slippage floor, so every setup was rejected for 18 months while the backtest kept
        // running. When the account can no longer afford the smallest allowed stop, stop the bot
        // and say so, instead of idling silently above the equity floor.
        [Parameter("Stop When Untradable", Group = "Risk", DefaultValue = true)]
        public bool StopWhenUntradable { get; set; }

        // Live log 15/10/2026: the same strategy ran as two instances (labels MLB and H4TP) on one
        // $210 account, so every setup was taken twice (double risk) and each copy kept its own
        // daily limits. With this on, the bot stands aside while any OTHER label holds a position
        // or pending order on this symbol.
        [Parameter("Exclusive Symbol (one bot per symbol)", Group = "Risk", DefaultValue = true)]
        public bool ExclusiveSymbol { get; set; }

        // ------------------------------------------------------------------ Execution
        [Parameter("Max Slippage (pips)", Group = "Execution", DefaultValue = 5, MinValue = 0, Step = 1)]
        public double MaxSlippagePips { get; set; }

        [Parameter("Max Spread (pips)", Group = "Execution", DefaultValue = 6, MinValue = 0.1, Step = 0.5)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Max Spread (fraction of risk)", Group = "Execution", DefaultValue = 0.15, MinValue = 0.01, MaxValue = 0.5, Step = 0.01)]
        public double MaxSpreadOfRisk { get; set; }

        [Parameter("Stale Quote (sec)", Group = "Execution", DefaultValue = 60, MinValue = 5, Step = 5)]
        public int StaleQuoteSeconds { get; set; }

        // ------------------------------------------------------------------ Sessions (UTC)
        [Parameter("Session 1 Start (UTC hhmm)", Group = "Sessions", DefaultValue = 100, MinValue = 0, MaxValue = 2359)]
        public int Session1Start { get; set; }

        [Parameter("Session 1 End (UTC hhmm)", Group = "Sessions", DefaultValue = 600, MinValue = 0, MaxValue = 2359)]
        public int Session1End { get; set; }

        [Parameter("Session 2 Start (UTC hhmm)", Group = "Sessions", DefaultValue = 1400, MinValue = 0, MaxValue = 2359)]
        public int Session2Start { get; set; }

        [Parameter("Session 2 End (UTC hhmm)", Group = "Sessions", DefaultValue = 1400, MinValue = 0, MaxValue = 2359)]
        public int Session2End { get; set; }

        // ------------------------------------------------------------------ News
        [Parameter("News Times UTC (yyyy-MM-dd HH:mm,...)", Group = "News", DefaultValue = "")]
        public string NewsTimes { get; set; }

        [Parameter("News Window (+/- min)", Group = "News", DefaultValue = 30, MinValue = 0, MaxValue = 240, Step = 5)]
        public int NewsWindowMinutes { get; set; }

        // ------------------------------------------------------------------ Report
        [Parameter("Feasibility Report Bars", Group = "Report", DefaultValue = 3000, MinValue = 200, Step = 100)]
        public int FeasibilityBars { get; set; }

        [Parameter("Print Each Rejection", Group = "Report", DefaultValue = false)]
        public bool VerboseRejections { get; set; }

        // ------------------------------------------------------------------ State
        private const string BreakoutTag = "-BO";
        private const string FadeTag = "-RF";
        private const string LadderTag = "-LD";
        private int _ladderSetups;
        private int _ladderFills;

        private AverageTrueRange _atr;
        private BollingerBands _bb;
        private Bars _trendBars;
        private ExponentialMovingAverage _trendEma;
        private AverageTrueRange _trendAtr;
        private double _volume;
        private double _barMinutes;
        private DateTime _lastTickTime = DateTime.MinValue;
        private bool _floorBreached;
        private readonly List<DateTime> _news = new List<DateTime>();
        private readonly HashSet<long> _breakevenDone = new HashSet<long>();
        private readonly Dictionary<string, int> _rejections = new Dictionary<string, int>();
        private int _ordersPlaced;
        private int _stopFills;
        private double _stopSlippageCash;
        private double _maxStopSlippageCash;

        private enum Regime
        {
            Unknown,
            Up,
            Down,
            Flat
        }

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
            _bb = Indicators.BollingerBands(Bars.ClosePrices, BbPeriod, BbStdDev, MovingAverageType.Simple);
            _trendBars = MarketData.GetBars(MapTimeFrame(TrendTimeFrame));
            _trendEma = Indicators.ExponentialMovingAverage(_trendBars.ClosePrices, TrendEmaPeriod);
            _trendAtr = Indicators.AverageTrueRange(_trendBars, 14, MovingAverageType.Simple);
            _barMinutes = EstimateBarMinutes();

            ParseNewsTimes();
            PendingOrders.Filled += OnOrderFilled;
            Positions.Closed += OnPositionClosed;

            Print("BUILD MLB-ASIA-2026-10-16b (Asian session 01:00-06:00 UTC)");
            Print("Started on {0} {1}. Volume={2} units ({3} lots). PipSize={4} PipValue={5}",
                SymbolName, TimeFrame, _volume, Symbol.VolumeInUnitsToQuantity(_volume), Symbol.PipSize, Symbol.PipValue);

            PrintConfig();
            PrintFeasibilityReport();
        }

        // Saved instances keep old parameter values when the code changes, so every run
        // logs the settings it actually used.
        private void PrintConfig()
        {
            Print("CONFIG | label={0} sizing={1} stopMode={2} minStopATR={3} riskPct={4} maxTrades/day={5} commission/lot={6} stopTrigger={7}",
                BotLabel, SizingMode, StopMode, MinStopAtr, MaxRiskPercent, MaxTradesPerDay, CommissionPerLot, StopTrigger);
            Print("CONFIG | slippage floor: stops must be >= {0:F2} price (expected slip {1} / max share {2:P0}) = ${3:F2} at {4} lots",
                CostFloorDistance(), ExpectedStopSlippage, MaxSlippageShare, CashForPriceMove(CostFloorDistance()), Symbol.VolumeInUnitsToQuantity(_volume));
            Print("CONFIG | entry={0} ladder: {1} orders over {2} ATR, stop {3} ATR beyond, min order stop {4} ATR, expiry {5} bars, trend ladder {6}",
                EntryMode, LadderOrders, LadderDepthAtr, LadderStopAtr, LadderMinStopAtr, LadderExpiryBars, UseTrendLadder);
            Print("CONFIG | sessions={0:D4}-{1:D4},{2:D4}-{3:D4} UTC | breakout={4} fade={5} | ATR={6} boxMaxATR={7} | maxSpreadPips={8}",
                Session1Start, Session1End, Session2Start, Session2End, UseBreakout, UseRangeFade, AtrPeriod, BoxMaxAtr, MaxSpreadPips);
            if (MaxSpreadPips > 20)
                Print("CONFIG WARNING: Max Spread {0} pips is very wide (on XAUUSD 1 pip = $0.10 at 0.01 lot). Recommended: 6.", MaxSpreadPips);
            if (LadderMinStopAtr < MinStopAtr * 0.66)
                Print("CONFIG WARNING: Ladder Min Order Stop {0} ATR is far below Min Stop {1} ATR - ladder stops will sit inside the slippage noise.", LadderMinStopAtr, MinStopAtr);
            if (CommissionPerLot <= 0)
                Print("CONFIG WARNING: Commission $ per Lot is 0 - the cost hurdle ignores commission. Enter your broker's round-turn rate.");
            // Warn only when a non-empty session actually overlaps 07:00-08:00 or 12:00-14:00 UTC.
            Func<int, int, int, int, bool> overlaps = (st, en, a, z) =>
                Minutes(en) > Minutes(st) && Minutes(st) < Minutes(z) && Minutes(en) > Minutes(a);
            if (overlaps(Session1Start, Session1End, 700, 800) || overlaps(Session1Start, Session1End, 1200, 1400) ||
                overlaps(Session2Start, Session2End, 700, 800) || overlaps(Session2Start, Session2End, 1200, 1400))
                Print("CONFIG WARNING: sessions include 07:00 or 12:00-13:59 UTC, the hours that lost money in every backtest.");
        }

        protected override void OnStop()
        {
            PendingOrders.Filled -= OnOrderFilled;
            Positions.Closed -= OnPositionClosed;
            PrintRejectionSummary();
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
                if (i < Math.Max(Math.Max(BoxBars, AtrPeriod), BbPeriod) + 2 || _floorBreached)
                    return;

                HandleClock();
                if (CheckUntradable())
                    return;

                // An unfilled ladder is re-priced every bar from fresh bands; a partly filled
                // ladder keeps its remaining orders (that is the scale-in).
                if (EntryMode == MicroEntryMode.LimitLadder && OurOrders().Any() && !OurPositions().Any())
                    CancelOurOrders();

                string reason;
                if (!CanOpenNew(out reason))
                {
                    if (reason != null)
                        Reject(reason);
                    return;
                }

                Regime regime = CurrentRegime();
                if (regime == Regime.Unknown)
                {
                    Reject("trend data not ready");
                    return;
                }

                if (EntryMode == MicroEntryMode.LimitLadder)
                {
                    TryLadder(i, regime);
                    return;
                }

                if (regime == Regime.Flat)
                {
                    if (UseRangeFade)
                        TryRangeFade(i);
                    else
                        Reject("flat trend (fade engine off)");
                }
                else if (UseBreakout)
                {
                    TryBreakout(i, regime == Regime.Up ? TradeType.Buy : TradeType.Sell);
                }
            }
            catch (Exception ex)
            {
                Print("OnBar error: {0}", ex);
            }
        }

        // ================================================================== Engine 1: breakout
        private void TryBreakout(int i, TradeType side)
        {
            if (!DirectionAllowed(side))
            {
                Reject("direction filter");
                return;
            }

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
            if (height > BoxMaxAtr * atr)
            {
                Reject("BO: box too wide (no compression)");
                return;
            }
            if (height < BoxMinAtr * atr)
            {
                Reject("BO: box too narrow");
                return;
            }

            double buffer = BufferAtr * atr;
            double spread = Symbol.Spread;
            double entry, structuralStop;
            // Bars are Bid-based: buy stops trigger on Ask and short stops are hit on Ask.
            if (side == TradeType.Buy)
            {
                entry = hi + buffer + spread;
                structuralStop = lo - buffer;
                if (entry <= Symbol.Ask) { Reject("BO: price already beyond box"); return; }
            }
            else
            {
                entry = lo - buffer;
                structuralStop = hi + buffer + spread;
                if (entry >= Symbol.Bid) { Reject("BO: price already beyond box"); return; }
            }

            double stopDist;
            if (!ResolveStopDistance(Math.Abs(entry - structuralStop), atr, "BO", out stopDist))
                return;

            double targetDist = RewardR * stopDist;
            if (!PassesCostAndSpread(stopDist, targetDist, "BO"))
                return;

            double sl = side == TradeType.Buy ? entry - stopDist : entry + stopDist;
            double tp = side == TradeType.Buy ? entry + targetDist : entry - targetDist;

            DateTime expiry = Server.Time.AddMinutes(OrderExpiryBars * _barMinutes);
            DateTime sessionEnd = CurrentSessionEnd(Server.Time);
            if (sessionEnd > Server.Time && sessionEnd < expiry)
                expiry = sessionEnd;

            entry = Math.Round(entry, Symbol.Digits);
            sl = Math.Round(sl, Symbol.Digits);
            tp = Math.Round(tp, Symbol.Digits);

            double volume = TradeVolume(stopDist);
            TradeResult r = PlaceStopLimitOrder(side, SymbolName, volume, entry, MaxSlippagePips, BotLabel + BreakoutTag,
                sl, tp, ProtectionType.Absolute, expiry, null, false, TriggerMethod());

            if (r.IsSuccessful)
            {
                _ordersPlaced++;
                Print("BO ORDER {0} {1} lots stop-limit @ {2} | SL {3} | TP {4} | risk ${5:F2} ({6:F1}% eq) | box {7:F2} = {8:F2} ATR | expires {9:HH:mm}",
                    side, Symbol.VolumeInUnitsToQuantity(volume), entry, sl, tp, CashForPriceMove(stopDist, volume),
                    CashForPriceMove(stopDist, volume) / Account.Equity * 100.0, height, height / atr, expiry);
            }
            else
            {
                Reject("BO: order rejected by server");
                Print("BO order placement failed: {0} (re-evaluated next bar).", r.Error);
            }
        }

        // ================================================================== Engine 2: range fade
        private void TryRangeFade(int i)
        {
            double atr = _atr.Result[i];
            double top = _bb.Top[i], bottom = _bb.Bottom[i], mid = _bb.Main[i];
            if (double.IsNaN(atr) || atr <= 0 || double.IsNaN(top) || double.IsNaN(bottom))
                return;

            double prevClose = Bars.ClosePrices[i - 1];
            double close = Bars.ClosePrices[i];

            // Re-entry signal: previous bar closed OUTSIDE the band, this bar closed back INSIDE.
            TradeType side;
            double extreme;
            if (prevClose < _bb.Bottom[i - 1] && close > bottom)
            {
                side = TradeType.Buy;
                extreme = Math.Min(Bars.LowPrices[i - 1], Bars.LowPrices[i]);
            }
            else if (prevClose > _bb.Top[i - 1] && close < top)
            {
                side = TradeType.Sell;
                extreme = Math.Max(Bars.HighPrices[i - 1], Bars.HighPrices[i]);
            }
            else
            {
                Reject("RF: no band re-entry");
                return;
            }

            if (!DirectionAllowed(side))
            {
                Reject("direction filter");
                return;
            }

            double entry = side == TradeType.Buy ? Symbol.Ask : Symbol.Bid;
            double buffer = FadeStopBufferAtr * atr;
            double structuralStopDist = side == TradeType.Buy
                ? entry - (extreme - buffer)
                : (extreme + buffer + Symbol.Spread) - entry;
            if (structuralStopDist <= 0)
            {
                Reject("RF: price already through the extreme");
                return;
            }

            double stopDist;
            if (!ResolveStopDistance(structuralStopDist, atr, "RF", out stopDist))
                return;

            double targetDist = FadeTargetFraction * (side == TradeType.Buy ? mid - entry : entry - mid);
            if (targetDist < FadeMinRewardRisk * stopDist)
            {
                Reject("RF: reward/risk to middle band too low");
                return;
            }
            if (!PassesCostAndSpread(stopDist, targetDist, "RF"))
                return;

            double slPips = Math.Round(stopDist / Symbol.PipSize, 1);
            double tpPips = Math.Round(targetDist / Symbol.PipSize, 1);
            double volume = TradeVolume(stopDist);

            TradeResult r = ExecuteMarketRangeOrder(side, SymbolName, volume, MaxSlippagePips, entry,
                BotLabel + FadeTag, slPips, tpPips, null, false, TriggerMethod());

            if (r.IsSuccessful && r.Position != null)
            {
                _ordersPlaced++;
                Print("RF FILL {0} {1} lots @ {2} | SL {3} | TP {4} | risk ${5:F2} ({6:F1}% eq) | target {7:F2}R",
                    side, Symbol.VolumeInUnitsToQuantity(volume), r.Position.EntryPrice, r.Position.StopLoss, r.Position.TakeProfit,
                    CashForPriceMove(stopDist, volume), CashForPriceMove(stopDist, volume) / Account.Equity * 100.0, targetDist / stopDist);
                if (!r.Position.StopLoss.HasValue)
                {
                    Print("Filled without a stop - closing immediately.");
                    ClosePosition(r.Position);
                }
            }
            else
            {
                Reject("RF: order rejected by server");
                Print("RF market order failed: {0}", r.Error);
            }
        }

        // ================================================================== Engine 3: limit ladder
        // Flat H1 regime  -> FADE ladder: sell limits stacked above the upper Bollinger band
        //                    (buy limits below the lower band), target the middle band.
        // Trend H1 regime -> PULLBACK ladder in the trend direction: buy limits from the middle
        //                    band down toward the lower band in an uptrend (mirror for downtrend),
        //                    target = Reward R x the shallowest order's risk.
        // All orders share ONE stop beyond the zone. Total risk if every order fills and the stop
        // is hit must fit the risk cap; shallow orders (largest risk) are dropped until it does.
        private void TryLadder(int i, Regime regime)
        {
            double atr = _atr.Result[i];
            double top = _bb.Top[i], bottom = _bb.Bottom[i], mid = _bb.Main[i];
            if (double.IsNaN(atr) || atr <= 0 || double.IsNaN(top) || double.IsNaN(bottom) || double.IsNaN(mid))
                return;

            TradeType side;
            double near, far;     // zone edge closest to price, and deepest edge
            bool fade;
            if (regime == Regime.Flat)
            {
                if (!UseRangeFade) { Reject("LD: flat regime but fade engine off"); return; }
                fade = true;
                // Fade whichever band price is closer to.
                double close = Bars.ClosePrices[i];
                side = (top - close) < (close - bottom) ? TradeType.Sell : TradeType.Buy;
                near = side == TradeType.Sell ? top : bottom;
                far = side == TradeType.Sell ? top + LadderDepthAtr * atr : bottom - LadderDepthAtr * atr;
            }
            else
            {
                if (!UseTrendLadder) { Reject("LD: trend regime but trend ladder off"); return; }
                fade = false;
                side = regime == Regime.Up ? TradeType.Buy : TradeType.Sell;
                near = mid;
                far = side == TradeType.Buy ? mid - LadderDepthAtr * atr : mid + LadderDepthAtr * atr;
            }

            if (!DirectionAllowed(side)) { Reject("direction filter"); return; }

            double spread = Symbol.Spread;
            // Bars are Bid: sell limits fill on Bid, buy limits on Ask (enter spread lower).
            double stop = side == TradeType.Buy ? far - LadderStopAtr * atr : far + LadderStopAtr * atr + spread;
            double price = side == TradeType.Buy ? Symbol.Ask : Symbol.Bid;

            // Build levels from near to far; only levels price has not already passed.
            var levels = new List<double>();
            int n = Math.Max(1, LadderOrders);
            for (int k = 0; k < n; k++)
            {
                double f = n == 1 ? 0.0 : (double)k / (n - 1);
                double lvl = near + (far - near) * f;
                if (side == TradeType.Buy) lvl -= spread;
                bool valid = side == TradeType.Buy ? lvl < price : lvl > price;
                double risk = Math.Abs(lvl - stop);
                if (valid && risk >= Math.Max(LadderMinStopAtr * atr, CostFloorDistance()))
                    levels.Add(Math.Round(lvl, Symbol.Digits));
            }
            if (levels.Count == 0) { Reject("LD: no valid level (price through the zone, or stops below the slippage floor)"); return; }

            // Fit total ladder risk inside the cap by dropping the shallowest (largest-risk) orders.
            double cap = RiskCapCash();
            Func<IEnumerable<double>, double> totalRisk = ls => ls.Sum(l => CashForPriceMove(Math.Abs(l - stop)));
            while (levels.Count > 0 && totalRisk(levels) > cap)
                levels.RemoveAt(0);
            if (levels.Count == 0) { Reject("LD: one order alone exceeds the risk cap"); return; }

            double shallow = levels[0];
            double tp = fade
                ? mid
                : (side == TradeType.Buy ? shallow + RewardR * (shallow - stop) : shallow - RewardR * (stop - shallow));
            double targetDist = side == TradeType.Buy ? tp - shallow : shallow - tp;
            if (targetDist <= 0) { Reject("LD: target not beyond entries"); return; }
            if (!PassesCostAndSpread(Math.Abs(shallow - stop), targetDist, "LD"))
                return;

            DateTime expiry = Server.Time.AddMinutes(LadderExpiryBars * _barMinutes);
            DateTime sessionEnd = CurrentSessionEnd(Server.Time);
            if (sessionEnd > Server.Time && sessionEnd < expiry)
                expiry = sessionEnd;

            string setupId = "L" + Server.Time.ToString("yyyyMMddHHmm", CultureInfo.InvariantCulture);
            stop = Math.Round(stop, Symbol.Digits);
            tp = Math.Round(tp, Symbol.Digits);
            int placed = 0;
            foreach (double lvl in levels)
            {
                TradeResult r = PlaceLimitOrder(side, SymbolName, _volume, lvl, BotLabel + LadderTag, stop, tp,
                    ProtectionType.Absolute, expiry, setupId, false, TriggerMethod());
                if (r.IsSuccessful) placed++;
                else Print("LD order at {0} rejected: {1}", lvl, r.Error);
            }
            if (placed == 0) { Reject("LD: all orders rejected by server"); return; }

            _ladderSetups++;
            Print("LADDER {0} {1} {2}x{3} lots @ {4} | shared SL {5} | TP {6} | max risk ${7:F2} ({8:F1}% eq) | expires {9:HH:mm}",
                fade ? "FADE" : "PULLBACK", side, placed, Symbol.VolumeInUnitsToQuantity(_volume),
                string.Join("/", levels.Select(l => l.ToString(CultureInfo.InvariantCulture))), stop, tp,
                totalRisk(levels), totalRisk(levels) / Account.Equity * 100.0, expiry);
        }

        // ================================================================== Shared trade checks
        // Applies the risk cap. In CappedAtRisk mode an over-wide stop is tightened to the cap,
        // but never below Min Stop ATR - a stop inside normal noise is just a donation.
        private bool ResolveStopDistance(double structural, double atr, string engine, out double stopDist)
        {
            double capDist = PriceForCash(RiskCapCash());
            double minDist = Math.Max(MinStopAtr * atr, CostFloorDistance());
            stopDist = structural;

            if (structural <= capDist)
            {
                if (structural >= minDist)
                    return true;
                if (minDist <= capDist)
                {
                    stopDist = minDist; // widen out of the noise band, still within the cap
                    return true;
                }
                Reject(engine + ": risk cap smaller than minimum stop (ATR or slippage floor)");
                return false;
            }

            if (StopMode == MicroStopMode.Structural)
            {
                Reject(engine + ": structural stop exceeds risk cap");
                return false;
            }
            if (capDist < minDist)
            {
                Reject(engine + ": risk cap smaller than minimum stop (ATR or slippage floor)");
                return false;
            }
            stopDist = capDist;
            return true;
        }

        private bool PassesCostAndSpread(double stopDist, double targetDist, string engine)
        {
            double spread = Symbol.Spread;
            if (spread / Symbol.PipSize > MaxSpreadPips + 1e-6) // tolerance: 6.0 pips computes as 6.0000000000036
            {
                Reject(engine + ": spread above absolute cap");
                return false;
            }
            if (spread > MaxSpreadOfRisk * stopDist)
            {
                Reject(engine + ": spread too large vs stop");
                return false;
            }

            double costPrice = spread + PriceForCash(CommissionPerLot * Symbol.VolumeInUnitsToQuantity(_volume));
            if (targetDist < CostHurdle * costPrice)
            {
                Reject(engine + ": target fails cost hurdle");
                return false;
            }
            if ((Server.Time - _lastTickTime).TotalSeconds > StaleQuoteSeconds)
            {
                Reject(engine + ": stale quotes");
                return false;
            }
            return true;
        }

        private Regime CurrentRegime()
        {
            int t = _trendBars.Count - 2; // last CLOSED trend bar
            if (t < TrendEmaPeriod + TrendSlopeBars)
                return Regime.Unknown;

            double ema = _trendEma.Result[t];
            double emaPrev = _trendEma.Result[t - TrendSlopeBars];
            double close = _trendBars.ClosePrices[t];
            double tAtr = _trendAtr.Result[t];
            if (double.IsNaN(ema) || double.IsNaN(emaPrev) || double.IsNaN(tAtr) || tAtr <= 0)
                return Regime.Unknown;

            double slope = ema - emaPrev;
            if (Math.Abs(slope) <= FlatSlopeAtr * tAtr)
                return Regime.Flat;
            if (close > ema && slope > 0)
                return Regime.Up;
            if (close < ema && slope < 0)
                return Regime.Down;

            // Sloping EMA but price on the wrong side: transition - treat as flat (no breakout).
            return Regime.Flat;
        }

        private bool DirectionAllowed(TradeType side)
        {
            return Direction == MicroDirection.Both ||
                   (Direction == MicroDirection.LongOnly && side == TradeType.Buy) ||
                   (Direction == MicroDirection.ShortOnly && side == TradeType.Sell);
        }

        // ================================================================== Guards
        // Returns false with reason=null for routine states that need no counting.
        private bool CanOpenNew(out string reason)
        {
            DateTime now = Server.Time;
            reason = null;

            if (!InSession(now) || PastFlatTime(now))
                return false;
            if (OurPositions().Any() || OurOrders().Any())
                return false;
            if (ExclusiveSymbol && (Positions.Any(p => p.SymbolName == SymbolName && !IsOurLabel(p.Label))
                                    || PendingOrders.Any(o => o.SymbolName == SymbolName && !IsOurLabel(o.Label))))
            {
                reason = "another bot active on this symbol";
                return false;
            }
            if (InNewsWindow(now)) { reason = "news window"; return false; }

            DateTime dayStart = now.Date;
            // A ladder's fills share one comment (setup id) and count as ONE trade.
            int tradesToday = OurHistory().Where(t => t.EntryTime >= dayStart).Select(SetupKey).Distinct().Count();
            if (tradesToday >= MaxTradesPerDay) { reason = "daily trade cap"; return false; }

            var closedToday = OurHistory().Where(t => t.ClosingTime >= dayStart)
                .GroupBy(SetupKey)
                .Select(g => new { Net = g.Sum(t => t.NetProfit), Closed = g.Max(t => t.ClosingTime) })
                .OrderByDescending(x => x.Closed).ToList();
            double dayPnl = closedToday.Sum(x => x.Net);
            if (dayPnl <= -Account.Balance * MaxDailyLossPercent / 100.0) { reason = "daily loss cap"; return false; }

            int streak = 0;
            foreach (var t in closedToday)
            {
                if (t.Net < 0) streak++;
                else break;
            }
            if (streak >= MaxDailyLossStreak) { reason = "loss streak today"; return false; }

            return true;
        }

        // Smallest stop distance at which expected stop slippage stays under the allowed share
        // of risk. Backtests: ~$0.65-0.95 slippage per stop regardless of stop size, so small
        // stops (low-volatility periods) lose to slippage even when entries are good.
        private double CostFloorDistance()
        {
            return MaxSlippageShare > 0 ? ExpectedStopSlippage / MaxSlippageShare : 0;
        }

        private static string SetupKey(HistoricalTrade t)
        {
            return !string.IsNullOrEmpty(t.Comment) && t.Comment.StartsWith("L", StringComparison.Ordinal)
                ? t.Comment
                : "P" + t.PositionId.ToString(CultureInfo.InvariantCulture);
        }

        private double RiskCapCash()
        {
            double cap = Account.Equity * MaxRiskPercent / 100.0;
            if (MaxRiskCash > 0)
                cap = Math.Min(cap, MaxRiskCash);
            return cap;
        }

        private bool CheckUntradable()
        {
            if (!StopWhenUntradable || _floorBreached || OurPositions().Any() || OurOrders().Any())
                return false;
            double minRisk = CashForPriceMove(CostFloorDistance()) + CommissionPerLot * Symbol.VolumeInUnitsToQuantity(_volume);
            if (RiskCapCash() >= minRisk)
                return false;

            _floorBreached = true;
            Print("UNTRADABLE: risk cap ${0:F2} ({1}% of equity {2:F2}) is below the smallest allowed stop ${3:F2}. " +
                  "No setup can pass, so the bot stops here. Equity needed to trade again: ${4:F0}.",
                RiskCapCash(), MaxRiskPercent, Account.Equity, minRisk, minRisk * 100.0 / MaxRiskPercent);
            Stop();
            return true;
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
            foreach (Position pos in OurPositions().ToArray())
            {
                if (!pos.StopLoss.HasValue)
                {
                    Print("Position {0} has no stop - closing for safety.", pos.Id);
                    ClosePosition(pos);
                    continue;
                }
                if (BreakevenR <= 0 || _breakevenDone.Contains(pos.Id))
                    continue;

                // Before the breakeven move the stop is still the initial stop, so it defines 1R.
                double initialRisk = Math.Abs(pos.EntryPrice - pos.StopLoss.Value);
                if (initialRisk <= 0)
                    continue;

                bool isBuy = pos.TradeType == TradeType.Buy;
                double favourable = isBuy ? Symbol.Bid - pos.EntryPrice : pos.EntryPrice - Symbol.Ask;
                if (favourable < BreakevenR * initialRisk)
                    continue;

                if (pos.TakeProfit.HasValue &&
                    (isBuy ? Symbol.Bid >= pos.TakeProfit.Value : Symbol.Ask <= pos.TakeProfit.Value))
                    continue; // TP is about to fill; modifying now is rejected by the server

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

            Print("FILLED {0} {1} @ {2} | SL {3} | TP {4}", p.Label, p.TradeType, p.EntryPrice, p.StopLoss, p.TakeProfit);
            if (p.Label == BotLabel + LadderTag)
                _ladderFills++;
            if (!p.StopLoss.HasValue)
            {
                Print("Filled without a stop - closing immediately.");
                ClosePosition(p);
                return;
            }

            // A limit filled through its price (gap) can leave the stop far inside the slippage
            // floor: Asian 2-week test, buy limit 4150.30 filled 4147.16 with SL 4146.14 (1.02 away),
            // stopped at 4144.20. Widen back to the floor; that risk is <= the planned level risk.
            double floor = CostFloorDistance();
            if (Math.Abs(p.EntryPrice - p.StopLoss.Value) < floor)
            {
                bool isBuy = p.TradeType == TradeType.Buy;
                double sl = Math.Round(isBuy ? p.EntryPrice - floor : p.EntryPrice + floor, Symbol.Digits);
                bool valid = isBuy ? sl < Symbol.Bid : sl > Symbol.Ask;
                if (valid)
                {
                    TradeResult r = p.ModifyStopLossPrice(sl);
                    if (r.IsSuccessful)
                        Print("GAP FILL: stop widened to the {0:F2} floor -> {1}", floor, sl);
                    else
                        Print("GAP FILL: widen failed ({0}), stop stays at {1}", r.Error, p.StopLoss);
                }
            }
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            Position p = args.Position;
            if (!IsOurs(p))
                return;
            _breakevenDone.Remove(p.Id);
            Print("CLOSED {0} {1} | reason={2} | net={3:F2} | equity={4:F2}", p.Label, p.TradeType, args.Reason, p.NetProfit, Account.Equity);
            RecordStopSlippage(p, args.Reason);

            if (p.Label == BotLabel + LadderTag && !OurPositions().Any())
                CancelOurOrders();
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

        // ================================================================== Diagnostics
        private void Reject(string reason)
        {
            int n;
            _rejections.TryGetValue(reason, out n);
            _rejections[reason] = n + 1;
            if (VerboseRejections)
                Print("REJECT: {0}", reason);
        }

        // How much worse than the stop price each stop-loss filled. This was the deciding cost
        // in every M5 gold backtest, so every run reports it.
        private void RecordStopSlippage(Position p, PositionCloseReason reason)
        {
            if (reason != PositionCloseReason.StopLoss || !p.StopLoss.HasValue)
                return;
            HistoricalTrade t = History.Where(h => h.PositionId == p.Id).OrderByDescending(h => h.ClosingTime).FirstOrDefault();
            if (t == null)
                return;
            double slip = p.TradeType == TradeType.Buy ? p.StopLoss.Value - t.ClosingPrice : t.ClosingPrice - p.StopLoss.Value;
            double cash = Math.Max(0, CashForPriceMove(slip, p.VolumeInUnits));
            _stopFills++;
            _stopSlippageCash += cash;
            _maxStopSlippageCash = Math.Max(_maxStopSlippageCash, cash);
            if (cash > 2.0 * CashForPriceMove(Symbol.Spread, p.VolumeInUnits) && cash > 1.0)
                Print("STOP SLIPPAGE | {0} stop {1} filled {2} -> ${3:F2} worse than planned", p.Id, p.StopLoss.Value, t.ClosingPrice, cash);
        }

        private StopTriggerMethod TriggerMethod()
        {
            return StopTrigger == MicroStopTrigger.DoubleTick ? StopTriggerMethod.DoubleTrade : StopTriggerMethod.Trade;
        }

        private void PrintRejectionSummary()
        {
            Print("RUN SUMMARY | orders placed/filled: {0} | closed trades: {1} | ladder setups {2}, ladder fills {3}",
                _ordersPlaced, OurHistory().Count(), _ladderSetups, _ladderFills);
            double net = OurHistory().Sum(t => t.NetProfit);
            Print("RUN SUMMARY | stop-loss fills: {0} | slippage total ${1:F2} (avg ${2:F2}, max ${3:F2}) | net ${4:F2} | net if stops filled exactly ${5:F2}",
                _stopFills, _stopSlippageCash, _stopFills > 0 ? _stopSlippageCash / _stopFills : 0, _maxStopSlippageCash, net, net + _stopSlippageCash);
            foreach (var kv in _rejections.OrderByDescending(k => k.Value))
                Print("RUN SUMMARY | rejected {0,6} x  {1}", kv.Value, kv.Key);
        }

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

            var atrs = new List<double>();
            for (int j = first; j <= last; j++)
            {
                double atr = _atr.Result[j];
                if (!double.IsNaN(atr) && atr > 0 && InSession(Bars.OpenTimes[j]))
                    atrs.Add(atr);
            }
            if (atrs.Count == 0)
            {
                Print("FEASIBILITY: no in-session bars in the sample.");
                return;
            }

            double cap = RiskCapCash();
            double capDist = PriceForCash(cap);
            double medAtr = Median(atrs);
            int fits = atrs.Count(a => MinStopAtr * a <= capDist);
            double pct = 100.0 * fits / atrs.Count;

            Print("FEASIBILITY | {0} lots = ${1:F2} per 1.00 price move | risk cap ${2:F2} ({3}% of ${4:F2}) = {5:F2} price",
                Symbol.VolumeInUnitsToQuantity(_volume), CashForPriceMove(1.0), cap, MaxRiskPercent, Account.Equity, capDist);
            Print("FEASIBILITY | median in-session ATR({0}, {1}) = {2:F2} price -> cap = {3:F2} ATR (minimum allowed stop {4} ATR)",
                AtrPeriod, TimeFrame, medAtr, capDist / medAtr, MinStopAtr);
            Print("FEASIBILITY | bars where a {0}-ATR stop fits the cap: {1:F0}%", MinStopAtr, pct);

            if (pct < 20)
                Print("VERDICT: volatility is too high for a ${0:F2} cap on {1} {2}. Expect almost no trades. " +
                      "Raise the cap, use a calmer symbol, or a lower timeframe.", cap, SymbolName, TimeFrame);
            else if (pct < 60)
                Print("VERDICT: trades only in calmer conditions - expect a modest trade count.");
            else
                Print("VERDICT: the risk cap fits normal volatility - account size is workable here.");
        }

        // ================================================================== Helpers
        private double CashForPriceMove(double priceDistance)
        {
            return CashForPriceMove(priceDistance, _volume);
        }

        private double CashForPriceMove(double priceDistance, double volume)
        {
            return priceDistance / Symbol.PipSize * Symbol.PipValue * volume;
        }

        // Compounding at a fixed minimum lot is a step function: size only grows when the
        // risk budget covers a WHOLE extra volume step. Rounded down, never up.
        private double TradeVolume(double stopDist)
        {
            if (SizingMode == MicroSizingMode.FixedLots)
                return _volume;

            double riskPerUnit = CashForPriceMove(stopDist, 1.0);
            if (riskPerUnit <= 0)
                return _volume;

            double units = RiskCapCash() / riskPerUnit;
            units = Math.Min(units, Symbol.VolumeInUnitsMax);
            if (units < _volume)
                return _volume; // ResolveStopDistance already guaranteed _volume fits the cap
            return Math.Max(_volume, Symbol.NormalizeVolumeInUnits(units, RoundingMode.Down));
        }

        private double PriceForCash(double cash)
        {
            return cash / (Symbol.PipValue * _volume) * Symbol.PipSize;
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
            return p.SymbolName == SymbolName && IsOurLabel(p.Label);
        }

        private bool IsOurLabel(string label)
        {
            return label == BotLabel + BreakoutTag || label == BotLabel + FadeTag || label == BotLabel + LadderTag;
        }

        private IEnumerable<PendingOrder> OurOrders()
        {
            return PendingOrders.Where(o => o.SymbolName == SymbolName && IsOurLabel(o.Label));
        }

        private IEnumerable<HistoricalTrade> OurHistory()
        {
            return History.Where(t => t.SymbolName == SymbolName && IsOurLabel(t.Label));
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
