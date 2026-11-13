// =====================================================================================
//  BarExporter — dumps the chart's bar history to CSV so the strategy can be researched
//  offline on real data (edge study before any more bot code).
//
//  Use: attach to a GBPUSD M5 chart and press Play (live/demo mode, NOT backtest).
//  It loads history back to "From Date", writes the CSV, prints the path, and stops itself.
//  Output: Documents\cAlgo\Data\<symbol>_<timeframe>.csv  (time in UTC)
//  Columns: timeUtc,open,high,low,close,tickVolume
//  Needs AccessRights.FileSystem (cTrader will ask you to allow it).
// =====================================================================================
using System;
using System.Globalization;
using System.IO;
using System.Text;
using cAlgo.API;

namespace cAlgo.Robots
{
    [Robot(AccessRights = AccessRights.FileSystem, TimeZone = TimeZones.UTC)]
    public class BarExporter : Robot
    {
        [Parameter("From Date (yyyy-MM-dd)", DefaultValue = "2021-01-01", Group = "Export")]
        public string FromDate { get; set; }

        [Parameter("Max History Load Calls", DefaultValue = 2000, MinValue = 1, Group = "Export")]
        public int MaxLoads { get; set; }

        protected override void OnStart()
        {
            try
            {
                DateTime from;
                if (!DateTime.TryParseExact(FromDate, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                        DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out from))
                {
                    Print("Bad From Date '{0}', expected yyyy-MM-dd.", FromDate);
                    Stop();
                    return;
                }

                int calls = 0;
                while (Bars.Count > 0 && Bars.OpenTimes[0] > from && calls < MaxLoads)
                {
                    int added = Bars.LoadMoreHistory();
                    calls++;
                    if (added <= 0) break;   // broker has no older data
                    if (calls % 50 == 0) Print("Loaded back to {0:yyyy-MM-dd} ({1} bars)...", Bars.OpenTimes[0], Bars.Count);
                }

                string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "cAlgo", "Data");
                Directory.CreateDirectory(dir);
                string file = Path.Combine(dir, string.Format("{0}_{1}.csv", SymbolName.Replace('.', '_'), TimeFrame));

                var sb = new StringBuilder();
                sb.AppendLine("timeUtc,open,high,low,close,tickVolume");
                int n = 0;
                for (int i = 0; i < Bars.Count - 1; i++)   // skip the forming bar
                {
                    if (Bars.OpenTimes[i] < from) continue;
                    sb.AppendFormat(CultureInfo.InvariantCulture, "{0:yyyy-MM-dd HH:mm},{1},{2},{3},{4},{5}\n",
                        Bars.OpenTimes[i], Bars.OpenPrices[i], Bars.HighPrices[i], Bars.LowPrices[i], Bars.ClosePrices[i], Bars.TickVolumes[i]);
                    n++;
                }
                File.WriteAllText(file, sb.ToString());
                Print("Exported {0} bars from {1:yyyy-MM-dd} to {2:yyyy-MM-dd} -> {3}",
                    n, Bars.OpenTimes[0] < from ? from : Bars.OpenTimes[0], Bars.OpenTimes[Bars.Count - 2], file);
            }
            catch (Exception ex)
            {
                Print("Export failed: {0}", ex.Message);
            }
            Stop();
        }
    }
}
