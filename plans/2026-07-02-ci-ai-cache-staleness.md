# CI 同日重跑撿到過期 AI 快取，導致 hotfix 後仍輸出舊結果

> 狀態：已執行完成，對應分支 `fix/ci-ai-cache-staleness`

## Context

PR #46（動能/反轉策略止損緩衝修正）合併後，使用者手動重跑 GitHub Actions 卻仍看到修正前的舊結果
（V 的止損 $331 等於買入區間下緣 $331，跟修正前的 bug 完全一樣）。使用者請我先確認上一次的修正有沒有
真的生效。

追查時間軸（用 `gh run list`/`gh run view` 對照 git log 與 GitHub Actions log）：

| 時間（UTC） | 事件 |
|---|---|
| 03:27:56 | 手動觸發一次 workflow，用**修正前**的 `ranker.py` 呼叫 DeepSeek，V 的止損算出 $331（等於買入區間下緣），結果存入 `.cache/ranked_20260701.json` |
| 05:53:26 | PR #46 合併進 main（`ranker.py` 的 Prompt 修正正式生效）|
| 06:10:01 | 使用者再次手動觸發 workflow（checkout 到的已經是修正後程式碼）|
| 06:10:21 | Actions log 顯示 `Cache hit for: screener-data-2026-07-02` —— 直接讀到 03:27 那次的舊 AI 快取，**沒有重新呼叫 DeepSeek**，把舊資料寫進 `watchlist.json` 並發布 |

診斷確認：修正本身沒有問題（本機重新用 `--no-ai-cache` 呼叫 DeepSeek，5 支動能策略候選的止損皆正確低於
買入區間下緣），問題出在 CI 的快取機制撿到了「合併前」留下的 AI 結果快取，跟修正生效與否無關。

## 根因

`.github/workflows/daily-screener.yml` 的快取步驟：

```yaml
- name: Cache screener price/info data
  uses: actions/cache@v4
  with:
    path: .cache/
    key: screener-data-${{ steps.date.outputs.date }}
```

這個步驟的**命名意圖**是快取 price/info 數據（跨同日重跑省下 yfinance 下載時間，這是合理且刻意的設計），
但 `path: .cache/` 是整個目錄，連帶把 `.cache/ranked_YYYYMMDD.json`（AI 選股結果快取）也一起快取進去。
Key 只用日期，不含程式碼版本，所以「當天內：先跑一次 → 改程式碼 → 合併 → 再跑一次」這個 hotfix 情境下，
第二次執行一定會撿到第一次留下的、用舊 prompt 產生的 AI 結果——這不是單次意外，只要同一天內重跑就會重演。

`main.py` 本身已經有 `--no-ai-cache` 旗標（只跳過 AI 快取，price/info 快取仍複用），但 CI workflow 呼叫的
是 `python main.py --dry-run --yes`，沒有帶這個旗標。

## 考慮過的方案

**選項 A（採用）**：在 `Run screener` 步驟的執行指令加上 `--no-ai-cache`：
```diff
- run: python main.py --dry-run --yes
+ run: python main.py --dry-run --yes --no-ai-cache
```

**選項 B（討論後排除）**：改動 `actions/cache` 的設定——例如把 `ranked_*.json` 排除在快取 `path` 之外，
或把 `key` 換成綁定 commit SHA 或原始碼 hash。
排除原因：這樣的改法同樣能解決問題，但會讓「快取範圍」與「`main.py` 旗標語意」變成兩層各自維護的規則
（一層在 workflow 的 cache 設定，一層在 Python 的 CLI 旗標），日後有人修改其中一層很容易忘記另一層。
選項 A 更直接、改動面更小，且完全對齊 `main.py` 原本就設計好的用途——`--no-ai-cache` 本來就是為了「重新
問 AI，但保留 price/info 快取」這個情境而存在，CI 的手動重跑正是這個情境的典型案例。

選擇選項 A 的原因：CI 情境下 AI 快取唯一的「好處」是同日重跑省一次 DeepSeek API 呼叫——但這正是這次 bug
的根源：CI 永遠不該因為「省一次 API 呼叫」而冒著發布過期 AI 決策的風險。一天正常只排程跑一次（cron 21:30
UTC），第一次執行時 AI 快取本來就是 miss（跨日 key 不同），加不加這個旗標對正常排程執行零差異；唯一有
差異的情境就是「同日內手動重跑」，而這正是我們想要修正的行為。

## 執行內容

### `.github/workflows/daily-screener.yml`

`Run screener` 步驟的執行指令加上 `--no-ai-cache`（不改動 `actions/cache` 的 `path`／`key` 設定，
price/info 快取邏輯完全不受影響）。

### 文件化

- `CLAUDE.md`：「CI 注意事項」新增一條說明；「快取說明」表格「AI 精選結果」列補充「CI 一律強制略過，
  只有本機手動執行才會複用」
- 本文件（`plans/2026-07-02-ci-ai-cache-staleness.md`）
- 不動 `specs/pipeline.md`——這是 CI 工作流程配置變更，不是 pipeline 內部邏輯變更，`main.py` 的
  `--no-ai-cache` 語意本身沒有變化，只是 CI 呼叫方式多帶一個既有旗標；本次修正無對應的 `src/` 模組
  規格文件可掛載，精簡摘要改記錄在 `CLAUDE.md` 的「CI 注意事項」

## 範圍外事項（不在本次修正內）

**已發布的錯誤資料**（`data/watchlist.json` 裡 V 的止損 $331）：本次修正不回頭清洗已發布資料。下次正常
排程執行時，`tracker.py` 只會追蹤既有 watchlist 條目的價格與狀態，不會重新產生新的 `buy_zone`/
`stop_loss`（這些欄位是進場時一次性寫入，DD-12 風控雙欄位設計），所以這筆錯誤資料會持續存在直到訊號
自然結算或失效。

## 驗證

CI workflow 的變更無法在本機完整重現 GitHub Actions 的快取行為，驗證方式：
1. YAML 語法檢查（`yaml.safe_load()`）確認 `daily-screener.yml` 格式正確，且 `Run screener` 步驟的
   `run` 指令確實含有 `--no-ai-cache`
2. 推送後於 GitHub Actions 頁面觀察下一次執行的 log，確認即使 `.cache/` 命中 price/info 快取，仍會出現
   DeepSeek API 呼叫的 log（而非 `[ranker] 複用今日 AI 快取` 訊息）
