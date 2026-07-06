"""訊號追蹤模組：追蹤選股結果是否已落入買入區間，並於觸發條件時結算歸檔。"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

_DATA_DIR        = Path(__file__).parent.parent / "data"
_WATCHLIST_PATH  = _DATA_DIR / "watchlist.json"
_PERF_PATH       = _DATA_DIR / "performance_history.json"

MIN_AI_CONFIDENCE  = int(os.getenv("MIN_AI_CONFIDENCE", "6"))  # AI 信心分數最低門檻（DD-14）
_DEFAULT_WATCH_DAYS = 5                    # 突破/動能策略 watch 上限（DD-15）
_WATCH_DAYS_BY_STRATEGY: dict[str, int] = {
    "反轉策略": 10,                         # 底部確認需更長時間
}
_DEFAULT_HOLD_DAYS = 10     # hold_period 無法解析時的預設持倉天數

# 結算原因常數
EXIT_PROFIT   = "CLOSED_PROFIT"
EXIT_LOSS     = "CLOSED_LOSS"
EXIT_TRAILING = "CLOSED_TRAILING_STOP"
EXIT_EXPIRED  = "FORCE_EXPIRED"

# 風控常數（DD-13）
BREAKEVEN_PROFIT_THRESHOLD = 0.5    # 達目標距離 50% 時觸發保本
TRAILING_ACTIVATION_PCT    = 0.10   # 峰值浮盈需超過 10% 才啟動移動停利
TRAILING_RETRACE_PCT       = 0.05   # 從峰值收盤回撤 5% 觸發出場


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
    """檢查今日（UTC）是否已執行過追蹤，回傳 True 表示已執行。
    使用 UTC 日期確保與 CI 環境行為一致（market_date 以 UTC 為基準）。"""
    today_utc = datetime.utcnow().date().isoformat()
    watchlist = load_watchlist()
    return any(today_utc in e.get("tracked_dates", []) for e in watchlist)


# ── 工具函式 ─────────────────────────────────────────────────────────

def _parse_hold_period(hold_period_str, default: int = _DEFAULT_HOLD_DAYS) -> int:
    """解析 hold_period 為整數天數。接受 int/float 直接回傳，或從字串萃取最大數值。
    下界固定為 1（DD-19）：AI 若給出 <=0 的異常值，同日觸價成交（active_days
    首輪即為 1）會被誤判為 FORCE_EXPIRED，故無條件夾在最小值 1。"""
    if isinstance(hold_period_str, int):
        return max(1, hold_period_str)
    if isinstance(hold_period_str, float):
        return max(1, int(hold_period_str))
    s = str(hold_period_str) if hold_period_str is not None else ""
    if not s or s.strip() in ("-", ""):
        return default
    nums = re.findall(r"\d+", s)
    if not nums:
        return default
    return max(1, max(int(n) for n in nums))


def _count_trading_days(start: str, end: str) -> int:
    """計算兩日期間的交易日數（僅計週一至週五，不排除法定假日）。"""
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return sum(1 for i in range((d1 - d0).days) if (d0 + timedelta(days=i)).weekday() < 5)


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
    """批次下載最新收盤價、盤中高低點與 EMA。High/Low 與 Close 同列對齊，
    NaN 時 fallback 為 close（DD-10、DD-19）。"""
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
        price_date = close.index[-1]
        price = float(close.iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(close) >= 20 else None
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else None

        # 日內高低點：必須與 price 同一列（price_date）讀取，避免 Close 缺值時
        # dropna() 讓 price 落在前一列、但 High/Low 卻取自最新殘缺列而日期錯位
        # （破壞 today_low <= price <= today_high 恆等式，DD-19 依賴此恆等式）；
        # NaN fallback → close，避免停損免疫 Bug（DD-10）
        high_raw = df["High"].loc[price_date] if "High" in df.columns else float("nan")
        low_raw  = df["Low"].loc[price_date]  if "Low"  in df.columns else float("nan")
        today_high = float(high_raw) if pd.notna(high_raw) else price
        today_low  = float(low_raw)  if pd.notna(low_raw)  else price

        result[sym] = {
            "price":        round(price, 2),
            "today_high":   round(today_high, 2),
            "today_low":    round(today_low, 2),
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
    today_low: float | None = None,
) -> tuple[str, str | None]:
    """
    評估訊號狀態：股價是否已落入買入區間，或訊號是否失效。
    回傳 (new_status, invalid_reason)。
    已失效者直接回傳原因，不再重新判斷。

    盤中限價單模擬進場（DD-19）：使用者實際下單方式是在買入區間上緣
    （buy_zone_upper）掛限價單，只要 today_low <= buy_zone_upper 即視為
    當日觸價成交，優先於下方所有以收盤價 price 為準的判定（含追高失效）。
    此檢查嚴格位於 invalid/active 短路之後、其餘判定之前，確保既有 invalid
    條目不會被追溯復活。today_low 為 None（呼叫端未提供）或今日未觸價
    （today_low > buy_zone_upper）時，完全退化為下方原本以收盤價為準的
    判定，行為與 DD-19 之前逐字元相同。

    失效條件依策略類型差異化（僅適用 watch 狀態，且僅在今日未觸價成交時
    才有意義：正常 AI 參數配置下 stop_loss 恆低於 buy_zone_lower、EMA20
    恆低於 buy_zone_upper，觸價檢查必然先行成立；以下分支實務上僅作為
    未提供 today_low 時的退化路徑，以及 AI 給出異常參數的防禦網）：
    - 反轉策略：進場點本就在 EMA50 下方，以跌破 AI 止損價為失效門檻
    - 動能/突破策略：跌破 EMA20 即失效

    狀態機下限（今日未觸價成交時，以收盤價 price 為準）：
    - price >= lower → active（進場）
    - price < lower 但 >= stop_loss → watch（繼續觀察，未觸及止損）
    - price < lower 且 < stop_loss → invalid（跌穿止損）

    active 部位不在此函式判定失效或到期：生命週期完全交給
    _check_settlement() 的四態結算（止損/停利/移動停利/到期）。
    此函式若對 active 部位另外翻 invalid，會使該部位繞過結算、
    不寫入 performance_history.json 便被 _is_expired() 無聲移除（DD-17）。
    """
    if entry.get("status") == "invalid":
        return "invalid", entry.get("invalid_reason")
    if entry.get("status") == "active":
        return "active", None

    # ── 盤中限價單模擬進場（DD-19）：today_low <= upper 即視為觸價成交 ──
    if today_low is not None and today_low <= entry["buy_zone_upper"]:
        return "active", None

    lower = entry.get("buy_zone_lower", 0.0)
    upper = entry["buy_zone_upper"]
    strategy = entry.get("strategy", "")
    stop_loss_price = _parse_stop_loss(entry.get("stop_loss", "-"))

    # ── 失效條件：依策略差異化 ──
    if strategy == "反轉策略":
        # 反轉股進場點本就在 EMA50 下方，不能以 EMA50 為失效門檻
        if stop_loss_price is not None and price < stop_loss_price:
            return "invalid", f"跌破止損價 ${stop_loss_price:.2f}，反轉訊號失效"
    else:
        if ema20 is not None and price < ema20:
            return "invalid", "趨勢轉弱，訊號失效"

    # ── 追高失效（僅 watch 階段可達，active 已於上方短路）──
    if price > upper * 1.08:
        return "invalid", "已追高，錯過買點"

    # ── 狀態機判定 ──
    if price > upper * 1.01:
        return "watch", None       # 高於買入區間，等回落
    if price >= lower:
        # 開盤跳空安全攔截（DD-7，實務上已被 DD-19 的觸價檢查取代並列為
        # dormant：只要有提供 today_low，此分支便不可達，因為 price>=lower
        # 蘊含 today_low<=price<=upper，DD-19 檢查必然已先行成立並提早
        # return。保留作為未提供 today_low 時的向下相容路徑）：
        # 進場前確認未跌破止損（防止 AI 止損設在買入區間內的邊界案例）
        if stop_loss_price is not None and price <= stop_loss_price:
            return "invalid", f"開盤跳空跌破止損價 ${stop_loss_price:.2f}，拒絕進場"
        return "active", None      # 在買入區間內且高於止損，視為進場
    # price < lower：跌穿買入區下限
    if stop_loss_price is not None and price < stop_loss_price:
        return "invalid", f"跌破止損價 ${stop_loss_price:.2f}，錯過買點"
    return "watch", None           # 跌穿下限但未到止損，繼續觀察


def _check_settlement(
    entry: dict,
    price: float,
    today_high: float | None = None,
    today_low: float | None = None,
) -> tuple[str, float] | None:
    """
    判斷 active 部位是否觸發結算（DD-10、DD-12、DD-13）。
    回傳 (exit_reason, exit_price) 或 None（未觸發）。

    優先順序：
    1. 黑天鵝（同日 today_low≤effective_stop_loss 且 today_high≥target）→ CLOSED_LOSS
    2. 盤中止損：today_low ≤ effective_stop_loss → CLOSED_LOSS，exit_price = effective_stop_loss
    3. 盤中停利：today_high ≥ target   → CLOSED_PROFIT，exit_price = target
    4. 移動停利：峰值浮盈>10% 且收盤回撤≥5%（僅動能/突破）→ CLOSED_TRAILING_STOP，exit_price = close
    5. 時間到期：active_days ≥ hold_period → FORCE_EXPIRED，exit_price = close

    拆股情境：應傳入 adj（已縮放的臨時字典）確保所有門檻為正確絕對值（DD-3）。
    止損使用 effective_stop_loss（含保本後上移值），fallback 為原始 stop_loss（DD-12）。
    """
    if entry.get("status") != "active":
        return None

    target      = _parse_target(entry.get("target", "-"))
    stop_loss   = (entry.get("effective_stop_loss")
                   or _parse_stop_loss(entry.get("stop_loss", "-")))
    hold_limit  = _parse_hold_period(entry.get("hold_period", "-"))
    active_days = entry.get("active_days", 0)

    # 黑天鵝：同日 Low≤stop 且 High≥target，保守判為停損
    if (today_low is not None and today_high is not None
            and stop_loss is not None and today_low <= stop_loss
            and target is not None and today_high >= target):
        return EXIT_LOSS, stop_loss

    # 盤中止損（今日最低價觸及有效止損）
    if today_low is not None and stop_loss is not None and today_low <= stop_loss:
        return EXIT_LOSS, stop_loss

    # 盤中停利（今日最高價觸及目標）
    if today_high is not None and target is not None and today_high >= target:
        return EXIT_PROFIT, target

    # 移動停利（收盤觸發；雙欄位穿透 fallback；精確排除反轉策略，DD-13）
    strategy = entry.get("strategy") or entry.get("assigned_strategy") or ""
    if strategy != "反轉策略":
        entry_price  = entry.get("active_entry_price") or 0
        prev_highest = entry.get("highest_close_since_active") or entry_price
        if entry_price > 0 and prev_highest > entry_price:
            max_gain_pct = (prev_highest - entry_price) / entry_price
            retrace_pct  = (prev_highest - price) / prev_highest
            if max_gain_pct >= TRAILING_ACTIVATION_PCT and retrace_pct >= TRAILING_RETRACE_PCT:
                return EXIT_TRAILING, price

    # 持倉天數到期（使用收盤價）
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
    holding_days = entry.get("active_days") or (
        _count_trading_days(active_start, exit_date) if active_start and exit_date else 0
    )

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
            "planned_stop_loss": (
                f"${entry['planned_stop_loss']:.2f}"
                if entry.get("planned_stop_loss")
                else entry.get("stop_loss", "-")
            ),
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
            # 純數學判定，與出場原因解耦：CLOSED_TRAILING_STOP 正回報同樣計 Win（DD-13）
            "is_win":     return_pct > 0 if return_pct is not None else None,
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


def _apply_risk_controls(
    adj: dict, price: float, split_factor: float, original_entry: dict
) -> None:
    """
    保本鎖定與最高收盤更新（DD-12、DD-13）。
    adj 中的欄位為 split-scaled 調整後標尺，用於本輪結算比對。
    original_entry 以原生未拆股標尺持久化至 watchlist.json，避免逆向除法累積誤差。
    """
    if adj.get("status") != "active":
        return

    entry_price    = adj.get("active_entry_price") or 0
    target         = _parse_target(adj.get("target", "-"))
    effective_sl   = adj.get("effective_stop_loss")
    buy_zone_upper = adj.get("buy_zone_upper", 0)
    prev_highest   = adj.get("highest_close_since_active") or entry_price

    if entry_price <= 0:
        return

    # 向後相容：存量 active 持倉首次遇到新版 code 時，一次性初始化風控欄位
    if effective_sl is None:
        fallback_sl = (adj.get("planned_stop_loss")
                       or _parse_stop_loss(adj.get("stop_loss", "-")))
        adj["planned_stop_loss"]              = fallback_sl
        adj["effective_stop_loss"]            = fallback_sl
        adj["is_breakeven_locked"]            = False
        original_entry["planned_stop_loss"]   = fallback_sl
        original_entry["effective_stop_loss"] = fallback_sl
        original_entry.setdefault("is_breakeven_locked", False)
        effective_sl = fallback_sl

    # ── 保本鎖定（Fix #2：明示旗標，非浮點差判定）──
    if (not adj.get("is_breakeven_locked", False)
            and target is not None
            and effective_sl is not None
            and buy_zone_upper > (effective_sl or 0)):
        breakeven_threshold = entry_price + (target - entry_price) * BREAKEVEN_PROFIT_THRESHOLD
        if price >= breakeven_threshold:
            adj["effective_stop_loss"] = buy_zone_upper
            adj["is_breakeven_locked"] = True
            # Fix #3：直接讀原生 buy_zone_upper，避免 buy_zone_upper / split_factor 除法誤差
            raw_upper = original_entry.get("buy_zone_upper") or (buy_zone_upper / split_factor)
            original_entry["effective_stop_loss"] = raw_upper
            original_entry["is_breakeven_locked"] = True
            print(f"[tracker] {adj.get('symbol', '')} 保本鎖定：止損上移至 ${raw_upper:.2f}")

    # ── 最高收盤更新（Fix #3：只在原生標尺創新高時才寫回 DB）──
    today_price_raw = price / split_factor
    stored_highest  = original_entry.get("highest_close_since_active") or 0
    if today_price_raw > stored_highest:
        original_entry["highest_close_since_active"] = today_price_raw
        adj["highest_close_since_active"]            = price
    # 未創新高：original_entry 保持唯讀，不累積浮點誤差


def _days(entry: dict) -> int:
    """回傳已追蹤天數（唯一日期數量）。供 is_rerun 防重複執行使用。"""
    return len(entry.get("tracked_dates", []))


def _max_watch_days(entry: dict) -> int:
    """依策略與訊號當下大盤環境回傳 watch/invalid 天數上限（DD-15、DD-16）。"""
    strategy = entry.get("strategy", "")
    regime   = entry.get("entry_regime", "")
    vix      = entry.get("vix_value")

    if strategy == "突破策略" and regime == "CONSOLIDATION_VOLATILE":
        return 3  # 高波動整理市假突破風險升高，縮短觀察期（DD-16）

    if strategy == "反轉策略" and regime == "PANIC_REVERSAL" and vix is not None and vix > 35:
        return 5  # VIX 暴噴級尖底，V 型反彈應快速兌現，遲遲不進場視為真黑天鵝（DD-16）

    return _WATCH_DAYS_BY_STRATEGY.get(strategy, _DEFAULT_WATCH_DAYS)


def _is_expired(entry: dict) -> bool:
    """
    判斷是否已到期應移除。
    - watch / invalid：超過策略對應 watch 上限（DD-15）個追蹤日即到期
    - active：由 _check_settlement() 接管（FORCE_EXPIRED），此處永不到期
    """
    status = entry.get("status", "watch")
    if status == "active":
        return False   # active 部位由結算邏輯控制生命週期
    return _days(entry) >= _max_watch_days(entry)


# ── 主函式 ──────────────────────────────────────────────────────────

def run_tracker(
    new_ranked: list[dict],
    market_context: dict | None = None,
    market_date: str | None = None,
) -> tuple[list[dict], dict]:
    """
    執行訊號追蹤流程。
    回傳 (updated_watchlist, categories)。

    執行順序（DD-11）：D（下載現有）→ E（評估現有）→ B/C（處理新訊號）。
    新訊號在當輪不被評估（1-day lag），下一個交易日才進入狀態機。

    categories 結構：
      active:   已落入買入區間的追蹤中股票
      watch:    等待回落的追蹤中股票
      invalid:  訊號失效但未到期的股票
      expired:  今日到期移除的股票（快照）
      settled:  今日觸發結算並歸檔的股票（快照）
      new:      本次新加入的股票（含完整 AI 資料）
      reset:    本次重新入選並重置的股票（含完整 AI 資料）
    """
    today = market_date or date.today().isoformat()
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

    # ── D. 批次下載現有持倉最新價格（High/Low/Close/EMA）────────────
    existing_symbols = list(existing.keys())
    latest = _fetch_latest(existing_symbols)
    print(f"[tracker] 追蹤清單：{len(existing_symbols)} 支，成功取得 {len(latest)} 支最新數據")

    # ── E. 評估現有持倉狀態、更新計數器、執行結算 ───────────────────
    settled_entries: list[dict] = []

    for entry in watchlist:
        sym = entry["symbol"]

        # 同日重跑判定：tracked_dates 是否已含今日（DD-18）。
        # watch_days/active_days 的遞增必須依此去重，否則同一天內多次執行
        # （手動重跑並確認繼續）會讓計數器被重複累加，提前觸發 FORCE_EXPIRED
        # 或 watch 上限，並污染 performance_history.json 的 holding_days。
        already_tracked_today = today in entry["tracked_dates"]
        if not already_tracked_today:
            entry["tracked_dates"].append(today)

        if sym not in latest:
            entry.setdefault("current_price", None)
            continue

        price        = latest[sym]["price"]
        ema20        = latest[sym]["ema20"]
        ema50        = latest[sym].get("ema50")
        close_series = latest[sym].get("close_series")
        today_high   = latest[sym].get("today_high")
        today_low    = latest[sym].get("today_low")

        # 拆股免疫：以信號日的 auto_adjust 歷史價計算平移因子（DD-3）
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
            tgt = _parse_target(entry.get("target", "-"))
            if tgt:
                adj["target"] = f"${tgt * split_factor:.2f}"
            # DD-12：風控欄位同步縮放（不寫回 watchlist）
            planned_sl = entry.get("planned_stop_loss") or sl
            eff_sl     = entry.get("effective_stop_loss") or planned_sl
            adj["planned_stop_loss"]  = planned_sl * split_factor if planned_sl else None
            adj["effective_stop_loss"] = eff_sl    * split_factor if eff_sl    else None
            adj["is_breakeven_locked"] = entry.get("is_breakeven_locked", False)
            # active_entry_price 與 highest_close 在 watch→active 初始化後才有效，暫留空（Fix #1）
            adj["active_entry_price"]          = (entry.get("active_entry_price") or 0) * split_factor
            highest = entry.get("highest_close_since_active") or entry.get("active_entry_price") or 0
            adj["highest_close_since_active"]  = highest * split_factor
            new_status, reason = _eval_status(adj, price, ema20, ema50, today_low=today_low)
            settlement_entry = adj   # 結算也使用縮放後的 adj（DD-10）
        else:
            new_status, reason = _eval_status(entry, price, ema20, ema50, today_low=today_low)
            settlement_entry = entry

        prev_status = entry.get("status", "watch")
        entry["status"]         = new_status
        entry["invalid_reason"] = reason
        entry["current_price"]  = price

        # 存量條目 fallback：正常路徑已在建立條目時直接寫入訊號日收盤（DD-17），
        # 此處僅補救舊資料或 stock["price"] 缺失的極端情況，補寫值仍為次日收盤
        # （非精確訊號日價），僅供拆股平移退化使用，不影響新條目。
        if entry.get("signal_date_close") is None:
            entry["signal_date_close"] = price

        # 首次進入 active：記錄代理進場價、日期，並初始化風控欄位（DD-12、DD-13、DD-19）
        if new_status == "active" and prev_status == "watch":
            # 盤中限價單模擬進場（DD-19）：以買入區間上緣（使用者實際掛單價位）
            # 作為進場代理價，取代原本的收盤價。settlement_entry 的 buy_zone_upper
            # 拆股情境下已正確縮放（DD-3），與 active_entry_price 既有慣例
            # （皆以「當下現值標尺」存儲，非原始拆股前標尺）一致。
            entry_fill_price = settlement_entry["buy_zone_upper"]
            if entry.get("active_entry_price") is None:
                entry["active_entry_price"] = entry_fill_price
                entry["active_start_date"]  = today
            if entry.get("planned_stop_loss") is None:
                planned_val = _parse_stop_loss(entry.get("stop_loss", "-"))
                entry["planned_stop_loss"]   = planned_val
                entry["effective_stop_loss"] = planned_val
            entry.setdefault("is_breakeven_locked", False)
            if entry.get("highest_close_since_active") is None:
                # 以原生標尺存儲（Fix #3）
                entry["highest_close_since_active"] = price

            # Fix #1：同步寫入 settlement_entry（adj），防止開倉當日停損時 entry_price=0 除零
            if settlement_entry is not entry:
                settlement_entry["active_entry_price"]         = entry_fill_price
                settlement_entry["planned_stop_loss"]          = (entry["planned_stop_loss"] or 0) * split_factor
                settlement_entry["effective_stop_loss"]        = settlement_entry["planned_stop_loss"]
                settlement_entry["is_breakeven_locked"]        = False
                settlement_entry["highest_close_since_active"] = price
            else:
                settlement_entry["active_entry_price"]         = entry_fill_price
                settlement_entry["planned_stop_loss"]          = entry["planned_stop_loss"]
                settlement_entry["effective_stop_loss"]        = entry["effective_stop_loss"]
                settlement_entry["is_breakeven_locked"]        = False
                settlement_entry["highest_close_since_active"] = price

        # 計數器遞增（直接寫入 entry，確保被 JSON 序列化）
        # 同日重跑（already_tracked_today=True）不重複遞增（DD-18）
        if not already_tracked_today:
            if new_status == "watch":
                entry["watch_days"] = entry.get("watch_days", 0) + 1
            elif new_status == "active":
                entry["active_days"] = entry.get("active_days", 0) + 1

        # 風控更新：保本鎖定 + 最高收盤追蹤（DD-12、DD-13）；僅持續 active 狀態執行
        if prev_status == "active" and new_status == "active":
            _apply_risk_controls(settlement_entry, price, split_factor, entry)

        # 結算檢查：盤中 High/Low 實質觸價 + 移動停利（DD-10、DD-13）
        settlement = _check_settlement(settlement_entry, price, today_high, today_low)
        if settlement:
            exit_reason, exit_price = settlement
            _archive_to_performance_history(entry, exit_reason, exit_price, today)
            entry["_settled"]     = True
            entry["_exit_reason"] = exit_reason   # 供 publisher 渲染今日結算區段
            entry["_exit_price"]  = exit_price
            settled_entries.append(entry)

    # ── B/C（後移）. 處理今日 L3 新訊號（雙軌分流，DD-9/DD-11）──────
    # 重建 existing，反映 E 後的最新狀態（含 status 變化）
    existing = {e["symbol"]: e for e in watchlist}
    reset_symbols: set[str] = set()
    new_entries: list[dict] = []
    reset_entries: list[dict] = []

    for stock in new_ranked:
        sym = stock["symbol"]
        confidence = stock.get("confidence") or 0
        if confidence < MIN_AI_CONFIDENCE:
            print(f"[tracker] {sym} AI 信心分數 {confidence} < {MIN_AI_CONFIDENCE}，跳過")
            continue
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
            # 訊號日收盤價（DD-17）：直接取自 L2 訊號日資料（stock["price"]），
            # 不得延遲到首個評估日才寫入，否則與 _calc_split_factor 的
            # tracked_dates[0] 錨定日錯位，日漲跌超過 ±1% 即誤判拆股。
            "signal_date_close":  stock.get("price"),
            # ── 進場追蹤 ──
            "active_entry_price": None,
            "active_start_date":  None,
            # ── 日期錨定（DD-11）──
            "date_added":         today,
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
            if existing[sym].get("status") == "active":
                # active 持倉再入選：訊號免疫，跳過重置（DD-9）
                print(f"[tracker] {sym} 已持倉（active），跳過重置，沿用原交易計劃")
            else:
                # watch / invalid：訊號覆寫展期，重置觀察期與 AI 參數
                existing[sym].update(base)
                reset_symbols.add(sym)
                reset_entries.append(stock)
        else:
            # 全新個股：加入 watchlist，本輪不評估（1-day lag 天然實現）
            watchlist.append({
                "symbol": sym,
                "name":   stock.get("name", sym),
                "sector": stock.get("sector", "Unknown"),
                **base,
            })
            new_entries.append(stock)

    # ── F. 分類（移除前快照）────────────────────────────────────────
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

    # ── G. 移除已到期與已結算 ────────────────────────────────────────
    watchlist = [
        e for e in watchlist
        if not _is_expired(e) and not e.get("_settled")
    ]

    # ── H. 儲存 ──────────────────────────────────────────────────────
    save_watchlist(watchlist)
    print(f"[tracker] watchlist 更新完成，保留 {len(watchlist)} 筆"
          f"（結算歸檔 {len(settled_entries)} 筆）")

    return watchlist, categories
