// =====================================================================================
//  BarExporter — prints every closed bar to the cBot LOG so the history can be studied
//  offline. No file access, no internet, works in a normal backtest.
//
//  How to use:
//   1. New cBot -> paste this file -> Build.
//   2. Backtest it on GBPUSD m5, period as long as the broker allows (e.g. 2021 -> today),
//      data "m1 bars" or "tick data", any starting capital. It places no trades.
//   3. When it finishes, save the log (the same way you saved log.txt before) and upload it.
//  Each bar is one line:  B,yyyyMMddHHmm,open,high,low,close,tickVolume   (time = UTC)
//  The first and last lines say which dates the broker actually supplied.
// =====================================================================================
using System;
using System.Globalization;
using cAlgo.API;

namespace cAlgo.Robots
{
    [Robot(AccessRights = AccessRights.None, TimeZone = TimeZones.UTC)]
    public class BarExporter : Robot
    {
        private int _count;
        private DateTime _first = DateTime.MinValue, _last;

        protected override void OnStart()
        {
            Print("EXPORT START symbol={0} timeframe={1} mode={2}", SymbolName, TimeFrame, RunningMode);
            if (!IsBacktesting)
                Print("NOTE: run this exporter as a BACKTEST over the period you want; live mode only prints new bars.");
        }

        protected override void OnBar()
        {
            int i = Bars.Count - 2;   // last CLOSED bar
            if (i < 0) return;
            DateTime t = Bars.OpenTimes[i];
            if (_first == DateTime.MinValue) _first = t;
            _last = t;
            _count++;
            Print(string.Format(CultureInfo.InvariantCulture, "B,{0:yyyyMMddHHmm},{1},{2},{3},{4},{5}",
                t, Bars.OpenPrices[i], Bars.HighPrices[i], Bars.LowPrices[i], Bars.ClosePrices[i], Bars.TickVolumes[i]));
        }

        protected override void OnStop()
        {
            Print("EXPORT END bars={0} first={1:yyyy-MM-dd HH:mm} last={2:yyyy-MM-dd HH:mm}", _count, _first, _last);
        }
    }
}
