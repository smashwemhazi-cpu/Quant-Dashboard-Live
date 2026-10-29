# T1 on Dukascopy XAUUSD ticks — NOT RUN (data source blocked)

**Status (2026-10-29): no results.** The test could not run because the environment can't download the data.

## What happened
Step 1 network check:

```
$ curl -sS -o /dev/null -w "%{http_code}" https://datafeed.dukascopy.com/datafeed/XAUUSD/2026/08/20/10h_ticks.bi5
curl: (56) CONNECT tunnel failed, response 403
000
```

The session's egress proxy refused the CONNECT to `datafeed.dukascopy.com:443` (`connect_rejected`, policy
denial). `dukascopy-node` downloads from the same host, so no tick data could be fetched. Nothing was
downloaded, no bars were built, and T1 was not simulated. **No numbers in this file come from Dukascopy data.**

## What's needed
Under the cloud environment's Network access settings, add **`datafeed.dukascopy.com`** (or `*.dukascopy.com`)
to **Allowed domains**, or pick a broader access level. Steps:
https://code.claude.com/docs/en/cloud-environments#network-access

## Ready to run once access is allowed
`dukas_ticks.py` (this folder) carries out the pre-registered plan. T1's rules and the cost model are unchanged.

```
python dukas_ticks.py fetch <workdir> 2025-01 2026-10   # month by month: ticks -> M1 BID OHLC + median spread (pips), raw CSV deleted
python dukas_ticks.py check <workdir>                   # rows per month, first/last, price range, spread p50/p90 by UTC hour
python dukas_ticks.py run   <workdir>                   # T1 SL60/100 x RR1.5/2.0 at COST spread 0.30, then SL100 at measured 07:00-16:30 median spread
```

Keep `<workdir>` outside the repo (the bar and tick data must not be committed). The tick-to-M1 conversion has
only been checked on a hand-made 3-tick CSV, not yet on real Dukascopy output.

## Reference: the earlier bar-data results (unchanged)
On minute bars (see README): SL100/TP150 had PF 1.10 overall and PF 0.74 in 2026. Until the tick test runs,
treat T1 as **unvalidated**. The 2026 bar-data result is a loss, and nothing here changes that.
