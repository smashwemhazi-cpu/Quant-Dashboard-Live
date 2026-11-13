import os, time, math, asyncio
import ccxt.async_support as ccxt
import numpy as np
from decimal import Decimal, ROUND_DOWN

# =============================================================================
# APEX v30.0 — PROP FIRM EDITION (Auto-Cleanse, BB-Width, Dynamic Delta)
# =============================================================================
# Kept as received for the review in bots/kraken/REVIEW.md, with ONE change: the boot-time "auto-cleanse"
# (market-converting EUR/USD/USDT/USDG into USDC) only runs if you authorise it by setting the environment
# variable APEX_AUTHORISE_CONVERSION=YES. DO NOT RUN LIVE. The replacement is bots/kraken/krakenbot/.

SPOT_API_KEY = os.getenv("KRAKEN_SPOT_KEY", "")
SPOT_API_SEC = os.getenv("KRAKEN_SPOT_SEC", "")
FUT_API_KEY  = os.getenv("KRAKEN_FUT_KEY",  "")
FUT_API_SEC  = os.getenv("KRAKEN_FUT_SEC",  "")

spot = ccxt.kraken({'apiKey': SPOT_API_KEY, 'secret': SPOT_API_SEC, 'enableRateLimit': True})
fut = ccxt.krakenfutures({'apiKey': FUT_API_KEY, 'secret': FUT_API_SEC, 'enableRateLimit': True})

# --- INSTITUTIONAL PARAMETERS ---
QUOTE_ASSET         = 'USDC'  
USDC_RESERVE_SPOT   = 250.0       # Hard cash floor
FUTURES_RESERVE_CAP = 100.0       # Strict cap on derivatives margin
BASE_USD_PER_BUY    = 25.0        # Optimized for a $1,500 deployable account

# Replaced ATR with instant Standard Deviation & RSI
BB_PERIOD           = 20
RSI_PERIOD          = 14
RSI_OVERSOLD        = 30          # Halts buying in a crash
EMA_200_LOOKBACK    = 200         # Macro trend detector for Dynamic Hedging

MIN_FUNDING_RATE    = 0.00008     
HEDGE_CHUNK_USD     = 50.0        

TARGET_ASSETS = [
    {'ticker': 'BTC',  'spot': 'BTC/USDC',  'fut': 'PI_XBTUSD', 'prec': 1, 'sz': 5, 'min_usd': 15.0},
    {'ticker': 'SOL',  'spot': 'SOL/USDC',  'fut': 'PI_SOLUSD', 'prec': 2, 'sz': 2, 'min_usd': 15.0},
    {'ticker': 'DOGE', 'spot': 'DOGE/USDC', 'fut': 'PI_XDGUSD', 'prec': 5, 'sz': 0, 'min_usd': 10.0}
]

# FRAGMENTED FIAT TO AUTO-CLEANSE ON BOOT
FIAT_FRAGMENTS = ['EUR', 'USD', 'USDT', 'USDG']

def trunc(n, d=0):
    return float(Decimal(str(n)).quantize(Decimal(f"1.e-{d}"), rounding=ROUND_DOWN))

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    deltas = np.diff(closes)
    seed = deltas[:period]
    up = seed[seed >= 0].sum()/period
    down = -seed[seed < 0].sum()/period
    rs = up/down if down != 0 else 0
    rsi = np.zeros_like(closes)
    rsi[:period] = 100. - 100./(1. + rs)
    for i in range(period, len(closes)):
        delta = deltas[i - 1]
        if delta > 0:
            upval = delta
            downval = 0.
        else:
            upval = 0.
            downval = -delta
        up = (up*(period - 1) + upval)/period
        down = (down*(period - 1) + downval)/period
        rs = up/down if down != 0 else 0
        rsi[i] = 100. - 100./(1. + rs)
    return rsi[-1]

def calc_bb_width(closes, period=20):
    if len(closes) < period: return 0.002
    recent = closes[-period:]
    std = np.std(recent)
    sma = np.mean(recent)
    return (std * 2) / sma if sma else 0.002

def calc_ema(closes, period):
    if len(closes) < period: return closes[-1] if closes else 0.0
    k = 2.0 / (period + 1)
    e = sum(closes[:period]) / period
    for c in closes[period:]: e = (c - e) * k + e
    return e

async def startup_portfolio_cleanse():
    print("\n" + "="*50 + "\n[SYSTEM BOOT] Initiating Portfolio Auto-Cleanse...\n" + "="*50)
    bals = await spot.fetch_balance()
    
    for fiat in FIAT_FRAGMENTS:
        amount = float(bals.get(fiat, {}).get('free', 0.0))
        if amount > 5.0: # Kraken minimum conversion threshold
            pair = f"{QUOTE_ASSET}/{fiat}" if fiat in ['EUR', 'USD'] else f"{fiat}/{QUOTE_ASSET}"
            side = 'buy' if fiat in ['EUR', 'USD'] else 'sell'
            print(f"  [CLEANSE] Detected fragmented {amount:.2f} {fiat}. Sweeping to {QUOTE_ASSET}...")
            try:
                # Market order to consolidate into USDC
                await spot.create_order(pair, 'market', side, amount)
                print(f"      └─> [SUCCESS] {fiat} consolidated.")
            except Exception as e:
                print(f"      └─> [ERR] Could not sweep {fiat}: {e}")
    print("Portfolio consolidation complete. Trading Engine Online.\n")

