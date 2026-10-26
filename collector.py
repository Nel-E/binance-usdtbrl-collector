#!/usr/bin/env python3
"""Read-only Binance USDT/BRL public market collector (stdlib only)."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SYMBOL = "USDTBRL"
API_ROOT = os.getenv("BINANCE_MARKET_DATA_URL", "https://data-api.binance.vision").rstrip("/")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
HISTORY_LIMIT = 672  # 15-minute snapshots for seven days, if the schedule runs on time


def utc_string(ms: int | None = None) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms is not None else datetime.now(timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_json(path: str, **params):
    query = "?" + urlencode(params) if params else ""
    url = API_ROOT + path + query
    error = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "public-usdtbrl-collector/1.0", "Accept": "application/json"})
            with urlopen(req, timeout=12) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            error = exc
            if isinstance(exc, HTTPError) and exc.code not in (418, 429, 500, 502, 503, 504):
                break
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Binance public GET {path} failed: {type(error).__name__}: {error}")


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as fp:
        tmp = Path(fp.name)
        json.dump(data, fp, indent=2, ensure_ascii=False, allow_nan=False)
        fp.write("\n")
    os.replace(tmp, path)


def simple_ema(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    ema = sum(values[:n]) / n
    alpha = 2 / (n + 1)
    for val in values[n:]:
        ema = val * alpha + ema * (1 - alpha)
    return ema


def macd_hist(values: list[float]):
    if len(values) < 35:
        return None
    def series(n):
        output = []
        ema = sum(values[:n]) / n
        for i, x in enumerate(values):
            if i < n - 1:
                output.append(None)
            elif i == n - 1:
                output.append(ema)
            else:
                ema = x * (2 / (n + 1)) + ema * (1 - 2 / (n + 1))
                output.append(ema)
        return output
    a, b = series(12), series(26)
    diff = [x - y for x, y in zip(a, b) if x is not None and y is not None]
    signal = simple_ema(diff, 9)
    return None if signal is None else round(diff[-1] - signal, 7)


def parse_klines(rows, server_time_ms: int, period: str):
    cleaned = []
    for row in rows:
        if len(row) < 11:
            raise ValueError("Kline response missing required fields")
        item = {
            "open_utc": utc_string(int(row[0])),
            "close_utc": utc_string(int(row[6])),
            "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]),
            "volume_usdt": float(row[5]),
            "quote_volume_brl": float(row[7]),
            "trades": int(row[8]),
            "taker_buy_volume_usdt": float(row[9]),
            "closed": int(row[6]) < server_time_ms,
        }
        cleaned.append(item)
    closed = [c for c in cleaned if c["closed"]]
    prices = [c["close"] for c in closed]
    volumes = [c["volume_usdt"] for c in closed]
    most_recent = closed[-1] if closed else None
    prev_volumes = volumes[-6:-1]
    avg_volume = sum(prev_volumes) / len(prev_volumes) if prev_volumes else None
    summary = {
        "interval": period,
        "latest_closed": most_recent,
        "current_incomplete": next((c for c in reversed(cleaned) if not c["closed"]), None),
        "sma7_closed": round(sum(prices[-7:]) / 7, 6) if len(prices) >= 7 else None,
        "sma25_closed": round(sum(prices[-25:]) / 25, 6) if len(prices) >= 25 else None,
        "macd_histogram_closed": macd_hist(prices),
        "latest_volume_ratio_to_previous5": round(volumes[-1] / avg_volume, 3) if avg_volume else None,
        "latest_candle_buy_volume_pct": round(100 * most_recent["taker_buy_volume_usdt"] / most_recent["volume_usdt"], 2) if most_recent and most_recent["volume_usdt"] else None,
        "closed_candles": closed[-40:],
    }
    return summary


def parse_depth(raw, mid: float):
    bids = sorted([(float(p), float(q)) for p, q in raw["bids"]], reverse=True)
    asks = sorted([(float(p), float(q)) for p, q in raw["asks"]])
    if not bids or not asks or bids[0][0] >= asks[0][0]:
        raise ValueError("No valid order book, or crossed best quotes")
    def side(levels, side, limit=30):
        return [{"price_brl": p, "quantity_usdt": q, "notional_brl": round(p*q, 2)} for p, q in levels[:limit]]
    bands = {}
    for pct in (0.1, 0.5, 1.0):
        threshold = mid * pct / 100
        bid_quote = sum(p*q for p, q in bids if mid - p <= threshold)
        ask_quote = sum(p*q for p, q in asks if p - mid <= threshold)
        total = bid_quote + ask_quote
        bands[f"{pct:.1f}%"] = {
            "bid_notional_brl": round(bid_quote, 2),
            "ask_notional_brl": round(ask_quote, 2),
            "bid_share_pct": round(100 * bid_quote / total, 2) if total else None,
        }
    def biggest(levels):
        return sorted(side(levels, None, len(levels)), key=lambda x: x["notional_brl"], reverse=True)[:8]
    return {
        "limit_per_side_requested": 100,
        "last_update_id": raw["lastUpdateId"],
        "best_bid": bids[0][0], "best_ask": asks[0][0],
        "visible_bids": side(bids, "bid"), "visible_asks": side(asks, "ask"),
        "largest_visible_bid_walls": biggest(bids),
        "largest_visible_ask_walls": biggest(asks),
        "depth_bands_midpoint": bands,
        "warning": "Only a REST snapshot of top 100 levels per side. Resting walls can cancel, move or be spoofed; depth proportions are not probabilities.",
    }


def validate_price(value, label: str) -> float:
    val = float(value)
    if not math.isfinite(val) or val <= 0:
        raise ValueError(f"Invalid {label}: {value!r}")
    return val


def capture() -> dict:
    time_start_ms = int(time.time() * 1000)
    clock = fetch_json("/api/v3/time")
    server_ms = int(clock["serverTime"])
    book = fetch_json("/api/v3/ticker/bookTicker", symbol=SYMBOL)
    ticker = fetch_json("/api/v3/ticker/24hr", symbol=SYMBOL)
    depth_raw = fetch_json("/api/v3/depth", symbol=SYMBOL, limit=100)
    k15 = fetch_json("/api/v3/klines", symbol=SYMBOL, interval="15m", limit=130)
    k1h = fetch_json("/api/v3/klines", symbol=SYMBOL, interval="1h", limit=130)
    best_bid = validate_price(book["bidPrice"], "bookTicker bid")
    best_ask = validate_price(book["askPrice"], "bookTicker ask")
    last = validate_price(ticker["lastPrice"], "last price")
    if best_bid >= best_ask:
        raise ValueError("Crossed or locked bookTicker quotes; refusing to write misleading snapshot")
    mid = (best_ask + best_bid)/2
    order_book = parse_depth(depth_raw, mid)
    captured = datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "symbol": SYMBOL,
        "source": "Binance public Spot REST market-data-only API (no credentials)",
        "api_base_url": API_ROOT,
        "captured_at_utc": captured.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "captured_at_brt": captured.astimezone(ZoneInfo("America/Sao_Paulo")).isoformat(timespec="seconds"),
        "capture_duration_seconds": round((time.time()*1000 - time_start_ms)/1000, 2),
        "binance_server_time_utc_at_start": utc_string(server_ms),
        "spot": {
            "last_trade_price_brl_per_usdt": last,
            "best_bid_brl_per_usdt": best_bid,
            "best_bid_quantity_usdt": float(book["bidQty"]),
            "best_ask_brl_per_usdt": best_ask,
            "best_ask_quantity_usdt": float(book["askQty"]),
            "mid_price_brl_per_usdt": round(mid, 6),
            "spread_brl": round(best_ask-best_bid, 6),
            "spread_pct_of_mid": round(100*(best_ask-best_bid)/mid, 5),
        },
        "rolling_24h": {
            "price_change_pct": float(ticker["priceChangePercent"]),
            "high_brl_per_usdt": float(ticker["highPrice"]),
            "low_brl_per_usdt": float(ticker["lowPrice"]),
            "volume_usdt": float(ticker["volume"]),
            "quote_volume_brl": float(ticker["quoteVolume"]),
            "number_of_trades": int(ticker["count"]),
        },
        "candles": {"15m": parse_klines(k15, server_ms, "15m"), "1h": parse_klines(k1h, server_ms, "1h")},
        "depth": order_book,
        "interpretation_notes": [
            "Prices are Binance quotes, not Mercado Bitcoin fills; actual execution differs after fees/slippage.",
            "Klines have an explicit closed flag. Use closed candles for signals.",
            "REST endpoints are fetched sequentially, not at exactly the same instant.",
            "This collector does not include NZD, user balances, trades, or forecasts.",
        ],
    }


def write_results(result: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_json(DATA_DIR / "latest.json", result)
    compact = {
        "captured_at_utc": result["captured_at_utc"],
        "bid_brl": result["spot"]["best_bid_brl_per_usdt"],
        "ask_brl": result["spot"]["best_ask_brl_per_usdt"],
        "last_brl": result["spot"]["last_trade_price_brl_per_usdt"],
        "spread_pct": result["spot"]["spread_pct_of_mid"],
        "depth_bid_share_half_pct": result["depth"]["depth_bands_midpoint"]["0.5%"]["bid_share_pct"],
        "latest_closed_15m": result["candles"]["15m"]["latest_closed"],
        "latest_closed_1h": result["candles"]["1h"]["latest_closed"],
        "rolling_24h_volume_usdt": result["rolling_24h"]["volume_usdt"],
    }
    history_file = DATA_DIR / "history.json"
    if history_file.exists():
        history = json.loads(history_file.read_text(encoding="utf-8"))
        if not isinstance(history, list):
            raise ValueError("Invalid history file")
    else:
        history = []
    if not history or history[-1]["captured_at_utc"] != compact["captured_at_utc"]:
        history.append(compact)
    atomic_json(history_file, history[-HISTORY_LIMIT:])
    atomic_json(DATA_DIR / "status.json", {
        "ok": True, "captured_at_utc": result["captured_at_utc"],
        "message": "Binance public REST snapshot collected successfully.",
    })


def main():
    try:
        result = capture()
        write_results(result)
        print(f"OK {result['captured_at_utc']} Binance {SYMBOL} bid={result['spot']['best_bid_brl_per_usdt']} ask={result['spot']['best_ask_brl_per_usdt']}")
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        try:
            atomic_json(DATA_DIR / "status.json", {
                "ok": False, "attempted_at_utc": utc_string(),
                "message": message,
                "latest_json_may_be_stale": True,
            })
        except Exception as write_exc:
            print(f"Also failed to write status: {write_exc}", file=sys.stderr)
        print(message, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
