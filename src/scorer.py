"""L2 技術指標評分系統（滿分 100 分）。"""

from __future__ import annotations

import pandas as pd


# ── 各項最高分 ──────────────────────────────────────────────────
WEIGHT_MA = 25       # 均線多頭排列
WEIGHT_RSI = 20      # RSI 健康區間
WEIGHT_MACD = 20     # MACD 柱狀體為正且遞增
WEIGHT_VOLUME = 20   # 量能放大
WEIGHT_MOMENTUM = 15 # 價格動能（20日漲幅）


# ── 純 pandas 指標計算（不依賴 pandas-ta / numba）──────────────

def _ema(series: pd.Series, span: int) -> float:
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def _calc_rsi(close: pd.Series, length: int = 14) -> float:
    """Wilder 平滑 RSI，回傳原始數值供評分與硬條件判斷共用。"""
    if len(close) < length:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = (100 - 100 / (1 + rs)).dropna()
    return float(rsi.iloc[-1]) if not rsi.empty else float("nan")


def _macd_histogram(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return (macd_line - signal_line).dropna()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> float:
    """Wilder 平滑 ATR（Average True Range）。"""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_series = tr.ewm(alpha=1 / length, adjust=False).mean().dropna()
    return float(atr_series.iloc[-1]) if not atr_series.empty else float("nan")


# ── 各項評分函式 ────────────────────────────────────────────────

def _score_ma(close: pd.Series) -> float:
    """均線多頭排列 EMA5 > EMA10 > EMA20 > EMA50，完全符合得 25 分，部分符合比例給分。"""
    if len(close) < 50:
        return 0.0
    e5, e10, e20, e50 = _ema(close, 5), _ema(close, 10), _ema(close, 20), _ema(close, 50)
    if any(pd.isna(v) for v in [e5, e10, e20, e50]):
        return 0.0
    conditions = [e5 > e10, e10 > e20, e20 > e50]
    return round(WEIGHT_MA * sum(conditions) / len(conditions), 2)


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
    hist = _macd_histogram(close)
    if len(hist) < 2:
        return 0.0
    last, prev = float(hist.iloc[-1]), float(hist.iloc[-2])
    if last > 0 and last > prev:
        return float(WEIGHT_MACD)
    if last > 0:
        return float(WEIGHT_MACD * 0.5)
    return 0.0


def _score_volume(df: pd.DataFrame) -> float:
    """量能 × K 線位置綁定：爆量但 K_pos < 0.6（出貨訊號）直接歸零。"""
    vol = df["Volume"].dropna()
    if len(vol) < 5:
        return 0.0
    avg30 = float(vol.tail(30).mean()) if len(vol) >= 30 else float(vol.mean())
    today_vol = float(vol.iloc[-1])
    if avg30 == 0:
        return 0.0
    ratio = today_vol / avg30

    close = df["Close"].dropna()
    high = df["High"].dropna()
    low = df["Low"].dropna()
    if len(close) < 1 or len(high) < 1 or len(low) < 1:
        return 0.0
    c, h, l = float(close.iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1])
    # K_pos = 0.5 防除零（High == Low 代表無波動，視為中性）
    k_pos = 0.5 if h == l else (c - l) / (h - l)

    if ratio >= 1.5:
        return float(WEIGHT_VOLUME) if k_pos >= 0.6 else 0.0
    if ratio >= 1.0:
        return float(WEIGHT_VOLUME * 0.5) if k_pos >= 0.6 else 0.0
    return 0.0


def _score_momentum(df: pd.DataFrame) -> float:
    """ATR 倍數動能：(Close[-1]-Close[-20])/ATR14，≥2ATR=滿分；≥1ATR=半分；>0=1/4；其餘0。"""
    close = df["Close"].dropna()
    high = df["High"].dropna()
    low = df["Low"].dropna()
    if len(close) < 20 or len(high) < 15 or len(low) < 15:
        return 0.0
    p0, p1 = float(close.iloc[-20]), float(close.iloc[-1])
    if p0 == 0:
        return 0.0
    atr14 = _atr(high, low, close, length=14)
    if pd.isna(atr14) or atr14 <= 0:
        return 0.0
    momentum_atr = (p1 - p0) / atr14
    if momentum_atr >= 2.0:
        return float(WEIGHT_MOMENTUM)
    if momentum_atr >= 1.0:
        return float(WEIGHT_MOMENTUM * 0.5)
    if momentum_atr > 0:
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
    mom = _score_momentum(df)
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