async def bi_directional_sweeper():
    try:
        fut_bal = await fut.fetch_balance()
        fut_cash = float(fut_bal.get('free', {}).get(QUOTE_ASSET, 0.0))
        
        # If Futures cash exceeds the $100 cap by at least $10, sweep it back to Spot
        if fut_cash > FUTURES_RESERVE_CAP + 10.0:
            sweep_amount = trunc(fut_cash - FUTURES_RESERVE_CAP, 2)
            print(f"   [SWEEPER] Margin Bloat Detected. Routing ${sweep_amount:.2f} {QUOTE_ASSET} back to Spot to compound grid.")
            await spot.private_post_wallettransfer({
                'asset': QUOTE_ASSET, 'from': 'Futures Wallet', 'to': 'Spot Wallet', 'amount': str(sweep_amount)
            })
    except Exception: pass

async def adaptive_grid_cycle():
    bals = await spot.fetch_balance()
    usdc_free = float(bals.get(QUOTE_ASSET, {}).get('free', 0.0))
    deployable_cash = max(0.0, usdc_free - USDC_RESERVE_SPOT)
    print(f"   [LIQUIDITY] Deployable Cash: ${deployable_cash:.2f} | Reserve: ${USDC_RESERVE_SPOT:.2f}")

    for a in TARGET_ASSETS:
        try:
            ob = await spot.fetch_order_book(a['spot'], limit=5)
            ohlcv = await spot.fetch_ohlcv(a['spot'], '15m', limit=250)
            ords = await spot.fetch_open_orders(a['spot'])
            
            px = ob['bids'][0][0]
            coin_bal = float(bals.get(a['ticker'], {}).get('free', 0.0))
            closes = [x[4] for x in ohlcv]
            
            # --- INSTANT ADAPTIVE INDICATORS ---
            ema_200 = calc_ema(closes, EMA_200_LOOKBACK) # Macro Trend
            rsi_15m = calc_rsi(closes, RSI_PERIOD)       # Oversold detector
            bb_width = calc_bb_width(closes, BB_PERIOD)  # Instant Volatility
            
            # Widen spread instantly if Bollinger Bands expand
            spread = max(0.002, bb_width * 0.4)
            target_buy = trunc(px * (1 - spread), a['prec'])
            target_sell = trunc(px * (1 + spread), a['prec'])

            has_buy = False
            for o in ords:
                if o['side'] == 'buy':
                    has_buy = True
                    # Clean up stale orders drifting from BB target
                    if abs(o['price'] - target_buy) / target_buy > 0.0015:
                        await spot.cancel_order(o['id'], a['spot'])
                        has_buy = False
            
            # Circuit Breaker: Halt if RSI < 30 (Crashing)
            trend_safe = rsi_15m > RSI_OVERSOLD
            
            if not has_buy and trend_safe and deployable_cash >= a['min_usd']:
                # Volatility scaling: Buy slightly heavier if BB is wide
                vol_multiplier = min(1.5, max(0.8, bb_width * 50))
                trade_size_usd = min(deployable_cash, BASE_USD_PER_BUY * vol_multiplier)
                buy_vol = trunc(trade_size_usd / target_buy, a['sz'])
                
                if buy_vol * target_buy >= a['min_usd']:
                    await spot.create_order(a['spot'], 'limit', 'buy', buy_vol, target_buy, {'postOnly': True})
                    print(f"      └─> [+] {a['ticker']} GRID BUY: {buy_vol} @ ${target_buy} (Vol-Scale: {vol_multiplier:.2f}x)")
            elif not trend_safe:
                print(f"      └─> [!] {a['ticker']} BUY HALTED: RSI ({rsi_15m:.1f}) indicates falling knife.")

            if coin_bal * px >= a['min_usd'] and not any(o['side'] == 'sell' for o in ords):
                sell_vol = trunc(coin_bal, a['sz'])
                await spot.create_order(a['spot'], 'limit', 'sell', sell_vol, target_sell, {'postOnly': True})

            # --- DYNAMIC DELTA HEDGING ---
            # If price is above 200 EMA, we are in a bull market. Reduce short hedge exposure to 0.5x to let spot bags run.
            macro_trend = "BULL" if px > ema_200 else "BEAR"
            target_leverage = 0.5 if macro_trend == "BULL" else 1.0
            
            # (Note: In a full deployment, this triggers a re-balancing of the Futures size. 
            # For now, it adjusts the leverage parameter for the next Yield Farmer entry).
            os.environ[f"{a['ticker']}_TARGET_LEV"] = str(target_leverage)

        except Exception as e:
            pass

async def main():
    await spot.load_markets()
    await fut.load_markets()
    
    # 1. Cleanse the portfolio on boot -- ONLY with explicit authorisation (no silent EUR/USD -> USDC conversion)
    if os.getenv("APEX_AUTHORISE_CONVERSION", "") == "YES":
        await startup_portfolio_cleanse()
    else:
        print("[SYSTEM BOOT] Auto-cleanse SKIPPED: currency conversion not authorised "
              "(set APEX_AUTHORISE_CONVERSION=YES to allow it).")
    
    cycle = 0
    while True:
        try:
            print(f"\n{'='*62}\n APEX v30.0 (PROP FIRM)  {datetime.now().strftime('%H:%M:%S')}  [#{cycle}]\n{'='*62}")

            await adaptive_grid_cycle()

            if cycle % 12 == 0:
                await bi_directional_sweeper() 

            cycle += 1
            await asyncio.sleep(15)

        except asyncio.CancelledError:
            await spot.close(); await fut.close()
            break
        except Exception:
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
