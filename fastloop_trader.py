#!/usr/bin/env python3
"""
Polymarket FastLoop Trading Bot

Trades Polymarket BTC 5-minute fast markets using CEX price momentum.
Default signal: Binance BTCUSDT candles. Agents can customize signal source.
Trades directly via Polymarket CLOB API (no intermediary).

Usage:
    python fast_trader.py              # Dry run (show opportunities, no trades)
    python fast_trader.py --live       # Execute real trades
    python fast_trader.py --positions  # Show current fast market positions
    python fast_trader.py --quiet      # Only output on trades/errors

Requires:
    POLYMARKET_PRIVATE_KEY environment variable (Polygon wallet private key)
"""

import os
import sys
import json
import math
import time
import argparse
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, MarketOrderArgs, OrderType, BalanceAllowanceParams, AssetType, BookParams
from py_clob_client.order_builder.constants import BUY, SELL

# Force line-buffered stdout for non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

# Optional: Trade Journal integration
try:
    from tradejournal import log_trade
    JOURNAL_AVAILABLE = True
except ImportError:
    try:
        from skills.tradejournal import log_trade
        JOURNAL_AVAILABLE = True
    except ImportError:
        JOURNAL_AVAILABLE = False
        def log_trade(*args, **kwargs):
            pass

# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

CONFIG_SCHEMA = {
    "strategy": {"default": "arbitrage", "env": "FASTLOOP_STRATEGY", "type": str,
                 "help": "Strategy type: 'arbitrage' (dual-side), 'momentum', or 'value'"},
    "windows": {"default": ["15m", "5m"], "env": None, "type": list,
                "help": "Market windows to scan (15m preferred, 5m backup)"},
    "min_value_edge": {"default": 0.12, "env": "FASTLOOP_VALUE_EDGE", "type": float,
                       "help": "Min edge from fair value to trigger trade (value strategy)"},
    "max_price": {"default": 0.58, "env": "FASTLOOP_MAX_PRICE", "type": float,
                  "help": "Max price to pay per share (buy low)"},
    "min_price": {"default": 0.35, "env": "FASTLOOP_MIN_PRICE", "type": float,
                  "help": "Min price to consider (avoid extreme cheap)"},
    "max_position": {"default": 5.0, "env": "FASTLOOP_MAX_POSITION", "type": float,
                     "help": "Max $ per trade"},
    "signal_source": {"default": "binance", "env": "FASTLOOP_SIGNAL", "type": str,
                      "help": "Price feed source (binance, coingecko)"},
    "lookback_minutes": {"default": 3, "env": "FASTLOOP_LOOKBACK", "type": int,
                         "help": "Minutes of price history for momentum calc"},
    "min_time_remaining": {"default": 60, "env": "FASTLOOP_MIN_TIME", "type": int,
                           "help": "Min seconds before expiry (late entry)"},
    "max_time_remaining": {"default": 180, "env": "FASTLOOP_MAX_TIME", "type": int,
                           "help": "Max seconds before expiry (late entry window)"},
    "asset": {"default": "BTC", "env": "FASTLOOP_ASSET", "type": str,
              "help": "Asset to trade (BTC only recommended)"},
    "volume_confidence": {"default": False, "env": "FASTLOOP_VOL_CONF", "type": bool,
                          "help": "Require volume confirmation"},
    "daily_budget": {"default": 50.0, "env": "FASTLOOP_DAILY_BUDGET", "type": float,
                     "help": "Max total spend per UTC day"},
    # Arbitrage strategy settings
    "entry_threshold": {"default": 0.45, "env": "FASTLOOP_ENTRY_THRESHOLD", "type": float,
                        "help": "Max ask price to trigger buy on either side (arb strategy)"},
    "max_combined_cost": {"default": 0.97, "env": "FASTLOOP_MAX_COMBINED", "type": float,
                          "help": "Max combined avg cost per matched YES+NO pair (arb strategy)"},
    "arb_trade_size": {"default": 2.0, "env": "FASTLOOP_ARB_TRADE_SIZE", "type": float,
                       "help": "USD per individual arb trade (smaller, more frequent)"},
    "max_unhedged_ratio": {"default": 0.6, "env": "FASTLOOP_MAX_UNHEDGED", "type": float,
                           "help": "Max ratio of unmatched shares to total shares"},
    "max_trades_per_window": {"default": 20, "env": "FASTLOOP_MAX_TRADES_WINDOW", "type": int,
                              "help": "Max trades per market window (arb strategy)"},
    "arb_min_time_remaining": {"default": 120, "env": "FASTLOOP_ARB_MIN_TIME", "type": int,
                               "help": "Min seconds before expiry for arb entry"},
}

TRADE_SOURCE = "sdk:fastloop"
SMART_SIZING_PCT = 0.05  # 5% of balance per trade
MIN_SHARES_PER_ORDER = 5  # Polymarket minimum

# Asset → Binance symbol mapping
ASSET_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

# Asset → Gamma API search patterns
ASSET_PATTERNS = {
    "BTC": ["bitcoin up or down"],
    "ETH": ["ethereum up or down"],
    "SOL": ["solana up or down"],
}


def _load_config(schema, skill_file, config_filename="config.json"):
    """Load config with priority: config.json > env vars > defaults."""
    from pathlib import Path
    config_path = Path(skill_file).parent / config_filename
    file_cfg = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_cfg = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    result = {}
    for key, spec in schema.items():
        if key in file_cfg:
            result[key] = file_cfg[key]
        elif spec.get("env") and os.environ.get(spec["env"]):
            val = os.environ.get(spec["env"])
            type_fn = spec.get("type", str)
            try:
                if type_fn == bool:
                    result[key] = val.lower() in ("true", "1", "yes")
                else:
                    result[key] = type_fn(val)
            except (ValueError, TypeError):
                result[key] = spec.get("default")
        else:
            result[key] = spec.get("default")
    return result


def _get_config_path(skill_file, config_filename="config.json"):
    from pathlib import Path
    return Path(skill_file).parent / config_filename


def _update_config(updates, skill_file, config_filename="config.json"):
    """Update config.json with new values."""
    from pathlib import Path
    config_path = Path(skill_file).parent / config_filename
    existing = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    existing.update(updates)
    with open(config_path, "w") as f:
        json.dump(existing, f, indent=2)
    return existing


# Load config (with support for non-schema fields from config.json)
cfg = _load_config(CONFIG_SCHEMA, __file__)

# Load additional fields directly from config.json
from pathlib import Path
_config_path = Path(__file__).parent / "config.json"
_raw_cfg = {}
if _config_path.exists():
    try:
        with open(_config_path) as f:
            _raw_cfg = json.load(f)
    except:
        pass

STRATEGY = _raw_cfg.get("strategy", cfg.get("strategy", "value"))
WINDOWS = _raw_cfg.get("windows", cfg.get("windows", ["15m", "5m"]))
MIN_VALUE_EDGE = _raw_cfg.get("min_value_edge", cfg.get("min_value_edge", 0.12))
MAX_PRICE = _raw_cfg.get("max_price", cfg.get("max_price", 0.58))
MIN_PRICE = _raw_cfg.get("min_price", cfg.get("min_price", 0.35))
MAX_POSITION_USD = _raw_cfg.get("max_position", cfg.get("max_position", 5.0))
SIGNAL_SOURCE = _raw_cfg.get("signal_source", cfg.get("signal_source", "binance"))
LOOKBACK_MINUTES = _raw_cfg.get("lookback_minutes", cfg.get("lookback_minutes", 3))
MIN_TIME_REMAINING = _raw_cfg.get("min_time_remaining", cfg.get("min_time_remaining", 60))
MAX_TIME_REMAINING = _raw_cfg.get("max_time_remaining", cfg.get("max_time_remaining", 180))
ASSET = _raw_cfg.get("asset", cfg.get("asset", "BTC")).upper()
VOLUME_CONFIDENCE = _raw_cfg.get("volume_confidence", cfg.get("volume_confidence", False))
DAILY_BUDGET = _raw_cfg.get("daily_budget", cfg.get("daily_budget", 50.0))

# Arbitrage strategy constants
ENTRY_THRESHOLD = _raw_cfg.get("entry_threshold", cfg.get("entry_threshold", 0.45))
MAX_COMBINED_COST = _raw_cfg.get("max_combined_cost", cfg.get("max_combined_cost", 0.97))
ARB_TRADE_SIZE = _raw_cfg.get("arb_trade_size", cfg.get("arb_trade_size", 2.0))
MAX_UNHEDGED_RATIO = _raw_cfg.get("max_unhedged_ratio", cfg.get("max_unhedged_ratio", 0.6))
MAX_TRADES_PER_WINDOW = _raw_cfg.get("max_trades_per_window", cfg.get("max_trades_per_window", 20))
ARB_MIN_TIME_REMAINING = _raw_cfg.get("arb_min_time_remaining", cfg.get("arb_min_time_remaining", 120))


# =============================================================================
# Daily Budget Tracking
# =============================================================================

def _get_spend_path(skill_file):
    from pathlib import Path
    return Path(skill_file).parent / "daily_spend.json"


def _load_daily_spend(skill_file):
    """Load today's spend. Resets if date != today (UTC)."""
    spend_path = _get_spend_path(skill_file)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if spend_path.exists():
        try:
            with open(spend_path) as f:
                data = json.load(f)
            if data.get("date") == today:
                return data
        except (json.JSONDecodeError, IOError):
            pass
    return {"date": today, "spent": 0.0, "trades": 0}


def _save_daily_spend(skill_file, spend_data):
    """Save daily spend to file."""
    spend_path = _get_spend_path(skill_file)
    with open(spend_path, "w") as f:
        json.dump(spend_data, f, indent=2)


# =============================================================================
# Window Position Tracking (Arbitrage Strategy)
# =============================================================================

def _get_window_positions_path(skill_file):
    from pathlib import Path
    return Path(skill_file).parent / "window_positions.json"


def load_window_positions(skill_file):
    """Load window position tracking state. Prune expired windows."""
    pos_path = _get_window_positions_path(skill_file)
    positions = {}
    if pos_path.exists():
        try:
            with open(pos_path) as f:
                positions = json.load(f)
        except (json.JSONDecodeError, IOError):
            positions = {}

    # Prune windows that expired > 30 min ago
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=30)
    pruned = {}
    for slug, data in positions.items():
        try:
            end_str = data.get("market_end_time", "")
            if end_str:
                end_time = datetime.fromisoformat(end_str)
                if end_time > cutoff:
                    pruned[slug] = data
        except (KeyError, ValueError):
            pass  # Drop entries with bad data
    return pruned


def save_window_positions(skill_file, positions):
    """Save window position state to disk."""
    pos_path = _get_window_positions_path(skill_file)
    with open(pos_path, "w") as f:
        json.dump(positions, f, indent=2)


