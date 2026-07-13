---
name: spec-first-docs-gate
description: 觀察到以下任一狀態時載入：需求與現有規格衝突；準備實作新功能或改變既有行為；Plan Mode 的 plan 已核准執行完；發現規格/CLAUDE.md/README 與程式碼描述不一致；想引用或新增 DD 編號。
---

# Spec-First 工作流與文件同步義務

事實時間戳：2026-07-13。

## 基本流

```
需求 → 更新/新增 specs/<module>.md → 實作 → PR 引用規格節次
```

模組↔規格對照（全部檔案已驗證存在）：scorer/tracker/ranker/market/pipeline/analyzer/publisher/earnings 各有同名 `specs/*.md`；`fetcher.py` 掛 `specs/pipeline.md`（快取節）；`filter.py` 掛 `specs/pipeline.md`（L1 節）+ `specs/earnings.md`（財報牆）。新模組從 `specs/_template.md` 複製起步。

## 規則：DD 是已解決的爭議，不得繞過

**觸發**：你的方案與某條 Design Decision 相抵觸，或「更好的做法」恰好是某 DD 已捨棄的選項。
**步驟**：先讀該 DD 與其連結的 `plans/YYYY-MM-DD-*.md` 完整版（捨棄理由都在裡面）→ 若仍認為該翻案，**先取得用戶同意、先改規格**，再動程式碼。
- 正例：想在 Python 端強制修正 AI 回傳的 stop_loss → 查到 ranker DD-15 的 plan 已明確捨棄此方案（理由：AI 敘述與實存數字不一致、除錯更難）→ 改為修 Prompt 措辭。
- 反例（DD 檔案中記錄過的合理化模式）：「這只是小修，直接在 tracker 端把不合理的值 clamp 掉就好，不用動規格。」——clamp 正是被否決過的「Python 端修正」變體；沿用被否決方案而不知情，是本 repo 設 plans/ 資料夾要防的第一號事故。
**完成定義**：PR 描述引用了規格節次；若翻案，規格的 DD 已更新並說明為何改變。

## 規則：Plan 文件化雙檔義務（同一 commit）

**觸發**：任何經 Plan Mode 核准並執行完成的 plan。
**步驟**：①plan 全文（含探索過程、捨棄方案）存 `plans/YYYY-MM-DD-<slug>.md`；②對應 `specs/<module>.md` 新增 DD-N 濃縮版（最終選擇/原因/捨棄方案），結尾 `→ 詳見 plans/YYYY-MM-DD-<slug>.md` 互連。**不得只做其中一份**——`~/.claude/plans/` 不在版控內，session 結束即失傳。
**完成定義**：同 commit 內同時出現 plans/ 新檔與 specs/ 的新 DD 段落。

## 規則：CLAUDE.md / README 同 commit 更新

**觸發**：改動影響架構速覽、模組對照、快取行為、L2 評分表、Regime 表、狀態機、專案結構任一描述。
**步驟**：同一 commit 內更新 CLAUDE.md 對應章節與 README 對應章節，不遺留過時描述（CLAUDE.md 禁止事項明文）。前端 `_INFO_HTML` 是第三份要同步的（見 publisher-frontend-sync）。
**完成定義**：`git show --stat` 中程式碼、規格、CLAUDE.md/README（必要時 + index.html）同批出現。

## DD 編號的已知地雷（引用前必讀）

- **DD 編號是 per-spec 的，跨檔會撞名**：`specs/ranker.md` DD-20（L3 精選 5→3）與 `specs/tracker.md` DD-20（持倉上限名單制）是**兩件無關的事**。引用時必須帶模組名（「tracker DD-20」）。
- **程式碼註解的 DD 編號可能與 spec 編號錯位**：`tracker.py` 內有一處 fallback 跳過邏輯註解標 DD-20，但其完整敘述在 `specs/ranker.md` DD-18（tracker 只是消費端，經明確決策不另立 DD，見 plans/2026-07-07-market-context-single-etf-resilience.md）。以 spec 為權威，程式碼註解編號只是路標。
- **DD 可原地修訂**：tracker DD-20 v1（事後擇優）同日被 v2（事前名單制）原地修訂，v1 的 plan 檔保留作決策軌跡並在檔頭標註已被修訂。讀 plan 檔先看檔頭有無「已修訂」註記。

## 需求不明時

多種合理解讀 → 先問用戶（AskUserQuestion），不得臆測擴充範圍（CLAUDE.md 慣例；DD-20 v2 正是用戶經 AskUserQuestion 選定方案的實例）。

再驗證：`ls specs plans`（對照對照表與雙檔義務是否仍成立）
