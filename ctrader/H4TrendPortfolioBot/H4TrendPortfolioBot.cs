// =====================================================================================
//  H4TrendPortfolioBot  -  multi-symbol H4 trend-following cBot for a small account
// -------------------------------------------------------------------------------------
//  Why this design (from four M5 gold backtests, ~390 trades, PF 0.87-0.92):
//    * Stop slippage + spread + commission were ~25-30% of R on $4-5 stops. That, not the
//      entry logic, killed the edge. Rule here: trade only when cost/R <= Max Cost/Risk.
//    * Patterns found by slicing one backtest did not survive. This bot uses textbook
//      trend-following rules with fixed, round defaults and NO time-of-day filters.
//    * Zero-trade runs were hard to diagnose. This bot prints a per-symbol feasibility
//      table at start and a per-symbol rejection summary at the end.
//
//  Strategy (per symbol, on closed H4 bars):
//    Regime : close vs 200 EMA, 50/200 EMA alignment, 200 EMA slope, optional ADX.
//    Entry  : Donchian breakout of the prior N bars in the regime direction.
//    Stop   : Stop ATR x ATR(14), server-side. Trail: chandelier from the extreme since entry.
//    Exit   : trail, or regime flip (close back through the 200 EMA).
//  Portfolio: one position per symbol, max positions, max positions per currency,
//             total open-risk cap, closed-balance drawdown pause, equity floor.
//
//  Verified against cTrader.Automate 1.0.21 (cAlgo.API). Attach to ANY chart of one of
//  the traded symbols; the bot loads H4 bars for every symbol itself.
//  Research code - backtest on unseen data and demo-test before risking capital.
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
    public enum TrendDirectionFilter
    {
        Both,
        LongOnly,
        ShortOnly
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class H4TrendPortfolioBot : Robot
    {
        // ------------------------------------------------------------------ Universe
        [Parameter("Symbols (comma separated)", Group = "Universe", DefaultValue = "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,XAUUSD")]
        public string SymbolList { get; set; }

        [Parameter("Symbol Suffix (auto = from chart)", Group = "Universe", DefaultValue = "auto")]
        public string SymbolSuffix { get; set; }

        [Parameter("Label", Group = "Universe", DefaultValue = "H4TP")]
        public string BotLabel { get; set; }

        [Parameter("Direction", Group = "Universe", DefaultValue = TrendDirectionFilter.Both)]
        public TrendDirectionFilter Direction { get; set; }

        // ------------------------------------------------------------------ Signal
        [Parameter("Fast EMA", Group = "Signal", DefaultValue = 50, MinValue = 10, MaxValue = 150, Step = 10)]
        public int FastEma { get; set; }

        [Parameter("Slow EMA", Group = "Signal", DefaultValue = 200, MinValue = 100, MaxValue = 300, Step = 50)]
        public int SlowEma { get; set; }

        [Parameter("Slow EMA Slope (bars)", Group = "Signal", DefaultValue = 10, MinValue = 3, MaxValue = 50, Step = 1)]
        public int SlopeBars { get; set; }

        [Parameter("Donchian Period", Group = "Signal", DefaultValue = 20, MinValue = 10, MaxValue = 60, Step = 5)]
        public int DonchianPeriod { get; set; }

        [Parameter("Use ADX Filter", Group = "Signal", DefaultValue = true)]
        public bool UseAdx { get; set; }

        [Parameter("ADX Min", Group = "Signal", DefaultValue = 20, MinValue = 10, MaxValue = 40, Step = 1)]
        public double AdxMin { get; set; }

        // ------------------------------------------------------------------ Exits
        [Parameter("ATR Period", Group = "Exits", DefaultValue = 14, MinValue = 5, MaxValue = 50, Step = 1)]
        public int AtrPeriod { get; set; }

        [Parameter("Initial Stop (ATR x)", Group = "Exits", DefaultValue = 2.0, MinValue = 1.0, MaxValue = 5.0, Step = 0.25)]
        public double StopAtr { get; set; }

        [Parameter("Chandelier Trail (ATR x)", Group = "Exits", DefaultValue = 3.0, MinValue = 1.5, MaxValue = 6.0, Step = 0.25)]
        public double TrailAtr { get; set; }

        [Parameter("Exit On Regime Flip", Group = "Exits", DefaultValue = true)]
        public bool ExitOnFlip { get; set; }

        // ------------------------------------------------------------------ Risk
        [Parameter("Risk Per Trade %", Group = "Risk", DefaultValue = 1.5, MinValue = 0.1, MaxValue = 5.0, Step = 0.1)]
        public double RiskPercent { get; set; }

        [Parameter("Min-Lot Risk Ceiling %", Group = "Risk", DefaultValue = 3.5, MinValue = 0.1, MaxValue = 10.0, Step = 0.1)]
        public double MinLotRiskCeiling { get; set; }

        [Parameter("Max Portfolio Open Risk %", Group = "Risk", DefaultValue = 8.0, MinValue = 0.5, MaxValue = 20.0, Step = 0.5)]
        public double MaxPortfolioRisk { get; set; }

        [Parameter("Max Open Positions", Group = "Risk", DefaultValue = 3, MinValue = 1, MaxValue = 20, Step = 1)]
        public int MaxPositions { get; set; }

        [Parameter("Max Positions Per Currency", Group = "Risk", DefaultValue = 2, MinValue = 1, MaxValue = 10, Step = 1)]
        public int MaxPerCurrency { get; set; }

        [Parameter("Gap Buffer (stop x)", Group = "Risk", DefaultValue = 1.2, MinValue = 1.0, MaxValue = 2.0, Step = 0.05)]
        public double GapBuffer { get; set; }

        [Parameter("Max Drawdown % (pause)", Group = "Risk", DefaultValue = 20, MinValue = 5, MaxValue = 50, Step = 1)]
        public double MaxDrawdownPercent { get; set; }

        [Parameter("Drawdown Pause (days)", Group = "Risk", DefaultValue = 14, MinValue = 1, MaxValue = 90, Step = 1)]
        public int DrawdownPauseDays { get; set; }

        [Parameter("Equity Floor $ (stops bot)", Group = "Risk", DefaultValue = 100, MinValue = 0, Step = 5)]
        public double EquityFloor { get; set; }

        // ------------------------------------------------------------------ Costs & execution
        [Parameter("Commission $ per Lot (round turn)", Group = "Costs", DefaultValue = 8, MinValue = 0, Step = 0.5)]
        public double CommissionPerLot { get; set; }

        [Parameter("Expected Slippage (pips)", Group = "Costs", DefaultValue = 1.0, MinValue = 0, Step = 0.5)]
        public double ExpectedSlippagePips { get; set; }

        [Parameter("Max Cost / Risk", Group = "Costs", DefaultValue = 0.10, MinValue = 0.01, MaxValue = 0.5, Step = 0.01)]
        public double MaxCostToRisk { get; set; }

        [Parameter("Max Slippage On Entry (pips)", Group = "Costs", DefaultValue = 5, MinValue = 0, Step = 1)]
        public double MaxEntrySlippagePips { get; set; }

        [Parameter("Entry Retry Window (min)", Group = "Costs", DefaultValue = 120, MinValue = 5, MaxValue = 239, Step = 5)]
        public int EntryWindowMinutes { get; set; }

        [Parameter("Friday Entry Cutoff (UTC hhmm)", Group = "Costs", DefaultValue = 1800, MinValue = 0, MaxValue = 2359)]
        public int FridayCutoffUtc { get; set; }

        // ------------------------------------------------------------------ State
        private const string PeakKey = "H4TP.PeakBalance";
        private readonly List<Market> _markets = new List<Market>();
        private readonly Dictionary<long, PendingClose> _closeQueue = new Dictionary<long, PendingClose>();
        private double _peakBalance;
        private DateTime _pauseUntil = DateTime.MinValue;
        private bool _stopped;

        private sealed class Market
        {
            public string Name;
            public Symbol Sym;
            public Bars Bars;
            public ExponentialMovingAverage EmaFast;
            public ExponentialMovingAverage EmaSlow;
            public AverageTrueRange Atr;
            public DirectionalMovementSystem Dms;
            public DateTime LastProcessedBar = DateTime.MinValue;
            public PendingEntry Pending;
            public int Signals;
            public int Orders;
            public readonly Dictionary<string, int> Rejections = new Dictionary<string, int>();
        }

        private sealed class PendingEntry
        {
            public TradeType Side;
            public double StopDistance;
            public DateTime Expires;
        }

        private sealed class PendingClose
        {
            public string Reason;
            public DateTime Expires;
        }

        // ================================================================== Lifecycle
        protected override void OnStart()
        {
            string suffix = ResolveSuffix();
            foreach (string raw in SymbolList.Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string name = ResolveSymbolName(raw.Trim(), suffix);
                if (name == null)
                {
                    Print("UNIVERSE | {0}: not found at this broker (tried with and without suffix '{1}') - skipped.", raw, suffix);
                    continue;
                }

                try
                {
                    var m = new Market { Name = name, Sym = Symbols.GetSymbol(name) };
                    m.Bars = MarketData.GetBars(TimeFrame.Hour4, name);
                    m.EmaFast = Indicators.ExponentialMovingAverage(m.Bars.ClosePrices, FastEma);
                    m.EmaSlow = Indicators.ExponentialMovingAverage(m.Bars.ClosePrices, SlowEma);
                    m.Atr = Indicators.AverageTrueRange(m.Bars, AtrPeriod, MovingAverageType.Simple);
                    m.Dms = Indicators.DirectionalMovementSystem(m.Bars, 14);
                    m.Bars.BarOpened += OnAnyBarOpened;
                    m.LastProcessedBar = m.Bars.OpenTimes.LastValue;
                    _markets.Add(m);
                }
                catch (Exception ex)
                {
                    Print("UNIVERSE | {0}: failed to load ({1}) - skipped.", name, ex.Message);
                }
            }

            if (_markets.Count == 0)
            {
                Print("No tradable symbols loaded. Check 'Symbols' and 'Symbol Suffix'. Stopping.");
                Stop();
                return;
            }

            InitPeakBalance();
            Positions.Closed += OnPositionClosed;
            Timer.Start(TimeSpan.FromSeconds(30));

            PrintConfig();
            PrintFeasibility();
        }

        protected override void OnStop()
        {
            foreach (Market m in _markets)
                if (m.Bars != null)
                    m.Bars.BarOpened -= OnAnyBarOpened;
            Positions.Closed -= OnPositionClosed;
            PersistPeakBalance();
            PrintRunSummary();
        }

        protected override void OnTimer()
        {
            try
            {
                if (_stopped)
                    return;
                CheckEquityFloor();

                // Fallback in case a BarOpened event was missed.
                foreach (Market m in _markets)
                    if (m.Bars.OpenTimes.LastValue > m.LastProcessedBar)
                        ProcessNewBar(m);

                foreach (Market m in _markets)
                    TryExecutePending(m);

                ProcessCloseQueue();
            }
            catch (Exception ex)
            {
                Print("OnTimer error: {0}", ex);
            }
        }

        private void OnAnyBarOpened(BarOpenedEventArgs args)
        {
            try
            {
                Market m = _markets.FirstOrDefault(x => x.Bars == args.Bars);
                if (m != null && !_stopped)
                    ProcessNewBar(m);
            }
            catch (Exception ex)
            {
                Print("BarOpened error: {0}", ex);
            }
        }

        // ================================================================== Per-bar logic
        private void ProcessNewBar(Market m)
        {
            DateTime open = m.Bars.OpenTimes.LastValue;
            if (open <= m.LastProcessedBar)
                return;
            m.LastProcessedBar = open;

            int i = m.Bars.Count - 2; // last closed bar
            if (i < Math.Max(SlowEma + SlopeBars, DonchianPeriod + AtrPeriod) + 2)
            {
                Reject(m, "warming up (not enough H4 history)");
                return;
            }

            ManagePosition(m, i);
            m.Pending = null;
            EvaluateEntry(m, i, open);
        }

        private void EvaluateEntry(Market m, int i, DateTime barOpen)
        {
            if (OurPositions(m).Any())
                return;

            double close = m.Bars.ClosePrices[i];
            double fast = m.EmaFast.Result[i], slow = m.EmaSlow.Result[i];
            double slope = slow - m.EmaSlow.Result[i - SlopeBars];
            bool adxOk = !UseAdx || m.Dms.ADX[i] >= AdxMin;

            double hh = double.MinValue, ll = double.MaxValue;
            for (int j = i - DonchianPeriod; j < i; j++)
            {
                hh = Math.Max(hh, m.Bars.HighPrices[j]);
                ll = Math.Min(ll, m.Bars.LowPrices[j]);
            }

            TradeType side;
            if (close > slow && fast > slow && slope > 0 && close > hh)
                side = TradeType.Buy;
            else if (close < slow && fast < slow && slope < 0 && close < ll)
                side = TradeType.Sell;
            else
                return; // no signal this bar (normal - not counted)

            m.Signals++;
            if (!adxOk) { Reject(m, "ADX below minimum"); return; }
            if (Direction == TrendDirectionFilter.LongOnly && side == TradeType.Sell) { Reject(m, "direction filter"); return; }
            if (Direction == TrendDirectionFilter.ShortOnly && side == TradeType.Buy) { Reject(m, "direction filter"); return; }

            double atr = m.Atr.Result[i];
            if (double.IsNaN(atr) || atr <= 0) { Reject(m, "ATR not ready"); return; }

            m.Pending = new PendingEntry
            {
                Side = side,
                StopDistance = StopAtr * atr,
                Expires = barOpen.AddMinutes(EntryWindowMinutes)
            };
            Print("{0} SIGNAL {1} | close={2} ATR={3} stop={4:F1} pips | ADX={5:F1}",
                m.Name, side, close, atr, StopAtr * atr / m.Sym.PipSize, m.Dms.ADX[i]);
            TryExecutePending(m);
        }

        private void TryExecutePending(Market m)
        {
            PendingEntry p = m.Pending;
            if (p == null)
                return;

            DateTime now = Server.Time;
            if (now > p.Expires)
            {
                Reject(m, "entry window expired (cost/spread never acceptable)");
                m.Pending = null;
                return;
            }

            // Hard blocks: drop the signal.
            string block = PortfolioBlock(m, now);
            if (block != null)
            {
                Reject(m, block);
                m.Pending = null;
                return;
            }

            // Soft blocks: wait for a better quote until the window expires.
            Symbol s = m.Sym;
            if (!QuoteValid(s))
                return;
            double costRatio = CostToRisk(m, p.StopDistance);
            if (costRatio > MaxCostToRisk)
            {
                if (now.AddSeconds(30) > p.Expires)
                    Print("{0}: cost/R {1:P0} > {2:P0} at window end.", m.Name, costRatio, MaxCostToRisk);
                return;
            }

            string why;
            double volume = Volume(m, p.StopDistance, out why);
            if (volume <= 0)
            {
                Reject(m, why);
                m.Pending = null;
                return;
            }

            double stopPips = Math.Round(p.StopDistance / s.PipSize, 1);
            double basePrice = p.Side == TradeType.Buy ? s.Ask : s.Bid;
            TradeResult r = ExecuteMarketRangeOrder(p.Side, m.Name, volume, MaxEntrySlippagePips, basePrice,
                BotLabel, stopPips, null);

            if (r.IsSuccessful && r.Position != null)
            {
                m.Orders++;
                Print("{0} FILL {1} {2} lots @ {3} | SL {4} ({5} pips) | risk ${6:F2} | cost/R {7:P0}",
                    m.Name, p.Side, s.VolumeInUnitsToQuantity(volume), r.Position.EntryPrice, r.Position.StopLoss,
                    stopPips, CashFor(m, p.StopDistance, volume), costRatio);
                if (!r.Position.StopLoss.HasValue)
                    AttachOrClose(m, r.Position, p.StopDistance);
                m.Pending = null;
            }
            else if (r.Error == ErrorCode.TechnicalError || r.Error == ErrorCode.Disconnected ||
                     r.Error == ErrorCode.Timeout || r.Error == ErrorCode.MarketClosed)
            {
                Print("{0}: transient order error {1} - retrying until {2:HH:mm}.", m.Name, r.Error, p.Expires);
            }
            else
            {
                Reject(m, "order rejected: " + r.Error);
                m.Pending = null;
            }
        }

        private void ManagePosition(Market m, int i)
        {
            double atr = m.Atr.Result[i];
            if (double.IsNaN(atr) || atr <= 0)
                return;
            Symbol s = m.Sym;

            foreach (Position pos in OurPositions(m).ToArray())
            {
                if (_closeQueue.ContainsKey(pos.Id))
                    continue;
                if (!pos.StopLoss.HasValue)
                {
                    AttachOrClose(m, pos, StopAtr * atr);
                    continue;
                }

                bool isBuy = pos.TradeType == TradeType.Buy;
                double close = m.Bars.ClosePrices[i];
                double slow = m.EmaSlow.Result[i];
                if (ExitOnFlip && (isBuy ? close < slow : close > slow))
                {
                    QueueClose(pos, "regime flip (close through slow EMA)");
                    continue;
                }

                int entryIdx = i;
                while (entryIdx > 0 && m.Bars.OpenTimes[entryIdx] > pos.EntryTime)
                    entryIdx--;

                double extreme = isBuy ? double.MinValue : double.MaxValue;
                for (int j = entryIdx; j <= i; j++)
                    extreme = isBuy ? Math.Max(extreme, m.Bars.HighPrices[j]) : Math.Min(extreme, m.Bars.LowPrices[j]);

                double trail = Math.Round(isBuy ? extreme - TrailAtr * atr : extreme + TrailAtr * atr, s.Digits);
                bool improves = isBuy ? trail > pos.StopLoss.Value + s.TickSize : trail < pos.StopLoss.Value - s.TickSize;
                bool valid = isBuy ? trail < s.Bid : trail > s.Ask;
                if (improves && valid)
                {
                    TradeResult r = pos.ModifyStopLossPrice(trail);
                    if (!r.IsSuccessful)
                        Print("{0}: trail update failed ({1}); previous stop stays.", m.Name, r.Error);
                }
                else if (improves && !valid)
                {
                    QueueClose(pos, "trail beyond market");
                }
            }
        }

        // ================================================================== Risk & costs
        private string PortfolioBlock(Market m, DateTime now)
        {
            if (now < _pauseUntil)
                return "drawdown pause";
            if (DrawdownBreached())
                return "drawdown pause";
            if (now.DayOfWeek == DayOfWeek.Friday && now.Hour * 100 + now.Minute >= FridayCutoffUtc)
                return "Friday cutoff (weekend gap)";
            if (OurPositions(m).Any())
                return "already in position";

            var open = AllOurPositions().ToList();
            if (open.Count >= MaxPositions)
                return "max open positions";

            foreach (string ccy in Currencies(m.Name))
            {
                int n = open.Count(p => Currencies(p.SymbolName).Contains(ccy));
                if (n >= MaxPerCurrency)
                    return "max positions in " + ccy;
            }
            return null;
        }

        private double Volume(Market m, double stopDistance, out string why)
        {
            why = null;
            Symbol s = m.Sym;
            double equity = Account.Equity;
            double riskPerUnit = CashFor(m, stopDistance * GapBuffer, 1.0);
            if (equity <= 0 || riskPerUnit <= 0 || double.IsNaN(riskPerUnit))
            {
                why = "invalid equity or pip value";
                return 0;
            }

            double budget = equity * RiskPercent / 100.0;
            double portfolioLeft = equity * MaxPortfolioRisk / 100.0 - OpenRiskCash();
            if (portfolioLeft <= 0)
            {
                why = "portfolio open-risk cap reached";
                return 0;
            }

            double raw = Math.Min(budget, portfolioLeft) / riskPerUnit;
            if (raw < s.VolumeInUnitsMin)
            {
                // Small-account rule: allow exactly the broker minimum if it stays under the ceiling.
                double minRisk = s.VolumeInUnitsMin * riskPerUnit;
                if (minRisk <= equity * MinLotRiskCeiling / 100.0 && minRisk <= portfolioLeft)
                    return s.VolumeInUnitsMin;
                why = string.Format("min lot risks {0:F1}% (> ceiling {1}%)", minRisk / equity * 100.0, MinLotRiskCeiling);
                return 0;
            }

            double units = s.NormalizeVolumeInUnits(Math.Min(raw, s.VolumeInUnitsMax), RoundingMode.Down);
            if (units * riskPerUnit > Math.Min(budget, portfolioLeft) * 1.001)
            {
                why = "normalized volume exceeds risk budget";
                return 0;
            }
            return units;
        }

        // Spread + commission + expected slippage, as a fraction of the stop distance.
        private double CostToRisk(Market m, double stopDistance)
        {
            Symbol s = m.Sym;
            double unitsPerLot = s.QuantityToVolumeInUnits(1.0);
            double cashPerPriceUnit = s.PipValue / s.PipSize; // per 1 unit of volume
            double commissionPrice = unitsPerLot > 0 && cashPerPriceUnit > 0
                ? (CommissionPerLot / unitsPerLot) / cashPerPriceUnit
                : 0;
            double cost = s.Spread + commissionPrice + ExpectedSlippagePips * s.PipSize;
            return stopDistance > 0 ? cost / stopDistance : double.MaxValue;
        }

        private double CashFor(Market m, double priceDistance, double volume)
        {
            return priceDistance / m.Sym.PipSize * m.Sym.PipValue * volume;
        }

        private double OpenRiskCash()
        {
            double total = 0;
            foreach (Position p in AllOurPositions())
            {
                Market m = _markets.FirstOrDefault(x => x.Name == p.SymbolName);
                if (m == null || !p.StopLoss.HasValue)
                {
                    total += Account.Equity * RiskPercent / 100.0;
                    continue;
                }
                double d = p.TradeType == TradeType.Buy ? p.EntryPrice - p.StopLoss.Value : p.StopLoss.Value - p.EntryPrice;
                if (d > 0)
                    total += CashFor(m, d, p.VolumeInUnits);
            }
            return total;
        }

        private bool DrawdownBreached()
        {
            if (Account.Balance > _peakBalance)
                _peakBalance = Account.Balance;
            if (_peakBalance <= 0)
                return false;

            double dd = (_peakBalance - Account.Balance) / _peakBalance * 100.0;
            if (dd < MaxDrawdownPercent)
                return false;

            _pauseUntil = Server.Time.AddDays(DrawdownPauseDays);
            Print("DRAWDOWN PAUSE: balance {0:F2} is {1:F1}% below peak {2:F2}; no new entries until {3:yyyy-MM-dd}.",
                Account.Balance, dd, _peakBalance, _pauseUntil);
            _peakBalance = Account.Balance;
            PersistPeakBalance();
            return true;
        }

        private void CheckEquityFloor()
        {
            if (EquityFloor <= 0 || Account.Equity >= EquityFloor)
                return;
            _stopped = true;
            Print("EQUITY FLOOR: equity {0:F2} < {1:F2}. Closing all positions and stopping.", Account.Equity, EquityFloor);
            foreach (Position p in AllOurPositions().ToArray())
                ClosePosition(p);
            Stop();
        }

        // ================================================================== Exits
        private void QueueClose(Position pos, string reason)
        {
            if (_closeQueue.ContainsKey(pos.Id))
                return;
            _closeQueue[pos.Id] = new PendingClose { Reason = reason, Expires = Server.Time.AddMinutes(EntryWindowMinutes) };
            Print("{0}: exit queued ({1}).", pos.SymbolName, reason);
            ProcessCloseQueue();
        }

        // Market exits wait for a sane spread (e.g. after rollover); the server-side stop keeps
        // protecting the position meanwhile, and the exit is forced once the window expires.
        private void ProcessCloseQueue()
        {
            foreach (long id in _closeQueue.Keys.ToArray())
            {
                Position pos = Positions.FirstOrDefault(p => p.Id == id);
                if (pos == null)
                {
                    _closeQueue.Remove(id);
                    continue;
                }
                Market m = _markets.FirstOrDefault(x => x.Name == pos.SymbolName);
                bool forced = Server.Time > _closeQueue[id].Expires;
                bool spreadOk = m != null && QuoteValid(m.Sym) && pos.StopLoss.HasValue &&
                                m.Sym.Spread <= MaxCostToRisk * Math.Abs(pos.EntryPrice - pos.StopLoss.Value);
                if (!forced && !spreadOk)
                    continue;

                TradeResult r = ClosePosition(pos);
                if (r.IsSuccessful)
                {
                    Print("{0}: exited ({1}).", pos.SymbolName, _closeQueue[id].Reason);
                    _closeQueue.Remove(id);
                }
                else
                {
                    Print("{0}: close failed ({1}); retrying - server-side stop remains.", pos.SymbolName, r.Error);
                }
            }
        }

        private void AttachOrClose(Market m, Position pos, double stopDistance)
        {
            double sl = pos.TradeType == TradeType.Buy ? pos.EntryPrice - stopDistance : pos.EntryPrice + stopDistance;
            TradeResult r = pos.ModifyStopLossPrice(Math.Round(sl, m.Sym.Digits));
            if (!r.IsSuccessful)
            {
                Print("{0}: could not attach stop ({1}) - closing for safety.", m.Name, r.Error);
                ClosePosition(pos);
            }
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            Position p = args.Position;
            if (p.Label != BotLabel)
                return;
            _closeQueue.Remove(p.Id);
            if (Account.Balance > _peakBalance)
                _peakBalance = Account.Balance;
            Print("{0} CLOSED {1} | {2} | net {3:F2} | swap {4:F2} | balance {5:F2}",
                p.SymbolName, p.TradeType, args.Reason, p.NetProfit, p.Swap, Account.Balance);
        }

        // ================================================================== Diagnostics
        private void Reject(Market m, string reason)
        {
            int n;
            m.Rejections.TryGetValue(reason, out n);
            m.Rejections[reason] = n + 1;
        }

        private void PrintConfig()
        {
            Print("CONFIG | symbols={0} | EMA {1}/{2} slope {3} | Donchian {4} | ADX {5} ({6}) | stop {7} ATR, trail {8} ATR, flip-exit {9}",
                string.Join(",", _markets.Select(x => x.Name)), FastEma, SlowEma, SlopeBars, DonchianPeriod,
                UseAdx ? "on" : "off", AdxMin, StopAtr, TrailAtr, ExitOnFlip);
            Print("CONFIG | risk {0}% (min-lot ceiling {1}%) | portfolio cap {2}% | max pos {3}, per ccy {4} | cost/R <= {5:P0} | commission ${6}/lot, slippage {7} pips",
                RiskPercent, MinLotRiskCeiling, MaxPortfolioRisk, MaxPositions, MaxPerCurrency, MaxCostToRisk,
                CommissionPerLot, ExpectedSlippagePips);
        }

        // Measured on this broker's own H4 history and current quotes.
        private void PrintFeasibility()
        {
            double equity = Account.Equity;
            Print("FEASIBILITY | equity ${0:F2} | per symbol: median H4 ATR, stop, min-lot risk, cost/R", equity);
            int ok = 0;
            foreach (Market m in _markets)
            {
                Symbol s = m.Sym;
                int last = m.Bars.Count - 2;
                var atrs = new List<double>();
                for (int j = Math.Max(AtrPeriod + 1, last - 500); j <= last; j++)
                {
                    double a = m.Atr.Result[j];
                    if (!double.IsNaN(a) && a > 0)
                        atrs.Add(a);
                }
                if (atrs.Count == 0)
                {
                    Print("FEASIBILITY | {0,-10} no H4 history loaded", m.Name);
                    continue;
                }

                atrs.Sort();
                double stop = StopAtr * atrs[atrs.Count / 2];
                double minRisk = CashFor(m, stop * GapBuffer, s.VolumeInUnitsMin);
                double minRiskPct = minRisk / equity * 100.0;
                double cost = QuoteValid(s) ? CostToRisk(m, stop) : double.NaN;

                string verdict;
                if (minRiskPct > MinLotRiskCeiling)
                    verdict = "SKIPPED: min lot too big for account";
                else if (!double.IsNaN(cost) && cost > MaxCostToRisk)
                    verdict = "SKIPPED: costs too high vs stop";
                else
                {
                    verdict = "OK";
                    ok++;
                }

                Print("FEASIBILITY | {0,-10} ATR {1,7:F1} pips | stop {2,7:F1} pips | min lot {3} = ${4,6:F2} ({5,4:F1}%) | cost/R {6} | {7}",
                    m.Name, atrs[atrs.Count / 2] / s.PipSize, stop / s.PipSize, s.VolumeInUnitsToQuantity(s.VolumeInUnitsMin),
                    minRisk, minRiskPct, double.IsNaN(cost) ? "n/a" : cost.ToString("P0", CultureInfo.InvariantCulture), verdict);
            }

            if (ok == 0)
                Print("VERDICT: no symbol is tradable at this account size and these settings - expect ZERO trades. " +
                      "Raise 'Min-Lot Risk Ceiling %', add capital, or add symbols with smaller minimum lots.");
            else
                Print("VERDICT: {0} of {1} symbols tradable.", ok, _markets.Count);
        }

        private void PrintRunSummary()
        {
            foreach (Market m in _markets)
            {
                Print("RUN SUMMARY | {0,-10} signals {1,4} | filled {2,4}", m.Name, m.Signals, m.Orders);
                foreach (var kv in m.Rejections.OrderByDescending(k => k.Value))
                    Print("RUN SUMMARY | {0,-10}   rejected {1,4} x {2}", m.Name, kv.Value, kv.Key);
            }
        }

        // ================================================================== Helpers
        private IEnumerable<Position> AllOurPositions()
        {
            return Positions.Where(p => p.Label == BotLabel);
        }

        private IEnumerable<Position> OurPositions(Market m)
        {
            return Positions.Where(p => p.Label == BotLabel && p.SymbolName == m.Name);
        }

        private static bool QuoteValid(Symbol s)
        {
            return !double.IsNaN(s.Bid) && !double.IsNaN(s.Ask) && s.Bid > 0 && s.Ask >= s.Bid;
        }

        // "EURUSD.mp" -> {"EUR","USD"}; non-FX names (US500, USOIL) -> empty.
        private static string[] Currencies(string symbolName)
        {
            string core = symbolName.Split('.')[0].ToUpperInvariant();
            if (core.Length == 6 && core.All(char.IsLetter))
                return new[] { core.Substring(0, 3), core.Substring(3, 3) };
            return new string[0];
        }

        private string ResolveSuffix()
        {
            if (!string.Equals(SymbolSuffix, "auto", StringComparison.OrdinalIgnoreCase))
                return SymbolSuffix ?? "";
            int dot = SymbolName.IndexOf('.');
            return dot >= 0 ? SymbolName.Substring(dot) : "";
        }

        private string ResolveSymbolName(string baseName, string suffix)
        {
            if (!string.IsNullOrEmpty(suffix) && !baseName.EndsWith(suffix, StringComparison.Ordinal) && Symbols.Exists(baseName + suffix))
                return baseName + suffix;
            return Symbols.Exists(baseName) ? baseName : null;
        }

        private void InitPeakBalance()
        {
            _peakBalance = Account.Balance;
            if (RunningMode != RunningMode.RealTime)
                return;
            try
            {
                string stored = LocalStorage.GetString(PeakKey);
                double v;
                if (!string.IsNullOrEmpty(stored) && double.TryParse(stored, NumberStyles.Float, CultureInfo.InvariantCulture, out v) && v > _peakBalance)
                    _peakBalance = v;
            }
            catch (Exception ex)
            {
                Print("LocalStorage read failed: {0}", ex.Message);
            }
        }

        private void PersistPeakBalance()
        {
            if (RunningMode != RunningMode.RealTime)
                return;
            try
            {
                LocalStorage.SetString(PeakKey, _peakBalance.ToString(CultureInfo.InvariantCulture));
            }
            catch (Exception ex)
            {
                Print("LocalStorage write failed: {0}", ex.Message);
            }
        }
    }
}