def update_window_position(window_positions, slug, side, shares, price, market):
    """Update accumulated position after a successful trade."""
    now_str = datetime.now(timezone.utc).isoformat()

    if slug not in window_positions:
        clob_ids = market.get("clob_token_ids", ["", ""])
        end_time = market.get("end_time")
        end_time_str = end_time.isoformat() if isinstance(end_time, datetime) else str(end_time or "")
        window_positions[slug] = {
            "yes_token_id": clob_ids[0] if len(clob_ids) > 0 else "",
            "no_token_id": clob_ids[1] if len(clob_ids) > 1 else "",
            "yes_shares": 0, "yes_avg_price": 0, "yes_total_cost": 0,
            "no_shares": 0, "no_avg_price": 0, "no_total_cost": 0,
            "trade_count": 0,
            "market_end_time": end_time_str,
            "market_question": market.get("question", ""),
            "condition_id": market.get("condition_id", ""),
            "pending_orders": [],
            "created_at": now_str,
            "last_trade_at": now_str,
        }

    pos = window_positions[slug]
    cost = shares * price

    if side == "yes":
        old_total = pos["yes_shares"] * pos["yes_avg_price"]
        pos["yes_shares"] += shares
        pos["yes_avg_price"] = (old_total + cost) / pos["yes_shares"] if pos["yes_shares"] > 0 else price
        pos["yes_total_cost"] += cost
    else:
        old_total = pos["no_shares"] * pos["no_avg_price"]
        pos["no_shares"] += shares
        pos["no_avg_price"] = (old_total + cost) / pos["no_shares"] if pos["no_shares"] > 0 else price
        pos["no_total_cost"] += cost

    pos["trade_count"] += 1
    pos["last_trade_at"] = now_str
    return pos


# =============================================================================
# API Helpers
# =============================================================================

# CLOB client singleton
_clob_client = None


def get_clob_client():
    """Initialize and return the Polymarket CLOB client (singleton)."""
    global _clob_client
    if _clob_client is not None:
        return _clob_client

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        print("Error: POLYMARKET_PRIVATE_KEY environment variable not set")
        print("Set it to your Polygon wallet private key (hex string)")
        sys.exit(1)

    # Polymarket proxy wallet address (holds the funds)
    funder = os.environ.get("POLYMARKET_WALLET_ADDRESS", "")

    # Detect signature type: if funder is set and differs from the signer,
    # use signature_type=1 (proxy/Magic wallet)
    sig_type = int(os.environ.get("POLYMARKET_SIG_TYPE", "1"))

    kwargs = {
        "host": "https://clob.polymarket.com",
        "key": private_key,
        "chain_id": 137,
        "signature_type": sig_type,
    }
    if funder:
        kwargs["funder"] = funder

    _clob_client = ClobClient(**kwargs)
    # Derive API credentials for authenticated endpoints
    _clob_client.set_api_creds(_clob_client.create_or_derive_api_creds())
    return _clob_client


def get_wallet_address():
    """Return the wallet address from env or derive from private key."""
    addr = os.environ.get("POLYMARKET_WALLET_ADDRESS")
    if addr:
        return addr.lower()
    # Derive from private key
    from eth_account import Account
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if pk:
        return Account.from_key(pk).address.lower()
    print("Error: No wallet address available")
    sys.exit(1)


