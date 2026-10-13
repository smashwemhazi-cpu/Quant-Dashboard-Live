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

    public enum MicroStopMode
    {
        // Use the structural stop; skip the trade if it costs more than the risk cap.
        Structural,
        // Tighten the structural stop to the risk cap, provided it stays >= Min Stop ATR.
        CappedAtRisk
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

        [Parameter("Fade Min Reward/Risk", Group = "Range Fade", DefaultValue = 1.0, MinValue = 0.5, MaxValue = 3.0, Step = 0.1)]
        public double FadeMinRewardRisk { get; set; }

        // ------------------------------------------------------------------ Stops & exits
        [Parameter("Stop Mode", Group = "Stops", DefaultValue = MicroStopMode.CappedAtRisk)]
        public MicroStopMode StopMode { get; set; }

        [Parameter("Min Stop (ATR x)", Group = "Stops", DefaultValue = 1.0, MinValue = 0.3, MaxValue = 4.0, Step = 0.1)]
        public double MinStopAtr { get; set; }

        [Parameter("Breakeven At (R, 0=off)", Group = "Stops", DefaultValue = 1.0, MinValue = 0.0, MaxValue = 3.0, Step = 0.25)]
        public double BreakevenR { get; set; }

        [Parameter("Flat Time (UTC hhmm)", Group = "Stops", DefaultValue = 2030, MinValue = 0, MaxValue = 2359)]
        public int FlatTimeUtc { get; set; }

        [Parameter("Friday Flat Time (UTC hhmm)", Group = "Stops", DefaultValue = 1930, MinValue = 0, MaxValue = 2359)]
        public int FridayFlatTimeUtc { get; set; }

        // ------------------------------------------------------------------ Costs
        [Parameter("Commission $ per Lot (round turn)", Group = "Costs", DefaultValue = 0, MinValue = 0, Step = 0.5)]
        public double CommissionPerLot { get; set; }

        [Parameter("Cost Hurdle (target / cost x)", Group = "Costs", DefaultValue = 3.0, MinValue = 1.0, MaxValue = 10.0, Step = 0.5)]
        public double CostHurdle { get; set; }

        // ------------------------------------------------------------------ Risk
        [Parameter("Max Risk Per Trade %", Group = "Risk", DefaultValue = 3.0, MinValue = 0.5, MaxValue = 10.0, Step = 0.25)]
        public double MaxRiskPercent { get; set; }

        [Parameter("Max Risk Per Trade $ (0=off)", Group = "Risk", DefaultValue = 0, MinValue = 0, Step = 1)]
        public double MaxRiskCash { get; set; }

        [Parameter("Max Trades Per Day", Group = "Risk", DefaultValue = 4, MinValue = 1, MaxValue = 20, Step = 1)]
        public int MaxTradesPerDay { get; set; }

        [Parameter("Max Daily Loss %", Group = "Risk", DefaultValue = 6.0, MinValue = 1.0, MaxValue = 20.0, Step = 0.5)]
        public double MaxDailyLossPercent { get; set; }

        [Parameter("Max Losses In A Row (per day)", Group = "Risk", DefaultValue = 2, MinValue = 1, MaxValue = 10, Step = 1)]
        public int MaxDailyLossStreak { get; set; }

        [Parameter("Equity Floor $ (stops bot)", Group = "Risk", DefaultValue = 120, MinValue = 0, Step = 5)]
        public double EquityFloor { get; set; }

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
        [Parameter("Session 1 Start (UTC hhmm)", Group = "Sessions", DefaultValue = 700, MinValue = 0, MaxValue = 2359)]
        public int Session1Start { get; set; }

        [Parameter("Session 1 End (UTC hhmm)", Group = "Sessions", DefaultValue = 1100, MinValue = 0, MaxValue = 2359)]
        public int Session1End { get; set; }

        [Parameter("Session 2 Start (UTC hhmm)", Group = "Sessions", DefaultValue = 1230, MinValue = 0, MaxValue = 2359)]
        public int Session2Start { get; set; }

        [Parameter("Session 2 End (UTC hhmm)", Group = "Sessions", DefaultValue = 1700, MinValue = 0, MaxValue = 2359)]
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

            Print("Started on {0} {1}. Volume={2} units ({3} lots). PipSize={4} PipValue={5}",
                SymbolName, TimeFrame, _volume, Symbol.VolumeInUnitsToQuantity(_volume), Symbol.PipSize, Symbol.PipValue);

            PrintFeasibilityReport();
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

            TradeResult r = PlaceStopLimitOrder(side, SymbolName, _volume, entry, MaxSlippagePips, BotLabel + BreakoutTag,
                sl, tp, ProtectionType.Absolute, expiry);

            if (r.IsSuccessful)
            {
                _ordersPlaced++;
                Print("BO ORDER {0} stop-limit @ {1} | SL {2} | TP {3} | risk ${4:F2} ({5:F1}% eq) | box {6:F2} = {7:F2} ATR | expires {8:HH:mm}",
                    side, entry, sl, tp, CashForPriceMove(stopDist), CashForPriceMove(stopDist) / Account.Equity * 100.0,
                    height, height / atr, expiry);
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

            double targetDist = side == TradeType.Buy ? mid - entry : entry - mid;
            if (targetDist < FadeMinRewardRisk * stopDist)
            {
                Reject("RF: reward/risk to middle band too low");
                return;
            }
            if (!PassesCostAndSpread(stopDist, targetDist, "RF"))
                return;

            double slPips = Math.Round(stopDist / Symbol.PipSize, 1);
            double tpPips = Math.Round(targetDist / Symbol.PipSize, 1);

            TradeResult r = ExecuteMarketRangeOrder(side, SymbolName, _volume, MaxSlippagePips, entry,
                BotLabel + FadeTag, slPips, tpPips);

            if (r.IsSuccessful && r.Position != null)
            {
                _ordersPlaced++;
                Print("RF FILL {0} @ {1} | SL {2} | TP {3} | risk ${4:F2} ({5:F1}% eq) | target {6:F2}R",
                    side, r.Position.EntryPrice, r.Position.StopLoss, r.Position.TakeProfit,
                    CashForPriceMove(stopDist), CashForPriceMove(stopDist) / Account.Equity * 100.0, targetDist / stopDist);
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

        // ================================================================== Shared trade checks
        // Applies the risk cap. In CappedAtRisk mode an over-wide stop is tightened to the cap,
        // but never below Min Stop ATR - a stop inside normal noise is just a donation.
        private bool ResolveStopDistance(double structural, double atr, string engine, out double stopDist)
        {
            double capDist = PriceForCash(RiskCapCash());
            double minDist = MinStopAtr * atr;
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
                Reject(engine + ": risk cap smaller than Min Stop ATR (volatility too high for account)");
                return false;
            }

            if (StopMode == MicroStopMode.Structural)
            {
                Reject(engine + ": structural stop exceeds risk cap");
                return false;
            }
            if (capDist < minDist)
            {
                Reject(engine + ": risk cap smaller than Min Stop ATR (volatility too high for account)");
                return false;
            }
            stopDist = capDist;
            return true;
        }

        private bool PassesCostAndSpread(double stopDist, double targetDist, string engine)
        {
            double spread = Symbol.Spread;
            if (spread / Symbol.PipSize > MaxSpreadPips)
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
            if (InNewsWindow(now)) { reason = "news window"; return false; }

            DateTime dayStart = now.Date;
            int tradesToday = OurHistory().Count(t => t.EntryTime >= dayStart);
            if (tradesToday >= MaxTradesPerDay) { reason = "daily trade cap"; return false; }

            var closedToday = OurHistory().Where(t => t.ClosingTime >= dayStart).OrderByDescending(t => t.ClosingTime).ToList();
            double dayPnl = closedToday.Sum(t => t.NetProfit);
            if (dayPnl <= -Account.Balance * MaxDailyLossPercent / 100.0) { reason = "daily loss cap"; return false; }

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
            Print("CLOSED {0} {1} | reason={2} | net={3:F2} | equity={4:F2}", p.Label, p.TradeType, args.Reason, p.NetProfit, Account.Equity);
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

        private void PrintRejectionSummary()
        {
            Print("RUN SUMMARY | orders placed/filled: {0} | closed trades: {1}", _ordersPlaced, OurHistory().Count());
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
            return priceDistance / Symbol.PipSize * Symbol.PipValue * _volume;
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
            return label == BotLabel + BreakoutTag || label == BotLabel + FadeTag;
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
