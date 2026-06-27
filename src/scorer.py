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


def _is_oversold_reversal_candidate(sym: str, df: pd.DataFrame) -> bool:
    """判斷是否為超賣反轉候選：RSI < 35 且 20 日負乖離超過 15%。
    PANIC_REVERSAL 環境下強制放行此類股票進入 L3，不受分數門檻限制。
    """
    close = df["Close"].dropna()
    if len(close) < 20:
        return False
    rsi_val = _calc_rsi(close)
    if pd.isna(rsi_val) or rsi_val >= 35:
        return False
    p20d = float(close.iloc[-20])
    if p20d == 0:
        return False
    dev_20d = (float(close.iloc[-1]) - p20d) / p20d * 100
    return dev_20d <= -15.0


def score_stock(sym: str, df: pd.DataFrame) -> dict:
    """計算單支股票技術指標評分，回傳含各項分數與總分的字典。"""
    close = df["Close"].dropna()
    latest_close = float(close.iloc[-1]) if len(close) > 0 else 0.0

    rsi_val = _calc_rsi(close)

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
    regime: str = "",
) -> list[dict]:
    """對所有通過 L1 的股票評分，回傳候選股，依總分降序排列。

    PANIC_REVERSAL 環境下兩層放行：
    1. 動態門檻降至 40 分（讓輕度超跌股進入）
    2. 強制放行 RSI < 35 + 20 日跌幅 > 15% 的超賣反轉股（得分通常 < 20 分但正是目標標的）
    """
    results = [
        score_stock(sym, price_data[sym])
        for sym in symbols
        if sym in price_data and len(price_data[sym]) >= 20
    ]
    effective_min = 40.0 if regime == "PANIC_REVERSAL" else min_score

    force_pass: set[str] = set()
    if regime == "PANIC_REVERSAL":
        for sym in symbols:
            if sym in price_data and _is_oversold_reversal_candidate(sym, price_data[sym]):
                force_pass.add(sym)
        if force_pass:
            print(f"[scorer] PANIC_REVERSAL 強制放行 {len(force_pass)} 支超賣反轉候選股")

    candidates = sorted(
        [r for r in results if r["total_score"] >= effective_min or r["symbol"] in force_pass],
        key=lambda x: x["total_score"],
        reverse=True,
    )
    suffix = (
        f"（PANIC_REVERSAL，門檻 {effective_min:.0f} 分 + 強制放行 {len(force_pass)} 支）"
        if regime == "PANIC_REVERSAL" else ""
    )
    print(f"[scorer] L2 評分：{len(symbols)} 支 → {len(candidates)} 支進入 L3{suffix}")
    return candidates