def _api_request(url, method="GET", data=None, headers=None, timeout=15):
    """Make an HTTP request. Returns parsed JSON or None on error."""
    try:
        req_headers = headers or {}
        if "User-Agent" not in req_headers:
            req_headers["User-Agent"] = "fastloop-polymarket/2.0"
        body = None
        if data:
            body = json.dumps(data).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        req = Request(url, data=body, headers=req_headers, method=method)
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        try:
            error_body = json.loads(e.read().decode("utf-8"))
            return {"error": error_body.get("detail", str(e)), "status_code": e.code}
        except Exception:
            return {"error": str(e), "status_code": e.code}
    except URLError as e:
        return {"error": f"Connection error: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Slack Notifications
# =============================================================================

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def send_slack_notification(message, emoji="🤖"):
    """Send a notification to Slack webhook."""
    if not SLACK_WEBHOOK_URL:
        return  # Slack not configured, silently skip
    
    try:
        payload = {
            "text": f"{emoji} *FastLoop Bot*\n{message}",
            "unfurl_links": False,
            "unfurl_media": False,
        }
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            SLACK_WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urlopen(req, timeout=10) as resp:
            pass  # Success
    except Exception as e:
        print(f"  ⚠️ Slack notification failed: {e}")


def notify_trade(side, shares, price, market_question, pnl_potential=None, suffix=""):
    """Send Slack notification for a new trade."""
    short_market = market_question[:50] if len(market_question) > 50 else market_question
    cost = shares * price
    msg = f"📈 *New Trade*\n"
    msg += f"• Market: {short_market}\n"
    msg += f"• Side: *{side.upper()}*\n"
    msg += f"• Shares: {shares:.1f} @ ${price:.2f}\n"
    msg += f"• Cost: ${cost:.2f}"
    if pnl_potential:
        msg += f"\n• Potential profit: ${pnl_potential:.2f}"
    if suffix:
        msg += f"\n{suffix}"
    send_slack_notification(msg, "📈")


def sell_position(condition_id, side, shares, clob_token_ids=None):
    """Sell a position by placing a SELL order on the CLOB orderbook.

    For winning positions, the price should be near $1.00.
    For losing positions, sell at whatever the market offers.
    """
    client = get_clob_client()

    # If we don't have clob_token_ids, look up from Gamma API
    if not clob_token_ids:
        gamma_url = f"https://gamma-api.polymarket.com/markets?condition_id={condition_id}"
        market_data = _api_request(gamma_url)
        if market_data and isinstance(market_data, list) and len(market_data) > 0:
            raw_ids = market_data[0].get("clobTokenIds", "[]")
            try:
                clob_token_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
            except (json.JSONDecodeError, TypeError):
                return {"error": "Could not parse CLOB token IDs"}
        else:
            return {"error": "Could not find market in Gamma API"}

    if not clob_token_ids or len(clob_token_ids) < 2:
        return {"error": "Invalid CLOB token IDs"}

    token_id = clob_token_ids[0] if side == "yes" else clob_token_ids[1]

    try:
        # Get current best bid price for this token
        try:
            book = client.get_order_book(token_id)
        except Exception as ob_err:
            err_str = str(ob_err)
            if "404" in err_str or "No orderbook" in err_str.lower():
                return {"error": "Market resolved (no orderbook)", "expired": True}
            raise

        best_bid = 0.99  # default for winning positions
        if book and hasattr(book, 'bids') and book.bids and len(book.bids) > 0:
            best_bid = float(book.bids[0].price)

        sell_price = round(max(0.01, best_bid - 0.01), 2)
        sell_size = round(shares, 2)

        if sell_size < 1:
            return {"error": f"Position too small to sell ({sell_size} shares)"}

        order_args = OrderArgs(
            token_id=token_id,
            price=sell_price,
            size=sell_size,
            side=SELL,
        )
        signed = client.create_order(order_args)
        resp = client.post_order(signed, OrderType.GTC)

        if resp and resp.get("success"):
            return {
                "success": True,
                "order_id": resp.get("orderID") or resp.get("orderId"),
                "shares_sold": sell_size,
                "price": sell_price,
            }
        else:
            return {"error": resp.get("errorMsg", "Unknown error") if resp else "No response"}
    except Exception as e:
        return {"error": str(e)}


def auto_sell_positions(redeemable):
    """Sell all winning/redeemable positions on the orderbook."""
    sold = []
    failed = []

    for p in redeemable:
        condition_id = p.get("condition_id") or p.get("market_id")
        if not condition_id:
            continue

        side = (p.get("side") or p.get("redeemable_side") or "yes").lower()
        question = (p.get("question") or "Unknown")[:40]
        shares = p.get("shares", 0) or p.get("shares_yes", 0) or p.get("shares_no", 0)
        value = p.get("current_value", 0)

        if shares <= 0:
            continue

        print(f"  💰 Selling {question}... ({shares:.1f} shares, ~${value:.2f})")
        result = sell_position(condition_id, side, shares)

        if result and result.get("success"):
            price = result.get("price", 0)
            print(f"    ✅ Sold {result['shares_sold']:.1f} shares @ ${price:.2f}")
            sold.append(p)
        elif result and result.get("expired"):
            print(f"    ℹ️  Market resolved/expired — skipping (no orderbook)")
            sold.append(p)  # Mark as processed so we don't retry
        else:
            error = result.get("error", "Unknown error")
            print(f"    ⚠️  Sell failed: {error}")
            failed.append((p, error))

    return sold, failed


def notify_sold(sold, failed):
    """Send Slack notification for auto-sold positions."""
    if not sold and not failed:
        return

    total_value = sum(p.get("current_value", 0) for p in sold)

    if sold:
        msg = f"💰 *Auto-Sold {len(sold)} Position(s)!*\n"
        msg += f"• Total: *~${total_value:.2f}* returned to wallet\n\n"

        for p in sold[:5]:
            question = p.get("question", "Unknown")[:40]
            value = p.get("current_value", 0)
            msg += f"  ✅ {question}... (${value:.2f})\n"

        if len(sold) > 5:
            msg += f"  ... and {len(sold) - 5} more\n"

        send_slack_notification(msg, "💰")

    if failed:
        msg = f"⚠️ *{len(failed)} Sell(s) Failed*\n"
        for p, error in failed[:3]:
            question = p.get("question", "Unknown")[:40]
            msg += f"  • {question}...: {error[:50]}\n"
        msg += "\n_Check positions on polymarket.com_"
        send_slack_notification(msg, "⚠️")


def check_and_sell_winners():
    """Check for redeemable/winning positions, sell them on orderbook, notify."""
    positions = get_positions()
    redeemable = [p for p in positions if p.get("redeemable") == True]

    if not redeemable:
        return

    # Track which positions we've already processed
    notified_path = _get_spend_path(__file__).parent / "notified_redeemable.json"
    notified_ids = set()

    if notified_path.exists():
        try:
            with open(notified_path) as f:
                notified_ids = set(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass

    # Find new redeemable positions we haven't processed yet
    new_redeemable = []
    for p in redeemable:
        pos_id = p.get("condition_id") or p.get("market_id") or p.get("question", "")[:50]
        if pos_id and pos_id not in notified_ids:
            new_redeemable.append(p)
            notified_ids.add(pos_id)

    if new_redeemable:
        total_value = sum(p.get("current_value", 0) for p in new_redeemable)
        print(f"\n💰 Found {len(new_redeemable)} winning position(s) worth ${total_value:.2f}")

        # Sell all positions on the orderbook
        sold, failed = auto_sell_positions(new_redeemable)

        # Notify via Slack
        notify_sold(sold, failed)

        # Save updated processed IDs
        with open(notified_path, "w") as f:
            json.dump(list(notified_ids), f)


# =============================================================================
# Sprint Market Discovery
# =============================================================================

def _generate_market_slugs(asset="BTC", window="5m", count=12):
    """Generate expected market slugs for current and upcoming time windows.
    
    Polymarket uses slugs like: btc-updown-5m-{unix_timestamp}
    where the timestamp is the START time of the window (rounded to 5 or 15 min).
    """
    now = datetime.now(timezone.utc)
    window_minutes = 5 if window == "5m" else 15
    
    # Round current time DOWN to nearest window boundary
    current_minute = now.minute
    rounded_minute = (current_minute // window_minutes) * window_minutes
    base_time = now.replace(minute=rounded_minute, second=0, microsecond=0)
    
    # Generate slugs for past few windows and upcoming windows
    asset_prefix = asset.lower()
    slugs = []
    
    for i in range(-2, count):  # Start from 2 windows ago to catch active ones
        window_start = base_time + timedelta(minutes=i * window_minutes)
        timestamp = int(window_start.timestamp())
        slug = f"{asset_prefix}-updown-{window}-{timestamp}"
        slugs.append((slug, window_start + timedelta(minutes=window_minutes)))  # end_time
    
    return slugs


def discover_fast_market_markets(asset="BTC", window="5m"):
    """Find active fast markets on Polymarket via Gamma API.
    
    The Gamma API doesn't return today's fast markets in general queries,
    so we generate expected slugs and query each directly.
    """
    now = datetime.now(timezone.utc)
    markets = []
    
    # Generate slugs for current and upcoming windows
    slug_candidates = _generate_market_slugs(asset, window, count=12)
    
    print(f"  Checking {len(slug_candidates)} potential market windows...")
    
    for slug, expected_end_time in slug_candidates:
        # Query this specific market by slug
        url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
        result = _api_request(url)
        
        if not result or isinstance(result, dict) or len(result) == 0:
            continue
        
        m = result[0]  # Should be exactly one result
        closed = m.get("closed", False)
        
        if closed:
            continue
        
        # Get end time from API
        end_time = None
        end_date_str = m.get("endDate") or m.get("end_date")
        if end_date_str:
            try:
                end_time = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except:
                end_time = expected_end_time
        else:
            end_time = expected_end_time
        
        remaining = (end_time - now).total_seconds() if end_time else None
        
        if remaining and remaining > 0:
            print(f"  📍 Found: {m.get('question', '')[:50]}... expires in {remaining:.0f}s")
            
            # Parse clobTokenIds (needed for direct CLOB trading)
            clob_token_ids_raw = m.get("clobTokenIds", "[]")
            try:
                clob_token_ids = json.loads(clob_token_ids_raw) if isinstance(clob_token_ids_raw, str) else (clob_token_ids_raw or [])
            except (json.JSONDecodeError, TypeError):
                clob_token_ids = []

            markets.append({
                "question": m.get("question", ""),
                "slug": slug,
                "condition_id": m.get("conditionId", ""),
                "clob_token_ids": clob_token_ids,
                "end_time": end_time,
                "outcomes": m.get("outcomes", []),
                "outcome_prices": m.get("outcomePrices", "[]"),
                "fee_rate_bps": int(m.get("fee_rate_bps") or m.get("feeRateBps") or 0),
            })
    
    return markets


def _parse_fast_market_end_time(question):
    """Parse end time from fast market question.
    e.g., 'Bitcoin Up or Down - February 15, 5:30AM-5:35AM ET' → datetime
    """
    import re
    # Match pattern: "Month Day, StartTime-EndTime ET"
    pattern = r'(\w+ \d+),.*?-\s*(\d{1,2}:\d{2}(?:AM|PM))\s*ET'
    match = re.search(pattern, question)
    if not match:
        return None
    try:
        date_str = match.group(1)
        time_str = match.group(2)
        year = datetime.now(timezone.utc).year
        dt_str = f"{date_str} {year} {time_str}"
        # Parse as ET (UTC-5)
        dt = datetime.strptime(dt_str, "%B %d %Y %I:%M%p")
        # Convert ET to UTC (+5 hours)
        dt = dt.replace(tzinfo=timezone.utc) + timedelta(hours=5)
        return dt
    except Exception:
        return None


def discover_all_windows(asset="BTC", windows=None):
    """Discover markets across multiple windows (15m and 5m).
    
    Returns markets sorted by preference (15m first, then by time remaining).
    """
    if windows is None:
        windows = WINDOWS
    
    all_markets = []
    for window in windows:
        print(f"\n  Scanning {window} markets...")
        markets = discover_fast_market_markets(asset, window)
        for m in markets:
            m["window"] = window  # Tag with window type
        all_markets.extend(markets)
    
    return all_markets


def calculate_momentum_fair_value(momentum):
    """Calculate fair value for YES/NO based on CEX momentum.
    
    Instead of flat 50¢, shift fair value based on how much BTC has moved.
    A clear directional move means the momentum side is worth more than 50¢.
    
    Scaling: 1.5x multiplier so that a 0.1% BTC move → 15¢ fair value shift.
    This reflects that even small BTC moves strongly predict the 5m/15m outcome.
    
    Returns: (yes_fair, no_fair, momentum_strength)
    """
    if not momentum or momentum.get("direction") == "neutral":
        return 0.50, 0.50, 0.0
    
    momentum_pct = abs(momentum["momentum_pct"])
    direction = momentum["direction"]
    
    # Aggressive scaling: small BTC moves have big predictive power for short windows
    # 0.02% → 3¢ shift, 0.05% → 7.5¢, 0.1% → 15¢, 0.2% → 25¢ (cap)
    fair_value_shift = min(0.25, momentum_pct * 1.50)
    
    if direction == "up":
        yes_fair = 0.50 + fair_value_shift
        no_fair = 0.50 - fair_value_shift
    else:
        yes_fair = 0.50 - fair_value_shift
        no_fair = 0.50 + fair_value_shift
    
    return yes_fair, no_fair, momentum_pct


# Minimum CEX momentum to consider a trade (skip noise)
# BTC typically moves 0.01-0.05% per 3 minutes; 0.04% was too high (bot was idle ~90%)
MIN_MOMENTUM_PCT = 0.015


def find_best_fast_market(markets, momentum=None, verbose=True):
    """Pick the best fast_market to trade using momentum-informed fair value.
    
    Strategy: Use CEX price momentum to estimate which side will win,
    then find markets where that side is still underpriced.
    Trade WITH momentum (if BTC is going up, buy YES while it's still cheap).
    
    Returns: (best_market, filter_stats) where filter_stats shows why markets were filtered
    """
    now = datetime.now(timezone.utc)
    candidates = []
    
    # Calculate momentum-based fair values
    yes_fair, no_fair, momentum_strength = calculate_momentum_fair_value(momentum)
    momentum_direction = momentum.get("direction", "neutral") if momentum else "neutral"
    
    # Track why markets are filtered
    stats = {
        "total": len(markets),
        "filtered_time_early": 0,
        "filtered_time_late": 0,
        "filtered_price_range": 0,
        "filtered_low_edge": 0,
        "filtered_no_momentum": 0,
        "in_window": 0,
        "candidates": 0,
    }
    
    for m in markets:
        end_time = m.get("end_time")
        if not end_time:
            continue
        remaining = (end_time - now).total_seconds()
        
        # Time filter
        if remaining > MAX_TIME_REMAINING:
            stats["filtered_time_early"] += 1
            continue
        if remaining < MIN_TIME_REMAINING:
            stats["filtered_time_late"] += 1
            continue
        
        stats["in_window"] += 1
        
        # Parse market prices
        try:
            prices = json.loads(m.get("outcome_prices", "[]"))
            yes_price = float(prices[0]) if prices else 0.5
            no_price = float(prices[1]) if len(prices) > 1 else (1 - yes_price)
        except Exception:
            yes_price = 0.5
            no_price = 0.5
        
        # Momentum-informed edge calculation
        # Edge = how much the market underprices the momentum side
        yes_edge = yes_fair - yes_price
        no_edge = no_fair - no_price
        
        # ONLY buy the side momentum favors — never go counter-momentum
        if momentum_direction == "up":
            # Momentum says UP → only consider YES
            if yes_edge > 0 and yes_price >= MIN_PRICE and yes_price <= MAX_PRICE:
                best_side = "yes"
                best_price = yes_price
                best_edge = yes_edge
            else:
                stats["filtered_price_range"] += 1
                continue
        elif momentum_direction == "down":
            # Momentum says DOWN → only consider NO
            if no_edge > 0 and no_price >= MIN_PRICE and no_price <= MAX_PRICE:
                best_side = "no"
                best_price = no_price
                best_edge = no_edge
            else:
                stats["filtered_price_range"] += 1
                continue
        else:
            stats["filtered_no_momentum"] += 1
            continue
        
        # Only consider if edge is above threshold
        if best_edge < MIN_VALUE_EDGE:
            stats["filtered_low_edge"] += 1
            if verbose:
                fair = yes_fair if best_side == "yes" else no_fair
                print(f"    ↳ {m.get('question', '')[:40]}... {best_side.upper()}=${best_price:.2f} fair=${fair:.2f} edge={best_edge:.0%} (need {MIN_VALUE_EDGE:.0%})")
            continue
        
        # Score: prefer 15m over 5m, then by edge size, then by time remaining (earlier = better)
        window_bonus = 100 if m.get("window") == "15m" else 0
        time_bonus = remaining / MAX_TIME_REMAINING * 20  # Prefer earlier entry
        score = window_bonus + (best_edge * 100) + time_bonus
        
        m["_best_side"] = best_side
        m["_best_price"] = best_price
        m["_best_edge"] = best_edge
        m["_yes_fair"] = yes_fair
        m["_no_fair"] = no_fair
        candidates.append((score, remaining, m))
        stats["candidates"] += 1
    
    if not candidates:
        return None, stats
    
    # Sort by score (highest first), then by most time remaining (earlier entry)
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    return candidates[0][2], stats


# =============================================================================
# CEX Price Signal
# =============================================================================

def get_binance_momentum(symbol="BTCUSDT", lookback_minutes=5):
    """Get price momentum from Binance public API.
    Returns: {momentum_pct, direction, price_now, price_then, avg_volume, candles}
    """
    # Try multiple Binance endpoints (some are blocked from cloud IPs)
    endpoints = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
        "https://data-api.binance.vision",
    ]
    
    result = None
    for base_url in endpoints:
        url = f"{base_url}/api/v3/klines?symbol={symbol}&interval=1m&limit={lookback_minutes}"
        result = _api_request(url)
        if result and not isinstance(result, dict):
            break  # Success - got list of klines
        # Log which endpoint failed
        error_msg = result.get("error", "Unknown error") if isinstance(result, dict) else "No response"
        print(f"    (Binance {base_url} failed: {error_msg})")
    
    if not result or isinstance(result, dict):
        return None

    try:
        # Kline format: [open_time, open, high, low, close, volume, ...]
        candles = result
        if len(candles) < 2:
            return None

        price_then = float(candles[0][1])   # open of oldest candle
        price_now = float(candles[-1][4])    # close of newest candle
        momentum_pct = ((price_now - price_then) / price_then) * 100
        direction = "up" if momentum_pct > 0 else "down"

        volumes = [float(c[5]) for c in candles]
        avg_volume = sum(volumes) / len(volumes)
        latest_volume = volumes[-1]

        # Volume ratio: latest vs average (>1 = above average activity)
        volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 1.0

        return {
            "momentum_pct": momentum_pct,
            "direction": direction,
            "price_now": price_now,
            "price_then": price_then,
            "avg_volume": avg_volume,
            "latest_volume": latest_volume,
            "volume_ratio": volume_ratio,
            "candles": len(candles),
        }
    except (IndexError, ValueError, KeyError):
        return None


def get_coingecko_momentum(asset="bitcoin", lookback_minutes=5):
    """Fallback: get price from CoinGecko (less accurate, ~1-2 min lag)."""
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={asset}&vs_currencies=usd"
    result = _api_request(url)
    if not result or isinstance(result, dict) and result.get("error"):
        return None
    price_now = result.get(asset, {}).get("usd")
    if not price_now:
        return None
    # CoinGecko doesn't give candle data on free tier, so just return current price
    # Agent would need to track history across calls for momentum
    return {
        "momentum_pct": 0,  # Can't calculate without history
        "direction": "neutral",
        "price_now": price_now,
        "price_then": price_now,
        "avg_volume": 0,
        "latest_volume": 0,
        "volume_ratio": 1.0,
        "candles": 0,
    }


COINGECKO_ASSETS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}


def get_momentum(asset="BTC", source="binance", lookback=5):
    """Get price momentum from configured source."""
    if source == "binance":
        symbol = ASSET_SYMBOLS.get(asset, "BTCUSDT")
        return get_binance_momentum(symbol, lookback)
    elif source == "coingecko":
        cg_id = COINGECKO_ASSETS.get(asset, "bitcoin")
        return get_coingecko_momentum(cg_id, lookback)
    else:
        return None


# =============================================================================
# Arbitrage Strategy — Orderbook Analysis & Decision Logic
# =============================================================================

def get_orderbook_prices(client, clob_token_ids):
    """Fetch best ask/bid for both YES and NO tokens in a single batch call.

    Returns dict with yes/no best ask, bid, and depth, or None on error.
    """
    if not clob_token_ids or len(clob_token_ids) < 2:
        return None

    yes_token_id = clob_token_ids[0]
    no_token_id = clob_token_ids[1]

    try:
        books = client.get_order_books([
            BookParams(token_id=yes_token_id),
            BookParams(token_id=no_token_id),
        ])

        if not books or len(books) < 2:
            return None

        yes_book, no_book = books[0], books[1]
        result = {"yes_token_id": yes_token_id, "no_token_id": no_token_id}

        # YES side
        if yes_book.asks and len(yes_book.asks) > 0:
            result["yes_best_ask"] = float(yes_book.asks[0].price)
            result["yes_ask_size"] = float(yes_book.asks[0].size)
        else:
            result["yes_best_ask"] = None
            result["yes_ask_size"] = 0

        if yes_book.bids and len(yes_book.bids) > 0:
            result["yes_best_bid"] = float(yes_book.bids[0].price)
        else:
            result["yes_best_bid"] = None

        # NO side
        if no_book.asks and len(no_book.asks) > 0:
            result["no_best_ask"] = float(no_book.asks[0].price)
            result["no_ask_size"] = float(no_book.asks[0].size)
        else:
            result["no_best_ask"] = None
            result["no_ask_size"] = 0

        if no_book.bids and len(no_book.bids) > 0:
            result["no_best_bid"] = float(no_book.bids[0].price)
        else:
            result["no_best_bid"] = None

        return result

    except Exception:
        return None


def calculate_combined_cost(window_pos):
    """Calculate combined cost per matched pair and profit metrics.

    A 'matched pair' = min(yes_shares, no_shares). These are guaranteed to
    resolve to $1.00 regardless of outcome.
    """
    yes_shares = window_pos.get("yes_shares", 0)
    no_shares = window_pos.get("no_shares", 0)
    yes_avg = window_pos.get("yes_avg_price", 0)
    no_avg = window_pos.get("no_avg_price", 0)

    matched_pairs = min(yes_shares, no_shares)
    unmatched_yes = yes_shares - matched_pairs
    unmatched_no = no_shares - matched_pairs
    total_shares = yes_shares + no_shares

    if matched_pairs > 0:
        combined_cost_per_pair = yes_avg + no_avg
        guaranteed_profit_per_pair = 1.0 - combined_cost_per_pair
    else:
        combined_cost_per_pair = 0
        guaranteed_profit_per_pair = 0

    unhedged_ratio = (unmatched_yes + unmatched_no) / total_shares if total_shares > 0 else 0

    return {
        "matched_pairs": matched_pairs,
        "unmatched_yes": unmatched_yes,
        "unmatched_no": unmatched_no,
        "combined_cost_per_pair": combined_cost_per_pair,
        "guaranteed_profit_per_pair": guaranteed_profit_per_pair,
        "total_guaranteed_profit": guaranteed_profit_per_pair * matched_pairs,
        "total_invested": window_pos.get("yes_total_cost", 0) + window_pos.get("no_total_cost", 0),
        "unhedged_ratio": unhedged_ratio,
    }


def should_buy_side(side, ask_price, window_pos, momentum=None):
    """Decide whether to buy the given side at the given ask price.

    Four gates: price threshold, trade count, combined cost projection,
    and unhedged ratio. Returns (should_buy, reason, priority_score).
    """
    # Gate 1: Price must be below entry threshold
    if ask_price > ENTRY_THRESHOLD:
        return False, f"ask ${ask_price:.2f} > threshold ${ENTRY_THRESHOLD:.2f}", 0

    # Gate 2: Trade count limit per window
    trade_count = window_pos.get("trade_count", 0)
    if trade_count >= MAX_TRADES_PER_WINDOW:
        return False, f"hit max trades ({MAX_TRADES_PER_WINDOW})", 0

    # Gate 3: Combined cost projection
    yes_shares = window_pos.get("yes_shares", 0)
    no_shares = window_pos.get("no_shares", 0)
    yes_avg = window_pos.get("yes_avg_price", 0)
    no_avg = window_pos.get("no_avg_price", 0)

    new_shares = ARB_TRADE_SIZE / ask_price if ask_price > 0 else 0

    if side == "yes":
        new_yes_shares = yes_shares + new_shares
        new_yes_avg = ((yes_avg * yes_shares) + (ask_price * new_shares)) / new_yes_shares if new_yes_shares > 0 else ask_price
        projected_combined = new_yes_avg + no_avg
        proj_unmatched = abs(new_yes_shares - no_shares)
        proj_total = new_yes_shares + no_shares
    else:
        new_no_shares = no_shares + new_shares
        new_no_avg = ((no_avg * no_shares) + (ask_price * new_shares)) / new_no_shares if new_no_shares > 0 else ask_price
        projected_combined = yes_avg + new_no_avg
        proj_unmatched = abs(yes_shares - new_no_shares)
        proj_total = yes_shares + new_no_shares

    # Only enforce combined cost when both sides have shares
    other_has_shares = (no_shares > 0 if side == "yes" else yes_shares > 0)
    if other_has_shares and projected_combined > MAX_COMBINED_COST:
        return False, f"combined ${projected_combined:.3f} > max ${MAX_COMBINED_COST:.2f}", 0

    # Gate 4: Unhedged ratio limit
    # Skip this gate when no existing position — must allow initial trades on either side
    total_existing = yes_shares + no_shares
    if total_existing > 0 and proj_total > 0:
        proj_unhedged_ratio = proj_unmatched / proj_total
        if proj_unhedged_ratio > MAX_UNHEDGED_RATIO:
            return False, f"unhedged {proj_unhedged_ratio:.0%} > max {MAX_UNHEDGED_RATIO:.0%}", 0

    # Priority scoring (higher = more attractive)
    price_score = (ENTRY_THRESHOLD - ask_price) / ENTRY_THRESHOLD * 100

    # Bonus for balancing (buying the underweight side)
    balance_score = 0
    if side == "yes" and no_shares > yes_shares:
        balance_score = 20
    elif side == "no" and yes_shares > no_shares:
        balance_score = 20

    # Momentum tiebreaker (small influence)
    momentum_score = 0
    if momentum and momentum.get("direction") != "neutral":
        mom_pct = abs(momentum.get("momentum_pct", 0))
        if side == "yes" and momentum["direction"] == "up":
            momentum_score = min(10, mom_pct * 200)
        elif side == "no" and momentum["direction"] == "down":
            momentum_score = min(10, mom_pct * 200)

    priority = price_score + balance_score + momentum_score
    return True, "ok", priority


def find_arbitrage_opportunities(markets, client, window_positions, momentum=None, verbose=True):
    """Scan all active markets for dual-side buy opportunities.

    Uses Gamma API outcome_prices for price discovery (NOT orderbook asks,
    which are $0.99 on thin fast markets). Places limit orders at Gamma price.

    Returns ranked list of opportunities sorted by priority (highest first).
    """
    now = datetime.now(timezone.utc)
    opportunities = []

    for m in markets:
        end_time = m.get("end_time")
        if not end_time:
            continue
        remaining = (end_time - now).total_seconds()

        if remaining < ARB_MIN_TIME_REMAINING:
            continue

        clob_token_ids = m.get("clob_token_ids", [])
        if not clob_token_ids or len(clob_token_ids) < 2:
            continue

        slug = m.get("slug", "")
        window_pos = window_positions.get(slug, {})

        # Use Gamma API outcome_prices (reflects actual market pricing)
        # NOT orderbook asks (which sit at $0.99 on thin fast markets)
        try:
            prices = json.loads(m.get("outcome_prices", "[]"))
            yes_price = float(prices[0]) if prices else None
            no_price = float(prices[1]) if len(prices) > 1 else None
        except (json.JSONDecodeError, IndexError, ValueError):
            yes_price = None
            no_price = None

        if yes_price is None and no_price is None:
            if verbose:
                print(f"    skip {slug}: no outcome prices available")
            continue

        if verbose:
            yes_str = f"${yes_price:.3f}" if yes_price else "n/a"
            no_str = f"${no_price:.3f}" if no_price else "n/a"
            print(f"    {slug}: YES={yes_str} NO={no_str} ({remaining:.0f}s left)")

        # Evaluate YES side
        if yes_price is not None:
            can_buy, reason, priority = should_buy_side("yes", yes_price, window_pos, momentum)
            if can_buy:
                opportunities.append({
                    "market": m, "side": "yes", "price": yes_price,
                    "priority": priority, "reason": reason,
                    "window_pos": window_pos,
                    "remaining_seconds": remaining,
                })
            elif verbose and yes_price <= ENTRY_THRESHOLD:
                print(f"      YES skip: {reason}")

        # Evaluate NO side
        if no_price is not None:
            can_buy, reason, priority = should_buy_side("no", no_price, window_pos, momentum)
            if can_buy:
                opportunities.append({
                    "market": m, "side": "no", "price": no_price,
                    "priority": priority, "reason": reason,
                    "window_pos": window_pos,
                    "remaining_seconds": remaining,
                })
            elif verbose and no_price <= ENTRY_THRESHOLD:
                print(f"      NO skip: {reason}")

    opportunities.sort(key=lambda x: -x["priority"])
    return opportunities


# =============================================================================
# Import & Trade
# =============================================================================

def get_portfolio():
    """Get portfolio summary: USDC balance.

    Tries CLOB API first, falls back to on-chain RPC query.
    """
    # Try CLOB API balance first
    try:
        client = get_clob_client()
        bal = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        balance_raw = float(bal.get("balance", 0)) if isinstance(bal, dict) else 0
        # CLOB returns balance in raw USDC units (6 decimals)
        balance = balance_raw / 1e6
        if balance > 0:
            return {"balance_usdc": balance}
    except Exception:
        pass

    # Fallback: query USDC.e balance on-chain via RPC
    try:
        address = get_wallet_address()
        usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        data = "0x70a08231" + address[2:].lower().zfill(64)
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "eth_call",
            "params": [{"to": usdc_contract, "data": data}, "latest"], "id": 1
        }).encode()
        rpc_url = os.environ.get("POLYGON_RPC_URL", "https://1rpc.io/matic")
        req = Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
        resp = json.loads(urlopen(req, timeout=10).read())
        if "result" in resp:
            balance = int(resp["result"], 16) / 1e6
            return {"balance_usdc": balance}
    except Exception as e:
        print(f"  ⚠️  Could not fetch balance: {e}")

    return {"balance_usdc": 0}


