"""抓取大盤狀態（S&P 500、VIX）與產業龍頭 ETF 走勢，計算市場廣度並判定市場環境 Regime。"""

from __future__ import annotations

import json
import os

import pandas as pd
import yfinance as yf


BREADTH_SMOOTHING_DAYS = 3  # Regime 廣度平滑窗口（specs/market.md DD-3）

# 產業 → 代表性 ETF
SECTOR_ETF_MAP: dict[str, str] = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}

# 廣度計算排除的 tickers（DD-5）：板塊 ETF + SPY 不計入 S&P 500 廣度分母
_BREADTH_EXCLUDED: frozenset[str] = frozenset(SECTOR_ETF_MAP.values()) | {"SPY"}


def _ema(series: pd.Series, span: int) -> float:
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = (100 - 100 / (1 + rs)).dropna()
    return float(rsi.iloc[-1]) if not rsi.empty else float("nan")


def _vix_label(vix: float) -> str:
    if vix < 15:
        return "低恐慌（市場樂觀）"
    if vix < 20:
        return "正常"
    if vix < 25:
        return "輕微恐慌"
    if vix < 30:
        return "中度恐慌"
    return "高度恐慌（避險情緒濃厚）"


def _trend_label(chg_5d: float) -> str:
    if chg_5d > 1.0:
        return "強勢上漲"
    if chg_5d > 0.3:
        return "溫和上漲"
    if chg_5d > -0.3:
        return "盤整"
    if chg_5d > -1.0:
        return "溫和下跌"
    return "明顯下跌"


