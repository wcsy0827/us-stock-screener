# publisher.py

## Purpose

將 pipeline 產出（categories、stats、market_context）轉換為 HTML 報告，發布至 GitHub Pages。負責每日報告頁（`docs/reports/YYYY-MM-DD.html`）與首頁索引（`docs/index.html`）。

## Behavior

- **必須**：報告日期（`date_str`、檔名、索引鍵）皆來自 `stats["date"]`（為 `market_date` 的 `datetime` 物件，不得是 `datetime.now()`）。
- **必須**：每日報告頁顯示資料截止日標籤（`美股資料截止日`），並透過前端 JavaScript 動態顯示資料新鮮度提示。
- **必須**：每次 `publish()` 執行後寫入 `docs/data/last_run.json`，記錄實際執行時間（UTC）與掃描統計，供前端核實。
- **不得**：在 `publish()` 或任何 `_build_*` 函數中以 `datetime.now()` 決定 `date_str`（但 `last_run.json` 的 `run_at_utc` 欄位例外，此欄位本就是執行時刻的事實記錄）。
- **若 `_INFO_HTML`、`_CSS` 或 `_build_index` 模板有變動**：同一 commit 內執行 `python src/publisher.py` 重新生成 `docs/index.html` 並一起 commit（DD-6）；`tests/test_publisher_info_sync.py` 以全等比對守門，漂移即紅燈。
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

### DD-5: 歷史報告列表由前端動態渲染，不寫死進 index.html

- **選擇**：`_build_index()` 輸出固定的 `<div id="report-list">` 佔位元素，JS fetch `data/reports-index.json` 後動態生成報告連結。`_build_index` 不再接受 `report_index` 參數。
- **原因**：舊做法將報告清單硬編碼進 HTML，導致手動重置 `reports-index.json` 後首頁仍顯示舊資料，必須同步手動修改 `index.html`。動態渲染後，`reports-index.json` 是唯一的真實來源，重置或新增報告只需更新 JSON，`index.html` 本身不需要跟著變動。
- **`index.html` 何時需要重新生成**：只有 `_build_index()` 模板本身（CSS、佈局、script 邏輯）改變時才需要重新執行 `publish()` 或手動同步；報告清單新增/刪除不再需要。
- **捨棄**：靜態 HTML 生成（index.html 與 JSON 雙重維護，手動重置時容易不同步）。

### DD-6: index.html 同步自動化——全等比對守門 + `sync_index()` 一鍵再生成

- **選擇**：`_build_index()` 自 DD-5 起為無參數確定性函式（只嵌入靜態字串 `_CSS` 與 `_INFO_HTML`，無日期、無資料輸入），`docs/index.html` 是其純函數輸出。同步改由 `sync_index()`（CLI 入口 `python src/publisher.py`）程式化完成；`publish()` 內的 index.html 寫檔也統一走 `sync_index()`，單一出口。守門測試 `tests/test_publisher_info_sync.py` 由「`_INFO_HTML` 行子字串比對」升級為「整檔全等比對 `_build_index()` 輸出」。
- **原因**：手動同步規則的根本前提（index.html 含動態內容、無法離線再生成）已被 DD-5 消除；純函數輸出的同步不該由人做。全等比對同時補上舊測試守不住 `_CSS`／JS 漂移的缺口，且失敗時修復動作是一條命令而非人工比對編輯。
- **`sync_index()` 以 `newline="\n"` 固定 LF**：避免 Windows 本機執行產生 CRLF 造成整檔 whitespace diff（CI Linux 輸出即為 LF）；測試端 `read_text` 的 universal newlines 讀入一律為 `\n`，跨平台行為一致。
- **捨棄**：pre-commit hook（引入額外基礎設施，且繞過 hook 即失守）；測試內自動改寫檔案（CI 綠燈但 repo 內已部署的 Pages 檔案仍是舊的，掩蓋漂移）；維持行子字串比對（守不住 `_CSS`/JS 漂移，修復仍靠人工）。→ 詳見 `plans/2026-07-03-index-html-auto-sync.md`

### DD-7: 每日報告顯示動態止損、移動停利觸發線，watch/invalid 剩餘天數改讀 tracker 的策略上限

- **選擇**：`_tracking_row()` 對 active 部位優先顯示 `effective_stop_loss`（若存在，缺失時 fallback 為 AI 原始 `stop_loss`），`is_breakeven_locked=True` 時附加「🔒保本」標記；動能/突破策略且峰值浮盈已達 `TRAILING_ACTIVATION_PCT`（10%）門檻時，額外顯示移動停利觸發線 `highest_close_since_active × (1 - TRAILING_RETRACE_PCT)`（反轉策略精確排除，與 DD-13 口徑一致）。watch/invalid 狀態的「剩 N 天自動移除」不再寫死 `5 - days`，改呼叫 `tracker._max_watch_days(entry)` 取得該筆訊號實際的策略/Regime/VIX 差異化上限（DD-15/16）。
- **原因**：使用者的實際操作方式是「收盤後跑篩選、次一交易日盤中依買入區間掛單，並手動遵守停損停利區間」。系統內部（`tracker.py` DD-12/13）早已自動把止損上移做保本鎖定、計算移動停利觸發價，但報告只顯示 AI 原始止損字串，等於使用者手動跟單時用的是過時門檻，與系統實際結算邏輯脫節。同理，watch/invalid 剩餘天數寫死 5 天，反轉策略（10 日）、高波動整理市的突破策略（3 日）、VIX 尖底的反轉策略（5 日）顯示的倒數天數全部錯誤，可能讓使用者誤判某檔訊號已到期或還有餘裕。
- **捨棄**：只顯示 AI 原始 `stop_loss`（簡單但與系統實際結算門檻不一致）；在 `publisher.py` 內重寫一份 watch 上限查表（DRY 違反，`tracker.py` DD-15/16 已是單一事實來源，直接呼叫 `_max_watch_days()` 即可）。

