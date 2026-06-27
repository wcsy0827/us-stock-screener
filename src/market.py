"""抓取大盤狀態（S&P 500、VIX）與產業龍頭 ETF 走勢，計算市場廣度並判定市場環境 Regime。"""

from __future__ import annotations

import pandas as pd
import yfinance as yf


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

def calculate_market_breadth(all_stocks_data: dict) -> float:
    """
    計算市場廣度：收盤價高於 50 SMA 的股票比例（%）。
    排除歷史 K 線不足 50 根的股票，確保 50 SMA 計算精準。
    回傳 0~100 的百分比值。
    """
    above = 0
    total = 0
    for sym, df in all_stocks_data.items():
        close = df["Close"].dropna()
        if len(close) < 50:
            continue
        sma50 = float(close.tail(50).mean())
        total += 1
        if float(close.iloc[-1]) > sma50:
            above += 1
    if total == 0:
        return 50.0  # 無法計算時回傳中性值
    pct = round(above / total * 100, 1)
    print(f"[market] 市場廣度：{above}/{total} 支股票站上50SMA = {pct}%")
    return pct


# ── 市場環境狀態機 ───────────────────────────────────────────────────

def determine_market_regime(breadth_pct: float, vix_value: float) -> dict:
    """
    根據市場廣度與 VIX 判定市場環境模式（Regime）。
    回傳含 regime、primary_strategy、ai_prompt_hint 的字典。

    分類矩陣：
      breadth >= 60% + VIX < 20  → BULL_TREND       → 動能策略
      breadth 35~60%（任何 VIX） → CONSOLIDATION    → 突破策略
      breadth < 35% + VIX >= 25  → PANIC_REVERSAL   → 反轉策略
      breadth < 35% + VIX < 25   → BEAR_DISTRIBUTION → 全面防禦
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
    elif breadth_pct >= 35:
        return {
            "regime": "CONSOLIDATION",
            "primary_strategy": "突破策略",
            "ai_prompt_hint": (
                f"目前大盤環境為【震盪整理】，市場廣度中性（{breadth_pct}% 股票站上50SMA），"
                f"走勢不明確。請嚴格執行【突破策略】，只選帶量突破關鍵壓力位的個股，"
                f"嚴防假突破，等確認訊號再進場。"
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

def fetch_regime_quick(all_stocks_data: dict) -> tuple[str, float, float]:
    """
    快速判定大盤 Regime，只下載 VIX，搭配已有 price_data 計算廣度。
    回傳 (regime, breadth_pct, vix_value)。
    在 pipeline Step 2.5 呼叫，比 fetch_market_context 早執行，
    讓 scorer 能根據 regime 動態調整 L2 門檻。
    """
    breadth_pct = calculate_market_breadth(all_stocks_data)
    vix_value = 20.0  # 下載失敗時使用中性值
    try:
        raw = yf.download(
            "^VIX", period="5d", interval="1d",
            auto_adjust=True, progress=False,
        )
        close = raw["Close"].dropna() if "Close" in raw.columns else pd.Series(dtype=float)
        if not close.empty:
            vix_value = float(close.iloc[-1])
    except Exception as e:
        print(f"[market] fetch_regime_quick VIX 下載失敗，使用預設值 20.0：{e}")
    regime_dict = determine_market_regime(breadth_pct, vix_value)
    regime = regime_dict["regime"]
    print(f"[market] 快速 Regime：{regime}（廣度={breadth_pct}%，VIX={vix_value:.1f}）")
    return regime, breadth_pct, vix_value


# ── 主函式 ───────────────────────────────────────────────────────────

def fetch_market_context(
    candidate_sectors: set[str] | None = None,
    all_stocks_data: dict | None = None,
) -> dict:
    """
    抓取大盤 + 相關產業 ETF 走勢，計算市場廣度並判定市場環境 Regime。

    candidate_sectors: 候選股涵蓋的產業集合，只抓相關 ETF；
                       傳 None 則抓全部 11 個產業 ETF。
    all_stocks_data:   fetcher 已下載的全市場日 K 字典，用於計算市場廣度（50SMA）；
                       傳 None 則略過廣度計算，不填入 regime 欄位。
    回傳結構：
      {
        "sp500": {...},
        "vix":   {"value": 18.5, "label": "正常"},
        "sectors": {"Technology": {...}, ...},
        "market_breadth_pct": 68.5,          # 僅在 all_stocks_data 有值時存在
        "regime": "BULL_TREND",              # 同上
        "primary_strategy": "動能策略",       # 同上
        "ai_prompt_hint": "...",             # 同上
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

    # VIX（同時取得數值供 Regime 判斷使用）
    vix_value = 20.0  # 無法取得時的中性 fallback
    vix_df = _get("^VIX")
    if not vix_df.empty:
        vix_close = vix_df["Close"].dropna()
        if not vix_close.empty:
            vix_value = float(vix_close.iloc[-1])
            vix_5d_ago = float(vix_close.iloc[-5]) if len(vix_close) >= 5 else vix_value
            context["vix"] = {
                "value": round(vix_value, 2),
                "change_5d": round(vix_value - vix_5d_ago, 2),
                "label": _vix_label(vix_value),
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

    # 市場廣度計算 + Regime 判定（需要全體個股日 K）
    if all_stocks_data:
        try:
            breadth_pct = calculate_market_breadth(all_stocks_data)
            regime_info = determine_market_regime(breadth_pct, vix_value)
            context["market_breadth_pct"] = breadth_pct
            context.update(regime_info)
            print(f"[market] Regime 判定：{regime_info['regime']}，主推策略：{regime_info['primary_strategy'] or '全面防禦'}")
        except Exception as e:
            print(f"[market] 警告：市場廣度計算失敗：{e}")

    return context
