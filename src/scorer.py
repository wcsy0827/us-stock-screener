"""L2 技術指標評分系統（滿分 100 分）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from market import SECTOR_ETF_MAP


# ── 各項最高分 ──────────────────────────────────────────────────
WEIGHT_MA = 20       # 均線多頭排列
WEIGHT_RSI = 18      # RSI 健康區間
WEIGHT_MACD = 17     # MACD 柱狀體為正且遞增
WEIGHT_VOLUME = 15   # 量能放大（含趨勢係數）
WEIGHT_MOMENTUM = 15 # 多週期動能（20 日 ATR + 5 日確認）
WEIGHT_RS = 15       # 相對強度（個股 vs 板塊 ETF）

L2_TARGET_COUNT = 55  # L2 候選池排名上限（目標區間 50~60 取中位數），DD-10


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
    """均線多頭排列 EMA5 > EMA10 > EMA20 > EMA50，完全符合得 20 分，部分符合比例給分。"""
    if len(close) < 50:
        return 0.0
    e5, e10, e20, e50 = _ema(close, 5), _ema(close, 10), _ema(close, 20), _ema(close, 50)
    if any(pd.isna(v) for v in [e5, e10, e20, e50]):
        return 0.0
    conditions = [e5 > e10, e10 > e20, e20 > e50]
    return round(WEIGHT_MA * sum(conditions) / len(conditions), 2)


def _score_rsi(rsi: float, regime: str = "") -> float:
    """RSI 評分（DD-6）：BULL_TREND 下 50~80 為健康區；其他 Regime 50~70 為健康區。"""
    if pd.isna(rsi):
        return 0.0
    if regime == "BULL_TREND":
        if 50 <= rsi <= 80:
            return float(WEIGHT_RSI)
        if rsi > 80 or 40 <= rsi < 50:
            return float(WEIGHT_RSI * 0.5)
        return 0.0
    else:
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
    """量能 VTF 分數 × 5 日量能趨勢係數（DD-4/DD-7）。"""
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

    # VTF 基礎分（爆量但 K_pos < 0.6 視為出貨訊號，直接歸零）
    if ratio >= 1.5:
        vtf_base = float(WEIGHT_VOLUME) if k_pos >= 0.6 else 0.0
    elif ratio >= 1.0:
        vtf_base = float(WEIGHT_VOLUME * 0.5) if k_pos >= 0.6 else 0.0
    else:
        vtf_base = 0.0

    if vtf_base == 0.0:
        return 0.0

    # 5 日量能趨勢係數（D2 防禦：長度不足或均量為零時套用平穩係數）
    vol_tail = vol.values[-5:]
    if len(vol_tail) < 5 or avg30 == 0:
        vol_trend_5d = 0.0
    else:
        slope = np.polyfit(range(5), vol_tail, 1)[0]
        vol_trend_5d = float(np.clip(slope / avg30, -1.0, 1.0))

    if vol_trend_5d > 0.2:
        multiplier = 1.0       # 持續放量
    elif vol_trend_5d >= -0.1:
        multiplier = 0.85      # 平穩
    else:
        multiplier = 0.65      # 縮量趨勢

    return round(vtf_base * multiplier, 2)


def _score_momentum(df: pd.DataFrame) -> float:
    """多週期 ATR 動能：20 日主趨勢 × 5 日方向確認（DD-5/DD-8）。"""
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
    momentum_20d_atr = (p1 - p0) / atr14

    # 5 日短期動能作為方向確認
    p5 = float(close.iloc[-5]) if len(close) >= 5 else p0
    momentum_5d_atr = (p1 - p5) / atr14

    if momentum_20d_atr >= 2.0:
        # 中期強勢：5 日方向確認是否一致
        return float(WEIGHT_MOMENTUM) if momentum_5d_atr >= 0.5 else float(WEIGHT_MOMENTUM * 0.5)
    elif momentum_20d_atr >= 1.0:
        return float(WEIGHT_MOMENTUM * 0.5) if momentum_5d_atr >= 0.3 else float(WEIGHT_MOMENTUM * 0.25)
    elif momentum_20d_atr > 0:
        return float(WEIGHT_MOMENTUM * 0.25)
    return 0.0


def _calc_rs_score(sym: str, df: pd.DataFrame, sector: str, price_data: dict) -> float:
    """相對強度：個股 5 日報酬率 − 板塊 ETF 5 日報酬率（DD-9）。"""
    close = df["Close"].dropna()
    if len(close) < 5:
        return 0.0
    p_start = float(close.iloc[-5])
    if p_start == 0:
        return 0.0
    stock_5d_ret = (float(close.iloc[-1]) - p_start) / p_start * 100

    # 查找板塊 ETF；板塊未知或 ETF 缺資料 → fallback SPY
    etf_ticker = SECTOR_ETF_MAP.get(sector, "") if sector else ""
    if not etf_ticker or etf_ticker not in price_data:
        etf_ticker = "SPY"

    etf_df = price_data.get(etf_ticker)
    if etf_df is None or etf_df.empty:
        return 0.0
    etf_close = etf_df["Close"].dropna()
    if len(etf_close) < 5:
        return 0.0
    etf_start = float(etf_close.iloc[-5])
    if etf_start == 0:
        return 0.0
    etf_5d_ret = (float(etf_close.iloc[-1]) - etf_start) / etf_start * 100

    rs_5d = stock_5d_ret - etf_5d_ret
    if rs_5d >= 2.0:
        return float(WEIGHT_RS)   # 15 分
    if rs_5d >= 0.5:
        return 8.0
    if rs_5d >= -0.5:
        return 3.0
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


def score_stock(
    sym: str,
    df: pd.DataFrame,
    regime: str = "",
    sector: str = "",
    price_data: dict | None = None,
) -> dict:
    """計算單支股票技術指標評分，回傳含各項分數與總分的字典。"""
    close = df["Close"].dropna()
    latest_close = float(close.iloc[-1]) if len(close) > 0 else 0.0

    rsi_val = _calc_rsi(close)

    ma = _score_ma(close)
    rsi = _score_rsi(rsi_val, regime=regime)
    macd = _score_macd(close)
    vol = _score_volume(df)
    mom = _score_momentum(df)
    rs = _calc_rs_score(sym, df, sector, price_data) if price_data is not None else 0.0

    return {
        "symbol": sym,
        "price": latest_close,
        "sector": sector,       # D3：供 ranker._diversify_candidates 使用
        "total_score": round(ma + rsi + macd + vol + mom + rs, 2),
        "ma_score": ma,
        "rsi_score": rsi,
        "macd_score": macd,
        "volume_score": vol,
        "momentum_score": mom,
        "rs_score": rs,
    }


def score_all(
    symbols: list[str],
    price_data: dict[str, pd.DataFrame],
    min_score: float = 60.0,
    regime: str = "",
    sector_map: dict[str, str] | None = None,
) -> list[dict]:
    """對所有通過 L1 的股票評分，回傳候選股，依總分降序排列。

    PANIC_REVERSAL 環境下兩層放行：
    1. 動態門檻降至 40 分（讓輕度超跌股進入）
    2. 強制放行 RSI < 35 + 20 日跌幅 > 15% 的超賣反轉股（得分通常 < 20 分但正是目標標的）

    CONSOLIDATION_VOLATILE 環境門檻提高至 65 分（高 VIX 整理期需更強技術訊號）。
    """
    results = []
    for sym in symbols:
        if sym in price_data and len(price_data[sym]) >= 20:
            sector = (sector_map or {}).get(sym, "")
            results.append(score_stock(sym, price_data[sym], regime=regime, sector=sector, price_data=price_data))

    # 動態門檻（Regime 感知）：PANIC 固定 40，CONSOLIDATION_VOLATILE 取 max(min_score, 65)，其餘用 min_score
    if regime == "PANIC_REVERSAL":
        effective_min = 40.0
    elif regime == "CONSOLIDATION_VOLATILE":
        effective_min = max(min_score, 65.0)
    else:
        effective_min = min_score

    force_pass: set[str] = set()
    if regime == "PANIC_REVERSAL":
        for sym in symbols:
            if sym in price_data and _is_oversold_reversal_candidate(sym, price_data[sym]):
                force_pass.add(sym)
        if force_pass:
            print(f"[scorer] PANIC_REVERSAL 強制放行 {len(force_pass)} 支超賣反轉候選股")

    qualified = sorted(
        [r for r in results if r["total_score"] >= effective_min or r["symbol"] in force_pass],
        key=lambda x: x["total_score"],
        reverse=True,
    )

    # 排名上限（DD-10）：品質門檻之上疊加 Top N 截斷，避免通過數量隨大盤強弱大幅波動
    # 同分邊界一律保留（不引入 tie-breaker），force_pass 股票不受排名上限排除
    if len(qualified) > L2_TARGET_COUNT:
        cutoff_score = qualified[L2_TARGET_COUNT - 1]["total_score"]
        candidates = [r for r in qualified if r["total_score"] >= cutoff_score or r["symbol"] in force_pass]
    else:
        candidates = qualified

    if regime == "PANIC_REVERSAL":
        suffix = f"（PANIC_REVERSAL，門檻 {effective_min:.0f} 分 + 強制放行 {len(force_pass)} 支 + Top {L2_TARGET_COUNT} 排名上限）"
    elif regime == "CONSOLIDATION_VOLATILE":
        suffix = f"（CONSOLIDATION_VOLATILE，門檻 {effective_min:.0f} 分 + Top {L2_TARGET_COUNT} 排名上限）"
    else:
        suffix = f"（門檻 {effective_min:.0f} 分 + Top {L2_TARGET_COUNT} 排名上限）"
    print(f"[scorer] L2 評分：{len(symbols)} 支 → {len(candidates)} 支進入 L3{suffix}")
    return candidates