def get_positions():
    """Get current positions from Polymarket Data API.

    Returns list normalized to match the expected format for
    backward compatibility with position-checking logic.
    """
    address = get_wallet_address()
    url = f"https://data-api.polymarket.com/positions?user={address}&sizeThreshold=0.1&limit=500"
    raw_positions = _api_request(url)

    if not raw_positions or (isinstance(raw_positions, dict) and raw_positions.get("error")):
        return []

    if not isinstance(raw_positions, list):
        return []

    normalized = []
    for p in raw_positions:
        outcome = (p.get("outcome", "") or "").lower()
        size = float(p.get("size", 0) or 0)

        pos = {
            "question": p.get("title", ""),
            "slug": p.get("eventSlug", "") or p.get("slug", ""),
            "condition_id": p.get("conditionId", ""),
            "market_id": p.get("conditionId", ""),
            "side": outcome,
            "shares": size,
            "shares_yes": size if outcome == "yes" else 0,
            "shares_no": size if outcome == "no" else 0,
            "avg_cost": float(p.get("avgPrice", 0) or 0),
            "cost_basis": float(p.get("initialValue", 0) or 0),
            "current_value": float(p.get("currentValue", 0) or 0),
            "pnl": float(p.get("cashPnl", 0) or 0),
            "current_price": float(p.get("curPrice", 0) or 0) if p.get("curPrice") else None,
            "resolves_at": p.get("endDate", ""),
            "redeemable": p.get("redeemable", False),
            "redeemable_side": outcome if p.get("redeemable") else None,
        }
        normalized.append(pos)

    return normalized


