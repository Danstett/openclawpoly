---
name: polymarket-fast-loop
displayName: Polymarket FastLoop Trader
description: Trade Polymarket BTC 5-minute and 15-minute fast markets using CEX price momentum signals via direct CLOB API. Default signal is Binance BTC/USDT klines. Use when user wants to trade sprint/fast markets, automate short-term crypto trading, or use CEX momentum as a Polymarket signal.
metadata: {"clawdbot":{"emoji":"⚡","requires":{"env":["POLYMARKET_PRIVATE_KEY"]},"cron":null,"autostart":false}}
authors:
  - OpenClaw
version: "2.0.0"
published: true
---

# Polymarket FastLoop Trader

Trade Polymarket's 5-minute BTC fast markets using real-time price momentum from Binance.

> **Polymarket only.** All trades execute on Polymarket with real USDC via the CLOB API. Use `--live` for real trades, dry-run is the default.

**How it works:** Every cycle, the script finds the current live BTC fast market, checks BTC price momentum on Binance, and trades if momentum diverges from market odds.

**This is a template.** The default signal (Binance momentum) gets you started. Your agent's reasoning is the edge — layer on sentiment analysis, multi-exchange spreads, news feeds, or custom signals to improve it.

> Fast markets carry Polymarket's 10% fee (`is_paid: true`). Factor this into your edge calculations.

## When to Use This Skill

Use this skill when the user wants to:
- Trade BTC sprint/fast markets (5-minute or 15-minute)
- Automate short-term crypto prediction trading
- Use CEX price momentum as a Polymarket signal
- Monitor sprint market positions

## Setup Flow

When user asks to install or configure this skill:

1. **Wallet setup**
   - Create or use an existing Polygon wallet
   - Fund with USDC on Polygon mainnet
   - Export private key and store as `POLYMARKET_PRIVATE_KEY`
   - Token allowances are handled automatically on first trade

2. **Ask about settings** (or confirm defaults)
   - Asset: BTC, ETH, or SOL (default BTC)
   - Min value edge: Min divergence to trade (default 12%)
   - Max position: Amount per trade (default $5.00)
   - Windows: 5m and/or 15m (default both, 15m preferred)

3. **Set up cron or loop** (user drives scheduling — see "How to Run on a Loop")

## Quick Start

```bash
# Activate virtual environment
source venv/bin/activate

# Set your private key
export POLYMARKET_PRIVATE_KEY="your-hex-private-key"

# Dry run — see what would happen
python fastloop_trader.py

# Go live
python fastloop_trader.py --live

# Live + quiet (for cron/heartbeat loops)
python fastloop_trader.py --live --quiet

# Live + smart sizing (5% of balance per trade)
python fastloop_trader.py --live --smart-sizing --quiet
```

## How to Run on a Loop

The script runs **one cycle** — your bot drives the loop. Set up a cron job or heartbeat:

**Every 5 minutes (one per fast market window):**
```
*/5 * * * * cd /path/to/skill && source venv/bin/activate && python fastloop_trader.py --live --quiet
```

**Built-in loop mode:**
```bash
python fastloop_trader.py --live --loop --interval 60
```

## Configuration

Configure via `config.json`, environment variables, or `--set`:

```bash
# Change min value edge
python fastloop_trader.py --set min_value_edge=0.08

# Trade ETH instead of BTC
python fastloop_trader.py --set asset=ETH

# Multiple settings
python fastloop_trader.py --set min_value_edge=0.10 --set max_position=10
```

### Settings

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `min_value_edge` | 0.12 | `FASTLOOP_VALUE_EDGE` | Min edge from fair value to trigger |
| `max_position` | 5.0 | `FASTLOOP_MAX_POSITION` | Max $ per trade |
| `signal_source` | binance | `FASTLOOP_SIGNAL` | Price feed (binance, coingecko) |
| `lookback_minutes` | 3 | `FASTLOOP_LOOKBACK` | Minutes of price history |
| `min_time_remaining` | 60 | `FASTLOOP_MIN_TIME` | Skip markets with less time left (seconds) |
| `max_time_remaining` | 180 | `FASTLOOP_MAX_TIME` | Max seconds before entry deadline |
| `asset` | BTC | `FASTLOOP_ASSET` | Asset to trade (BTC, ETH, SOL) |
| `daily_budget` | 50.0 | `FASTLOOP_DAILY_BUDGET` | Max total spend per UTC day |

## CLI Options

```bash
python fastloop_trader.py                    # Dry run
python fastloop_trader.py --live             # Real trades
python fastloop_trader.py --live --quiet     # Silent except trades/errors
python fastloop_trader.py --smart-sizing     # Portfolio-based sizing
python fastloop_trader.py --positions        # Show open fast market positions
python fastloop_trader.py --config           # Show current config
python fastloop_trader.py --set KEY=VALUE    # Update config
python fastloop_trader.py --live --loop      # Continuous mode
```

## Signal Logic

Default signal (Binance momentum):

1. Fetch recent one-minute candles from Binance (`BTCUSDT`)
2. Calculate momentum: `(price_now - price_Nmin_ago) / price_Nmin_ago`
3. Convert to fair value: `0.50 +/- min(0.25, abs(momentum) * 1.50)`
4. Trade when:
   - Momentum >= 1.5% (hardcoded threshold)
   - Edge >= `min_value_edge` (momentum-adjusted fair value vs market price)
   - Market price in buy range (`min_price` to `max_price`)

## Source Tagging

All trades are tagged with `source: "sdk:fastloop"`. This means:
- You can track fast market P&L separately
- Other strategies won't interfere with your positions

## Troubleshooting

**"No active fast markets found"**
- Fast markets may not be running (off-hours, weekends)
- Check Polymarket directly for active BTC fast markets

**"No CLOB token IDs for this market"**
- Gamma API didn't return token IDs — market may not be tradeable yet

**"Order not filled — no liquidity"**
- Fast market has thin book, try smaller position size
- Try different price range settings

**"Failed to fetch price data"**
- Binance API may be down or rate limited
- Try `--set signal_source=coingecko` as fallback
