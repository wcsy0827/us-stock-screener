---
name: run-and-operate
description: 觀察到以下任一狀態時載入：需要在本機執行選股流程或驗證改動；不確定該帶哪些 CLI 旗標；報告日期與今天日期不一致；同一天需要重跑；想預覽前端頁面。
---

# 執行與日常操作

事實時間戳：2026-07-13（依 main.py、README、CLAUDE.md 驗證）。

## 指令矩陣（全部經 main.py parse_args 驗證存在）

```powershell
python main.py --dry-run              # 完整流程，不 git push（互動式重跑確認）
python main.py --dry-run --yes        # 跳過「今日已執行過」確認（CI/腳本用）
python main.py --dry-run --no-cache   # 重下 price/info + 重問 AI（三快取全跳過）
python main.py --dry-run --yes --no-ai-cache   # 只重問 DeepSeek，price/info 快取複用
python main.py --dry-run --top 10 --min-score 65
$env:PYTHONUTF8=1; python main.py     # 正式執行：生成 HTML 並由 publisher 直接 git push
.\run.ps1 --dry-run                   # Windows 包裝（自動設 PYTHONUTF8）
```

## 依場景選指令（選錯旗標 = 白跑或誤診）

**觸發**：改了程式碼要驗證，或懷疑輸出有問題。

| 你剛改了什麼 | 用什麼跑 | 為什麼 |
|---|---|---|
| `scorer.py` / `filter.py` | `--dry-run --yes` | 只重跑評分；但注意 AI 快取以日期為 key、不感知候選池內容——候選池變動大時要加 `--no-ai-cache`，否則 AI 精選還是舊的 |
| `ranker.py` Prompt 或策略 | `--dry-run --yes --no-ai-cache` | 不加就讀到舊 AI 快取，**你會誤以為修正無效**（PR #46 後真實發生過，見 failure-archaeology） |
| `tracker.py` / `publisher.py` | 先 `pytest`，再 `--dry-run --yes` | 這兩個模組不涉及快取 |
| 懷疑市場數據本身有問題 | `--dry-run --yes --no-cache` | 全部重下 |

**完成定義**：log 中確認走到預期路徑（例如加了 `--no-ai-cache` 後**不再**出現 `[ranker] 複用今日 AI 快取`），且產出的報告/watchlist 反映改動。

## 報告日期心智模型（最常見的「假 bug」）

**觸發**：報告標題日期比今天舊、或「跑了但沒有新報告」。

- 報告日期 = `market_date` = SPY 最後**完整收盤**交易日，不是系統時鐘。台灣時間白天/晚上跑，美股未收盤（美東 16:15 前），`trim_incomplete_session()` 會捨棄當日殘缺 K 棒，日期自動回退前一交易日——**這是設計行為，不是 bug**。
- 美股 N 日的完整數據要到 UTC 20:00+（台灣 N+1 日凌晨 04:00 後）才存在；每日排程 UTC 21:30 產出 N 日報告。
- 正例：台灣 7/1 22:00 手動跑得到 6/30 報告 → 正常，收工。
- 反例（觀察過的合理化）：「報告日期不對，我來把日期改成 `datetime.now()`。」——絕對禁止。`stats["date"]` 必須來自 `market_date`（CLAUDE.md 設計決策 12）；改成系統時鐘會產生「標題 7/1、內容 6/30」的錯誤標籤，且本機 UTC+8 與 CI UTC 行為分裂。

## 同日重跑與副作用

- `check_already_run_today()` 以 **UTC 日期**判斷（`tracker.py:57`），台灣本地與 CI 行為一致。
- 同日重跑會自動取代當日新增的 watch 條目（`date_added == today`），**不需手動清 watchlist**；跨日條目的 `watch_days`/`active_days` 已依 `tracked_dates` 去重，重跑不虛增（DD-18）。
- `--dry-run` 仍會**真實寫入** `data/watchlist.json`、`data/performance_history.json`（若有結算）、`docs/reports/*.html`、`docs/data/last_run.json`。若只是驗證、不打算 commit 這些 runtime 資料，驗證後用 `git checkout -- data/ docs/` 還原（本 repo 既有慣例：「驗證後還原 dry-run 副作用，不納入 commit」）。**例外**：若你這次改了 `publisher.py` 靜態文字/模板，再生成的 `docs/index.html` 是守門測試要求**同 commit 保留**的，不得一併還原（見 publisher-frontend-sync）。
- 完全重置追蹤狀態：刪 `data/watchlist.json` 與 `data/performance_history.json` 再跑——performance_history **未 commit 的增量刪了就沒了**（已 commit 部分可從 git 還原），先備份（見 data-and-caches）。

## 前端預覽

`file://` 直開 `docs/index.html` 時 `fetch()` 被瀏覽器安全限制擋下（上次執行時間、資料核實面板空白）。完整預覽：

```powershell
python main.py --dry-run --yes
cd docs; python -m http.server 8080   # 開 http://localhost:8080
```

再驗證：`python main.py --dry-run --yes`（預期產出 docs/reports/<market_date>.html 且 log 無紅字）
