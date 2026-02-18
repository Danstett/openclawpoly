# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Polymarket FastLoop Trader** — an automated trading bot that trades Polymarket's BTC 5-minute and 15-minute prediction markets using real-time price momentum from Binance. Trades execute directly via the Polymarket CLOB API with real USDC on Polygon.

## Commands

### Trading Engine (Python)

```bash
# Activate venv first
source venv/bin/activate

python fastloop_trader.py                    # Dry run (default, no trades)
python fastloop_trader.py --live             # Execute real trades
python fastloop_trader.py --live --quiet     # Silent except trades/errors
python fastloop_trader.py --positions        # Show open positions
python fastloop_trader.py --config           # Show current config
python fastloop_trader.py --set KEY=VALUE    # Update config.json setting
python fastloop_trader.py --live --loop --interval 60  # Continuous mode
python fastloop_trader.py --live --smart-sizing        # 5% of balance per trade
```

Requires `POLYMARKET_PRIVATE_KEY` env var (Polygon wallet private key).
Optional: `POLYMARKET_WALLET_ADDRESS` (auto-derived from private key if not set).

### Dashboard (Next.js)

```bash
cd dashboard && npm install && npm run dev   # Local dev at localhost:3000
cd dashboard && npm run build                # Production build
```

Requires `POLYMARKET_WALLET_ADDRESS` env var (set in `.env.local` for dashboard).

## Architecture

The project has two independent components:

### 1. Trading Engine — `fastloop_trader.py` (single file)

All trading logic lives in one file, organized into these sections:

- **Configuration**: `CONFIG_SCHEMA` dict defines all settings with defaults, env var names, and types. Priority: `config.json` > env vars > defaults.
- **Daily Budget Tracking**: `daily_spend.json` tracks spend per UTC day.
- **CLOB Client**: Singleton `ClobClient` from `py-clob-client` with private key auth + EIP-712 signing. `_api_request()` for raw HTTP to Gamma/Binance.
- **Slack Notifications**: Optional webhook notifications on trades.
- **Position Selling**: Auto-sells winning positions on the orderbook (replaces on-chain redemption). Tracks via `notified_redeemable.json`.
- **Market Discovery**: Generates expected market slugs by time window, queries Gamma API. Extracts `clobTokenIds` for direct CLOB trading.
- **Momentum & Fair Value**: Fetches Binance klines, calculates momentum %, converts to fair value shift (capped at 25c from 50c baseline, 1.5x multiplier). Fallback to CoinGecko.
- **Trade Execution**: Places orders directly via Polymarket CLOB using `OrderArgs` + `post_order()`. Verifies fill status.
- **Main Strategy** (`run_fast_market_strategy()`): Orchestrates full cycle — config load, momentum fetch, market discovery, selection, validation, execution, notification.
- **CLI**: argparse with `--live`, `--loop`, `--quiet`, `--positions`, `--config`, `--set`, `--smart-sizing`.

Entry point: `main.py` wraps `fastloop_trader.py` with Railway/LOOP env var auto-detection.

### 2. Dashboard — `dashboard/`

Next.js 14 + React 18 + Tailwind CSS. Single page (`app/page.tsx`) showing portfolio, positions, and trade history. API routes in `app/api/` query Polymarket Data API directly. Auto-refreshes every 60 seconds.

## Key Trading Logic

1. Fetch BTC 1-minute candles from Binance (tries multiple endpoints for cloud IP compatibility)
2. Calculate momentum: `(price_now - price_Nmin_ago) / price_Nmin_ago`
3. Exit if momentum < `MIN_MOMENTUM_PCT` (1.5% hardcoded threshold)
4. Convert momentum to fair value: `0.50 +/- min(0.25, abs(momentum) * 1.50)`
5. Discover active markets via Gamma API slug matching (extracts `clobTokenIds`)
6. Select best market: filter by time window, price range, direction alignment, min edge
7. Execute trade via Polymarket CLOB (GTC order, verify fill)

All trades tagged with `source: "sdk:fastloop"`. Fast markets charge 10% fee on winnings.

## External APIs

- **Polymarket CLOB** (`clob.polymarket.com`): Order placement, orderbook, balance via `py-clob-client`
- **Polymarket Data API** (`data-api.polymarket.com`): Positions, trade history (used by dashboard)
- **Gamma API** (`gamma-api.polymarket.com`): Market discovery by slug, `clobTokenIds`
- **Binance** (`api.binance.com` + fallbacks): BTC price klines
- **CoinGecko** (fallback): Price data if Binance fails
- **Slack Webhooks** (optional): Trade notifications

## State Files (gitignored or ephemeral)

- `config.json`: Trading configuration (committed)
- `daily_spend.json`: Today's spend tracker (resets on UTC midnight)
- `notified_redeemable.json`: Tracks auto-sold winning positions

## Conventions

- `py-clob-client` is the only external Python dependency (plus its transitive deps)
- Use `venv/` for Python virtual environment
- Functions prefixed with `_` are internal; public functions form the API
- Print-based logging with emoji indicators (no logging framework)
- `--live` flag required for real trades; dry-run is always the default
- Config updates via `--set KEY=VALUE` validate against `CONFIG_SCHEMA`
