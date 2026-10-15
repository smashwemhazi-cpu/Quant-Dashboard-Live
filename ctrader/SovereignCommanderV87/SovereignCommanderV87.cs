// SovereignCommander V87 "Apex Volume SMC": rebuild of the user's V86.
//
// Why V86 never traded: it demanded, on the SAME closed bar, a liquidity sweep
// (Low[1] below the 20-bar low) AND a bullish FVG (Low[1] above High[3]). Bar 3 is inside
// the 20-bar window, so Low[1] < Low[3] <= High[3]: the two conditions contradict each other
// and the buy (and mirrored sell) signal is impossible.
//
// V87 keeps V86's idea and runs it as a sequence, as the concept is meant to work:
//   1. Sweep        a bar wicks beyond the N-bar extreme and closes back inside.
//   2. Displacement within W bars after the sweep, a bar with body >= DisplacementAtr x ATR in
//                   the new direction AND tick volume >= surge x the average of the bars BEFORE
//                   it (V86 averaged the bar into its own baseline).
//   3. FVG          that displacement bar leaves a 3-bar gap (Low[j] > High[j-2] for longs).
//   4. Entry        LIMIT order at the gap midpoint (no entry slippage), expiring after N bars;
//                   cancelled if price reaches the target first or closes through the gap.
//   5. Stop         beyond the sweep extreme + buffer, never tighter than the slippage floor;
//                   if that risk exceeds the % cap the trade is SKIPPED (V86 clamped to $7,
//                   which put the stop inside the structure it was protecting).
//   6. Exit         take profit at TakeProfitR (0 = trail only) plus V86's ATR trail, now a
//                   chandelier from the extreme since entry, updated on closed bars.
// Risk layers from the validated MicroLotBreakoutBot: %-equity cap, $4 slippage floor, daily
// loss count/cash rebuilt from History (net, this label only), equity floor, untradable stop,
// spread/quote checks, DoubleTick stops, Friday shield, checked TradeResults, and a funnel
// summary so a zero-trade run says why.
using System;
using System.Collections.Generic;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo
{
    public enum V87Direction { Both, LongOnly, ShortOnly }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class SovereignCommander_V87_ApexVolumeSMC : Robot
    {
        // ---------------- 1. Risk ----------------
        [Parameter("Trade Direction", Group = "1. Risk Management", DefaultValue = V87Direction.Both)]
        public V87Direction TradeDirection { get; set; }

        [Parameter("Fixed Lot Size", Group = "1. Risk Management", DefaultValue = 0.01, MinValue = 0.01, Step = 0.01)]
        public double LotSize { get; set; }

        [Parameter("Max Risk % per Trade (skip above)", Group = "1. Risk Management", DefaultValue = 3.0, MinValue = 0.5, MaxValue = 10)]
        public double MaxRiskPercent { get; set; }

        [Parameter("Max Daily Losses (Circuit Breaker)", Group = "1. Risk Management", DefaultValue = 2, MinValue = 1, MaxValue = 5)]
        public int MaxDailyLosses { get; set; }

        [Parameter("Max Daily Loss % of Balance", Group = "1. Risk Management", DefaultValue = 6.0, MinValue = 1, MaxValue = 20)]
        public double MaxDailyLossPercent { get; set; }

        [Parameter("Equity Floor $ (stops bot, 0 = off)", Group = "1. Risk Management", DefaultValue = 120, MinValue = 0)]
        public double EquityFloor { get; set; }

        [Parameter("Commission $ per Lot (round turn)", Group = "1. Risk Management", DefaultValue = 8.0, MinValue = 0)]
        public double CommissionPerLot { get; set; }

        // ---------------- 2. Sessions ----------------
        [Parameter("Enable Asian Session (01-06 UTC)", Group = "2. Session Engine", DefaultValue = false)]
        public bool EnableAsianSession { get; set; }

        [Parameter("Enable London Session (07-12 UTC)", Group = "2. Session Engine", DefaultValue = true)]
        public bool EnableLondonSession { get; set; }

        [Parameter("Enable New York Session (13-19 UTC)", Group = "2. Session Engine", DefaultValue = true)]
        public bool EnableNySession { get; set; }

        // ---------------- 3. Volatility & volume ----------------
        [Parameter("ATR Period", Group = "3. Volatility & Volume Engine", DefaultValue = 14, MinValue = 2)]
        public int AtrPeriod { get; set; }

        [Parameter("Volume SMA Period", Group = "3. Volatility & Volume Engine", DefaultValue = 20, MinValue = 2)]
        public int VolumeSmaPeriod { get; set; }

        [Parameter("Displacement Volume Surge Multiplier", Group = "3. Volatility & Volume Engine", DefaultValue = 1.4, MinValue = 0, Step = 0.1)]
        public double VolumeSurgeThreshold { get; set; }

        [Parameter("Displacement Body (x ATR)", Group = "3. Volatility & Volume Engine", DefaultValue = 0.8, MinValue = 0, Step = 0.1)]
        public double DisplacementAtr { get; set; }

        // ---------------- 4. Structure ----------------
        [Parameter("Structure Lookback Bars", Group = "4. ICT Liquidity Pools", DefaultValue = 20, MinValue = 5)]
        public int StructureLookbackBars { get; set; }

        [Parameter("Displacement Window (bars after sweep)", Group = "4. ICT Liquidity Pools", DefaultValue = 6, MinValue = 1)]
        public int DisplacementWindow { get; set; }

        [Parameter("Min FVG Size (x ATR)", Group = "4. ICT Liquidity Pools", DefaultValue = 0.15, MinValue = 0, Step = 0.05)]
        public double MinFvgAtr { get; set; }

        [Parameter("H1 Macro EMA Period", Group = "4. ICT Liquidity Pools", DefaultValue = 200, MinValue = 10)]
        public int H1EmaPeriod { get; set; }

        // ---------------- 5. Orders & exits ----------------
        [Parameter("Stop Buffer beyond Sweep (x ATR)", Group = "5. Orders & Exits", DefaultValue = 0.25, MinValue = 0, Step = 0.05)]
        public double StopBufferAtr { get; set; }

        [Parameter("Expected Stop Slippage $", Group = "5. Orders & Exits", DefaultValue = 0.8, MinValue = 0, Step = 0.05)]
        public double ExpectedStopSlippage { get; set; }

        [Parameter("Max Slippage Share of Risk", Group = "5. Orders & Exits", DefaultValue = 0.20, MinValue = 0.05, MaxValue = 1, Step = 0.05)]
        public double MaxSlippageShare { get; set; }

        [Parameter("Take Profit (R, 0 = trail only)", Group = "5. Orders & Exits", DefaultValue = 2.0, MinValue = 0, Step = 0.25)]
        public double TakeProfitR { get; set; }

        [Parameter("Order Expiry (bars)", Group = "5. Orders & Exits", DefaultValue = 6, MinValue = 1)]
        public int OrderExpiryBars { get; set; }

        [Parameter("Max Spread (pips)", Group = "5. Orders & Exits", DefaultValue = 6, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        // ---------------- 6. Friday ----------------
        [Parameter("Enable Friday Shield", Group = "6. Friday Weekend Shield", DefaultValue = true)]
        public bool EnableFridayShield { get; set; }

        [Parameter("Friday No-New-Trades Hour (UTC)", Group = "6. Friday Weekend Shield", DefaultValue = 16, MinValue = 0, MaxValue = 23)]
        public int FridayNoNewTradesHour { get; set; }

        [Parameter("Friday Flatten Hour (UTC)", Group = "6. Friday Weekend Shield", DefaultValue = 20, MinValue = 0, MaxValue = 23)]
        public int FridayFlattenHour { get; set; }

        // ---------------- 7. Trail ----------------
        [Parameter("Enable ATR Trend Trail", Group = "7. Trend Trail", DefaultValue = true)]
        public bool EnableTrendTrailing { get; set; }

        [Parameter("ATR Trail Multiplier", Group = "7. Trend Trail", DefaultValue = 2.0, MinValue = 0.5, Step = 0.1)]
        public double AtrTrailMultiplier { get; set; }

        [Parameter("Start Trailing (R)", Group = "7. Trend Trail", DefaultValue = 1.5, MinValue = 0, Step = 0.25)]
        public double TrailTriggerR { get; set; }

        private const string Label = "SovApexV87";
        private const string BuildTag = "V87-2026-10-15a";

        private class Setup
        {
            public bool IsBuy;
            public int SweepIndex;
            public double SweepExtreme;
            public double Pool;
        }

        private class Plan
        {
            public bool IsBuy;
            public double Entry, Stop, Tp, FvgTop, FvgBottom;
        }

        private AverageTrueRange _atr;
        private ExponentialMovingAverage _h1Ema;
        private Bars _h1Bars;
        private Setup _long, _short;
        private Plan _plan;
        private bool _stopped;
        private readonly Dictionary<long, double> _initialRisk = new Dictionary<long, double>();
        private readonly Dictionary<string, int> _funnel = new Dictionary<string, int>();

        protected override void OnStart()
        {
            _atr = Indicators.AverageTrueRange(Bars, AtrPeriod, MovingAverageType.Exponential);
            _h1Bars = MarketData.GetBars(TimeFrame.Hour);
            _h1Ema = Indicators.ExponentialMovingAverage(_h1Bars.ClosePrices, H1EmaPeriod);

            Positions.Closed += OnPositionClosed;
            PendingOrders.Filled += OnOrderFilled;

            foreach (var p in OurPositions())
                if (p.StopLoss.HasValue)
                    _initialRisk[p.Id] = Math.Abs(p.EntryPrice - p.StopLoss.Value);

            double units = Units();
            double minRisk = CashFor(CostFloor(), units) + Commission(units);
            Print("BUILD {0} | {1} {2} | units {3} | pipSize {4} | equity {5:F2}", BuildTag, SymbolName, TimeFrame, units, Symbol.PipSize, Account.Equity);
            Print("CONFIG risk cap {0}% = ${1:F2} | smallest trade risks ${2:F2} (floor {3:F2} price) | equity needed to trade ${4:F0}",
                MaxRiskPercent, Account.Equity * MaxRiskPercent / 100, minRisk, CostFloor(), minRisk * 100 / MaxRiskPercent);
            Print("CONFIG sweep {0} bars | displacement {1}xATR + volume {2}x SMA{3} within {4} bars | FVG >= {5}xATR | H1 EMA{6}",
                StructureLookbackBars, DisplacementAtr, VolumeSurgeThreshold, VolumeSmaPeriod, DisplacementWindow, MinFvgAtr, H1EmaPeriod);
            Print("CONFIG TP {0}R | trail {1} {2}xATR from {3}R | sessions asia={4} london={5} ny={6}",
                TakeProfitR, EnableTrendTrailing, AtrTrailMultiplier, TrailTriggerR, EnableAsianSession, EnableLondonSession, EnableNySession);
            if (!SymbolName.StartsWith("XAU", StringComparison.OrdinalIgnoreCase))
                Print("WARNING: stops and floors are sized for gold; {0} needs its own settings and test.", SymbolName);
            if (TakeProfitR <= 0 && !EnableTrendTrailing)
                Print("WARNING: no take profit and no trail: winners can only exit at the stop or on Friday.");
        }

        protected override void OnStop()
        {
            var ours = History.Where(t => t.SymbolName == SymbolName && t.Label == Label).ToList();
            double gp = ours.Where(t => t.NetProfit > 0).Sum(t => t.NetProfit);
            double gl = -ours.Where(t => t.NetProfit < 0).Sum(t => t.NetProfit);
            Print("RUN SUMMARY trades={0} wins={1} net=${2:F2} PF={3:F2}", ours.Count, ours.Count(t => t.NetProfit > 0), gp - gl, gl > 0 ? gp / gl : 0);
            Print("RUN SUMMARY funnel: " + string.Join(" | ", _funnel.OrderByDescending(k => k.Value).Select(k => k.Key + " " + k.Value)));
        }

        protected override void OnTick()
        {
            if (_stopped || !IsQuoteValid())
                return;
            CheckEquityFloor();
            CheckFridayAutoKill();
            CancelIfPlayedOut();
        }

        protected override void OnBar()
        {
            if (_stopped)
                return;
            int i = Bars.Count - 2; // last closed bar
            if (i < StructureLookbackBars + VolumeSmaPeriod + 5)
                return;
            double atr = _atr.Result[i];
            if (double.IsNaN(atr) || atr <= 0)
                return;

            CancelIfFvgFailed(i);
            TrailOpenPositions(atr);

            if (TradeDirection != V87Direction.ShortOnly)
                _long = Advance(_long, true, i, atr);
            if (TradeDirection != V87Direction.LongOnly)
                _short = Advance(_short, false, i, atr);
        }

        // ================= Signal: sweep -> displacement (+volume) -> FVG =================

        private Setup Advance(Setup s, bool isBuy, int i, double atr)
        {
            if (s != null)
            {
                if (isBuy ? Bars.ClosePrices[i] < s.SweepExtreme : Bars.ClosePrices[i] > s.SweepExtreme)
                {
                    Count("setup invalidated (closed through sweep)");
                    s = null;
                }
                else if (i - s.SweepIndex > DisplacementWindow)
                {
                    Count("setup expired (no displacement)");
                    s = null;
                }
                else if (i - s.SweepIndex >= 1 && IsDisplacement(isBuy, i, atr))
                {
                    double top, bottom;
                    if (Fvg(isBuy, i, atr, out top, out bottom))
                    {
                        Count("displacement + FVG");
                        TryPlace(s, top, bottom, atr);
                    }
                    else
                        Count("displacement without FVG");
                    s = null;
                }
            }

            if (s == null)
            {
                double pool = isBuy
                    ? Enumerable.Range(i - StructureLookbackBars, StructureLookbackBars).Min(k => Bars.LowPrices[k])
                    : Enumerable.Range(i - StructureLookbackBars, StructureLookbackBars).Max(k => Bars.HighPrices[k]);
                bool swept = isBuy
                    ? Bars.LowPrices[i] < pool && Bars.ClosePrices[i] > pool
                    : Bars.HighPrices[i] > pool && Bars.ClosePrices[i] < pool;
                if (swept)
                {
                    Count("sweep");
                    s = new Setup { IsBuy = isBuy, SweepIndex = i, Pool = pool, SweepExtreme = isBuy ? Bars.LowPrices[i] : Bars.HighPrices[i] };
                }
            }
            return s;
        }

        private bool IsDisplacement(bool isBuy, int j, double atr)
        {
            double o = Bars.OpenPrices[j], c = Bars.ClosePrices[j];
            bool body = Math.Abs(c - o) >= DisplacementAtr * atr && (isBuy ? c > o : c < o);
            double avg = 0;
            for (int k = j - VolumeSmaPeriod; k < j; k++) // baseline excludes the displacement bar
                avg += Bars.TickVolumes[k];
            avg /= VolumeSmaPeriod;
            bool volume = VolumeSurgeThreshold <= 0 || Bars.TickVolumes[j] >= avg * VolumeSurgeThreshold;
            return body && volume;
        }

        private bool Fvg(bool isBuy, int j, double atr, out double top, out double bottom)
        {
            top = isBuy ? Bars.LowPrices[j] : Bars.LowPrices[j - 2];
            bottom = isBuy ? Bars.HighPrices[j - 2] : Bars.HighPrices[j];
            return top - bottom >= Math.Max(MinFvgAtr * atr, Symbol.PipSize);
        }

        // ================= Order =================

        private void TryPlace(Setup s, double top, double bottom, double atr)
        {
            string why;
            if (!CanOpen(s.IsBuy, out why))
            {
                Count("blocked: " + why);
                return;
            }

            double units = Units();
            double entry = Round((top + bottom) / 2);
            double stop = s.IsBuy ? s.SweepExtreme - StopBufferAtr * atr - Symbol.Spread
                                  : s.SweepExtreme + StopBufferAtr * atr + Symbol.Spread;
            if (Math.Abs(entry - stop) < CostFloor())
                stop = s.IsBuy ? entry - CostFloor() : entry + CostFloor(); // widen only, never tighten
            stop = Round(stop);
            double dist = Math.Abs(entry - stop);

            double risk = CashFor(dist, units) + Commission(units);
            double cap = Account.Equity * MaxRiskPercent / 100;
            if (risk > cap)
            {
                Count("skipped: risk above cap");
                return;
            }
            if (s.IsBuy ? entry >= Symbol.Ask : entry <= Symbol.Bid)
            {
                Count("skipped: price already back in the gap");
                return;
            }
            double margin = Symbol.GetEstimatedMargin(s.IsBuy ? TradeType.Buy : TradeType.Sell, units);
            if (margin > Account.FreeMargin * 0.8)
            {
                Count("skipped: margin");
                return;
            }

            double? tp = TakeProfitR > 0 ? Round(s.IsBuy ? entry + TakeProfitR * dist : entry - TakeProfitR * dist) : (double?)null;
            DateTime expiry = Server.Time.AddMinutes(OrderExpiryBars * BarMinutes());
            var r = PlaceLimitOrder(s.IsBuy ? TradeType.Buy : TradeType.Sell, SymbolName, units, entry, Label, stop, tp,
                ProtectionType.Absolute, expiry, "V87", false, StopTriggerMethod.DoubleTrade);
            if (!r.IsSuccessful)
            {
                Count("order failed");
                Print("ORDER FAILED {0}: {1}", s.IsBuy ? "Buy" : "Sell", r.Error);
                return;
            }
            Count("order placed");
            _plan = new Plan { IsBuy = s.IsBuy, Entry = entry, Stop = stop, Tp = tp ?? 0, FvgTop = top, FvgBottom = bottom };
            Print("ORDER {0} limit {1} SL {2} TP {3} | risk ${4:F2} ({5:F2}% eq) | sweep {6} pool {7:F2} | FVG {8:F2}-{9:F2} | atr {10:F2}",
                s.IsBuy ? "Buy" : "Sell", entry, stop, tp.HasValue ? tp.Value.ToString() : "trail", risk, risk / Account.Equity * 100,
                s.SweepExtreme, s.Pool, bottom, top, atr);
        }

        private bool CanOpen(bool isBuy, out string why)
        {
            why = null;
            DateTime now = Server.Time;
            int h = now.Hour;
            bool inSession = (EnableAsianSession && h >= 1 && h < 6) || (EnableLondonSession && h >= 7 && h < 12) || (EnableNySession && h >= 13 && h < 19);
            if (!inSession) { why = "session"; return false; }
            if (EnableFridayShield && now.DayOfWeek == DayOfWeek.Friday && h >= FridayNoNewTradesHour) { why = "friday"; return false; }
            if (OurPositions().Any() || OurOrders().Any()) { why = "busy"; return false; }
            if (!IsQuoteValid() || Symbol.Spread / Symbol.PipSize > MaxSpreadPips) { why = "spread"; return false; }

            if (_h1Bars.Count < H1EmaPeriod + 2) { why = "h1 history"; return false; }
            double h1Close = _h1Bars.ClosePrices.Last(1), h1Ema = _h1Ema.Result.Last(1);
            if (isBuy ? h1Close <= h1Ema : h1Close >= h1Ema) { why = "h1 trend"; return false; }

            // Circuit breaker from History: survives restarts, net P/L, this bot only (V86 counted
            // gross P/L of every bot, in memory).
            var today = History.Where(t => t.SymbolName == SymbolName && t.Label == Label && t.ClosingTime.Date == now.Date).ToList();
            if (today.Count(t => t.NetProfit < 0) >= MaxDailyLosses) { why = "daily losses"; return false; }
            if (-today.Sum(t => t.NetProfit) >= Account.Balance * MaxDailyLossPercent / 100) { why = "daily loss cash"; return false; }
            return true;
        }

        // ================= Management =================

        private void CancelIfPlayedOut()
        {
            if (_plan == null || _plan.Tp <= 0)
                return;
            bool reached = _plan.IsBuy ? Symbol.Bid >= _plan.Tp : Symbol.Ask <= _plan.Tp;
            if (reached)
                CancelOurOrders("missed: target reached before fill");
        }

        private void CancelIfFvgFailed(int i)
        {
            if (_plan == null || !OurOrders().Any())
                return;
            double c = Bars.ClosePrices[i];
            if (_plan.IsBuy ? c < _plan.FvgBottom : c > _plan.FvgTop)
                CancelOurOrders("cancelled: closed through FVG");
        }

        // Chandelier from the extreme since entry (V86 trailed from the current price every tick).
        private void TrailOpenPositions(double atr)
        {
            if (!EnableTrendTrailing)
                return;
            foreach (var p in OurPositions().ToList())
            {
                double risk;
                if (!_initialRisk.TryGetValue(p.Id, out risk) || risk <= 0 || !p.StopLoss.HasValue)
                    continue;
                bool isBuy = p.TradeType == TradeType.Buy;
                double progress = isBuy ? Symbol.Bid - p.EntryPrice : p.EntryPrice - Symbol.Ask;
                if (progress < TrailTriggerR * risk)
                    continue;

                double extreme = isBuy ? double.MinValue : double.MaxValue;
                for (int k = Bars.Count - 2; k >= 0 && Bars.OpenTimes[k + 1] > p.EntryTime; k--)
                    extreme = isBuy ? Math.Max(extreme, Bars.HighPrices[k]) : Math.Min(extreme, Bars.LowPrices[k]);
                if (extreme == double.MinValue || extreme == double.MaxValue)
                    continue;

                double sl = Round(isBuy ? extreme - AtrTrailMultiplier * atr : extreme + AtrTrailMultiplier * atr);
                bool improves = isBuy ? sl > p.StopLoss.Value + Symbol.PipSize : sl < p.StopLoss.Value - Symbol.PipSize;
                bool valid = isBuy ? sl < Symbol.Bid - Symbol.Spread : sl > Symbol.Ask + Symbol.Spread;
                if (!improves || !valid)
                    continue;
                var r = p.ModifyStopLossPrice(sl);
                if (r.IsSuccessful) Count("trail moves");
                else Print("TRAIL FAILED pos {0}: {1}", p.Id, r.Error);
            }
        }

        private void CheckFridayAutoKill()
        {
            if (!EnableFridayShield || Server.Time.DayOfWeek != DayOfWeek.Friday || Server.Time.Hour < FridayFlattenHour)
                return;
            CancelOurOrders("cancelled: friday");
            foreach (var p in OurPositions().ToList())
            {
                var r = ClosePosition(p);
                if (r.IsSuccessful) Print("[WEEKEND SHIELD] closed {0}", p.Id);
                else Print("[WEEKEND SHIELD] close failed {0}: {1} (retrying next tick)", p.Id, r.Error);
            }
        }

        private void CheckEquityFloor()
        {
            double units = Units();
            double minRisk = CashFor(CostFloor(), units) + Commission(units);
            bool floor = EquityFloor > 0 && Account.Equity < EquityFloor;
            bool untradable = !OurPositions().Any() && !OurOrders().Any() && Account.Equity * MaxRiskPercent / 100 < minRisk;
            if (!floor && !untradable)
                return;
            _stopped = true;
            Print(floor ? "EQUITY FLOOR: equity {0:F2} < {1:F2}. Flattening and stopping."
                        : "UNTRADABLE: equity {0:F2} cannot afford the smallest stop at {2}% risk. Stopping.",
                Account.Equity, EquityFloor, MaxRiskPercent);
            CancelOurOrders("cancelled: stopping");
            foreach (var p in OurPositions().ToList()) ClosePosition(p);
            Stop();
        }

        private void OnOrderFilled(PendingOrderFilledEventArgs args)
        {
            var p = args.Position;
            if (p == null || p.SymbolName != SymbolName || p.Label != Label)
                return;
            Count("filled");
            if (!p.StopLoss.HasValue)
            {
                Print("Filled without a stop (gap through the level): closing.");
                ClosePosition(p);
                return;
            }
            _initialRisk[p.Id] = Math.Abs(p.EntryPrice - p.StopLoss.Value);
            Print("FILLED {0} @ {1} SL {2} TP {3}", p.TradeType, p.EntryPrice, p.StopLoss, p.TakeProfit);
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            var p = args.Position;
            if (p.SymbolName != SymbolName || p.Label != Label)
                return;
            double risk;
            _initialRisk.TryGetValue(p.Id, out risk);
            _initialRisk.Remove(p.Id);
            double r = risk > 0 ? p.NetProfit / CashFor(risk, p.VolumeInUnits) : 0;
            Print("CLOSED {0} net ${1:F2} ({2:F2}R) reason {3} equity {4:F2}", p.TradeType, p.NetProfit, r, args.Reason, Account.Equity);
        }

        // ================= Helpers =================

        private void CancelOurOrders(string reason)
        {
            foreach (var o in OurOrders().ToList())
            {
                var r = CancelPendingOrder(o);
                if (r.IsSuccessful) Count(reason);
                else Print("CANCEL FAILED {0}: {1}", o.Id, r.Error);
            }
        }

        private double Units()
        {
            double u = Symbol.NormalizeVolumeInUnits(Symbol.QuantityToVolumeInUnits(LotSize), RoundingMode.Down);
            return Math.Max(u, Symbol.VolumeInUnitsMin);
        }

        private double CostFloor() { return MaxSlippageShare > 0 ? ExpectedStopSlippage / MaxSlippageShare : 0; }
        private double CashFor(double dist, double units) { return dist / Symbol.PipSize * Symbol.PipValue * units; }
        private double Commission(double units) { return CommissionPerLot * Symbol.VolumeInUnitsToQuantity(units); }
        private double Round(double price) { return Math.Round(price, Symbol.Digits); }

        private double BarMinutes()
        {
            int n = Bars.Count;
            double m = n >= 3 ? Math.Min((Bars.OpenTimes[n - 1] - Bars.OpenTimes[n - 2]).TotalMinutes,
                                         (Bars.OpenTimes[n - 2] - Bars.OpenTimes[n - 3]).TotalMinutes) : 5;
            return m > 0 ? m : 5;
        }

        private bool IsQuoteValid()
        {
            double b = Symbol.Bid, a = Symbol.Ask;
            return !double.IsNaN(b) && !double.IsNaN(a) && b > 0 && a >= b;
        }

        private IEnumerable<Position> OurPositions() { return Positions.Where(p => p.SymbolName == SymbolName && p.Label == Label); }
        private IEnumerable<PendingOrder> OurOrders() { return PendingOrders.Where(o => o.SymbolName == SymbolName && o.Label == Label); }

        private void Count(string key)
        {
            int n;
            _funnel.TryGetValue(key, out n);
            _funnel[key] = n + 1;
        }
    }
}
