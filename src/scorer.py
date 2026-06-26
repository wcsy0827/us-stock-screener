"""L2 技術指標評分系統（滿分 100 分）。"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta


# ── 各項最高分 ──────────────────────────────────────────────────
WEIGHT_MA = 25       # 均線多頭排列
WEIGHT_RSI = 20      # RSI 健康區間
WEIGHT_MACD = 20     # MACD 柱狀體為正且遞增
WEIGHT_VOLUME = 20   # 量能放大
WEIGHT_MOMENTUM = 15 # 價格動能（20日漲幅）


def _score_ma(close: pd.Series) -> float:
    """均線多頭排列 EMA5 > EMA10 > EMA20 > EMA50，完全符合得 25 分，部分符合比例給分。"""
    if len(close) < 50:
        return 0.0
    e5 = float(ta.ema(close, length=5).iloc[-1])
    e10 = float(ta.ema(close, length=10).iloc[-1])
    e20 = float(ta.ema(close, length=20).iloc[-1])
    e50 = float(ta.ema(close, length=50).iloc[-1])
    if any(pd.isna(v) for v in [e5, e10, e20, e50]):
        return 0.0
    conditions = [e5 > e10, e10 > e20, e20 > e50]
    return round(WEIGHT_MA * sum(conditions) / len(conditions), 2)


def _calc_rsi(close: pd.Series) -> float:
    """回傳 RSI 原始數值，供評分與硬條件判斷共用。"""
    if len(close) < 14:
        return float("nan")
    rsi_s = ta.rsi(close, length=14)
    if rsi_s is None or rsi_s.dropna().empty:
        return float("nan")
    return float(rsi_s.dropna().iloc[-1])


def _score_rsi(rsi: float) -> float:
    """RSI 50~70 健康多頭區間得滿分；40~50 或 70~80 各得一半；其餘（含 >80 超買）0 分。"""
    if pd.isna(rsi):
        return 0.0
    if 50 <= rsi <= 70:
        return float(WEIGHT_RSI)
    if (40 <= rsi < 50) or (70 < rsi <= 80):
        return float(WEIGHT_RSI * 0.5)
    return 0.0


def _score_macd(close: pd.Series) -> float:
    """MACD histogram 為正且遞增得滿分；僅為正得一半；其餘 0 分。"""
    if len(close) < 35:
        return 0.0
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        return 0.0
    hist_col = [c for c in macd_df.columns if "h" in c.lower()]
    if not hist_col:
        return 0.0
    hist = macd_df[hist_col[0]].dropna()
    if len(hist) < 2:
        return 0.0
    last, prev = float(hist.iloc[-1]), float(hist.iloc[-2])
    if last > 0 and last > prev:
        return float(WEIGHT_MACD)
    if last > 0:
        return float(WEIGHT_MACD * 0.5)
    return 0.0


def _score_volume(df: pd.DataFrame) -> float:
    """今日量 > 30日均量 × 1.5 得滿分；> 均量得一半；其餘 0 分。"""
    vol = df["Volume"].dropna()
    if len(vol) < 5:
        return 0.0
    avg30 = float(vol.tail(30).mean()) if len(vol) >= 30 else float(vol.mean())
    today = float(vol.iloc[-1])
    if avg30 == 0:
        return 0.0
    ratio = today / avg30
    if ratio >= 1.5:
        return float(WEIGHT_VOLUME)
    if ratio >= 1.0:
        return float(WEIGHT_VOLUME * 0.5)
    return 0.0


def _score_momentum(close: pd.Series) -> float:
    """20日漲幅 > 10% 得滿分；> 5% 得一半；> 0% 得 1/4；其餘 0 分。"""
    if len(close) < 20:
        return 0.0
    p0, p1 = float(close.iloc[-20]), float(close.iloc[-1])
    if p0 == 0:
        return 0.0
    chg = (p1 - p0) / p0
    if chg >= 0.10:
        return float(WEIGHT_MOMENTUM)
    if chg >= 0.05:
        return float(WEIGHT_MOMENTUM * 0.5)
    if chg > 0:
        return float(WEIGHT_MOMENTUM * 0.25)
    return 0.0


RSI_OVERBOUGHT = 80  # 超過此值直接排除


def score_stock(sym: str, df: pd.DataFrame) -> dict:
    """計算單支股票技術指標評分，回傳含各項分數與總分的字典。"""
    close = df["Close"].dropna()
    latest_close = float(close.iloc[-1]) if len(close) > 0 else 0.0

    rsi_val = _calc_rsi(close)

    # 硬條件：RSI > 80 超買，直接給 0 分排除出 L3
    if not pd.isna(rsi_val) and rsi_val > RSI_OVERBOUGHT:
        return {
            "symbol": sym,
            "price": latest_close,
            "total_score": 0.0,
            "ma_score": 0.0,
            "rsi_score": 0.0,
            "macd_score": 0.0,
            "volume_score": 0.0,
            "momentum_score": 0.0,
        }

    ma = _score_ma(close)
    rsi = _score_rsi(rsi_val)
    macd = _score_macd(close)
    vol = _score_volume(df)
    mom = _score_momentum(close)
    return {
        "symbol": sym,
        "price": latest_close,
        "total_score": round(ma + rsi + macd + vol + mom, 2),
        "ma_score": ma,
        "rsi_score": rsi,
        "macd_score": macd,
        "volume_score": vol,
        "momentum_score": mom,
    }


def score_all(
    symbols: list[str],
    price_data: dict[str, pd.DataFrame],
    min_score: float = 60.0,
) -> list[dict]:
    """對所有通過 L1 的股票評分，回傳 >= min_score 候選股，依總分降序排列。"""
    results = [
        score_stock(sym, price_data[sym])
        for sym in symbols
        if sym in price_data and len(price_data[sym]) >= 20
    ]
    candidates = sorted(
        [r for r in results if r["total_score"] >= min_score],
        key=lambda x: x["total_score"],
        reverse=True,
    )
    print(f"[scorer] L2 評分：{len(symbols)} 支 → {len(candidates)} 支 >= {min_score} 分")
    return candidates