def execute_trade(clob_token_ids, side, amount, price):
    """Execute a trade on Polymarket CLOB.

    Args:
        clob_token_ids: [yes_token_id, no_token_id] from Gamma API
        side: "yes" or "no"
        amount: USD amount to spend
        price: price per share (0.00-1.00)

    Returns: dict with success/error info
    """
    client = get_clob_client()

    # Select the correct token_id based on side
    token_id = clob_token_ids[0] if side == "yes" else clob_token_ids[1]

    # Calculate share count from USD amount
    size = math.floor((amount / price) * 100) / 100  # round down to 2 decimals

    if size < MIN_SHARES_PER_ORDER:
        return {"error": f"Size {size} below minimum {MIN_SHARES_PER_ORDER} shares"}

    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=round(size, 2),
            side=BUY,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)

        if resp and resp.get("success"):
            order_id = resp.get("orderID") or resp.get("orderId")

            # Brief wait then verify order was filled
            time.sleep(3)
            try:
                order_status = client.get_order(order_id)
                status = order_status.get("status", "") if order_status else ""
                size_matched = float(order_status.get("size_matched", 0) or 0) if order_status else 0

                if status == "MATCHED":
                    return {"success": True, "order_id": order_id, "shares_bought": size_matched or size}
                elif status == "LIVE":
                    # Partially filled or still open
                    if size_matched > 0:
                        client.cancel(order_id)
                        return {"success": True, "order_id": order_id, "shares_bought": size_matched}
                    else:
                        client.cancel(order_id)
                        return {"error": "Order not filled — no liquidity at this price"}
                else:
                    # Order accepted but status unclear — assume success
                    return {"success": True, "order_id": order_id, "shares_bought": size}
            except Exception:
                # Can't verify — assume order was accepted
                return {"success": True, "order_id": order_id, "shares_bought": size}
        else:
            error_msg = resp.get("errorMsg", "Unknown CLOB error") if resp else "No response"
            return {"error": error_msg}
    except Exception as e:
        return {"error": str(e)}


def place_limit_order(clob_token_ids, side, amount, price):
    """Place a GTC limit buy order that rests on the book until filled.

    Unlike execute_trade(), this does NOT cancel unfilled orders.
    Returns order info for pending order tracking.
    """
    client = get_clob_client()
    token_id = clob_token_ids[0] if side == "yes" else clob_token_ids[1]
    size = math.floor((amount / price) * 100) / 100

    if size < MIN_SHARES_PER_ORDER:
        return {"error": f"Size {size} below minimum {MIN_SHARES_PER_ORDER} shares"}

    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=round(size, 2),
            side=BUY,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)

        if resp and resp.get("success"):
            order_id = resp.get("orderID") or resp.get("orderId")

            # Brief wait then check initial fill status
            time.sleep(5)
            try:
                order_status = client.get_order(order_id)
                status = order_status.get("status", "") if order_status else ""
                size_matched = float(order_status.get("size_matched", 0) or 0) if order_status else 0

                if status == "MATCHED":
                    return {"success": True, "order_id": order_id,
                            "shares_bought": size_matched or size, "status": "MATCHED"}
                elif status == "LIVE":
                    # Order is resting — return as pending
                    return {"success": True, "order_id": order_id,
                            "shares_bought": size_matched, "size_requested": size,
                            "status": "LIVE", "pending": True}
                else:
                    return {"success": True, "order_id": order_id,
                            "shares_bought": size, "status": status}
            except Exception:
                return {"success": True, "order_id": order_id,
                        "shares_bought": 0, "size_requested": size,
                        "status": "UNKNOWN", "pending": True}
        else:
            error_msg = resp.get("errorMsg", "Unknown CLOB error") if resp else "No response"
            return {"error": error_msg}
    except Exception as e:
        return {"error": str(e)}


def check_pending_orders(window_positions, verbose=True):
    """Check status of pending limit orders from previous cycles.

    Updates window positions with any new fills. Returns (filled, canceled) counts.
    """
    client = get_clob_client()
    filled_count = 0
    canceled_count = 0

    for slug, wp in window_positions.items():
        pending = wp.get("pending_orders", [])
        if not pending:
            continue

        still_pending = []
        for order in pending:
            order_id = order.get("order_id")
            if not order_id:
                continue

            try:
                status_resp = client.get_order(order_id)
                status = status_resp.get("status", "") if status_resp else ""
                size_matched = float(status_resp.get("size_matched", 0) or 0) if status_resp else 0

                if status == "MATCHED":
                    side = order.get("side", "yes")
                    price = order.get("price", 0)
                    already_counted = order.get("matched_counted", 0)
                    new_shares = (size_matched or order.get("size", 0)) - already_counted

                    if new_shares > 0:
                        cost = new_shares * price
                        if side == "yes":
                            old_total = wp["yes_shares"] * wp["yes_avg_price"]
                            wp["yes_shares"] += new_shares
                            wp["yes_avg_price"] = (old_total + cost) / wp["yes_shares"] if wp["yes_shares"] > 0 else price
                            wp["yes_total_cost"] += cost
                        else:
                            old_total = wp["no_shares"] * wp["no_avg_price"]
                            wp["no_shares"] += new_shares
                            wp["no_avg_price"] = (old_total + cost) / wp["no_shares"] if wp["no_shares"] > 0 else price
                            wp["no_total_cost"] += cost
                        wp["trade_count"] += 1
                        if verbose:
                            print(f"    ✅ Filled: {new_shares:.1f} {side.upper()} @ ${price:.3f} ({slug})")
                    filled_count += 1

                elif status == "LIVE":
                    already_counted = order.get("matched_counted", 0)
                    new_matched = size_matched - already_counted
                    if new_matched > 0:
                        side = order.get("side", "yes")
                        price = order.get("price", 0)
                        cost = new_matched * price
                        if side == "yes":
                            old_total = wp["yes_shares"] * wp["yes_avg_price"]
                            wp["yes_shares"] += new_matched
                            wp["yes_avg_price"] = (old_total + cost) / wp["yes_shares"] if wp["yes_shares"] > 0 else price
                            wp["yes_total_cost"] += cost
                        else:
                            old_total = wp["no_shares"] * wp["no_avg_price"]
                            wp["no_shares"] += new_matched
                            wp["no_avg_price"] = (old_total + cost) / wp["no_shares"] if wp["no_shares"] > 0 else price
                            wp["no_total_cost"] += cost
                        order["matched_counted"] = size_matched
                        if verbose:
                            print(f"    📊 Partial fill: +{new_matched:.1f} {order.get('side', '?').upper()} @ ${price:.3f}")
                    still_pending.append(order)

                else:
                    # CANCELLED or other terminal status
                    canceled_count += 1
                    if verbose:
                        print(f"    ⏹️  Order {order_id[:12]}... {status}")

            except Exception:
                still_pending.append(order)

        wp["pending_orders"] = still_pending

    return filled_count, canceled_count