### DD-8: 留意清單顯示滿倉未進場狀態 + 今日統計顯示持倉上限

- **背景**：tracker DD-20 引入組合層級持倉上限（`MAX_ACTIVE_POSITIONS`，槽位制）後，watch 條目可能「今日觸價但因持倉已滿被擋下、未進場」。使用者依報告隔日掛單，若報告不呈現這個狀態，使用者無從得知該檔今天其實觸價過、也無從得知目前持倉相對上限還有多少名額。
- **選擇**：
  1. `_tracking_row()` watch 分支：`entry.get("slot_blocked_today")` 為 True 時，狀態文字改為「第 N 天（今日觸價但持倉已滿 X 支，未進場；剩 M 天自動移除）」；False 時維持既有「等待回落」文字。沿用 `.track-item.watch` 既有樣式，不改 `_CSS`。
  2. 「有效追蹤清單」段落標題經 `_section_html` 的 `note` 參數附加「上限 X 支」。
  3. 「今日統計」的有效持倉格顯示 `{active 數} / {MAX_ACTIVE_POSITIONS}`。
  4. 常數自 `tracker` import（`from tracker import MAX_ACTIVE_POSITIONS`），沿用 DD-7 的 `_max_watch_days` 單一事實來源先例，不在 publisher 內重複定義。
- **`_INFO_HTML` 只寫靜態文字，不插值 runtime 常數**：`docs/index.html` 由 `tests/test_publisher_info_sync.py` 全等比對守門；若 `_INFO_HTML` 插值 `MAX_ACTIVE_POSITIONS`，任何 `.env` 設了不同值的環境跑 pytest 都會誤紅。系統說明卡片寫「預設 5 支（`MAX_ACTIVE_POSITIONS` 可調）」的字面文字；每日報告（非全等守門範圍）才使用 runtime 常數。
- **捨棄**：在 `last_run.json` 加 `active_count`/`max_positions` 欄位（`_write_last_run` 不接觸 categories，需擴 plumbing，且今日統計已呈現同一資訊）；為被擋條目新增獨立 CSS badge（既有 track-status 文字已足夠傳達，避免 `_CSS` 變動觸發 index.html 再生成的額外面積）。

## Acceptance Criteria

- [ ] 每日報告頁標題顯示「美股資料截止日：YYYY-MM-DD（週X）」
- [ ] 當使用者瀏覽器日期 = 報告日期時，JS 顯示「今日最新數據」（綠色）
- [ ] 當使用者瀏覽器日期 > 報告日期時，JS 顯示「下一份報告將於 M/D 05:30 更新」（黃色）
- [ ] 每日報告頁可展開「資料來源」行，顯示 `run_at_utc`、`market_date`、掃描統計
- [ ] 首頁 JS 正確區分五個時段（尚未開盤 / 交易中 / 已收盤產生中 / 今日報告已產生 / 週末）
- [ ] 首頁 fetch `last_run.json` 後顯示實際上次執行時間（而非時鐘推算）
- [ ] `docs/data/last_run.json` 在每次 `publish()` 呼叫後更新
- [ ] `date_str` 始終等於 `market_date`（SPY 最後收盤日），與執行時區無關
- [ ] `docs/index.html` 與 `_build_index()` 輸出整檔全等；`python src/publisher.py` 可離線一鍵再生成（DD-6）
- [ ] **DD-7 動態止損**：active 條目有 `effective_stop_loss` 時，報告顯示該值而非原始 `stop_loss`；`is_breakeven_locked=True` 時額外顯示「🔒保本」
- [ ] **DD-7 動態止損 fallback**：active 條目缺 `effective_stop_loss` 時，退化顯示原始 `stop_loss`
- [ ] **DD-7 移動停利觸發線**：峰值浮盈達 10% 門檻的動能/突破策略 active 條目顯示「移動停利線 $X」；反轉策略一律不顯示；未達門檻不顯示
- [ ] **DD-7 watch/invalid 剩餘天數**：反轉策略 watch 顯示剩餘天數以 10 日上限計算（非寫死 5 日）；`entry_regime=CONSOLIDATION_VOLATILE` 的突破策略以 3 日上限計算
- [ ] **DD-8 滿倉未進場註記**：`slot_blocked_today=True` 的 watch 條目顯示「今日觸價但持倉已滿 X 支，未進場」；False 或缺欄位時維持既有「等待回落」文字
- [ ] **DD-8 持倉上限顯示**：今日統計的有效持倉格顯示「N / X」；有效追蹤清單標題含「上限 X 支」
- [ ] **DD-8 常數單一來源**：報告內所有上限數字取自 `tracker.MAX_ACTIVE_POSITIONS` import，publisher 內無第二份定義
- [ ] **DD-8 index.html 不受 env 影響**：`_INFO_HTML` 為靜態文字，`MAX_ACTIVE_POSITIONS` 設為任意值時 `_build_index()` 輸出不變
