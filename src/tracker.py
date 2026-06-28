"""訊號追蹤模組：追蹤選股結果是否已落入買入區間，並於觸發條件時結算歸檔。"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

_DATA_DIR        = Path(__file__).parent.parent / "data"
_WATCHLIST_PATH  = _DATA_DIR / "watchlist.json"
_PERF_PATH       = _DATA_DIR / "performance_history.json"

MAX_WATCH_DAYS    = 5       # watch 狀態最多等 5 個交易日（進場有效期限）
_DEFAULT_HOLD_DAYS = 10     # hold_period 無法解析時的預設持倉天數

# 結算原因常數
EXIT_PROFIT  = "CLOSED_PROFIT"
EXIT_LOSS    = "CLOSED_LOSS"
EXIT_EXPIRED = "FORCE_EXPIRED"


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

def _parse_hold_period(hold_period_str: str, default: int = _DEFAULT_HOLD_DAYS) -> int:
    """解析 "5-10 個交易日" 或 "7 天" → 取最大數值，無法解析則回傳 default。"""
    if not hold_period_str or hold_period_str.strip() in ("-", ""):
        return default
    nums = re.findall(r"\d+", hold_period_str)
    if not nums:
        return default
    return max(int(n) for n in nums)


def _parse_stop_loss(stop_loss_str: str) -> float | None:
    """解析 "$182.50" 或 "182" → 182.5，失敗回傳 None。"""
    if not stop_loss_str or stop_loss_str.strip() in ("-", ""):
        return None
    nums = re.findall(r"[\d,]+\.?\d*", stop_loss_str)
    if not nums:
        return None
    try:
        return float(nums[0].replace(",", ""))
    except ValueError:
        return None


def _parse_target(target_str: str) -> float | None:
    """解析 "$210" 或 "210" → 210.0，失敗回傳 None。"""
    return _parse_stop_loss(target_str)


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
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else None
        result[sym] = {
            "price":        round(price, 2),
            "ema20":        round(ema20, 2) if ema20 else None,
            "ema50":        round(ema50, 2) if ema50 else None,
            "close_series": close,
        }

    return result


def _calc_split_factor(signal_date: str, signal_date_close: float,
                       close_series: pd.Series) -> float:
    """
    從 yfinance auto_adjust 的歷史數據中查找信號日的調整後收盤價，
    計算拆股平移因子。無拆股時回傳 1.0。

    yfinance auto_adjust=True 會在拆股後回溯調整全部歷史價格，因此：
    - 若無拆股：signal_date 當日的現行調整價 ≈ 記錄時的 signal_date_close → factor ≈ 1.0
    - 若發生 N:1 拆股：signal_date 的現行調整價 = signal_date_close / N → factor = 1/N
    所有門檻乘以 factor 後即等比例修正，消除幽靈止損。
    """
    if not signal_date or not signal_date_close:
        return 1.0
    try:
        idx = pd.to_datetime(signal_date)
        valid = close_series[close_series.index.normalize() <= idx]
        if valid.empty:
            return 1.0
        adjusted_hist = float(valid.iloc[-1])
        return adjusted_hist / signal_date_close
    except Exception:
        return 1.0


def _eval_status(
    entry: dict,
    price: float,
    ema20: float | None,
    ema50: float | None = None,
) -> tuple[str, str | None]:
    """
    評估訊號狀態：股價是否已落入買入區間，或訊號是否失效。
    回傳 (new_status, invalid_reason)。
    已失效者直接回傳原因，不再重新判斷。

    失效條件依策略類型差異化：
    - 反轉策略：進場點本就在 EMA50 下方，以跌破 AI 止損價為失效門檻
    - 動能/突破策略：跌破 EMA20 即失效

    狀態機下限：
    - price >= lower → active（進場）
    - price < lower 但 >= stop_loss → watch（繼續觀察，未觸及止損）
    - price < lower 且 < stop_loss → invalid（跌穿止損）
    """
    if entry.get("status") == "invalid":
        return "invalid", entry.get("invalid_reason")

    lower = entry.get("buy_zone_lower", 0.0)
    upper = entry["buy_zone_upper"]
    strategy = entry.get("strategy", "")
    stop_loss_price = _parse_stop_loss(entry.get("stop_loss", "-"))
    current_status = entry.get("status", "watch")

    # ── 失效條件：依策略差異化 ──
    if strategy == "反轉策略":
        # 反轉股進場點本就在 EMA50 下方，不能以 EMA50 為失效門檻
        if stop_loss_price is not None and price < stop_loss_price:
            return "invalid", f"跌破止損價 ${stop_loss_price:.2f}，反轉訊號失效"
    else:
        if ema20 is not None and price < ema20:
            return "invalid", "趨勢轉弱，訊號失效"

    # ── 追高失效：僅適用非 active 狀態 ──
    # active 持倉大漲屬正常獲利波段，由 _check_settlement 接管
    if current_status != "active" and price > upper * 1.08:
        return "invalid", "已追高，錯過買點"

    # ── 狀態機判定 ──
    if price > upper * 1.01:
        return "watch", None       # 高於買入區間，等回落
    if price >= lower:
        return "active", None      # 在買入區間內，視為進場
    # price < lower：跌穿買入區下限
    if stop_loss_price is not None and price < stop_loss_price:
        return "invalid", f"跌破止損價 ${stop_loss_price:.2f}，錯過買點"
    return "watch", None           # 跌穿下限但未到止損，繼續觀察


def _check_settlement(entry: dict, price: float) -> tuple[str, float] | None:
    """
    判斷 active 部位是否觸發結算。
    回傳 (exit_reason, exit_price) 或 None（未觸發）。
    檢查優先順序：停利 > 停損 > 時間到期（FORCE_EXPIRED）。
    """
    if entry.get("status") != "active":
        return None

    target      = _parse_target(entry.get("target", "-"))
    stop_loss   = _parse_stop_loss(entry.get("stop_loss", "-"))
    hold_limit  = _parse_hold_period(entry.get("hold_period", "-"))
    active_days = entry.get("active_days", 0)

    if target is not None and price >= target:
        return EXIT_PROFIT, price
    if stop_loss is not None and price <= stop_loss:
        return EXIT_LOSS, price
    if active_days >= hold_limit:
        return EXIT_EXPIRED, price
    return None


def _archive_to_performance_history(
    entry: dict, exit_reason: str, exit_price: float, exit_date: str
) -> None:
    """將結算部位寫入 data/performance_history.json（原子寫入）。"""
    entry_price = entry.get("active_entry_price") or entry.get("buy_zone_lower", 0)
    if entry_price and entry_price > 0:
        return_pct = round((exit_price - entry_price) / entry_price * 100, 2)
    else:
        return_pct = None

    active_start = entry.get("active_start_date") or entry.get("date_added", "")
    try:
        holding_days = (
            (date.fromisoformat(exit_date) - date.fromisoformat(active_start)).days
            if active_start and exit_date else entry.get("active_days", 0)
        )
    except Exception:
        holding_days = entry.get("active_days", 0)

    record = {
        "meta_data": {
            "ticker":       entry["symbol"],
            "company_name": entry.get("name", ""),
            "sector":       entry.get("sector", ""),
        },
        "signal_details": {
            "signal_date":        entry.get("date_added", ""),
            "entry_regime":       entry.get("entry_regime", ""),
            "market_breadth_pct": entry.get("market_breadth_pct"),
            "vix_value":          entry.get("vix_value"),
            "l2_score":           entry.get("l2_score"),
            "assigned_strategy":  entry.get("strategy", ""),
            "ai_confidence":      entry.get("ai_confidence"),
            "ai_strategy_reason": entry.get("ai_strategy_reason", ""),
        },
        "execution_plan": {
            "buy_zone_lower":    entry.get("buy_zone_lower"),
            "buy_zone_upper":    entry.get("buy_zone_upper"),
            "planned_target":    entry.get("target", "-"),
            "planned_stop_loss": entry.get("stop_loss", "-"),
        },
        "actual_outcome": {
            "triggered_date":     entry.get("active_start_date", ""),
            "actual_entry_price": entry.get("active_entry_price"),
            "exit_date":          exit_date,
            "actual_exit_price":  round(exit_price, 2),
            "exit_reason":        exit_reason,
            "holding_days":       holding_days,
        },
        "performance_metrics": {
            "return_pct": return_pct,
            "is_win":     (return_pct > 0) if return_pct is not None else None,
        },
    }

    if _PERF_PATH.exists():
        try:
            with open(_PERF_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"history_records": []}
    else:
        data = {"history_records": []}

    data["history_records"].append(record)

    # 原子寫入：先寫暫存檔再 rename，防止寫入中途崩潰導致 JSON 損壞
    tmp_path = _PERF_PATH.with_suffix(".tmp")
    _DATA_DIR.mkdir(exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(_PERF_PATH)

    sign = f"{return_pct:+.2f}%" if return_pct is not None else "N/A"
    print(f"[tracker] {entry['symbol']} 結算歸檔（{exit_reason}，回報 {sign}）")


def _days(entry: dict) -> int:
    """回傳已追蹤天數（唯一日期數量）。供 is_rerun 防重複執行使用。"""
    return len(entry.get("tracked_dates", []))


def _is_expired(entry: dict) -> bool:
    """
    判斷是否已到期應移除。
    - watch / invalid：超過 MAX_WATCH_DAYS 個追蹤日即到期
    - active：由 _check_settlement() 接管（FORCE_EXPIRED），此處永不到期
    """
    status = entry.get("status", "watch")
    if status == "active":
        return False   # active 部位由結算邏輯控制生命週期
    return _days(entry) >= MAX_WATCH_DAYS


# ── 主函式 ──────────────────────────────────────────────────────────

def run_tracker(
    new_ranked: list[dict],
    market_context: dict | None = None,
) -> tuple[list[dict], dict]:
    """
    執行訊號追蹤流程。
    回傳 (updated_watchlist, categories)。

    categories 結構：
      active:   已落入買入區間的追蹤中股票
      watch:    等待回落的追蹤中股票
      invalid:  訊號失效但未到期的股票
      expired:  今日到期移除的股票（快照）
      settled:  今日觸發結算並歸檔的股票（快照）
      new:      本次新加入的股票（含完整 AI 資料）
      reset:    本次重新入選並重置的股票（含完整 AI 資料）
    """
    today = date.today().isoformat()
    watchlist = load_watchlist()
    mc = market_context or {}

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
            "buy_zone":           stock["buy_zone"],
            "buy_zone_lower":     lower,
            "buy_zone_upper":     upper,
            "target":             stock.get("target", "-"),
            "stop_loss":          stock.get("stop_loss", "-"),
            "hold_period":        stock.get("hold_period", "-"),
            "strategy":           stock.get("strategy", "-"),
            "tracked_dates":      [today],
            "status":             "watch",
            "invalid_reason":     None,
            # ── 計時器（持久化至 JSON）──
            "watch_days":         0,
            "active_days":        0,
            "signal_date_close":  None,
            # ── 進場追蹤 ──
            "active_entry_price": None,   # 首次轉 active 當日收盤（代理進場價）
            "active_start_date":  None,   # 首次轉 active 日期
            # ── 信號時刻大盤背景（供績效分析） ──
            "entry_regime":       mc.get("regime", ""),
            "market_breadth_pct": mc.get("market_breadth_pct"),
            "vix_value":          mc.get("vix", {}).get("value"),
            # ── AI 精選資訊 ──
            "l2_score":           stock.get("total_score"),
            "ai_confidence":      stock.get("confidence"),
            "ai_strategy_reason": stock.get("strategy_reason", ""),
        }
        if sym in existing:
            existing[sym].update(base)
            reset_symbols.add(sym)
            reset_entries.append(stock)
        else:
            watchlist.append({
                "symbol":     sym,
                "name":       stock.get("name", sym),
                "sector":     stock.get("sector", "Unknown"),
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

    # E. 更新 tracked_dates、狀態、計數器，並對 active 部位執行結算檢查
    settled_entries: list[dict] = []

    for entry in watchlist:
        sym = entry["symbol"]

        if today not in entry["tracked_dates"]:
            entry["tracked_dates"].append(today)

        if sym not in latest:
            entry.setdefault("current_price", None)
            continue

        price        = latest[sym]["price"]
        ema20        = latest[sym]["ema20"]
        ema50        = latest[sym].get("ema50")
        close_series = latest[sym].get("close_series")

        # 拆股免疫：以信號日的 auto_adjust 歷史價計算平移因子
        signal_close = entry.get("signal_date_close")
        signal_date  = entry["tracked_dates"][0] if entry.get("tracked_dates") else ""
        split_factor = 1.0
        if signal_close and close_series is not None:
            split_factor = _calc_split_factor(signal_date, signal_close, close_series)
        if abs(split_factor - 1.0) > 0.01:
            print(f"[tracker] {sym} 偵測到拆股，平移因子={split_factor:.4f}")
            adj = dict(entry)
            adj["buy_zone_lower"] = entry.get("buy_zone_lower", 0.0) * split_factor
            adj["buy_zone_upper"] = entry["buy_zone_upper"] * split_factor
            sl = _parse_stop_loss(entry.get("stop_loss", "-"))
            if sl:
                adj["stop_loss"] = f"${sl * split_factor:.2f}"
            new_status, reason = _eval_status(adj, price, ema20, ema50)
        else:
            new_status, reason = _eval_status(entry, price, ema20, ema50)

        prev_status = entry.get("status", "watch")
        entry["status"]        = new_status
        entry["invalid_reason"] = reason
        entry["current_price"] = price

        # 記錄信號日收盤價（首次追蹤時設定，供後續拆股校正使用）
        if entry.get("signal_date_close") is None:
            entry["signal_date_close"] = price

        # 首次進入 active：記錄代理進場價與日期
        if new_status == "active" and prev_status == "watch":
            if entry.get("active_entry_price") is None:
                entry["active_entry_price"] = price
                entry["active_start_date"]  = today

        # 計數器遞增（直接寫入 entry，確保被 JSON 序列化）
        if new_status == "watch":
            entry["watch_days"] = entry.get("watch_days", 0) + 1
        elif new_status == "active":
            entry["active_days"] = entry.get("active_days", 0) + 1

        # 結算檢查（僅對 active 部位）
        settlement = _check_settlement(entry, price)
        if settlement:
            exit_reason, exit_price = settlement
            _archive_to_performance_history(entry, exit_reason, exit_price, today)
            entry["_settled"] = True
            settled_entries.append(entry)

    # F. 分類（移除前快照）
    settled_symbols = {e["symbol"] for e in settled_entries}
    expired = [e for e in watchlist if _is_expired(e) and e["symbol"] not in settled_symbols]
    active = [
        e for e in watchlist
        if e["status"] == "active"
        and e["symbol"] not in reset_symbols
        and e["symbol"] not in settled_symbols
        and not _is_expired(e)
    ]
    watch = [
        e for e in watchlist
        if e["status"] == "watch"
        and e["symbol"] not in reset_symbols
        and e["symbol"] not in settled_symbols
        and not _is_expired(e)
    ]
    invalid = [
        e for e in watchlist
        if e["status"] == "invalid"
        and e["symbol"] not in reset_symbols
        and e["symbol"] not in settled_symbols
        and not _is_expired(e)
    ]

    categories = {
        "active":   active,
        "watch":    watch,
        "invalid":  invalid,
        "expired":  expired,
        "settled":  settled_entries,
        "new":      new_entries,
        "reset":    reset_entries,
    }

    # G. 移除已到期與已結算
    watchlist = [
        e for e in watchlist
        if not _is_expired(e) and not e.get("_settled")
    ]

    # H. 儲存
    save_watchlist(watchlist)
    print(f"[tracker] watchlist 更新完成，保留 {len(watchlist)} 筆"
          f"（結算歸檔 {len(settled_entries)} 筆）")

    return watchlist, categories
