"""訊號追蹤模組：追蹤選股結果是否已落入買入區間或訊號失效。"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

_DATA_DIR = Path(__file__).parent.parent / "data"
_WATCHLIST_PATH = _DATA_DIR / "watchlist.json"
MAX_TRACK_DAYS = 5


# ── I/O ─────────────────────────────────────────────────────────────

def load_watchlist() -> list[dict]:
    if not _WATCHLIST_PATH.exists():
        return []
    try:
        with open(_WATCHLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[tracker] watchlist 讀取失敗：{e}")
        return []


def save_watchlist(watchlist: list[dict]) -> None:
    _DATA_DIR.mkdir(exist_ok=True)
    with open(_WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)


def check_already_run_today() -> bool:
    """檢查今日是否已執行過追蹤，回傳 True 表示已執行。"""
    today = date.today().isoformat()
    watchlist = load_watchlist()
    return any(today in e.get("tracked_dates", []) for e in watchlist)


# ── 工具函式 ─────────────────────────────────────────────────────────

def _parse_buy_zone(buy_zone_str: str) -> tuple[float, float] | None:
    """解析 "$185～$188" → (185.0, 188.0)，失敗回傳 None。"""
    if not buy_zone_str or buy_zone_str.strip() in ("-", ""):
        return None
    nums = re.findall(r"[\d,]+\.?\d*", buy_zone_str)
    if len(nums) < 2:
        return None
    try:
        low = float(nums[0].replace(",", ""))
        high = float(nums[1].replace(",", ""))
        return (low, high) if low <= high else (high, low)
    except ValueError:
        return None


def _fetch_latest(symbols: list[str]) -> dict[str, dict]:
    """批次下載最新收盤價與 EMA20。"""
    if not symbols:
        return {}
    try:
        raw = yf.download(
            tickers=symbols,
            period="60d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"[tracker] 下載追蹤股票數據失敗：{e}")
        return {}

    def _get_df(sym: str) -> pd.DataFrame:
        try:
            df = raw[sym] if len(symbols) > 1 else raw
            return df.dropna(how="all")
        except Exception:
            return pd.DataFrame()

    result: dict[str, dict] = {}
    for sym in symbols:
        df = _get_df(sym)
        if df.empty:
            continue
        close = df["Close"].dropna()
        if close.empty:
            continue
        price = float(close.iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(close) >= 20 else None
        result[sym] = {"price": round(price, 2), "ema20": round(ema20, 2) if ema20 else None}

    return result


def _eval_status(entry: dict, price: float, ema20: float | None) -> tuple[str, str | None]:
    """
    評估訊號狀態：股價是否已落入買入區間，或訊號是否失效。
    回傳 (new_status, invalid_reason)。
    已失效者直接回傳原因，不再重新判斷。
    """
    if entry.get("status") == "invalid":
        return "invalid", entry.get("invalid_reason")

    upper = entry["buy_zone_upper"]

    if ema20 is not None and price < ema20:
        return "invalid", "趨勢轉弱，訊號失效"
    if price > upper * 1.08:
        return "invalid", "已追高，錯過買點"
    if price > upper * 1.01:
        return "watch", None
    return "active", None


def _days(entry: dict) -> int:
    """回傳已追蹤天數（唯一日期數量）。"""
    return len(entry.get("tracked_dates", []))


# ── 主函式 ──────────────────────────────────────────────────────────

def run_tracker(new_ranked: list[dict]) -> tuple[list[dict], dict]:
    """
    執行訊號追蹤流程。
    回傳 (updated_watchlist, categories)。

    categories 結構：
      active:  已落入買入區間的追蹤中股票
      watch:   等待回落的追蹤中股票
      invalid: 訊號失效但未到期的股票
      expired: 今日到期移除的股票（快照）
      new:     本次新加入的股票（含完整 AI 資料）
      reset:   本次重新入選並重置的股票（含完整 AI 資料）
    """
    today = date.today().isoformat()
    watchlist = load_watchlist()

    # 相容舊格式（days_tracked int → tracked_dates list）
    for entry in watchlist:
        if "tracked_dates" not in entry:
            entry["tracked_dates"] = []

    # 同一天重跑時，清除今天才新增的股票（讓新結果完整取代）
    # 跨日追蹤中的舊股票（date_added != today）不受影響
    is_rerun = any(today in e.get("tracked_dates", []) for e in watchlist)
    if is_rerun:
        watchlist = [e for e in watchlist if e.get("date_added") != today]
        print(f"[tracker] 今日重複執行，已清除今日新增的股票，重新以新結果取代")

    existing = {e["symbol"]: e for e in watchlist}
    reset_symbols: set[str] = set()
    new_entries: list[dict] = []
    reset_entries: list[dict] = []

    # B. 重置 / C. 新增
    for stock in new_ranked:
        sym = stock["symbol"]
        parsed = _parse_buy_zone(stock.get("buy_zone", "-"))
        if parsed is None:
            continue

        lower, upper = parsed
        base: dict = {
            "buy_zone": stock["buy_zone"],
            "buy_zone_lower": lower,
            "buy_zone_upper": upper,
            "target": stock.get("target", "-"),
            "stop_loss": stock.get("stop_loss", "-"),
            "hold_period": stock.get("hold_period", "-"),
            "strategy": stock.get("strategy", "-"),
            "tracked_dates": [today],
            "status": "watch",
            "invalid_reason": None,
        }
        if sym in existing:
            existing[sym].update(base)
            reset_symbols.add(sym)
            reset_entries.append(stock)
        else:
            watchlist.append({
                "symbol": sym,
                "name": stock.get("name", sym),
                "sector": stock.get("sector", "Unknown"),
                "date_added": today,
                **base,
            })
            new_entries.append(stock)

    # 重建 existing（含新增項）
    existing = {e["symbol"]: e for e in watchlist}

    # D. 批次下載最新價格
    all_symbols = list(existing.keys())
    latest = _fetch_latest(all_symbols)
    print(f"[tracker] 追蹤清單：{len(all_symbols)} 支，成功取得 {len(latest)} 支最新數據")

    # E. 更新 tracked_dates 與狀態
    for entry in watchlist:
        sym = entry["symbol"]

        if today not in entry["tracked_dates"]:
            entry["tracked_dates"].append(today)

        if sym in latest:
            price = latest[sym]["price"]
            ema20 = latest[sym]["ema20"]
            new_status, reason = _eval_status(entry, price, ema20)
            entry["status"] = new_status
            entry["invalid_reason"] = reason
            entry["current_price"] = price
        else:
            entry.setdefault("current_price", None)

    # F. 分類（移除前快照 expired，其餘依狀態分組）
    expired = [e for e in watchlist if _days(e) >= MAX_TRACK_DAYS]
    active = [
        e for e in watchlist
        if e["status"] == "active"
        and e["symbol"] not in reset_symbols
        and _days(e) < MAX_TRACK_DAYS
    ]
    watch = [
        e for e in watchlist
        if e["status"] == "watch"
        and e["symbol"] not in reset_symbols
        and _days(e) < MAX_TRACK_DAYS
    ]
    invalid = [
        e for e in watchlist
        if e["status"] == "invalid"
        and e["symbol"] not in reset_symbols
        and _days(e) < MAX_TRACK_DAYS
    ]

    categories = {
        "active":  active,
        "watch":   watch,
        "invalid": invalid,
        "expired": expired,
        "new":     new_entries,
        "reset":   reset_entries,
    }

    # G. 移除已到期
    watchlist = [e for e in watchlist if _days(e) < MAX_TRACK_DAYS]

    # H. 儲存
    save_watchlist(watchlist)
    print(f"[tracker] watchlist 更新完成，保留 {len(watchlist)} 筆")

    return watchlist, categories
