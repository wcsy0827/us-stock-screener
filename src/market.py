"""抓取大盤狀態（S&P 500、VIX）與產業龍頭 ETF 走勢，提供給 AI 做市場背景判斷。"""

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


def fetch_market_context(candidate_sectors: set[str] | None = None) -> dict:
    """
    抓取大盤 + 相關產業 ETF 走勢。

    candidate_sectors: 候選股涵蓋的產業集合，只抓相關 ETF；
                       傳 None 則抓全部 11 個產業 ETF。
    回傳結構：
      {
        "sp500": {...},
        "vix":   {"value": 18.5, "label": "正常"},
        "sectors": {"Technology": {...}, ...},
      }
    """
    # 決定要抓哪些 sector ETF
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

    # VIX
    vix_df = _get("^VIX")
    if not vix_df.empty:
        vix_close = vix_df["Close"].dropna()
        if not vix_close.empty:
            v = float(vix_close.iloc[-1])
            vix_5d_ago = float(vix_close.iloc[-5]) if len(vix_close) >= 5 else v
            context["vix"] = {
                "value": round(v, 2),
                "change_5d": round(v - vix_5d_ago, 2),
                "label": _vix_label(v),
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
    return context
