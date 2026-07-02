# 盤中執行殘缺K棒防呆（`fetcher.py` `trim_incomplete_session` / `specs/pipeline.md` DD-6）

> 狀態：已執行完成，對應分支 `fix/intraday-partial-bar-guard`

## Context

使用者提出兩個手動執行時間點的問題：台灣時間 7/1 22:00（美股盤中）與台灣時間 7/2 08:00（美股收盤後）。
換算後：

| 觸發時間（台灣） | 對應 UTC | 對應美東時間 (ET) | 美股狀態 |
|---|---|---|---|
| 7/1 22:00 | 7/1 14:00 | 7/1 10:00 AM | **開盤中**（開盤 30 分鐘後） |
| 7/2 08:00 | 7/2 00:00 | 7/1 20:00 | **收盤後 4 小時**，7/1 已完整收盤 |

在探索 `src/fetcher.py`、`src/pipeline.py`、`src/market.py` 後確認：

- `fetcher.py` 的 `fetch_batch()`（143-178 行）呼叫 `yf.download(tickers=batch, period="90d", interval="1d", ...)`。
  美股盤中執行時，yfinance 對「今天」這一列回傳的是**尚未收盤的殘缺 K 棒**（只反映當下累積量價，非最終
  收盤值），yfinance 本身不會過濾掉這一列。
- `pipeline.py:78`（`summary["market_date"] = spy_df.index[-1].date().isoformat()`）直接拿最後一列日期當
  `market_date`，**完全沒有檢查這一列是否已經收完**。
- 兩個時間點換算後最終的 `market_date` 都會是 `"2026-07-01"`，但資料品質天差地遠：前者是盤中殘缺數據，
  後者是完整收盤數據。報告標題無法區分兩者，L2 評分、RS 計算、甚至送進 DeepSeek 的候選池表格，都可能
  是用殘缺數據跑出來的。
- `.cache/price_YYYYMMDD.pkl` 快取以本機系統日期（Taiwan 本地日期）為 key，跨日後 key 不同會強制重下，
  不會延續前一晚的殘缺快取——但同一天內重跑仍會沿用同一份快取（含殘缺列），這是既有、有意的快取設計
  （`--no-cache` 是逃生閥），本次修正不打算動它。
- `market.py:236-239` 的 `fetch_regime_quick()` DD-5 遲滯帶邏輯是嚴格比較 `last_market_date < current_market_date`，
  且是即時讀 `price_data["SPY"]`（不是快取副本），所以只要 Step 2 階段把 `price_data` 修剪乾淨，Step 2.5
  會自動看到正確結果，不需要另外改 `market.py`。
- `specs/pipeline.md`、`specs/market.md` 都完全沒討論過這個問題，是全新的邊界情況，不是繞過既有結論。

## 考慮過的方案

**選項 A（採用）**：Step 2 完成後，統一對 `price_data` 執行一次 `trim_incomplete_session()`——比對
`price_data["SPY"]` 最後一列日期是否等於美東（`America/New_York`，DST-aware）當下日期，且美東現在時間
早於 `16:15`（收盤 16:00 + 15 分鐘 settle buffer，因為 yfinance 有時延遲幾分鐘才定案當日收盤K棒）。
成立則逐股比對日期、捨棄該殘缺列，過濾後列數 `< 20`（與 `fetch_batch` 既有門檻一致）的股票整支移除。
`market_date` 因此自然回退到前一個完整交易日。

**選項 B（捨棄）**：偵測到盤中執行時直接中斷流程並提示使用者稍後重跑。
捨棄原因：會打斷 GitHub Actions CI 自動化（CI 固定在 UTC 21:30 收盤後跑，理論上不會觸發，但任何未來的
排程調整都可能誤傷）；且使用者原本的心智模型就是「盤中觸發＝拿到前一日完整報告」（`CLAUDE.md` 已有的
UTC 時區表格即隱含此假設），直接中斷反而打破這個既有的、可預期的行為。

**選項 C（捨棄）**：只印警告訊息，但仍照常用殘缺數據跑完整流程。
捨棄原因：無法解決根本問題——`market_date` 依然會誤標，L2 評分/RS 計算/DeepSeek 候選池依然吃到殘缺數據，
只是使用者事後才知道要不要相信這份報告，防呆等於沒做。

選擇選項 A 的原因：不需要新增 CLI 旗標或中斷流程，行為對齊 `CLAUDE.md` 既有的「盤中觸發＝前一日報告」
假設，且是純函式（`now` 參數可注入，方便驗證），改動集中在 `fetcher.py` 一個新函式 + `pipeline.py` 一行
呼叫，不動 `market.py`、`ranker.py`、`tracker.py`。

## 執行內容

### 1. `src/fetcher.py`

- 新增常數 `MARKET_CLOSE_HOUR = 16`、`MARKET_CLOSE_MINUTE = 15`、`MIN_BARS = 20`
- 新增 `trim_incomplete_session(price_data: dict[str, pd.DataFrame], now: datetime | None = None) -> dict[str, pd.DataFrame]`，
  放在 `fetch_batch()` 之後、`fetch_info()` 之前
- 邏輯：以 SPY 最後一列日期為基準；美東當下時間早於 16:15 且日期等於今天 → 逐股過濾掉該日期的列，
  過濾後 `< 20` 列的股票整支移除；否則（週末/假日/已收盤）no-op，不印訊息
- 觸發時印出 `[fetcher] 偵測到 {date} 尚未收盤，已捨棄殘缺K棒（{N} 支股票受影響，{M} 支因列數不足被移除）`

### 2. `src/pipeline.py`

- `from fetcher import (...)` 加入 `trim_incomplete_session`
- 在 Step 2 的 `save_price_cache(price_data)` 之後、`sp500_count`/`market_date` 統計之前插入
  `price_data = trim_incomplete_session(price_data)`，確保無論資料來自快取或重新下載都統一經過防呆，
  且統計數字與 `market_date` 都反映修剪後的正確狀態

### 3. `specs/pipeline.md`

- 「快取策略」節新增「Step 2 收盤完整性防呆（DD-6）」小節
- Design Decisions 新增 DD-6，連結回本文件

### 4. `CLAUDE.md` / `README.md`

- 「十五個最重要的設計決策」新增一項摘要本次修正，連結 `specs/pipeline.md` DD-6
- 若快取說明表格或架構速覽 Step 2 描述涉及本次改動，同步更新一句話

## 驗證

專案沒有 `tests/` 目錄，以 `now` 參數注入方式手動驗證邏輯（不需等到真的盤中）：
- 建構 SPY 最後一列日期為「今天」的 `price_data`，傳入 `now=<今天 15:00 ET>` → 確認觸發修剪，最後一列
  回退到前一交易日
- 傳入 `now=<今天 17:00 ET>` → 確認 no-op，最後一列維持今天
- 實際執行 `python main.py --dry-run --yes`（非美股盤中時段）→ 確認 log 沒有出現 `[fetcher] 偵測到...尚未收盤`
  訊息，`market_date` 與 SPY 最後一列日期一致
