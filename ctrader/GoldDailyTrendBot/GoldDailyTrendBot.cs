// =====================================================================================
//  GoldDailyTrendBot  -  cTrader Automate cBot for XAUUSD on the Daily timeframe
// -------------------------------------------------------------------------------------
//  Strategy : Trend following (Donchian breakout) + with-trend pullback entries.
//  Regime   : Close vs 200 EMA, 50/200 EMA alignment, 200 EMA slope, optional ADX.
//  Exits    : Hard ATR stop (server-side), ATR chandelier trail, time stop, swap-bleed exit.
//  Risk     : Equity-%-risk sizing with weekend-gap buffer, total open-risk cap,
//             loss-streak de-risking, peak-equity drawdown halt, macro (USD) size overlay.
//  Execution: Delayed entry after the daily rollover, spread filter vs rolling median,
//             market-range (max slippage) orders, stale/invalid quote guard, retries.
//
//  Verified against cTrader.Automate 1.0.21 (cAlgo.API). Attach to an XAUUSD Daily chart.
//  This is research code - backtest and forward-test on demo before risking capital.
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
    public enum TradeDirectionMode
    {
        Both,
        LongOnly,
        ShortOnly
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.None)]
    public class GoldDailyTrendBot : Robot
    {
        // ------------------------------------------------------------------ General
        [Parameter("Label", Group = "General", DefaultValue = "GoldDTB")]
        public string BotLabel { get; set; }

        [Parameter("Direction", Group = "General", DefaultValue = TradeDirectionMode.Both)]
        public TradeDirectionMode Direction { get; set; }

        // ------------------------------------------------------------------ Regime
        [Parameter("Fast EMA", Group = "Regime", DefaultValue = 50, MinValue = 10, MaxValue = 150, Step = 10)]
        public int FastEmaPeriod { get; set; }

        [Parameter("Slow EMA", Group = "Regime", DefaultValue = 200, MinValue = 100, MaxValue = 300, Step = 25)]
        public int SlowEmaPeriod { get; set; }

        [Parameter("Slow EMA Slope Lookback", Group = "Regime", DefaultValue = 20, MinValue = 5, MaxValue = 60, Step = 5)]
        public int SlopeLookback { get; set; }

        [Parameter("Use ADX Filter", Group = "Regime", DefaultValue = true)]
        public bool UseAdxFilter { get; set; }

        [Parameter("ADX Period", Group = "Regime", DefaultValue = 14, MinValue = 7, MaxValue = 30, Step = 1)]
        public int AdxPeriod { get; set; }

        [Parameter("ADX Min", Group = "Regime", DefaultValue = 20, MinValue = 10, MaxValue = 40, Step = 1)]
        public double AdxMin { get; set; }

        // ------------------------------------------------------------------ Entries
        [Parameter("Donchian Period", Group = "Entries", DefaultValue = 20, MinValue = 10, MaxValue = 60, Step = 5)]
        public int DonchianPeriod { get; set; }

        [Parameter("Use Pullback Entries", Group = "Entries", DefaultValue = true)]
        public bool UsePullback { get; set; }

        [Parameter("Pullback EMA", Group = "Entries", DefaultValue = 20, MinValue = 10, MaxValue = 50, Step = 5)]
        public int PullbackEmaPeriod { get; set; }

        [Parameter("RSI Period", Group = "Entries", DefaultValue = 2, MinValue = 2, MaxValue = 14, Step = 1)]
        public int RsiPeriod { get; set; }

        [Parameter("RSI Oversold", Group = "Entries", DefaultValue = 10, MinValue = 1, MaxValue = 40, Step = 1)]
        public double RsiOversold { get; set; }

        [Parameter("RSI Overbought", Group = "Entries", DefaultValue = 90, MinValue = 60, MaxValue = 99, Step = 1)]
        public double RsiOverbought { get; set; }

        [Parameter("Cooldown After Loss (bars)", Group = "Entries", DefaultValue = 3, MinValue = 0, MaxValue = 20, Step = 1)]
        public int CooldownBars { get; set; }

        // ------------------------------------------------------------------ Exits
        [Parameter("ATR Period", Group = "Exits", DefaultValue = 20, MinValue = 5, MaxValue = 50, Step = 1)]
        public int AtrPeriod { get; set; }

        [Parameter("Initial Stop (ATR x)", Group = "Exits", DefaultValue = 2.5, MinValue = 1.0, MaxValue = 5.0, Step = 0.25)]
        public double InitialStopAtr { get; set; }

        [Parameter("Chandelier Trail (ATR x)", Group = "Exits", DefaultValue = 3.0, MinValue = 1.5, MaxValue = 6.0, Step = 0.25)]
        public double TrailAtr { get; set; }

        [Parameter("Pullback Time Stop (bars)", Group = "Exits", DefaultValue = 10, MinValue = 0, MaxValue = 40, Step = 1)]
        public int TimeStopBars { get; set; }

        [Parameter("Time Stop Min Progress (R)", Group = "Exits", DefaultValue = 1.0, MinValue = 0.0, MaxValue = 3.0, Step = 0.25)]
        public double TimeStopMinR { get; set; }

        [Parameter("Max Swap Bleed (R)", Group = "Exits", DefaultValue = 0.25, MinValue = 0.0, MaxValue = 2.0, Step = 0.05)]
        public double MaxSwapBleedR { get; set; }

        // ------------------------------------------------------------------ Risk
        [Parameter("Risk Per Trade %", Group = "Risk", DefaultValue = 0.75, MinValue = 0.05, MaxValue = 3.0, Step = 0.05)]
        public double RiskPercent { get; set; }

        [Parameter("Max Total Open Risk %", Group = "Risk", DefaultValue = 2.0, MinValue = 0.1, MaxValue = 10.0, Step = 0.1)]
        public double MaxTotalRiskPercent { get; set; }

        [Parameter("Gap Buffer (stop x)", Group = "Risk", DefaultValue = 1.25, MinValue = 1.0, MaxValue = 2.0, Step = 0.05)]
        public double GapBufferMultiplier { get; set; }

        [Parameter("Max Lots", Group = "Risk", DefaultValue = 50, MinValue = 0.01, Step = 0.01)]
        public double MaxLots { get; set; }

        [Parameter("Max Drawdown % (halt)", Group = "Risk", DefaultValue = 15, MinValue = 1, MaxValue = 50, Step = 1)]
        public double MaxDrawdownPercent { get; set; }

        [Parameter("Reset Drawdown Halt On Start", Group = "Risk", DefaultValue = false)]
        public bool ResetDrawdownHalt { get; set; }

        [Parameter("Loss Streak Trigger", Group = "Risk", DefaultValue = 4, MinValue = 0, MaxValue = 20, Step = 1)]
        public int LossStreakTrigger { get; set; }

        [Parameter("Loss Streak Risk Factor", Group = "Risk", DefaultValue = 0.5, MinValue = 0.1, MaxValue = 1.0, Step = 0.05)]
        public double LossStreakRiskFactor { get; set; }

        // ------------------------------------------------------------------ Execution
        [Parameter("Entry Delay After Daily Open (min)", Group = "Execution", DefaultValue = 60, MinValue = 0, MaxValue = 600, Step = 15)]
        public int EntryDelayMinutes { get; set; }

        [Parameter("Signal Expiry (hours)", Group = "Execution", DefaultValue = 8, MinValue = 1, MaxValue = 20, Step = 1)]
        public int SignalExpiryHours { get; set; }

        [Parameter("Max Slippage (pips)", Group = "Execution", DefaultValue = 50, MinValue = 0, Step = 1)]
        public double MaxSlippagePips { get; set; }

        [Parameter("Max Spread (pips)", Group = "Execution", DefaultValue = 80, MinValue = 1, Step = 1)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Max Spread vs Median (x)", Group = "Execution", DefaultValue = 2.5, MinValue = 1.0, MaxValue = 10.0, Step = 0.1)]
        public double MaxSpreadMedianMultiple { get; set; }

        [Parameter("Stale Quote (sec)", Group = "Execution", DefaultValue = 120, MinValue = 10, Step = 10)]
        public int StaleQuoteSeconds { get; set; }

        [Parameter("Max Order Retries", Group = "Execution", DefaultValue = 3, MinValue = 0, MaxValue = 10, Step = 1)]
        public int MaxRetries { get; set; }

        [Parameter("Retry Interval (sec)", Group = "Execution", DefaultValue = 30, MinValue = 5, Step = 5)]
        public int RetrySeconds { get; set; }

        // ------------------------------------------------------------------ Calendar
        [Parameter("Blackout Dates (yyyy-MM-dd,...)", Group = "Calendar", DefaultValue = "")]
        public string BlackoutDates { get; set; }

        [Parameter("Skip Pre-Weekend Entries", Group = "Calendar", DefaultValue = true)]
        public bool SkipPreWeekendEntries { get; set; }

        [Parameter("Stop On Sunday Candle", Group = "Calendar", DefaultValue = true)]
        public bool StopOnSundayCandle { get; set; }

        // ------------------------------------------------------------------ Macro overlay
        [Parameter("USD Proxy Symbol (blank=off)", Group = "Macro", DefaultValue = "")]
        public string DollarProxySymbol { get; set; }

        [Parameter("USD Proxy Inverted (e.g. EURUSD)", Group = "Macro", DefaultValue = false)]
        public bool DollarProxyInverted { get; set; }

        [Parameter("USD Lookback (bars)", Group = "Macro", DefaultValue = 20, MinValue = 5, MaxValue = 60, Step = 5)]
        public int DollarLookback { get; set; }

        [Parameter("USD Adverse Size Factor", Group = "Macro", DefaultValue = 0.5, MinValue = 0.0, MaxValue = 1.0, Step = 0.05)]
        public double DollarAdverseFactor { get; set; }

        // ------------------------------------------------------------------ State
        private const string BreakoutTag = "-BO";
        private const string PullbackTag = "-PB";
        private const string PeakEquityKey = "PeakEquity";
        private const int MinSpreadSamples = 30;
        private const int MaxSpreadSamples = 1440;

        private ExponentialMovingAverage _emaFast;
        private ExponentialMovingAverage _emaSlow;
        private ExponentialMovingAverage _emaPullback;
        private RelativeStrengthIndex _rsi;
        private AverageTrueRange _atr;
        private DirectionalMovementSystem _dms;
        private Bars _dollarBars;

        private readonly HashSet<DateTime> _blackout = new HashSet<DateTime>();
        private readonly List<double> _spreadSamples = new List<double>();
        private DateTime _lastSpreadSampleTime = DateTime.MinValue;
        private DateTime _lastTickTime = DateTime.MinValue;
        private double _peakEquity;
        private bool _drawdownHaltLogged;
        private PendingSignal _pending;
        private readonly Dictionary<long, PendingClose> _closeQueue = new Dictionary<long, PendingClose>();

        private sealed class PendingSignal
        {
            public TradeType TradeType;
            public bool IsBreakout;
            public double AtrAtSignal;
            public DateTime BarOpenTime;
            public DateTime NextAttemptTime;
            public int Attempts;
        }

        private sealed class PendingClose
        {
            public string Reason;
            public DateTime QueuedAt;
            public DateTime NextAttemptTime;
            public int Attempts;
        }

        // ================================================================== Lifecycle
        protected override void OnStart()
        {
            if (TimeFrame != TimeFrame.Daily)
            {
                Print("GoldDailyTrendBot must run on a Daily chart (current: {0}). Stopping.", TimeFrame);
                Stop();
                return;
            }

            _emaFast = Indicators.ExponentialMovingAverage(Bars.ClosePrices, FastEmaPeriod);
            _emaSlow = Indicators.ExponentialMovingAverage(Bars.ClosePrices, SlowEmaPeriod);
            _emaPullback = Indicators.ExponentialMovingAverage(Bars.ClosePrices, PullbackEmaPeriod);
            _rsi = Indicators.RelativeStrengthIndex(Bars.ClosePrices, RsiPeriod);
            _atr = Indicators.AverageTrueRange(AtrPeriod, MovingAverageType.Simple);
            _dms = Indicators.DirectionalMovementSystem(AdxPeriod);

            ParseBlackoutDates();

            if (HasSundayCandles())
            {
                Print("WARNING: Sunday stub candles detected - ATR/Donchian values are distorted. " +
                      "Use a broker/feed whose daily bar closes at 17:00 New York (GMT+2/+3 server).");
                if (StopOnSundayCandle)
                {
                    Stop();
                    return;
                }
            }

            LoadDollarProxy();
            InitPeakEquity();

            Positions.Closed += OnPositionClosed;
            Timer.Start(TimeSpan.FromSeconds(15));

            Print("Started on {0}. PipSize={1} PipValue={2} VolMin={3} VolStep={4}",
                SymbolName, Symbol.PipSize, Symbol.PipValue, Symbol.VolumeInUnitsMin, Symbol.VolumeInUnitsStep);
        }

        protected override void OnStop()
        {
            Positions.Closed -= OnPositionClosed;
            PersistPeakEquity();
        }

        protected override void OnTick()
        {
            try
            {
                if (!IsQuoteValid())
                    return;

                _lastTickTime = Server.Time;
                SampleSpread();
                UpdatePeakEquity();
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
                // New daily bar opened: index Count-2 is the bar that just closed.
                int i = Bars.Count - 2;
                if (i < RequiredHistory())
                    return;

                ManageOpenPositions(i);
                EvaluateSignals(i);
                PersistPeakEquity();
            }
            catch (Exception ex)
            {
                Print("OnBar error: {0}", ex);
            }
        }

        protected override void OnTimer()
        {
            try
            {
                ProcessCloseQueue();
                TryExecutePending();
            }
            catch (Exception ex)
            {
                Print("OnTimer error: {0}", ex);
            }
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            var p = args.Position;
            if (!IsOurs(p))
                return;
            Print("Closed {0} {1} {2:F2} lots | reason={3} | net={4:F2} | swap={5:F2}",
                p.Label, p.TradeType, Symbol.VolumeInUnitsToQuantity(p.VolumeInUnits), args.Reason, p.NetProfit, p.Swap);
        }

        // ================================================================== Signals
        private void EvaluateSignals(int i)
        {
            // Any un-filled signal from the previous bar is stale now.
            _pending = null;

            if (IsDrawdownHalted())
                return;

            // One position at a time keeps exposure and attribution simple.
            if (OurPositions().Any())
                return;

            DateTime tradingDate = TradingDate(Bars.OpenTimes[i + 1]);
            if (_blackout.Contains(tradingDate))
            {
                Print("{0:yyyy-MM-dd} is a blackout date - no new entries.", tradingDate);
                return;
            }
            if (SkipPreWeekendEntries && tradingDate.DayOfWeek == DayOfWeek.Friday)
                return;

            double close = Bars.ClosePrices[i];
            double emaFast = _emaFast.Result[i];
            double emaSlow = _emaSlow.Result[i];
            bool slopeUp = emaSlow > _emaSlow.Result[i - SlopeLookback];
            bool slopeDown = emaSlow < _emaSlow.Result[i - SlopeLookback];
            bool adxOk = !UseAdxFilter || _dms.ADX[i] >= AdxMin;

            bool regimeLong = close > emaSlow && emaFast > emaSlow && slopeUp && adxOk;
            bool regimeShort = close < emaSlow && emaFast < emaSlow && slopeDown && adxOk;

            // Donchian channel of the N bars BEFORE the signal bar (no look-ahead).
            double channelHigh = double.MinValue;
            double channelLow = double.MaxValue;
            for (int j = i - DonchianPeriod; j < i; j++)
            {
                channelHigh = Math.Max(channelHigh, Bars.HighPrices[j]);
                channelLow = Math.Min(channelLow, Bars.LowPrices[j]);
            }

            bool longBreakout = regimeLong && close > channelHigh;
            bool shortBreakout = regimeShort && close < channelLow;

            bool longPullback = UsePullback && regimeLong && !longBreakout &&
                                (_rsi.Result[i] <= RsiOversold || Bars.LowPrices[i] <= _emaPullback.Result[i]);
            bool shortPullback = UsePullback && regimeShort && !shortBreakout &&
                                 (_rsi.Result[i] >= RsiOverbought || Bars.HighPrices[i] >= _emaPullback.Result[i]);

            TradeType? side = null;
            bool isBreakout = false;
            if ((longBreakout || longPullback) && Direction != TradeDirectionMode.ShortOnly)
            {
                side = TradeType.Buy;
                isBreakout = longBreakout;
            }
            else if ((shortBreakout || shortPullback) && Direction != TradeDirectionMode.LongOnly)
            {
                side = TradeType.Sell;
                isBreakout = shortBreakout;
            }

            if (side == null)
                return;

            if (InCooldown(side.Value, i))
            {
                Print("{0} signal skipped: cooldown after a recent losing {0}.", side.Value);
                return;
            }

            double atr = _atr.Result[i];
            if (double.IsNaN(atr) || atr <= 0)
                return;

            _pending = new PendingSignal
            {
                TradeType = side.Value,
                IsBreakout = isBreakout,
                AtrAtSignal = atr,
                BarOpenTime = Bars.OpenTimes[i + 1],
                NextAttemptTime = Bars.OpenTimes[i + 1].AddMinutes(EntryDelayMinutes),
                Attempts = 0
            };

            Print("Signal: {0} {1} | close={2} ATR={3:F2} ADX={4:F1} RSI={5:F1} | executes after {6:HH:mm} UTC",
                side.Value, isBreakout ? "BREAKOUT" : "PULLBACK", close, atr, _dms.ADX[i], _rsi.Result[i],
                _pending.NextAttemptTime);
        }

        // ================================================================== Execution
        private void TryExecutePending()
        {
            if (_pending == null)
                return;

            DateTime now = Server.Time;
            if (now > _pending.BarOpenTime.AddHours(SignalExpiryHours))
            {
                Print("Signal expired without a fill (spread/quote conditions never cleared).");
                _pending = null;
                return;
            }
            if (now < _pending.NextAttemptTime)
                return;

            // Conditions that should be waited out (not counted as attempts).
            if (!IsQuoteValid())
                return;
            if ((now - _lastTickTime).TotalSeconds > StaleQuoteSeconds)
                return;
            if (!IsSpreadAcceptable())
                return;
            if (IsDrawdownHalted() || OurPositions().Any())
            {
                _pending = null;
                return;
            }

            double stopDistance = InitialStopAtr * _pending.AtrAtSignal;
            double stopPips = Math.Round(stopDistance / Symbol.PipSize, 1);
            double volume = CalculateVolume(_pending.TradeType, stopPips);
            if (volume <= 0)
            {
                _pending = null;
                return;
            }

            string label = BotLabel + (_pending.IsBreakout ? BreakoutTag : PullbackTag);
            double basePrice = _pending.TradeType == TradeType.Buy ? Symbol.Ask : Symbol.Bid;

            TradeResult result = ExecuteMarketRangeOrder(_pending.TradeType, SymbolName, volume,
                MaxSlippagePips, basePrice, label, stopPips, null);

            if (result.IsSuccessful && result.Position != null)
            {
                Print("Filled {0} {1} {2:F2} lots @ {3} | SL={4} | stop={5} pips",
                    label, _pending.TradeType, Symbol.VolumeInUnitsToQuantity(volume),
                    result.Position.EntryPrice, result.Position.StopLoss, stopPips);
                EnsureHardStop(result.Position, stopDistance);
                _pending = null;
                return;
            }

            _pending.Attempts++;
            ErrorCode? error = result.Error;
            Print("Order failed (attempt {0}/{1}): {2}", _pending.Attempts, MaxRetries + 1, error);

            if (IsTransient(error) && _pending.Attempts <= MaxRetries)
                _pending.NextAttemptTime = now.AddSeconds(RetrySeconds);
            else
                _pending = null;
        }

        private static bool IsTransient(ErrorCode? error)
        {
            return error == ErrorCode.TechnicalError
                || error == ErrorCode.Disconnected
                || error == ErrorCode.Timeout
                || error == ErrorCode.MarketClosed;
        }

        // A position must never live without a server-side stop.
        private void EnsureHardStop(Position position, double stopDistance)
        {
            if (position.StopLoss.HasValue)
                return;

            double sl = position.TradeType == TradeType.Buy
                ? position.EntryPrice - stopDistance
                : position.EntryPrice + stopDistance;
            TradeResult r = position.ModifyStopLossPrice(Math.Round(sl, Symbol.Digits));
            if (!r.IsSuccessful)
            {
                Print("Could not attach a stop ({0}) - closing position {1} for safety.", r.Error, position.Id);
                ClosePosition(position);
            }
        }

        // ================================================================== Position management
        private void ManageOpenPositions(int i)
        {
            double atrNow = _atr.Result[i];
            if (double.IsNaN(atrNow) || atrNow <= 0)
                return;

            foreach (Position pos in OurPositions().ToArray())
            {
                if (_closeQueue.ContainsKey(pos.Id))
                    continue;

                int entryIdx = EntryBarIndex(pos.EntryTime, i + 1);
                if (entryIdx < 1)
                    continue;

                double initialRiskPips = InitialStopAtr * _atr.Result[entryIdx - 1] / Symbol.PipSize;
                if (double.IsNaN(initialRiskPips) || initialRiskPips <= 0)
                    initialRiskPips = InitialStopAtr * atrNow / Symbol.PipSize;

                // Re-attach a stop if one is missing (e.g. after a manual edit or broker reject).
                EnsureHardStop(pos, initialRiskPips * Symbol.PipSize);
                if (!PositionStillOpen(pos))
                    continue;

                double rMultiple = pos.Pips / initialRiskPips;

                // --- Time stop: pullback trades that go nowhere free up risk budget.
                if (TimeStopBars > 0 && pos.Label.EndsWith(PullbackTag, StringComparison.Ordinal))
                {
                    int barsHeld = i - entryIdx + 1;
                    if (barsHeld >= TimeStopBars && rMultiple < TimeStopMinR)
                    {
                        QueueClose(pos, string.Format("time stop: held {0} bars at {1:F2}R", barsHeld, rMultiple));
                        continue;
                    }
                }

                // --- Swap bleed: negative carry is eating a meaningful slice of the initial risk.
                if (MaxSwapBleedR > 0 && pos.Swap < 0)
                {
                    double initialRiskCash = initialRiskPips * Symbol.PipValue * pos.VolumeInUnits;
                    if (-pos.Swap > MaxSwapBleedR * initialRiskCash && rMultiple < 1.0)
                    {
                        QueueClose(pos, string.Format("swap bleed: swap={0:F2} vs 1R={1:F2} at {2:F2}R",
                            pos.Swap, initialRiskCash, rMultiple));
                        continue;
                    }
                }

                // --- Chandelier trail from the extreme since entry (ratchets only).
                if (pos.TradeType == TradeType.Buy)
                {
                    double highest = double.MinValue;
                    for (int j = entryIdx; j <= i; j++)
                        highest = Math.Max(highest, Bars.HighPrices[j]);
                    double trail = Math.Round(highest - TrailAtr * atrNow, Symbol.Digits);

                    if (trail >= Symbol.Bid)
                    {
                        QueueClose(pos, string.Format("trail {0} above bid {1}", trail, Symbol.Bid));
                    }
                    else if (!pos.StopLoss.HasValue || trail > pos.StopLoss.Value + Symbol.TickSize)
                    {
                        ModifyStop(pos, trail);
                    }
                }
                else
                {
                    double lowest = double.MaxValue;
                    for (int j = entryIdx; j <= i; j++)
                        lowest = Math.Min(lowest, Bars.LowPrices[j]);
                    double trail = Math.Round(lowest + TrailAtr * atrNow, Symbol.Digits);

                    if (trail <= Symbol.Ask)
                    {
                        QueueClose(pos, string.Format("trail {0} below ask {1}", trail, Symbol.Ask));
                    }
                    else if (!pos.StopLoss.HasValue || trail < pos.StopLoss.Value - Symbol.TickSize)
                    {
                        ModifyStop(pos, trail);
                    }
                }
            }
        }

        // Discretionary exits are market orders; send them after the rollover spread spike,
        // like entries. The server-side stop keeps protecting the position while queued.
        private void QueueClose(Position pos, string reason)
        {
            if (_closeQueue.ContainsKey(pos.Id))
                return;
            DateTime now = Server.Time;
            _closeQueue[pos.Id] = new PendingClose
            {
                Reason = reason,
                QueuedAt = now,
                NextAttemptTime = Bars.OpenTimes.LastValue.AddMinutes(EntryDelayMinutes)
            };
            Print("Exit queued for {0}: {1}.", pos.Id, reason);
        }

        private void ProcessCloseQueue()
        {
            if (_closeQueue.Count == 0)
                return;

            DateTime now = Server.Time;
            foreach (long id in _closeQueue.Keys.ToArray())
            {
                PendingClose pc = _closeQueue[id];
                Position pos = Positions.FirstOrDefault(p => p.Id == id);
                if (pos == null)
                {
                    _closeQueue.Remove(id);
                    continue;
                }
                if (now < pc.NextAttemptTime || !IsQuoteValid())
                    continue;

                // Wait for normal spreads, but never longer than the signal-expiry window.
                bool overdue = now > pc.QueuedAt.AddHours(SignalExpiryHours);
                bool fresh = (now - _lastTickTime).TotalSeconds <= StaleQuoteSeconds;
                if (!overdue && (!fresh || !IsSpreadAcceptable()))
                    continue;

                TradeResult r = ClosePosition(pos);
                if (r.IsSuccessful)
                {
                    Print("Exited {0} ({1}).", id, pc.Reason);
                    _closeQueue.Remove(id);
                    continue;
                }

                pc.Attempts++;
                Print("Close failed for {0} (attempt {1}): {2}", id, pc.Attempts, r.Error);
                if (IsTransient(r.Error) && pc.Attempts <= MaxRetries)
                {
                    pc.NextAttemptTime = now.AddSeconds(RetrySeconds);
                }
                else
                {
                    Print("Giving up on discretionary exit for {0}; its server-side stop remains active.", id);
                    _closeQueue.Remove(id);
                }
            }
        }

        private void ModifyStop(Position pos, double newStop)
        {
            TradeResult r = pos.ModifyStopLossPrice(newStop);
            if (!r.IsSuccessful)
                Print("Trail update failed for {0}: {1} (existing stop stays in place).", pos.Id, r.Error);
        }

        // ================================================================== Risk
        private double CalculateVolume(TradeType side, double stopPips)
        {
            double equity = Account.Equity;
            double pipValue = Symbol.PipValue;
            if (equity <= 0 || stopPips <= 0 || double.IsNaN(pipValue) || pipValue <= 0)
            {
                Print("Sizing aborted: equity={0} stopPips={1} pipValue={2}", equity, stopPips, pipValue);
                return 0;
            }

            double riskPct = RiskPercent;

            int streak = CurrentLossStreak();
            if (LossStreakTrigger > 0 && streak >= LossStreakTrigger)
            {
                riskPct *= LossStreakRiskFactor;
                Print("Loss streak {0} - risk reduced to {1:F2}%.", streak, riskPct);
            }

            if (IsDollarAdverse(side))
            {
                riskPct *= DollarAdverseFactor;
                Print("USD proxy trending against {0} - risk reduced to {1:F2}%.", side, riskPct);
            }

            double riskCash = equity * riskPct / 100.0;

            // Portfolio cap: stay within the total open-risk budget.
            double budgetLeft = equity * MaxTotalRiskPercent / 100.0 - OpenRiskCash();
            riskCash = Math.Min(riskCash, budgetLeft);
            if (riskCash <= 0)
            {
                Print("Open-risk budget exhausted - no new entry.");
                return 0;
            }

            // Size off a gap-inflated stop so weekend/news gaps don't blow through the risk target.
            double units = riskCash / (stopPips * GapBufferMultiplier * pipValue);
            units = Math.Min(units, Symbol.QuantityToVolumeInUnits(MaxLots));
            units = Math.Min(units, Symbol.VolumeInUnitsMax);
            units = Symbol.NormalizeVolumeInUnits(units, RoundingMode.Down);

            if (units < Symbol.VolumeInUnitsMin)
            {
                // Never round UP to the minimum: that would exceed the risk target.
                Print("Computed volume below broker minimum ({0} < {1}) - skipping trade.", units, Symbol.VolumeInUnitsMin);
                return 0;
            }
            return units;
        }

        private double OpenRiskCash()
        {
            double total = 0;
            foreach (Position p in OurPositions())
            {
                if (!p.StopLoss.HasValue)
                {
                    // Unknown risk: count it as a full budget so nothing else opens.
                    total += Account.Equity * MaxTotalRiskPercent / 100.0;
                    continue;
                }
                double distance = p.TradeType == TradeType.Buy
                    ? p.EntryPrice - p.StopLoss.Value
                    : p.StopLoss.Value - p.EntryPrice;
                if (distance > 0)
                    total += distance / Symbol.PipSize * Symbol.PipValue * p.VolumeInUnits;
            }
            return total;
        }

        private int CurrentLossStreak()
        {
            int streak = 0;
            foreach (HistoricalTrade t in OurHistory().OrderByDescending(t => t.ClosingTime))
            {
                if (t.NetProfit < 0)
                    streak++;
                else
                    break;
            }
            return streak;
        }

        private bool InCooldown(TradeType side, int i)
        {
            if (CooldownBars <= 0)
                return false;

            HistoricalTrade last = OurHistory()
                .Where(t => t.TradeType == side)
                .OrderByDescending(t => t.ClosingTime)
                .FirstOrDefault();
            if (last == null || last.NetProfit >= 0)
                return false;

            int barsSince = 0;
            for (int j = i; j >= 0 && Bars.OpenTimes[j] > last.ClosingTime; j--)
                barsSince++;
            return barsSince < CooldownBars;
        }

        private bool IsDrawdownHalted()
        {
            if (_peakEquity <= 0)
                return false;

            double dd = (_peakEquity - Account.Equity) / _peakEquity * 100.0;
            if (dd < MaxDrawdownPercent)
                return false;

            if (!_drawdownHaltLogged)
            {
                Print("DRAWDOWN HALT: equity {0:F2} is {1:F1}% below peak {2:F2}. No new entries. " +
                      "Review, then restart with 'Reset Drawdown Halt On Start' = true.",
                      Account.Equity, dd, _peakEquity);
                _drawdownHaltLogged = true;
            }
            return true;
        }

        private void UpdatePeakEquity()
        {
            if (Account.Equity > _peakEquity)
                _peakEquity = Account.Equity;
        }

        private void InitPeakEquity()
        {
            _peakEquity = Account.Equity;
            if (RunningMode != RunningMode.RealTime)
                return;

            try
            {
                if (ResetDrawdownHalt)
                {
                    PersistPeakEquity();
                    return;
                }
                string stored = LocalStorage.GetString(PeakEquityKey);
                double value;
                if (!string.IsNullOrEmpty(stored) &&
                    double.TryParse(stored, NumberStyles.Float, CultureInfo.InvariantCulture, out value) &&
                    value > _peakEquity)
                {
                    _peakEquity = value;
                }
            }
            catch (Exception ex)
            {
                Print("LocalStorage read failed ({0}) - peak equity starts at current equity.", ex.Message);
            }
        }

        private void PersistPeakEquity()
        {
            if (RunningMode != RunningMode.RealTime)
                return;
            try
            {
                LocalStorage.SetString(PeakEquityKey, _peakEquity.ToString(CultureInfo.InvariantCulture));
            }
            catch (Exception ex)
            {
                Print("LocalStorage write failed: {0}", ex.Message);
            }
        }

        // ================================================================== Macro overlay
        private void LoadDollarProxy()
        {
            if (string.IsNullOrWhiteSpace(DollarProxySymbol))
                return;
            try
            {
                _dollarBars = MarketData.GetBars(TimeFrame.Daily, DollarProxySymbol.Trim());
                Print("USD proxy {0} loaded ({1} bars).", DollarProxySymbol, _dollarBars.Count);
            }
            catch (Exception ex)
            {
                _dollarBars = null;
                Print("USD proxy '{0}' unavailable ({1}) - macro overlay disabled.", DollarProxySymbol, ex.Message);
            }
        }

        private bool IsDollarAdverse(TradeType side)
        {
            if (_dollarBars == null || _dollarBars.Count < DollarLookback + 2)
                return false;

            int last = _dollarBars.Count - 2; // last closed bar
            double now = _dollarBars.ClosePrices[last];
            double then = _dollarBars.ClosePrices[last - DollarLookback];
            if (then <= 0 || double.IsNaN(now) || double.IsNaN(then))
                return false;

            double change = now / then - 1.0;
            bool dollarStrengthening = DollarProxyInverted ? change < 0 : change > 0;

            // A strengthening USD is a headwind for gold longs; a weakening USD for shorts.
            return side == TradeType.Buy ? dollarStrengthening : !dollarStrengthening;
        }

        // ================================================================== Quote / spread guards
        private bool IsQuoteValid()
        {
            double bid = Symbol.Bid, ask = Symbol.Ask;
            return !double.IsNaN(bid) && !double.IsNaN(ask) && bid > 0 && ask > 0 && ask >= bid;
        }

        private void SampleSpread()
        {
            if ((Server.Time - _lastSpreadSampleTime).TotalSeconds < 60)
                return;
            _lastSpreadSampleTime = Server.Time;

            _spreadSamples.Add(Symbol.Spread / Symbol.PipSize);
            if (_spreadSamples.Count > MaxSpreadSamples)
                _spreadSamples.RemoveAt(0);
        }

        private bool IsSpreadAcceptable()
        {
            double spreadPips = Symbol.Spread / Symbol.PipSize;
            if (spreadPips > MaxSpreadPips)
                return false;

            if (_spreadSamples.Count < MinSpreadSamples)
                return true;

            var sorted = _spreadSamples.OrderBy(x => x).ToList();
            double median = sorted[sorted.Count / 2];
            return median <= 0 || spreadPips <= median * MaxSpreadMedianMultiple;
        }

        // ================================================================== Helpers
        private IEnumerable<Position> OurPositions()
        {
            return Positions.Where(IsOurs);
        }

        private bool IsOurs(Position p)
        {
            return p.SymbolName == SymbolName && p.Label != null &&
                   (p.Label == BotLabel + BreakoutTag || p.Label == BotLabel + PullbackTag);
        }

        private IEnumerable<HistoricalTrade> OurHistory()
        {
            return History.Where(t => t.SymbolName == SymbolName && t.Label != null &&
                                      (t.Label == BotLabel + BreakoutTag || t.Label == BotLabel + PullbackTag));
        }

        private bool PositionStillOpen(Position pos)
        {
            return Positions.Any(p => p.Id == pos.Id);
        }

        private int EntryBarIndex(DateTime entryTime, int searchFrom)
        {
            for (int j = Math.Min(searchFrom, Bars.Count - 1); j >= 0; j--)
                if (Bars.OpenTimes[j] <= entryTime)
                    return j;
            return -1;
        }

        private int RequiredHistory()
        {
            return Math.Max(SlowEmaPeriod + SlopeLookback, Math.Max(DonchianPeriod, AtrPeriod) + 1) + 5;
        }

        // Daily bars of NY-close brokers open ~21:00-22:00 UTC on the PREVIOUS calendar day.
        // Shifting +12h maps every bar to the calendar day it actually trades.
        private static DateTime TradingDate(DateTime barOpenUtc)
        {
            return barOpenUtc.AddHours(12).Date;
        }

        private void ParseBlackoutDates()
        {
            if (string.IsNullOrWhiteSpace(BlackoutDates))
                return;

            foreach (string raw in BlackoutDates.Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries))
            {
                DateTime d;
                if (DateTime.TryParseExact(raw.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out d))
                    _blackout.Add(d.Date);
                else
                    Print("Ignoring invalid blackout date '{0}' (expected yyyy-MM-dd).", raw);
            }
            Print("{0} blackout date(s) loaded.", _blackout.Count);
        }

        // A Sunday stub bar shows up as two consecutive daily opens only a few hours apart.
        private bool HasSundayCandles()
        {
            int start = Math.Max(1, Bars.Count - 120);
            for (int j = start; j < Bars.Count; j++)
                if ((Bars.OpenTimes[j] - Bars.OpenTimes[j - 1]).TotalHours < 20)
                    return true;
            return false;
        }
    }
}
