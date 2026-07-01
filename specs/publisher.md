# publisher.py

## Purpose

將 pipeline 產出（categories、stats、market_context）轉換為 HTML 報告，發布至 GitHub Pages。負責每日報告頁（`docs/reports/YYYY-MM-DD.html`）與首頁索引（`docs/index.html`）。

## Behavior

- **必須**：報告日期（`date_str`、檔名、索引鍵）皆來自 `stats["date"]`（為 `market_date` 的 `datetime` 物件，不得是 `datetime.now()`）。
- **必須**：每日報告頁顯示資料截止日標籤（`美股資料截止日`），並透過前端 JavaScript 動態顯示資料新鮮度提示。
- **必須**：每次 `publish()` 執行後寫入 `docs/data/last_run.json`，記錄實際執行時間（UTC）與掃描統計，供前端核實。
- **不得**：在 `publish()` 或任何 `_build_*` 函數中以 `datetime.now()` 決定 `date_str`（但 `last_run.json` 的 `run_at_utc` 欄位例外，此欄位本就是執行時刻的事實記錄）。
- **若 `_INFO_HTML` 或 `_build_index` 模板有變動**：必須同一 commit 內手動更新 `docs/index.html`；或執行 `python main.py --dry-run --yes` 後一起 commit。
- **不得直接修改 `docs/` 下的 HTML 檔案**（會被下次執行覆蓋），所有 UI 變更必須在 `publisher.py` 中進行。

## Interface

```python
def publish(
    categories: dict,   # run_tracker 回傳的分類字典（active/watch/invalid/...）
    stats: dict,        # {"total", "l1_count", "l2_count", "ai_count", "date": datetime（market_date）}
    dry_run: bool,      # True = 只生成 HTML，不 git push
    market_context: dict | None,
) -> None:
    """生成每日報告 + 首頁索引，dry_run=False 時 git push。"""
```

## Design Decisions

### DD-1: 報告日期錨定 market_date，不用 datetime.now()

- **選擇**：`stats["date"]` 由 `main.py` 以 `datetime.strptime(market_date_str, "%Y-%m-%d")` 設定，`publisher.py` 直接取用。
- **原因**：CI 在 UTC 時區執行，台灣本地在 UTC+8。台灣時間 7/1 07:49 = UTC 6/30 23:49，此時 `datetime.now()` 在 CI 回傳 6/30，在台灣本地回傳 7/1，造成報告標題與數據日期不一致。`market_date`（= `price_data["SPY"].index[-1].date()`）是唯一不受執行環境時區影響的正確錨點。
- **捨棄**：`datetime.now()`（時區相依，跨環境不一致）、UTC now（CI 準確但本地執行仍可能偏差）。

### DD-2: 資料新鮮度標示用前端 JavaScript，不在後端計算

- **選擇**：在每日報告頁嵌入輕量 JS，於使用者瀏覽器端比對「報告資料日」與「瀏覽器本地日期」，動態顯示「今日最新」或「次日更新時間」。
- **原因**：靜態 HTML 無法知道使用者何時打開頁面；後端生成時間寫死在 HTML 中，使用者下週再看仍顯示「今日最新」，反而誤導。瀏覽器端計算才能反映使用者當下的真實情境。
- **捨棄**：後端寫死生成時間戳（靜態、不反映瀏覽時間點）、不加說明（造成用戶困惑，以為 7/1 執行拿到 6/30 報告是 bug）。

### DD-3: last_run.json 記錄實際執行時間

- **選擇**：`publish()` 在生成 HTML 後，立即寫入 `docs/data/last_run.json`，欄位包含 `run_at_utc`（UTC ISO 8601）、`market_date`、`total_scanned`、`l1_count`、`l2_count`、`ai_count`。
- **原因**：首頁 JS 目前靠時鐘推算「報告是否已產生」，無法區分自動 CI 與手動執行，也無法告訴使用者確切的更新時間。`last_run.json` 是不受時區影響的事實記錄，自動/手動執行皆更新，前端 fetch 後即可顯示精確時間。
- **`run_at_utc` 使用 `datetime.utcnow()`**：此欄位記錄的是執行時刻（execution timestamp），不是報告資料日；與 DD-1 的「報告日期不得用 `datetime.now()`」規則不衝突。
- **捨棄**：在 HTML 模板內寫死時間戳（會被瀏覽器快取讀到舊版本）、不記錄（前端只能靠時鐘猜測）。

### DD-4: 資料驗證面板透過 fetch last_run.json 實現

- **選擇**：每日報告頁的新鮮度徽章旁，加一個可展開的「資料來源」詳情行，JS fetch `../data/last_run.json` 後填入執行時間、market_date、掃描統計，讓使用者可核實。
- **原因**：純靠「✓ 今日最新數據」文字無法核實；顯示「產生時間：2026-06-30 21:31 UTC · 掃描 503 支」讓使用者有具體數字可對照，大幅降低「是不是抓到舊資料」的疑慮。
- **捨棄**：在 HTML 硬編碼統計數字（靜態，使用者查看時已過時）、不提供（體驗差）。

## Acceptance Criteria

- [ ] 每日報告頁標題顯示「美股資料截止日：YYYY-MM-DD（週X）」
- [ ] 當使用者瀏覽器日期 = 報告日期時，JS 顯示「今日最新數據」（綠色）
- [ ] 當使用者瀏覽器日期 > 報告日期時，JS 顯示「下一份報告將於 M/D 05:30 更新」（黃色）
- [ ] 每日報告頁可展開「資料來源」行，顯示 `run_at_utc`、`market_date`、掃描統計
- [ ] 首頁 JS 正確區分五個時段（尚未開盤 / 交易中 / 已收盤產生中 / 今日報告已產生 / 週末）
- [ ] 首頁 fetch `last_run.json` 後顯示實際上次執行時間（而非時鐘推算）
- [ ] `docs/data/last_run.json` 在每次 `publish()` 呼叫後更新
- [ ] `date_str` 始終等於 `market_date`（SPY 最後收盤日），與執行時區無關
