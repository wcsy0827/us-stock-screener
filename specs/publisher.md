# publisher.py

## Purpose

將 pipeline 產出（categories、stats、market_context）轉換為 HTML 報告，發布至 GitHub Pages。負責每日報告頁（`docs/reports/YYYY-MM-DD.html`）與首頁索引（`docs/index.html`）。

## Behavior

- **必須**：報告日期（`date_str`、檔名、索引鍵）皆來自 `stats["date"]`（為 `market_date` 的 `datetime` 物件，不得是 `datetime.now()`）。
- **必須**：每日報告頁顯示資料截止日標籤（`美股資料截止日`），並透過前端 JavaScript 動態顯示資料新鮮度提示。
- **不得**：在 `publish()` 或任何 `_build_*` 函數中以 `datetime.now()` 決定 `date_str`。
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

## Acceptance Criteria

- [ ] 每日報告頁標題顯示「美股資料截止日：YYYY-MM-DD（週X）」
- [ ] 當使用者瀏覽器日期 = 報告日期時，JS 顯示「今日最新數據」（綠色）
- [ ] 當使用者瀏覽器日期 > 報告日期時，JS 顯示「下一份報告將於 M/D 05:30 更新」（黃色）
- [ ] 首頁索引顯示目前 UTC 時間狀態，提示使用者何時有新報告
- [ ] `date_str` 始終等於 `market_date`（SPY 最後收盤日），與執行時區無關
