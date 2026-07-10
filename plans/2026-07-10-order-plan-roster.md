# 明日掛單計畫：前端決策區段 + tracker 事前名單制對齊（DD-20 v2 修訂）

> 核准日期：2026-07-10（同日於 `plans/2026-07-10-max-active-positions-cap.md` 的 v1 之後）。對應 `specs/tracker.md` DD-20（原地修訂為 v2）、`specs/publisher.md` DD-9。與 v1 同屬 PR #65。

## Context

系統未串接下單系統，使用者靠晚間報告人工決定隔日掛單。v1（持倉上限 + 事後擇優）合入同一 PR 後，使用者提出：「當需要使用 AI 信心分數排序來決定變成 active 的時候，系統內雖然可以判斷，但我從當天的報告看不出來。」站在人工下單角度盤點後發現兩個問題：

### 問題 1：報告缺少掛單決策資訊

使用者隔日早上需要回答三個問題，報告當時都答不了：

1. **明天可以掛幾張單？**（free_slots = 上限 − active 數）——報告只有「12 / 5 持倉」，需自行心算
2. **名額不足時掛哪幾檔？**——留意清單列不顯示 `ai_confidence`/`l2_score`、排序是加入順序非優先序；明日實際競爭池是「既有 watch + 今日新進 + 重新入選」的聯集，散在兩種版面（track-item 列 vs 股票卡片）無統一視圖
3. **每張單的參數？**——掛單價（買入區間上緣）、止損、目標分散兩處

### 問題 2：模擬與真實掛單錯位（帳實不符，比前端更深一層）

v1 的「事後擇優」語意：當日觸價者中信心高者得名額。但使用者只能**事前**掛前 N 名的單：

> 名額 2、排序 A>B>C>D。使用者早上掛 A、B 的限價單。當日只有 C 觸價回落，A、B 沒觸價。系統（v1）判 C 進場，但使用者根本沒掛 C 的單——這筆交易進了 `performance_history.json`，真實帳戶裡卻沒有它。

與 DD-17（虧損漏記）、DD-19（真實成交未追蹤）修復的是同一類「績效資料庫 ≠ 真實帳本」缺陷，方向相反（多記而非漏記）。

使用者經 AskUserQuestion 確認採**同步對齊**方案：tracker 進場資格改為「事前名單制」，僅優先序前 `free_slots` 名可轉 active，與報告「✅ 建議掛單」完全一致。

## 最終設計

### tracker.py（DD-20 原地修訂為名單制；PR 未合併不另立新 DD）

1. **新純函式 `compute_order_plan(watchlist)`**：`free_slots = max(0, MAX_ACTIVE_POSITIONS − active 數)`；`roster` = 全部 watch 條目依 `_slot_priority_key` 排序（含名額外備援供報告顯示）；`eligible` = 前 `free_slots` 名 symbol 集合。
2. **E 步驟**：開頭呼叫一次取 `eligible`；迴圈**還原為普通迭代**（v1 的優先序排序迭代與 `free_slots` 計數器整組移除——名單大小 ≤ 名額，同日全數轉換也不會超限，資格事前確定後排序迭代已無作用，程式反而更簡單）。
3. **閘門**：`new_status=="active" and prev_status=="watch" and sym not in eligible` → blocked（收盤價重跑 `_eval_status(..., today_low=None)`：invalid → 作廢；否則維持 watch + `slot_blocked_today=True`，log 改「觸價但未在掛單名單」）。名單內無條件放行。
4. **`categories["order_plan"]`**：G 步驟移除後對最終 watchlist 再算一次，供 publisher 渲染。
5. **確定性（不持久化）**：晚間 order_plan 與次日 E 開頭重算作用在同一份存檔（active 數與 watch 集合無變動、純函式），結果必然相同。已知邊界：兩次執行間改 env `MAX_ACTIVE_POSITIONS` 會使名單偏移（正常環境固定，接受）。

### publisher.py（DD-9）

1. **`_order_plan_section(order_plan)`** 純函式：roster 空 → 隱藏；標題註記「明日可進場名額 N 支」（0 時「名額 0，持倉已滿，明日不建議掛新單」但名單照列）；每列 = 排名、代號、名稱、策略、✅ 建議掛單（前 N，綠框）/⏸ 備援（黃框）、信心/L2、**掛單價（buy_zone_upper）**、買入區間、止損、目標、剩餘觀察天數（`_max_watch_days`）。置於新進/重新入選之後、今日統計之前。
2. 留意清單 watch/invalid 列補「信心 X/10｜L2 Y 分」（`_conf_l2_str` 共用 helper，缺值 N/A）。
3. 被擋註記文字統一為「今日觸價但未在掛單名單，未進場」（滿倉與被排擠統一為「不在名單」）。
4. `_INFO_HTML` active/watch 列改名單制口徑（靜態文字），index.html 再生成。

## 既有測試相容性核驗（實測確認）

v1 的 16 個 DD-20 測試**全數不需修改即通過**——每案觸價者要嘛在名單內、要嘛名額為 0，兩種語意結果相同。行為差異只在「名單內沒觸價、名單外觸價」的新情境（v1 放行、v2 擋下），以 `test_out_of_roster_touch_blocked_even_with_free_slot` 覆蓋。publisher 端 `test_blocked_text_reflects_patched_cap` 因新文字不含上限數字而移除，職責由 `TestOrderPlanSection` 的名額顯示測試接手。

## 考慮過但捨棄的方案

- **維持事後擇優、只做前端**（AskUserQuestion 另一選項）：帳實不符持續存在，報告的「建議掛單」與系統模擬資格不一致，前端資訊反而誤導。
- **名單持久化寫入 watchlist**：確定性重算已保證報告與次日資格一致，持久化徒增欄位與失步風險。
- **publisher 自行從 categories["watch"]+["new"]+["reset"] 重建名單**：欄位名不一致（`confidence` vs `ai_confidence`）、reset 條目不在 watch 分類、重複實作排序，違反單一事實來源。
- **被擋註記顯示上限數字**：名單制下「不在名單」才是準確語意（可能是滿倉、也可能是被排擠），上限數字已由名額註記呈現。

## 驗證

- `pytest`：164 全數通過（v1 的 151 + 新增 9 個 tracker、4 個 publisher，扣除 1 個改寫）。
- `python src/publisher.py` 再生成 index.html，全等守門綠。
- `python main.py --dry-run --yes` 實跑：12 active > 上限 5 → 名額 0，報告出現「📋 明日掛單計畫（名額 0，持倉已滿）」與完整優先序名單、留意清單列含信心/L2；runtime 資料驗證後還原不入 commit。
