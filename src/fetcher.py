"""用 yfinance 批次抓取股票近 90 天日 K 數據與基本面資訊。"""

from __future__ import annotations

import json
import os
import pickle
import time
from datetime import date
from pathlib import Path

import yfinance as yf
import pandas as pd


BATCH_SIZE = 50
PERIOD = "90d"
INTERVAL = "1d"
MAX_RETRIES = 3
RETRY_DELAY = 5

# 快取目錄（相對於專案根目錄）
_CACHE_DIR = Path(__file__).parent.parent / ".cache"


# ── 快取工具 ─────────────────────────────────────────────────────

def _today() -> str:
    return date.today().strftime("%Y%m%d")


def _price_cache_path(date_str: str) -> Path:
    return _CACHE_DIR / f"price_{date_str}.pkl"


def _info_cache_path(date_str: str) -> Path:
    return _CACHE_DIR / f"info_{date_str}.json"


def load_price_cache(date_str: str | None = None) -> dict[str, pd.DataFrame] | None:
    """讀取當日 price_data 快取，不存在則回傳 None。"""
    path = _price_cache_path(date_str or _today())
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        print(f"[cache] 讀取 price 快取：{path.name}（{len(data)} 支）")
        return data
    except Exception as e:
        print(f"[cache] price 快取讀取失敗，重新下載：{e}")
        return None


def save_price_cache(data: dict[str, pd.DataFrame], date_str: str | None = None) -> None:
    """儲存 price_data 到快取。"""
    _CACHE_DIR.mkdir(exist_ok=True)
    path = _price_cache_path(date_str or _today())
    with open(path, "wb") as f:
        pickle.dump(data, f)
    print(f"[cache] price 快取已儲存：{path.name}")


def load_info_cache(date_str: str | None = None) -> dict[str, dict] | None:
    """讀取當日 info_data 快取，不存在則回傳 None。"""
    path = _info_cache_path(date_str or _today())
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[cache] 讀取 info 快取：{path.name}（{len(data)} 支）")
        return data
    except Exception as e:
        print(f"[cache] info 快取讀取失敗，重新下載：{e}")
        return None


def save_info_cache(data: dict[str, dict], date_str: str | None = None) -> None:
    """儲存 info_data 到快取。"""
    _CACHE_DIR.mkdir(exist_ok=True)
    path = _info_cache_path(date_str or _today())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[cache] info 快取已儲存：{path.name}")


def clear_old_cache(keep_days: int = 7) -> None:
    """清除超過 keep_days 天的舊快取檔案。"""
    if not _CACHE_DIR.exists():
        return
    cutoff = date.today().toordinal() - keep_days
    removed = 0
    for f in _CACHE_DIR.glob("*.pkl"):
        try:
            file_date = int(f.stem.split("_")[1])
            y, m, d_ = file_date // 10000, (file_date % 10000) // 100, file_date % 100
            if date(y, m, d_).toordinal() < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            pass
    for f in _CACHE_DIR.glob("*.json"):
        try:
            file_date = int(f.stem.split("_")[1])
            y, m, d_ = file_date // 10000, (file_date % 10000) // 100, file_date % 100
            if date(y, m, d_).toordinal() < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            pass
    if removed:
        print(f"[cache] 清除 {removed} 個舊快取檔案")


# ── 下載函式 ─────────────────────────────────────────────────────

def _download_with_retry(tickers: list[str], **kwargs) -> pd.DataFrame:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return yf.download(tickers=tickers, **kwargs)
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            print(f"[fetcher] 下載失敗（第{attempt}次），{RETRY_DELAY}秒後重試：{e}")
            time.sleep(RETRY_DELAY * attempt)
    return pd.DataFrame()


def fetch_batch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """批次下載 OHLCV 日線數據，回傳 {symbol: DataFrame}。"""
    result: dict[str, pd.DataFrame] = {}

    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i : i + BATCH_SIZE]
        print(f"[fetcher] 下載 {i+1}~{min(i+BATCH_SIZE, len(symbols))} / {len(symbols)}")

        try:
            raw = _download_with_retry(
                tickers=batch,
                period=PERIOD,
                interval=INTERVAL,
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as e:
            print(f"[fetcher] 批次下載失敗，跳過：{e}")
            continue

        for sym in batch:
            try:
                df = raw.copy() if len(batch) == 1 else raw[sym].copy()
                df = df.dropna(how="all")
                if len(df) >= 20:
                    result[sym] = df
            except Exception:
                pass

        if i + BATCH_SIZE < len(symbols):
            time.sleep(1)

    print(f"[fetcher] 成功取得 {len(result)} 支股票數據")
    return result


def fetch_info(symbols: list[str]) -> dict[str, dict]:
    """抓取股票基本面資訊（市值、產業、公司名稱）。"""
    info_map: dict[str, dict] = {}

    for i, sym in enumerate(symbols):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                ticker = yf.Ticker(sym)
                info = ticker.info
                info_map[sym] = {
                    "market_cap": info.get("marketCap") or info.get("market_cap"),
                    "sector": info.get("sector", "Unknown"),
                    "name": info.get("shortName") or info.get("longName") or sym,
                    "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                    "fifty_two_week_low":  info.get("fiftyTwoWeekLow"),
                }
                break
            except Exception as e:
                if attempt == MAX_RETRIES:
                    info_map[sym] = {"market_cap": None, "sector": "Unknown", "name": sym}
                else:
                    time.sleep(RETRY_DELAY)

        if (i + 1) % 50 == 0:
            print(f"[fetcher] info {i+1}/{len(symbols)}")
            time.sleep(1)

    return info_map


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from universe import fetch_sp500

    symbols = fetch_sp500()[:10]
    data = fetch_batch(symbols)
    info = fetch_info(list(data.keys()))
    for sym, df in list(data.items())[:3]:
        print(f"{sym} ({info[sym]['name']}): ${df['Close'].iloc[-1]:.2f}, {info[sym]['sector']}")
