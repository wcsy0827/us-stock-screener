"""L1 硬條件篩選：排除不符合基本流動性/規模要求的股票。"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd


MIN_PRICE = float(os.getenv("MIN_PRICE", "5"))
MIN_DOLLAR_VOLUME = float(os.getenv("MIN_DOLLAR_VOLUME", "10000000"))  # $10M/日
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "300000000"))
MIN_TRADING_DAYS = 5   # 近5日至少有5筆數據（排除停牌）
EARNINGS_BLACKOUT_DAYS = 3


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
) -> list[str]:
    """
    排除未來 days_ahead 天內有已知財報的個股。
    earnings_data[sym] is None 視為無已知財報，通過過濾。
    """
    today = date.today()
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