def calculate_position_size(max_size, smart_sizing=False):
    """Calculate position size, optionally based on portfolio."""
    if not smart_sizing:
        return max_size
    portfolio = get_portfolio()
    if not portfolio or portfolio.get("error"):
        return max_size
    balance = portfolio.get("balance_usdc", 0)
    if balance <= 0:
        return max_size
    smart_size = balance * SMART_SIZING_PCT
    return min(smart_size, max_size)


# =============================================================================
# Arbitrage Strategy — Main Entry Point
# =============================================================================

def run_arbitrage_strategy(dry_run=True, positions_only=False, show_config=False,
                           smart_sizing=False, quiet=False):
    """Run one cycle of the dual-side arbitrage (probability compression) strategy.

    Instead of predicting BTC direction, buy both YES and NO at different times
    when each becomes cheap. Keep combined cost under $1.00 for guaranteed profit.
    """
    def log(msg, force=False):
        if not quiet or force:
            print(msg)

    log("⚡ Polymarket FastLoop Trading Bot (ARBITRAGE STRATEGY)")
    log("=" * 50)

    if dry_run:
        log("\n  [DRY RUN] No trades will be executed. Use --live to enable trading.")

    log(f"\n⚙️  Configuration:")
    log(f"  Strategy:           ARBITRAGE (dual-side probability compression)")
    log(f"  Asset:              {ASSET}")
    log(f"  Windows:            {', '.join(WINDOWS)}")
    log(f"  Entry threshold:    ${ENTRY_THRESHOLD:.2f} (buy when Gamma price drops below)")
    log(f"  Max combined cost:  ${MAX_COMBINED_COST:.2f} (per matched YES+NO pair)")
    log(f"  Trade size:         ${ARB_TRADE_SIZE:.2f} per trade")
    log(f"  Max unhedged ratio: {MAX_UNHEDGED_RATIO:.0%}")
    log(f"  Max trades/window:  {MAX_TRADES_PER_WINDOW}")
    log(f"  Min time remaining: {ARB_MIN_TIME_REMAINING}s")
    daily_spend = _load_daily_spend(__file__)
    log(f"  Daily budget:       ${DAILY_BUDGET:.2f} (${daily_spend['spent']:.2f} spent, {daily_spend['trades']} trades)")

    if show_config:
        config_path = _get_config_path(__file__)
        log(f"\n  Config file: {config_path}")
        log(f"\n  To change settings:")
        log(f'    python fastloop_trader.py --set entry_threshold=0.40')
        log(f'    python fastloop_trader.py --set arb_trade_size=3.0')
        return

    # Initialize CLOB client
    client = get_clob_client()

    # Check for winning positions and sell them
    if not dry_run:
        check_and_sell_winners()

    # Show positions if requested
    if positions_only:
        log("\n📊 Arbitrage Window Positions:")
        window_positions = load_window_positions(__file__)
        if not window_positions:
            log("  No active window positions")
        else:
            for slug, wp in window_positions.items():
                metrics = calculate_combined_cost(wp)
                log(f"\n  {slug}:")
                log(f"    YES: {wp['yes_shares']:.1f} shares @ avg ${wp['yes_avg_price']:.3f} (${wp['yes_total_cost']:.2f})")
                log(f"    NO:  {wp['no_shares']:.1f} shares @ avg ${wp['no_avg_price']:.3f} (${wp['no_total_cost']:.2f})")
                log(f"    Matched pairs: {metrics['matched_pairs']:.1f} | Combined: ${metrics['combined_cost_per_pair']:.3f}")
                log(f"    Guaranteed profit: ${metrics['total_guaranteed_profit']:.2f} | Trades: {wp['trade_count']}")

        # Also show Data API positions
        log("\n📊 All Sprint Positions:")
        positions = get_positions()
        fast_positions = [p for p in positions if "up or down" in (p.get("question", "") or "").lower()]
        if not fast_positions:
            log("  No open fast market positions")
        else:
            for pos in fast_positions:
                log(f"  • {pos.get('question', 'Unknown')[:60]}")
                log(f"    YES: {pos.get('shares_yes', 0):.1f} | NO: {pos.get('shares_no', 0):.1f} | P&L: ${pos.get('pnl', 0):.2f}")
        return

    # Load window positions state
    window_positions = load_window_positions(__file__)

    # Check pending orders from previous cycles
    pending_total = sum(len(wp.get("pending_orders", [])) for wp in window_positions.values())
    if pending_total > 0:
        log(f"\n📋 Checking {pending_total} pending order(s)...")
        filled, canceled = check_pending_orders(window_positions, verbose=not quiet)
        if filled > 0 or canceled > 0:
            log(f"  Pending orders: {filled} filled, {canceled} canceled")
            save_window_positions(__file__, window_positions)

    # Show current accumulated positions
    if window_positions:
        log(f"\n📦 Active window positions: {len(window_positions)}")
        for slug, wp in window_positions.items():
            metrics = calculate_combined_cost(wp)
            pending_count = len(wp.get("pending_orders", []))
            pending_str = f" pending={pending_count}" if pending_count > 0 else ""
            log(f"  {slug}: YES={wp['yes_shares']:.0f}@${wp['yes_avg_price']:.2f} "
                f"NO={wp['no_shares']:.0f}@${wp['no_avg_price']:.2f} "
                f"pairs={metrics['matched_pairs']:.0f} profit=${metrics['total_guaranteed_profit']:.2f}{pending_str}")

    # Step 1: Fetch momentum (optional tiebreaker)
    log(f"\n📈 Fetching {ASSET} momentum (tiebreaker only)...")
    momentum = get_momentum(ASSET, SIGNAL_SOURCE, LOOKBACK_MINUTES)
    if momentum:
        log(f"  {ASSET}: ${momentum['price_now']:,.2f} ({momentum['momentum_pct']:+.3f}% {momentum['direction']})")
    else:
        log(f"  Could not fetch momentum (continuing without tiebreaker)")

    # Step 2: Discover active markets
    log(f"\n🔍 Discovering {ASSET} fast markets ({', '.join(WINDOWS)})...")
    markets = discover_all_windows(ASSET, WINDOWS)
    log(f"\n  Found {len(markets)} active markets")

    if not markets:
        log("  No active fast markets found")
        if not quiet:
            print("📊 Summary: No markets available")
        save_window_positions(__file__, window_positions)
        return

    # Step 3: Scan for entry opportunities using Gamma prices
    log(f"\n🎯 Scanning Gamma prices for arbitrage opportunities...")
    opportunities = find_arbitrage_opportunities(
        markets, client, window_positions, momentum, verbose=not quiet
    )

    log(f"\n  Found {len(opportunities)} buyable opportunities")

    if not opportunities:
        if not quiet:
            print("📊 Summary: No arb opportunities (prices above threshold or limits hit)")
        save_window_positions(__file__, window_positions)
        return

    # Step 4: Execute the best opportunity (one trade per cycle)
    best = opportunities[0]
    m = best["market"]
    side = best["side"]
    price = best["price"]
    slug = m.get("slug", "")

    log(f"\n🎯 Best opportunity:", force=True)
    log(f"  Market:    {m['question'][:60]}", force=True)
    log(f"  Side:      {side.upper()} @ ${price:.3f}", force=True)
    log(f"  Priority:  {best['priority']:.1f}", force=True)
    log(f"  Remaining: {best['remaining_seconds']:.0f}s", force=True)

    # Show accumulated position context
    wp = best["window_pos"]
    if wp and (wp.get("yes_shares", 0) > 0 or wp.get("no_shares", 0) > 0):
        metrics = calculate_combined_cost(wp)
        log(f"  Accumulated: YES={wp.get('yes_shares', 0):.0f} NO={wp.get('no_shares', 0):.0f} "
            f"pairs={metrics['matched_pairs']:.0f} combined=${metrics['combined_cost_per_pair']:.3f}", force=True)

    # Daily budget check
    remaining_budget = DAILY_BUDGET - daily_spend["spent"]
    trade_amount = min(ARB_TRADE_SIZE, remaining_budget)
    if trade_amount < 0.50:
        log(f"  Budget exhausted (${daily_spend['spent']:.2f}/${DAILY_BUDGET:.2f})", force=True)
        save_window_positions(__file__, window_positions)
        return

    # Minimum order size check
    desired_shares = trade_amount / price if price > 0 else 0
    if desired_shares < MIN_SHARES_PER_ORDER:
        log(f"  Trade too small: {desired_shares:.1f} shares < min {MIN_SHARES_PER_ORDER}")
        save_window_positions(__file__, window_positions)
        return

    clob_token_ids = m.get("clob_token_ids", [])
    if not clob_token_ids or len(clob_token_ids) < 2:
        log(f"  No CLOB token IDs for this market", force=True)
        save_window_positions(__file__, window_positions)
        return

    if dry_run:
        log(f"  [DRY RUN] Would place limit buy {side.upper()} ${trade_amount:.2f} (~{desired_shares:.1f} shares) @ ${price:.3f}", force=True)
        # Still track in window_positions for dry-run visibility
        update_window_position(window_positions, slug, side, desired_shares, price, m)
    else:
        log(f"  Placing limit buy {side.upper()} ${trade_amount:.2f} @ ${price:.3f}...", force=True)
        result = place_limit_order(clob_token_ids, side, trade_amount, price)

        if result and result.get("success"):
            order_id = result.get("order_id", "")
            shares_filled = result.get("shares_bought", 0)
            order_status = result.get("status", "UNKNOWN")

            if order_status == "MATCHED":
                # Fully filled immediately
                log(f"  ✅ Filled: {shares_filled:.1f} {side.upper()} shares @ ${price:.3f}", force=True)
                update_window_position(window_positions, slug, side, shares_filled, price, m)
            elif result.get("pending"):
                # Order resting on book — track as pending
                # Ensure window position entry exists for pending tracking
                if slug not in window_positions:
                    update_window_position(window_positions, slug, side, 0, price, m)
                if "pending_orders" not in window_positions[slug]:
                    window_positions[slug]["pending_orders"] = []
                window_positions[slug]["pending_orders"].append({
                    "order_id": order_id,
                    "side": side,
                    "price": price,
                    "size": result.get("size_requested", desired_shares),
                    "matched_counted": shares_filled,
                    "placed_at": datetime.now(timezone.utc).isoformat(),
                })
                if shares_filled > 0:
                    log(f"  📊 Partial fill: {shares_filled:.1f} shares, rest pending ({order_id[:12]}...)", force=True)
                    update_window_position(window_positions, slug, side, shares_filled, price, m)
                else:
                    log(f"  📋 Order resting on book ({order_id[:12]}...) — will check next cycle", force=True)
            else:
                # Unknown status — assume filled
                shares_filled = shares_filled or desired_shares
                log(f"  ✅ Order accepted: {shares_filled:.1f} {side.upper()} shares @ ${price:.3f}", force=True)
                update_window_position(window_positions, slug, side, shares_filled, price, m)

            # Update daily spend
            daily_spend["spent"] += trade_amount
            daily_spend["trades"] += 1
            _save_daily_spend(__file__, daily_spend)

            # Calculate updated metrics
            updated_metrics = calculate_combined_cost(window_positions[slug])

            # Slack notification with arb context
            status_str = "FILLED" if order_status == "MATCHED" else "PENDING"
            suffix = (f"• Status: {status_str}\n"
                      f"• Arb: pairs={updated_metrics['matched_pairs']:.0f}, "
                      f"combined=${updated_metrics['combined_cost_per_pair']:.3f}, "
                      f"profit=${updated_metrics['total_guaranteed_profit']:.2f}")
            notify_trade(side, shares_filled or desired_shares, price, m["question"],
                         updated_metrics["total_guaranteed_profit"], suffix=suffix)

            # Trade journal
            if JOURNAL_AVAILABLE:
                log_trade(
                    trade_id=order_id,
                    source=TRADE_SOURCE,
                    thesis=f"ARB: {side.upper()} @ ${price:.3f}, combined=${updated_metrics['combined_cost_per_pair']:.3f}",
                    confidence=0.85,
                    asset=ASSET,
                    momentum_pct=round(momentum["momentum_pct"], 3) if momentum else 0,
                    volume_ratio=round(momentum["volume_ratio"], 2) if momentum else 1,
                    signal_source="arbitrage",
                )
        else:
            error = result.get("error", "Unknown") if result else "No response"
            log(f"  ❌ Order failed: {error}", force=True)
            send_slack_notification(f"❌ *Arb Order Failed*\n• Market: {m['question'][:40]}...\n• Error: {error}", "❌")

    # Save updated window positions
    save_window_positions(__file__, window_positions)

    # Summary
    if not quiet:
        print(f"\n📊 Summary:")
        print(f"  Strategy: ARBITRAGE | {side.upper()} @ ${price:.3f} | Window: {slug}")
        if window_positions.get(slug):
            metrics = calculate_combined_cost(window_positions[slug])
            print(f"  Pairs: {metrics['matched_pairs']:.0f} | Combined: ${metrics['combined_cost_per_pair']:.3f} | Profit: ${metrics['total_guaranteed_profit']:.2f}")