def _analyze(df: pd.DataFrame) -> dict:
    """從 OHLCV DataFrame 計算走勢摘要。"""
    close = df["Close"].dropna()
    if len(close) < 5:
        return {}

    price = float(close.iloc[-1])
    chg_5d = (price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100 if len(close) >= 5 else 0.0
    chg_20d = (price - float(close.iloc[-20])) / float(close.iloc[-20]) * 100 if len(close) >= 20 else 0.0

    rsi_val = _rsi(close) if len(close) >= 14 else float("nan")
    ema20_val = _ema(close, 20) if len(close) >= 20 else float("nan")
    ema50_val = _ema(close, 50) if len(close) >= 50 else float("nan")

    result: dict = {
        "price": round(price, 2),
        "change_5d_pct": round(chg_5d, 2),
        "change_20d_pct": round(chg_20d, 2),
        "trend_5d": _trend_label(chg_5d),
    }
    if not pd.isna(rsi_val):
        result["rsi"] = round(rsi_val, 2)
    if not pd.isna(ema20_val):
        result["above_ema20"] = price > ema20_val
    if not pd.isna(ema50_val):
        result["above_ema50"] = price > ema50_val

    return result


# ── 市場廣度計算 ─────────────────────────────────────────────────────

def calculate_market_breadth(
    all_stocks_data: dict,
    smoothing_days: int = BREADTH_SMOOTHING_DAYS,
) -> float:
    """
    計算市場廣度（收盤 > 50 SMA 比例），回傳近 smoothing_days 日算術平均（DD-3）。
    以現有 price_data 歷史切片模擬各日視角，無額外 API 請求。
    """
    def _breadth_for_offset(offset: int) -> float | None:
        above, total = 0, 0
        for sym, df in all_stocks_data.items():
            if sym in _BREADTH_EXCLUDED:  # 板塊 ETF 及 SPY 不計入廣度（DD-5）
                continue
            close = df["Close"].dropna()
            if len(close) < 50 + offset:
                continue
            effective = close.iloc[: len(close) - offset] if offset else close
            sma50 = float(effective.tail(50).mean())
            total += 1
            if float(effective.iloc[-1]) > sma50:
                above += 1
        if total == 0:
            return None
        pct = round(above / total * 100, 1)
        if offset == 0:
            print(f"[market] 市場廣度：{above}/{total} 支股票站上50SMA = {pct}%（今日）")
        return pct

    values = []
    for d in range(smoothing_days):
        v = _breadth_for_offset(d)
        if v is not None:
            values.append(v)

    if not values:
        return 50.0
    avg = round(sum(values) / len(values), 1)
    if smoothing_days > 1:
        print(f"[market] 市場廣度 {smoothing_days}日均：{avg}%（用於 Regime 判定）")
    return avg


# ── 市場環境狀態機 ───────────────────────────────────────────────────

def determine_market_regime(breadth_pct: float, vix_value: float) -> dict:
    """
    根據市場廣度與 VIX 判定市場環境模式（Regime）。
    回傳含 regime、primary_strategy、ai_prompt_hint 的字典。

    五象限分類矩陣（DD-4）：
      breadth >= 60% + VIX < 20          → BULL_TREND               → 動能策略
      breadth 35~60% + VIX < 20          → CONSOLIDATION            → 突破策略（積極）
      breadth 35~60% + VIX >= 20         → CONSOLIDATION_VOLATILE   → 突破策略（保守）
      breadth < 35% + VIX >= 25          → PANIC_REVERSAL           → 反轉策略
      breadth < 35% + VIX < 25           → BEAR_DISTRIBUTION        → 全面防禦
    """
    if breadth_pct >= 60 and vix_value < 20:
        return {
            "regime": "BULL_TREND",
            "primary_strategy": "動能策略",
            "ai_prompt_hint": (
                f"目前大盤環境為【強勢牛市】，市場廣度極佳（{breadth_pct}% 股票站上50SMA），"
                f"整體結構健康。請嚴格執行【動能策略】，優先選擇板塊領頭羊與均線多頭排列之強勢標的，"
                f"忽略左側反轉訊號。"
            ),
        }
    elif breadth_pct >= 35 and vix_value < 20:
        return {
            "regime": "CONSOLIDATION",
            "primary_strategy": "突破策略（積極）",
            "ai_prompt_hint": (
                f"目前大盤環境為【震盪整理】，市場廣度中性（{breadth_pct}% 股票站上50SMA），"
                f"VIX={vix_value:.1f} 波動正常。請執行【突破策略（積極型）】，"
                f"優先選帶量突破關鍵壓力位的個股，確認訊號後積極進場。"
            ),
        }
    elif breadth_pct >= 35:
        # 廣度 35~60% 且 VIX >= 20 → 高波動整理期，策略保守（DD-4）
        return {
            "regime": "CONSOLIDATION_VOLATILE",
            "primary_strategy": "突破策略（保守）",
            "ai_prompt_hint": (
                f"目前大盤環境為【高波動整理】，市場廣度中性（{breadth_pct}% 股票站上50SMA），"
                f"VIX={vix_value:.1f} 波動偏高。請執行【突破策略（保守型）】，"
                f"要求 VTF_Score >= 2.0、MACD POS_INC 且 RSI 50~65 才考慮進場；"
                f"訊號不明確一律跳過。"
            ),
        }
    elif vix_value >= 25:
        return {
            "regime": "PANIC_REVERSAL",
            "primary_strategy": "反轉策略",
            "ai_prompt_hint": (
                f"目前大盤環境為【恐慌超跌】，市場廣度偏低（{breadth_pct}% 股票站上50SMA），"
                f"VIX={vix_value:.1f} 恐慌情緒高。請執行【反轉策略】，尋找非理性殺低、"
                f"靠近長期支撐且出現底背離訊號的個股，嚴設止損，控制倉位。"
            ),
        }
    else:
        return {
            "regime": "BEAR_DISTRIBUTION",
            "primary_strategy": "",
            "ai_prompt_hint": (
                f"目前大盤環境為【陰跌熊市】，市場廣度極低（{breadth_pct}% 股票站上50SMA），"
                f"VIX={vix_value:.1f}。風險極高，系統啟動全面防禦，"
                f"禁止建立新倉位，請勿輸出任何買入建議，直接回傳空的 selections 陣列。"
            ),
        }


# ── 輕量 Regime 快速判定（L2 評分前使用）────────────────────────────

_HIGH_VIX_REGIMES = frozenset({"PANIC_REVERSAL", "CONSOLIDATION_VOLATILE"})


def fetch_regime_quick(
    all_stocks_data: dict,
    last_run_path: str = "docs/data/last_run.json",
) -> tuple[str, float, float, bool]:
    """
    快速判定大盤 Regime，只下載 VIX，搭配已有 price_data 計算廣度。
    回傳 (regime, breadth_pct, vix_value, vix_ok)。
    vix_ok=False 表示下載失敗，pipeline 應在 L3 前中斷。
    在 pipeline Step 2.5 呼叫，比 fetch_market_context 早執行，
    讓 scorer 能根據 regime 動態調整 L2 門檻。
    廣度邊界附近啟用遲滯帶，防止 Regime 每日翻轉（DD-5）。
    """
    # 1. 從 last_run.json 讀取前一日 Regime（嚴格校驗 market_date，DD-5）
    prev_regime: str | None = None
    try:
        if os.path.exists(last_run_path):
            with open(last_run_path, "r", encoding="utf-8") as f:
                last = json.load(f)
            last_market_date = last.get("market_date", "")
            spy_df = all_stocks_data.get("SPY")
            current_market_date = str(spy_df.index[-1].date()) if spy_df is not None and not spy_df.empty else ""
            if last_market_date and current_market_date and last_market_date < current_market_date:
                prev_regime = last.get("regime")
    except Exception as e:
        print(f"[market] 讀取 last_run.json 失敗，不啟用遲滯帶：{e}")

    # 2. 計算廣度
    breadth_pct = calculate_market_breadth(all_stocks_data)

    # 3. 下載 VIX
    vix_value = 20.0
    vix_ok = False
    try:
        raw = yf.download(
            "^VIX", period="5d", interval="1d",
            auto_adjust=True, progress=False,
        )
        close_col = raw["Close"] if "Close" in raw.columns else pd.Series(dtype=float)
        # yfinance 單 ticker 下載有時回傳單欄 DataFrame，squeeze() 統一轉為 Series
        close = (close_col.squeeze() if isinstance(close_col, pd.DataFrame) else close_col).dropna()
        if not close.empty:
            vix_value = float(close.iloc[-1])
            vix_ok = True
    except Exception as e:
        print(f"[market] fetch_regime_quick VIX 下載失敗，使用預設值 20.0：{e}")

    # 4. 計算基本 Regime
    regime_dict = determine_market_regime(breadth_pct, vix_value)
    new_regime = regime_dict["regime"]

    # 5. 遲滯帶：廣度在邊界 ±2% 且 VIX 未跨越結構邊界時，維持前日 Regime（DD-5）
    HYSTERESIS = 2.0
    if prev_regime:
        old_vix_high = prev_regime in _HIGH_VIX_REGIMES
        new_vix_high = new_regime in _HIGH_VIX_REGIMES
        vix_changed_structure = (old_vix_high != new_vix_high)

        if not vix_changed_structure:
            near_bull  = abs(breadth_pct - 60.0) <= HYSTERESIS
            near_panic = abs(breadth_pct - 35.0) <= HYSTERESIS
            if near_bull or near_panic:
                print(f"[market] 遲滯帶生效：廣度={breadth_pct}% 在邊界附近，維持前日 Regime={prev_regime}")
                new_regime = prev_regime

    vix_status = f"VIX={vix_value:.1f}" if vix_ok else f"VIX=20.0（fallback）"
    print(f"[market] 快速 Regime：{new_regime}（廣度={breadth_pct}%，{vix_status}）")
    return new_regime, breadth_pct, vix_value, vix_ok


# ── 主函式 ───────────────────────────────────────────────────────────

def fetch_market_context(
    candidate_sectors: set[str] | None = None,
    all_stocks_data: dict | None = None,
    breadth_pct: float | None = None,
    vix_value: float | None = None,
) -> dict:
    """
    抓取大盤 + 相關產業 ETF 走勢，計算市場廣度並判定市場環境 Regime。

    candidate_sectors: 候選股涵蓋的產業集合，只抓相關 ETF；傳 None 則抓全部 11 個。
    all_stocks_data:   全市場日 K 字典，僅在 breadth_pct 未提供時才重算廣度。
    breadth_pct:       Step 2.5 已計算的廣度，有值時直接複用，不重跑 O(n) 迴圈。
    vix_value:         Step 2.5 已下載的 VIX，有值時跳過重複下載。
    回傳結構：
      {
        "sp500": {...},
        "vix":   {"value": 18.5, "label": "正常"},
        "sectors": {"Technology": {...}, ...},
        "market_breadth_pct": 68.5,
        "regime": "BULL_TREND",
        "primary_strategy": "動能策略",
        "ai_prompt_hint": "...",
      }
    """
    sectors_to_fetch = {
        sector: etf
        for sector, etf in SECTOR_ETF_MAP.items()
        if candidate_sectors is None or sector in candidate_sectors
    }

    all_tickers = ["SPY", "^VIX"] + list(sectors_to_fetch.values())

    try:
        raw = yf.download(
            tickers=all_tickers,
            period="60d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"[market] 大盤數據下載失敗：{e}")
        return {}

    def _get(ticker: str) -> pd.DataFrame:
        try:
            df = raw[ticker] if len(all_tickers) > 1 else raw
            return df.dropna(how="all")
        except Exception:
            return pd.DataFrame()

    context: dict = {}

    # S&P 500
    spy = _analyze(_get("SPY"))
    if spy:
        context["sp500"] = spy

    # VIX（Step 2.5 已提供則直接複用，避免重複下載）
    vix_final = vix_value if vix_value is not None else 20.0
    vix_df = _get("^VIX")
    if not vix_df.empty:
        vix_close = vix_df["Close"].dropna()
        if not vix_close.empty:
            vix_5d_ago = float(vix_close.iloc[-5]) if len(vix_close) >= 5 else float(vix_close.iloc[-1])
            # 若 Step 2.5 未提供 VIX，才從下載資料中取值
            if vix_value is None:
                vix_final = float(vix_close.iloc[-1])
            context["vix"] = {
                "value": round(vix_final, 2),
                "change_5d": round(vix_final - vix_5d_ago, 2),
                "label": _vix_label(vix_final),
            }
    elif vix_value is not None:
        # 下載失敗但 Step 2.5 有值，仍填入 vix 區塊供 AI Prompt 使用
        context["vix"] = {
            "value": round(vix_final, 2),
            "change_5d": 0.0,
            "label": _vix_label(vix_final),
        }

    # 產業 ETF
    context["sectors"] = {}
    for sector, etf in sectors_to_fetch.items():
        data = _analyze(_get(etf))
        if data:
            context["sectors"][sector] = {**data, "etf": etf}

    ok_sectors = len(context.get("sectors", {}))
    print(
        f"[market] 大盤：SPY={'ok' if 'sp500' in context else 'fail'}，"
        f"VIX={'ok' if 'vix' in context else 'fail'}，"
        f"產業ETF={ok_sectors}個"
    )

    # 市場廣度計算 + Regime 判定（Step 2.5 已計算則直接複用，不重跑）
    if breadth_pct is not None or all_stocks_data:
        try:
            effective_breadth = (
                breadth_pct if breadth_pct is not None
                else calculate_market_breadth(all_stocks_data)
            )
            regime_info = determine_market_regime(effective_breadth, vix_final)
            context["market_breadth_pct"] = effective_breadth
            context.update(regime_info)
            source = "複用Step2.5" if breadth_pct is not None else "重新計算"
            print(f"[market] Regime 判定（{source}）：{regime_info['regime']}，主推策略：{regime_info['primary_strategy'] or '全面防禦'}")
        except Exception as e:
            print(f"[market] 警告：市場廣度計算失敗：{e}")

    return context
