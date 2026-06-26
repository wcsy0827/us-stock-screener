"""L1 硬條件篩選：排除不符合基本流動性/規模要求的股票。"""

from __future__ import annotations

import os
import pandas as pd


MIN_PRICE = float(os.getenv("MIN_PRICE", "5"))
MIN_VOLUME = float(os.getenv("MIN_VOLUME", "500000"))
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "300000000"))
MIN_TRADING_DAYS = 5   # 近5日至少有5筆數據（排除停牌）


def apply_filters(
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
) -> list[str]:
    """
    輸入全市場數據，輸出通過 L1 篩選的股票代號列表。

    篩選條件：
    - 最新收盤價 > MIN_PRICE
    - 近 30 日平均成交量 > MIN_VOLUME
    - 市值 > MIN_MARKET_CAP
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
        market_cap = (info_data.get(sym) or {}).get("market_cap")

        if latest_close <= MIN_PRICE:
            reasons[sym] = f"股價偏低(${latest_close:.2f})"
            continue

        if avg_vol_30 < MIN_VOLUME:
            reasons[sym] = f"成交量不足({avg_vol_30:,.0f})"
            continue

        if market_cap is not None and market_cap < MIN_MARKET_CAP:
            reasons[sym] = f"市值偏小(${market_cap/1e6:.0f}M)"
            continue

        if len(recent_5) < MIN_TRADING_DAYS:
            reasons[sym] = f"近5日交易不足({len(recent_5)}天)"
            continue

        passed.append(sym)

    print(f"[filter] L1 篩選：{len(price_data)} → {len(passed)} 支通過")
    return passed