# =============================================================================
# Main Strategy Logic (Momentum — legacy)
# =============================================================================

def run_fast_market_strategy(dry_run=True, positions_only=False, show_config=False,
                        smart_sizing=False, quiet=False):
    """Run one cycle of the fast_market trading strategy."""

    def log(msg, force=False):
        """Print unless quiet mode is on. force=True always prints."""
        if not quiet or force:
            print(msg)

    log("⚡ Polymarket FastLoop Trading Bot (MOMENTUM STRATEGY)")
    log("=" * 50)

    if dry_run:
        log("\n  [DRY RUN] No trades will be executed. Use --live to enable trading.")

    log(f"\n⚙️  Configuration:")
    log(f"  Strategy:         MOMENTUM (CEX-informed directional)")
    log(f"  Asset:            {ASSET}")
    log(f"  Windows:          {', '.join(WINDOWS)} (15m preferred)")
    log(f"  Min edge:         {MIN_VALUE_EDGE:.0%} (momentum-adjusted fair value)")
    log(f"  Price range:      ${MIN_PRICE:.2f} - ${MAX_PRICE:.2f} (buy zone)")
    log(f"  Max position:     ${MAX_POSITION_USD:.2f}")
    log(f"  Entry window:     {MIN_TIME_REMAINING}s - {MAX_TIME_REMAINING}s before expiry")
    log(f"  Signal source:    {SIGNAL_SOURCE}")
    daily_spend = _load_daily_spend(__file__)
    log(f"  Daily budget:     ${DAILY_BUDGET:.2f} (${daily_spend['spent']:.2f} spent today, {daily_spend['trades']} trades)")

    if show_config:
        config_path = _get_config_path(__file__)
        log(f"\n  Config file: {config_path}")
        log(f"\n  To change settings:")
        log(f'    python fast_trader.py --set entry_threshold=0.08')
        log(f'    python fast_trader.py --set asset=ETH')
        log(f'    Or edit config.json directly')
        return

    # Initialize CLOB client (validates private key)
    get_clob_client()

    # Check for winning positions and sell them (only once per position)
    if not dry_run:
        check_and_sell_winners()

    # Show positions if requested
    if positions_only:
        log("\n📊 Sprint Positions:")
        positions = get_positions()
        fast_market_positions = [p for p in positions if "up or down" in (p.get("question", "") or "").lower()]
        if not fast_market_positions:
            log("  No open fast market positions")
        else:
            for pos in fast_market_positions:
                log(f"  • {pos.get('question', 'Unknown')[:60]}")
                log(f"    YES: {pos.get('shares_yes', 0):.1f} | NO: {pos.get('shares_no', 0):.1f} | P&L: ${pos.get('pnl', 0):.2f}")
        return

    # Show portfolio if smart sizing
    if smart_sizing:
        log("\n💰 Portfolio:")
        portfolio = get_portfolio()
        if portfolio and not portfolio.get("error"):
            log(f"  Balance: ${portfolio.get('balance_usdc', 0):.2f}")

    # Step 1: Get CEX momentum FIRST — this drives our directional signal
    log(f"\n📈 Fetching {ASSET} momentum ({SIGNAL_SOURCE}, {LOOKBACK_MINUTES}m lookback)...")
    momentum = get_momentum(ASSET, SIGNAL_SOURCE, LOOKBACK_MINUTES)

    if momentum:
        log(f"  Price: ${momentum['price_now']:,.2f}")
        log(f"  Recent move: {momentum['momentum_pct']:+.3f}%")
        log(f"  Direction: {momentum['direction'].upper()}")
        log(f"  Volume ratio: {momentum['volume_ratio']:.1f}x avg")

        momentum_pct = abs(momentum["momentum_pct"])
        if momentum_pct < MIN_MOMENTUM_PCT:
            log(f"  ⏸️  Momentum {momentum_pct:.3f}% < {MIN_MOMENTUM_PCT}% threshold — market is flat, skip")
            if not quiet:
                print(f"📊 Summary: No trade (insufficient momentum: {momentum_pct:.3f}%)")
            return
        
        # Show fair value estimate
        yes_fair, no_fair, _ = calculate_momentum_fair_value(momentum)
        log(f"  Fair values: YES=${yes_fair:.2f} NO=${no_fair:.2f} (momentum-adjusted)")
    else:
        log("  ⚠️ Could not fetch CEX price — cannot determine direction, skip")
        if not quiet:
            print("📊 Summary: No trade (CEX data unavailable)")
        return

    # Step 2: Discover fast markets across all windows
    log(f"\n🔍 Discovering {ASSET} fast markets ({', '.join(WINDOWS)})...")
    markets = discover_all_windows(ASSET, WINDOWS)
    log(f"\n  Found {len(markets)} total active markets")

    if not markets:
        log("  No active fast markets found")
        if not quiet:
            print("📊 Summary: No markets available")
        return

    # Step 3: Find best market to trade (using momentum-informed fair value)
    log(f"\n🎯 Finding momentum opportunities...")
    best, stats = find_best_fast_market(markets, momentum=momentum, verbose=not quiet)
    
    # Show filter stats
    log(f"  Markets in time window ({MIN_TIME_REMAINING}s-{MAX_TIME_REMAINING}s): {stats['in_window']}/{stats['total']}")
    if stats['in_window'] > 0:
        log(f"  Filtered by price range: {stats['filtered_price_range']}")
        log(f"  Filtered by low edge (<{MIN_VALUE_EDGE:.0%}): {stats['filtered_low_edge']}")
        log(f"  Tradeable candidates: {stats['candidates']}")
    
    if not best:
        # Show why no markets qualify
        now = datetime.now(timezone.utc)
        market_times = []
        for m in markets:
            end_time = m.get("end_time")
            if end_time:
                remaining = (end_time - now).total_seconds()
                market_times.append(remaining)
        if market_times:
            soonest = min(market_times)
            log(f"  No markets in trading window ({MIN_TIME_REMAINING}s - {MAX_TIME_REMAINING}s)")
            log(f"  Soonest market expires in: {soonest:.0f}s ({soonest/60:.1f} min)")
        else:
            log(f"  No fast_markets with valid expiry times")
        if not quiet:
            print("📊 Summary: Waiting for market to enter trading window")
        return

    end_time = best.get("end_time")
    remaining = (end_time - datetime.now(timezone.utc)).total_seconds() if end_time else 0
    window_type = best.get("window", "5m")
    
    log(f"\n🎯 Selected: {best['question']}")
    log(f"  Window:     {window_type}")
    log(f"  Expires in: {remaining:.0f}s ({remaining/60:.1f} min)")

    # Get pre-calculated analysis from find_best_fast_market
    side = best.get("_best_side", "yes")
    price = best.get("_best_price", 0.5)
    value_edge = best.get("_best_edge", 0)
    yes_fair = best.get("_yes_fair", 0.5)
    no_fair = best.get("_no_fair", 0.5)
    
    # Parse market odds for display
    try:
        prices = json.loads(best.get("outcome_prices", "[]"))
        market_yes_price = float(prices[0]) if prices else 0.5
        market_no_price = float(prices[1]) if len(prices) > 1 else (1 - market_yes_price)
    except (json.JSONDecodeError, IndexError, ValueError):
        market_yes_price = 0.5
        market_no_price = 0.5
    
    log(f"  YES price:  ${market_yes_price:.3f} (fair: ${yes_fair:.3f})")
    log(f"  NO price:   ${market_no_price:.3f} (fair: ${no_fair:.3f})")
    log(f"  Best side:  {side.upper()} @ ${price:.3f}")
    log(f"  Edge:       {value_edge:.1%} (momentum-adjusted)")

    # Fee info (fast markets charge 10% on winnings)
    fee_rate_bps = best.get("fee_rate_bps", 0)
    fee_rate = fee_rate_bps / 10000
    if fee_rate > 0:
        log(f"  Fee rate:   {fee_rate:.0%} (Polymarket fast market fee)")

    # Step 4: Decision checks
    log(f"\n🧠 Momentum Strategy Analysis...")
    log(f"  {ASSET} momentum: {momentum['momentum_pct']:+.3f}% → favors {'YES' if momentum['direction'] == 'up' else 'NO'}")
    log(f"  Buying {side.upper()} @ ${price:.2f} (fair=${yes_fair if side == 'yes' else no_fair:.2f})")
    
    # Alignment check
    momentum_side = "yes" if momentum["direction"] == "up" else "no"
    if side == momentum_side:
        log(f"  ✓ Aligned with momentum — buying the direction CEX is moving")
    else:
        log(f"  ⚠️  Counter-momentum trade (momentum favors {momentum_side.upper()}, buying {side.upper()})")
    
    if value_edge < MIN_VALUE_EDGE:
        log(f"  ⏸️  Edge {value_edge:.1%} < minimum {MIN_VALUE_EDGE:.1%} — skip")
        if not quiet:
            print(f"📊 Summary: No trade (insufficient edge)")
        return
    
    # Fee-aware EV check
    if fee_rate > 0:
        win_profit = (1 - price) * (1 - fee_rate)
        breakeven = price / (win_profit + price)
        log(f"  Breakeven: {breakeven:.1%} win rate needed (fee-adjusted)")
        
        fair_value = yes_fair if side == "yes" else no_fair
        implied_win_rate = fair_value
        if implied_win_rate < breakeven + 0.02:
            log(f"  ⏸️  Implied win rate {implied_win_rate:.1%} < breakeven {breakeven:.1%} + 2% buffer — skip")
            if not quiet:
                print(f"📊 Summary: No trade (fees eat the edge)")
            return
    
    trade_rationale = f"MOMENTUM: {side.upper()} at ${price:.2f}, {ASSET} {momentum['momentum_pct']:+.3f}%, edge {value_edge:.0%}"

    # We have a value signal!
    position_size = calculate_position_size(MAX_POSITION_USD, smart_sizing)
    
    # Price already validated in find_best_fast_market, but double-check
    if price > MAX_PRICE or price < MIN_PRICE:
        log(f"  ⏸️  Price ${price:.2f} outside range ${MIN_PRICE:.2f}-${MAX_PRICE:.2f} — skip")
        if not quiet:
            print(f"📊 Summary: No trade (price too high: ${price:.2f})")
        return

    # Check for existing position on this market (avoid doubling down or contradicting)
    positions = get_positions()
    market_question_lower = best["question"].lower()
    for pos in positions:
        pos_question = (pos.get("question", "") or "").lower()
        if pos_question and market_question_lower in pos_question or pos_question in market_question_lower:
            existing_shares = pos.get("shares_yes", 0) + pos.get("shares_no", 0)
            if existing_shares > 0:
                log(f"  ⏸️  Already have position on this market ({existing_shares:.1f} shares) — skip")
                if not quiet:
                    print(f"📊 Summary: No trade (already have position)")
                return

    # Daily budget check
    remaining_budget = DAILY_BUDGET - daily_spend["spent"]
    if remaining_budget <= 0:
        log(f"  ⏸️  Daily budget exhausted (${daily_spend['spent']:.2f}/${DAILY_BUDGET:.2f} spent) — skip")
        if not quiet:
            print(f"📊 Summary: No trade (daily budget exhausted)")
        return
    if position_size > remaining_budget:
        position_size = remaining_budget
        log(f"  Budget cap: trade capped at ${position_size:.2f} (${daily_spend['spent']:.2f}/${DAILY_BUDGET:.2f} spent)")
    if position_size < 0.50:
        log(f"  ⏸️  Remaining budget ${position_size:.2f} < $0.50 — skip")
        if not quiet:
            print(f"📊 Summary: No trade (remaining budget too small)")
        return

    # Check minimum order size
    if price > 0:
        min_cost = MIN_SHARES_PER_ORDER * price
        if min_cost > position_size:
            log(f"  ⚠️  Position ${position_size:.2f} too small for {MIN_SHARES_PER_ORDER} shares at ${price:.2f}")
            return

    log(f"  ✅ Signal: {side.upper()} — {trade_rationale}", force=True)
    log(f"  Value edge: {value_edge:.1%}", force=True)

    # Step 5: Validate CLOB token IDs & Trade
    clob_token_ids = best.get("clob_token_ids", [])
    if not clob_token_ids or len(clob_token_ids) < 2:
        log(f"  ❌ No CLOB token IDs for this market — cannot trade", force=True)
        return

    token_id = clob_token_ids[0] if side == "yes" else clob_token_ids[1]
    log(f"\n🔗 Token ID ({side.upper()}): {token_id[:20]}...", force=True)

    if dry_run:
        est_shares = position_size / price if price > 0 else 0
        log(f"  [DRY RUN] Would buy {side.upper()} ${position_size:.2f} (~{est_shares:.1f} shares)", force=True)
    else:
        log(f"  Executing {side.upper()} trade for ${position_size:.2f}...", force=True)

        result = execute_trade(clob_token_ids, side, position_size, price)
        trade_succeeded = False

        if result and result.get("success"):
            trade_succeeded = True
            shares = result.get("shares_bought", 0)
            trade_id = result.get("order_id")
        else:
            error = result.get("error", "Unknown error") if result else "No response"
            is_no_liquidity = "liquidity" in error.lower() or "not filled" in error.lower()

            if is_no_liquidity:
                log(f"  ⏸️  No liquidity at ${price:.2f} — order book empty at this price", force=True)
            else:
                log(f"  ❌ Trade failed: {error}", force=True)
                send_slack_notification(f"❌ *Trade Failed*\n• Market: {best['question'][:40]}...\n• Error: {error}", "❌")

        if trade_succeeded:
            log(f"  ✅ Bought {shares:.1f} {side.upper()} shares @ ${price:.3f}", force=True)

            # Update daily spend
            daily_spend["spent"] += position_size
            daily_spend["trades"] += 1
            _save_daily_spend(__file__, daily_spend)

            # Send Slack notification
            potential_profit = shares * (1 - price) * 0.9
            notify_trade(side, shares, price, best["question"], potential_profit)

            # Log to trade journal
            if trade_id and JOURNAL_AVAILABLE:
                confidence = min(0.9, 0.5 + value_edge)
                log_trade(
                    trade_id=trade_id,
                    source=TRADE_SOURCE,
                    thesis=trade_rationale,
                    confidence=round(confidence, 2),
                    asset=ASSET,
                    momentum_pct=round(momentum["momentum_pct"], 3) if momentum else 0,
                    volume_ratio=round(momentum["volume_ratio"], 2) if momentum else 1,
                    signal_source=SIGNAL_SOURCE,
                )

    # Summary
    total_trades = 0 if dry_run else (1 if trade_succeeded else 0)
    show_summary = not quiet or total_trades > 0
    if show_summary:
        print(f"\n📊 Summary:")
        print(f"  Market: {best['question'][:50]}")
        print(f"  Strategy: MOMENTUM | {side.upper()} @ ${price:.2f} | Edge: {value_edge:.0%}")
        print(f"  Action: {'DRY RUN' if dry_run else ('TRADED' if total_trades else 'FAILED')}")


