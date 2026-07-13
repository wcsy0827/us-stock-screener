---
name: validation-and-qa
description: 觀察到以下任一狀態時載入：即將宣稱某個修改「完成」或「已驗證」；準備 commit / 開 PR；測試紅燈需要判斷修復方式；需要決定一個改動要用什麼證據支撐。
---

# 驗證與證據標準

事實時間戳：2026-07-13（pytest 實測 164 passed, 3.23s，離線）。

## 證據等級（本 repo 的收工門檻）

| 改動類型 | 必要證據 |
|---|---|
| `tracker.py` / `analyzer.py` 判斷邏輯 | 新增對應 pytest 案例 + 全套 `pytest` 綠（CLAUDE.md 明文：改 tracker 邏輯或新增 DD 須同步補測試）。fail-then-pass（先證明舊碼會紅）是本技能庫在其上加嚴的建議標準，非 CLAUDE.md 原文 |
| `ranker.py` Prompt | `python main.py --dry-run --yes --no-ai-cache` 實跑，人工檢查 AI 輸出是否符合新規則（例：止損確實低於買入區間下緣）。Prompt 是文字不是程式，pytest 只能守欄位/字樣，實際效果必須看真實 DeepSeek 回覆 |
| `publisher.py` 模板/靜態文字 | `python src/publisher.py` 再生成 + `pytest`（全等守門）+ 目視報告 HTML |
| `market.py` / `scorer.py` / `filter.py` | 對應單元測試（tests/test_market.py、test_filter.py 已有先例）+ `--dry-run --yes` 實跑對照 log |
| CI workflow | YAML 語法檢查 + 合併後盯下一次真實執行 log（見 ci-operations） |

## 規則：禁止的「偽驗證」

**觸發**：想快速確認改動沒弄壞東西。
- **不要跑 `ast.parse` / 純語法檢查迴圈**當驗證（CLAUDE.md 明文禁止）。語法對 ≠ 行為對。
  - 反例（repo 歷史上真實出現過的做法，出處 `plans/2026-07-02-stop-loss-buffer-fix.md` 驗證節第 1 條，後被慣例禁止）：「沒有 tests/ 目錄，用 `ast.parse` 確認 ranker.py 無語法錯誤。」——現在有 tests/ 了（164 個），且 `--dry-run` 可實跑驗證大半流程（需連網），沒有藉口。
  - 正例：改了 `_eval_status()` → 在 `tests/test_tracker.py` 加一個重現舊 bug 的案例，先看它紅，再看它綠。
- **不要憑記憶引用 PR/issue 編號**：回報前必先 `gh pr list`（或 `gh pr view <n>`）確認存在（CLAUDE.md 明文）。
- 測試綠是必要非充分：涉及報告呈現的改動要實際開啟生成的 HTML 確認。

## 規則：dry-run 副作用的處理

**觸發**：驗證用的 `--dry-run` 跑完，`git status` 出現 data/、docs/ 的變更。
**步驟**:這些是驗證的 runtime 副作用，不是你的改動。除非該 PR 本來就要更新報告資料，否則 `git checkout -- data/ docs/` 還原後再 commit（repo 慣例，多份 plan 文件均載明「驗證後還原不入 commit」；docs/index.html 例外——若你改了 publisher 靜態文字，再生成的 index.html 必須同 commit）。
**完成定義**：`git diff --stat` 只含你有意提交的檔案。

## 規則：test_publisher_info_sync 紅燈的唯一修法

**觸發**：pytest 在 `tests/test_publisher_info_sync.py` 失敗（docs/index.html 與 `_build_index()` 輸出不一致）。
**步驟**：執行 `python src/publisher.py` 一鍵再生成，將 index.html 與 publisher.py 改動放同一個 commit。**不要手工編輯 index.html 去湊**，也不要改測試遷就漂移。
**完成定義**：pytest 綠且 `git diff docs/index.html` 內容與模板改動語意一致。

## 提交前檢查清單

1. `pytest` 全綠（2026-07-13 基準：164 passed；數字會隨測試增加成長，紅燈=未完工）。
2. 文件同步：CLAUDE.md / README.md / 對應 `specs/*.md` 是否需要同 commit 更新（見 spec-first-docs-gate）。
3. `_INFO_HTML` 是否受影響（改了 L1/L2/L3 定義、Regime 邊界、狀態機規則就受影響）→ 再生成 index.html。
4. `git diff` 內無 `.env`、`.cache/`、非預期的 data/ 變更。
5. tracker.py 與 scorer.py 不在同一個 PR（CLAUDE.md 禁止事項，難以隔離問題）。

再驗證：`python -m pytest -q`（預期全綠且秒級完成、不連網）
