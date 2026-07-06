"""L1 硬條件篩選：排除不符合基本流動性/規模要求的股票。"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd


MIN_PRICE = float(os.getenv("MIN_PRICE", "5"))
MIN_DOLLAR_VOLUME = float(os.getenv("MIN_DOLLAR_VOLUME", "10000000"))  # $10M/日
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "300000000"))
MIN_TRADING_DAYS = 5   # 近5日至少有5筆數據（排除停牌）
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "8"))  # ATR14/收盤價百分比上限（DD-8）
EARNINGS_BLACKOUT_DAYS = 5


def _atr_pct(df: pd.DataFrame, length: int = 14) -> float | None:
    """ATR14 佔最新收盤價的百分比。歷史數據不足時回傳 None（視為無法判斷，不排除）。"""
    close = df["Close"].dropna()
    high = df["High"].dropna()
    low = df["Low"].dropna()
    if len(close) < length + 1 or len(high) < length + 1 or len(low) < length + 1:
        return None
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_series = tr.ewm(alpha=1 / length, adjust=False).mean().dropna()
    if atr_series.empty:
        return None
    atr14 = float(atr_series.iloc[-1])
    latest_close = float(close.iloc[-1])
    if latest_close == 0:
        return None
    return atr14 / latest_close * 100


def apply_filters(
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
) -> list[str]:
    """
    輸入全市場數據，輸出通過 L1 篩選的股票代號列表。

    篩選條件：
    - 最新收盤價 > MIN_PRICE
    - 近 30 日平均日成交額 > MIN_DOLLAR_VOLUME（股數 × 收盤價）
    - 市值 > MIN_MARKET_CAP；市值 None（API 缺失）視同不足直接排除
    - 近 5 日有交易（至少 5 筆有效數據）
    - ATR14/收盤價百分比 <= MAX_ATR_PCT；歷史數據不足 15 筆無法計算時不排除（DD-8）
    """
    passed: list[str] = []
    reasons: dict[str, str] = {}

    for sym, df in price_data.items():
        if len(df) < MIN_TRADING_DAYS:
            reasons[sym] = f"數據不足({len(df)}筆)"
            continue

        close = df["Close"].dropna()
        volume = df["Volume"].dropna()

        if len(close) == 0:
            reasons[sym] = "無收盤價數據"
            continue

        latest_close = float(close.iloc[-1])
        recent_5 = close.tail(5)
        avg_vol_30 = float(volume.tail(30).mean()) if len(volume) >= 30 else float(volume.mean())
        avg_dollar_vol_30 = avg_vol_30 * latest_close
        market_cap = (info_data.get(sym) or {}).get("market_cap")

        if latest_close <= MIN_PRICE:
            reasons[sym] = f"股價偏低(${latest_close:.2f})"
            continue

        if avg_dollar_vol_30 < MIN_DOLLAR_VOLUME:
            reasons[sym] = f"日成交額不足(${avg_dollar_vol_30/1e6:.1f}M)"
            continue

        if market_cap is None:
            reasons[sym] = "市值數據缺失"
            continue
        if market_cap < MIN_MARKET_CAP:
            reasons[sym] = f"市值偏小(${market_cap/1e6:.0f}M)"
            continue

        atr_pct = _atr_pct(df)
        if atr_pct is not None and atr_pct > MAX_ATR_PCT:
            reasons[sym] = f"波動過大(ATR%={atr_pct:.1f}%)"
            continue

        if len(recent_5) < MIN_TRADING_DAYS:
            reasons[sym] = f"近5日交易不足({len(recent_5)}天)"
            continue

        passed.append(sym)

    print(f"[filter] L1 流動性篩選：{len(price_data)} → {len(passed)} 支通過")
    return passed


def apply_earnings_filter(
    symbols: list[str],
    earnings_data: dict[str, date | None],
    days_ahead: int = EARNINGS_BLACKOUT_DAYS,
    today: date | None = None,
) -> list[str]:
    """
    排除未來 days_ahead 天內有已知財報的個股。
    earnings_data[sym] is None 視為無已知財報，通過過濾。

    today 應由呼叫端傳入 market_date（SPY 最後收盤日），與專案「一切錨定
    market_date、不用 datetime.now()」原則一致；未提供時 fallback 為
    date.today()，僅供未升級呼叫端或 market_date 缺失時使用。
    """
    today = today or date.today()
    cutoff = today + timedelta(days=days_ahead)
    passed: list[str] = []
    excluded = 0
    for sym in symbols:
        ed = earnings_data.get(sym)
        if ed is not None and today <= ed <= cutoff:
            excluded += 1
            continue
        passed.append(sym)
    if excluded:
        print(f"[filter] 財報防禦牆：排除 {excluded} 支（未來 {days_ahead} 天內有財報）")
    print(f"[filter] L1 財報過濾：{len(symbols)} → {len(passed)} 支通過")
    return passed