# =============================================================================
# Strategy Dispatcher
# =============================================================================

def run_strategy(dry_run=True, positions_only=False, show_config=False,
                 smart_sizing=False, quiet=False):
    """Dispatch to the configured strategy."""
    if STRATEGY == "arbitrage":
        run_arbitrage_strategy(dry_run=dry_run, positions_only=positions_only,
                               show_config=show_config, smart_sizing=smart_sizing,
                               quiet=quiet)
    else:
        # "momentum" or "value" — both use the existing function
        run_fast_market_strategy(dry_run=dry_run, positions_only=positions_only,
                                  show_config=show_config, smart_sizing=smart_sizing,
                                  quiet=quiet)


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket FastLoop Trading Bot")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Show opportunities without trading")
    parser.add_argument("--positions", action="store_true", help="Show current fast market positions")
    parser.add_argument("--config", action="store_true", help="Show current config")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Update config (e.g., --set entry_threshold=0.08)")
    parser.add_argument("--smart-sizing", action="store_true", help="Use portfolio-based position sizing")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Only output on trades/errors (ideal for high-frequency runs)")
    parser.add_argument("--loop", action="store_true",
                        help="Run continuously (for deployment). Also enabled by LOOP=1 env var.")
    parser.add_argument("--interval", type=int, default=60,
                        help="Seconds between runs in loop mode (default: 60)")
    args = parser.parse_args()

    if args.set:
        updates = {}
        for item in args.set:
            if "=" not in item:
                print(f"Invalid --set format: {item}. Use KEY=VALUE")
                sys.exit(1)
            key, val = item.split("=", 1)
            if key in CONFIG_SCHEMA:
                type_fn = CONFIG_SCHEMA[key].get("type", str)
                try:
                    if type_fn == bool:
                        updates[key] = val.lower() in ("true", "1", "yes")
                    else:
                        updates[key] = type_fn(val)
                except ValueError:
                    print(f"Invalid value for {key}: {val}")
                    sys.exit(1)
            else:
                print(f"Unknown config key: {key}")
                print(f"Valid keys: {', '.join(CONFIG_SCHEMA.keys())}")
                sys.exit(1)
        result = _update_config(updates, __file__)
        print(f"✅ Config updated: {json.dumps(updates)}")
        sys.exit(0)

    # Check for live mode (CLI flag or env var)
    live_mode = args.live or os.environ.get("LIVE", "").lower() in ("1", "true", "yes")
    dry_run = not live_mode
    
    # Check for loop mode (CLI flag or env var)
    loop_mode = args.loop or os.environ.get("LOOP", "").lower() in ("1", "true", "yes")
    interval = int(os.environ.get("LOOP_INTERVAL", args.interval))
    
    if loop_mode:
        print(f"🔄 Running in loop mode (interval: {interval}s, strategy: {STRATEGY})")

        # Initialize CLOB client and show wallet info
        client = get_clob_client()
        print(f"  Wallet: {get_wallet_address()}")
        portfolio = get_portfolio()
        if portfolio:
            print(f"  USDC Balance: ${portfolio.get('balance_usdc', 0):.2f}")

        print("=" * 50)
        run_count = 0
        while True:
            run_count += 1
            print(f"\n--- Run #{run_count} @ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} ---")
            try:
                run_strategy(
                    dry_run=dry_run,
                    positions_only=args.positions,
                    show_config=args.config,
                    smart_sizing=args.smart_sizing,
                    quiet=args.quiet,
                )
            except Exception as e:
                print(f"❌ Error in run #{run_count}: {e}")
            print(f"\n⏳ Sleeping {interval}s...")
            time.sleep(interval)
    else:
        run_strategy(
            dry_run=dry_run,
            positions_only=args.positions,
            show_config=args.config,
            smart_sizing=args.smart_sizing,
            quiet=args.quiet,
        )
