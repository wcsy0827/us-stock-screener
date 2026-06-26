"""從 Wikipedia 抓取 S&P 500 成分股清單。"""

import io
import pandas as pd
import requests


WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500() -> list[str]:
    """回傳 S&P 500 所有股票代號列表。"""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; sp500-screener/1.0)"}
    resp = requests.get(WIKIPEDIA_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]

    # Wikipedia 表格第一欄是 "Symbol"
    symbols: list[str] = df["Symbol"].str.replace(".", "-", regex=False).tolist()
    print(f"[universe] 共取得 {len(symbols)} 支 S&P 500 成分股")
    return symbols


if __name__ == "__main__":
    syms = fetch_sp500()
    print(syms[:10])
