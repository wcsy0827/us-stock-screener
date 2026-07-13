---
name: data-and-caches
description: 觀察到以下任一狀態時載入：準備刪除/清空/手改 data/ 或 .cache/ 下任何檔案；輸出疑似用到舊數據；watchlist 或績效檔內容與預期不符；需要判斷某個檔案能不能安全重生。
---

# 資料檔與快取：哪些可再生、哪些不可逆

事實時間戳：2026-07-13。

## 檔案語意表（動手前先分類）

| 檔案 | 性質 | 刪除後果 |
|---|---|---|
| `data/performance_history.json` | **帳本**（語意上只增不刪，結算歸檔寫入；在版控內，CI 每日 `git add data/`） | 已 commit 的部分可從 git 還原，**未 commit 的當日結算增量刪了就沒了**；analyzer 統計連動歸零。刪之前先備份或確認 git 內有最新版 |
| `data/watchlist.json` | 持久化狀態（跨日追蹤中的部位） | 追蹤中的 watch/active 部位全部消失，等於棄單；測試重置以外不要刪 |
| `data/ai_hints.json` | 可再生衍生檔 | 每輪 Step 5.7 全量重寫，刪了自動重建 |
| `docs/data/last_run.json` | 每次 publish() 覆寫；**同時是下一輪的輸入** | 見下方「last_run.json 是活的」 |
| `docs/reports/*.html`、`docs/index.html` | publisher 生成物 | 可再生，但**不得手改**（見 publisher-frontend-sync） |
| `.cache/price_YYYYMMDD.pkl` | 當日快取，key=**本機系統日期** | 可刪；`--no-cache` 跳過 |
| `.cache/info_YYYYMMDD.json` | 7 日快取（取最近一份） | 可刪 |
| `.cache/ranked_YYYYMMDD.json` | 當日 AI 快取，**不感知程式碼/候選池版本** | 可刪；改 Prompt 後不刪不 `--no-ai-cache` 就會讀舊結果 |
| `.cache/earnings_registry.json` | 30 日 per-symbol TTL | 可刪，會重新補抓 |

## 規則：last_run.json 是活的（不是純輸出）

**觸發**：想手改/刪除 last_run.json，或懷疑 Regime 判定怪異。
**內容**：`market.py` 的廣度遲滯帶（DD-5）每天讀前一日 `last_run.json` 的 `regime` 與 `market_date`——這個檔案被污染（regime 空字串、日期錯亂）會影響**下一次執行**的 Regime 判定。2026-07-06 事故中 `regime=""` 就是寫進了這個檔案（成因見 debugging-playbook S1）。
**步驟**：懷疑污染時先 `git log -p docs/data/last_run.json` 看它何時被寫壞；遲滯帶有 `last_market_date < current_market_date` 嚴格校驗與「VIX 跨結構邊界強制放行」保護，多數污染只影響邊界 ±2% 內的判定。
**完成定義**：知道你改動後下一輪 `fetch_regime_quick()` 會讀到什麼。

## 規則：快取 key 綁日期，不綁內容

**觸發**：「我改了程式碼重跑，結果一模一樣」或「同一天內第二次跑，數據沒更新」。
**心智模型**：三個 `.cache/` 快取全部以日期為 key。同一天內：改了 Prompt → `ranked_*.json` 照舊命中；美股從未開盤跑到收盤後再跑 → `price_*.pkl` 照舊命中（含舊數據）。這正是 CI 兩次真實事故的共同根因（詳見 failure-archaeology），CI 因此一律 `--no-cache`。
- 正例：改 `ranker.py` Prompt 後本機驗證 → `--dry-run --yes --no-ai-cache`，log 出現 DeepSeek 呼叫而非「複用今日 AI 快取」。
- 反例（事故中的真實心態）：「快取是當日的，今天的數據當然是新的。」——「當日」是指快取建立那一刻的當日系統日期，不是數據的市場日期；台灣白天建立的 price 快取裝的是美股前一日收盤。
**完成定義**：能說出這次執行的每個快取是 hit 還是 miss、hit 到的是什麼時候建立的。

## 測試重置（README 記載的建議流程，先備份）

```powershell
if (Test-Path data\performance_history.json) { Copy-Item data\performance_history.json data\performance_history.backup.json }
if (Test-Path data\watchlist.json) { Remove-Item data\watchlist.json }
if (Test-Path data\performance_history.json) { Remove-Item data\performance_history.json }
python main.py --dry-run --yes
```

驗證完若不打算保留 runtime 副作用：`git checkout -- data/ docs/`。

再驗證（PowerShell）：`ls data, docs/data; if (Test-Path .cache) { ls .cache }`（fresh clone 尚無 `.cache/`；對照上表檔名是否仍一致）
