"""用 yfinance 批次抓取股票近 90 天日 K 數據與基本面資訊。"""

from __future__ import annotations

import json
import os
import pickle
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
import pandas as pd


BATCH_SIZE = 50
PERIOD = "90d"
INTERVAL = "1d"
MAX_RETRIES = 3
RETRY_DELAY = 5
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 15  # 16:00 ET 收盤 + 15 分鐘 settle buffer（yfinance 有時延遲幾分鐘才定案當日收盤K棒）
MIN_BARS = 20  # 與 fetch_batch 的最低列數門檻一致

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


def load_info_cache() -> dict[str, dict] | None:
    """讀取 7 日內最新的 info 快取，不存在則回傳 None。基本面資料變動緩慢，可複用數日。"""
    if not _CACHE_DIR.exists():
        return None
    today_ord = date.today().toordinal()
    candidates = []
    for f in _CACHE_DIR.glob("info_*.json"):
        try:
            ds = f.stem.split("_")[1]
            y, m, d_ = int(ds[:4]), int(ds[4:6]), int(ds[6:])
            age = today_ord - date(y, m, d_).toordinal()
            if 0 <= age <= 7:
                candidates.append((age, f))
        except Exception:
            pass
    if not candidates:
        return None
    _, path = min(candidates, key=lambda x: x[0])
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[cache] 讀取 info 快取：{path.name}（{len(data)} 支）")
        return data
    except Exception as e:
        print(f"[cache] info 快取讀取失敗，重新下載：{e}")
        return None


def save_info_cache(data: dict[str, dict]) -> None:
    """儲存 info_data 到快取（以今日日期命名）。"""
    _CACHE_DIR.mkdir(exist_ok=True)
    path = _info_cache_path(_today())
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


def trim_incomplete_session(price_data: dict[str, pd.DataFrame], now: datetime | None = None) -> dict[str, pd.DataFrame]:
    """捨棄尚未收盤的殘缺當日 K 棒，避免 market_date 誤標為未完成的交易日。"""
    spy_df = price_data.get("SPY")
    if spy_df is None or spy_df.empty:
        return price_data

    et = ZoneInfo("America/New_York")
    now_et = (now or datetime.now(et)).astimezone(et)
    last_date = spy_df.index[-1].date()

    close_cutoff = now_et.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    if last_date != now_et.date() or now_et >= close_cutoff:
        return price_data

    affected = 0
    dropped = 0
    trimmed: dict[str, pd.DataFrame] = {}
    for sym, df in price_data.items():
        if df.empty or df.index[-1].date() != last_date:
            trimmed[sym] = df
            continue
        affected += 1
        df = df[df.index.map(lambda ts: ts.date()) != last_date]
        if len(df) >= MIN_BARS:
            trimmed[sym] = df
        else:
            dropped += 1

    print(f"[fetcher] 偵測到 {last_date} 尚未收盤，已捨棄殘缺K棒（{affected} 支股票受影響，{dropped} 支因列數不足被移除）")
    return trimmed


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
                    "earnings_date": info.get("earningsDate"),
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
