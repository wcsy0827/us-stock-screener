# 組合層級持倉上限（MAX_ACTIVE_POSITIONS）：觸價排序擇優進場

> 核准日期：2026-07-10。對應 `specs/tracker.md` DD-20、`specs/publisher.md` DD-8。
> **語意修訂**：本檔的「事後擇優」名額競爭已於同日修訂為「事前掛單名單制」，
> 見 `plans/2026-07-10-order-plan-roster.md`（DD-20 v2）；本檔保留作 v1 決策軌跡。

## Context

使用者觀察：「當每天累積的觀察清單越來越多，有可能一天就全部落入買進區間，變成持有的標的非常多，對應到真實操作我不太可能這樣做。」

排查當日 `data/watchlist.json`：16 筆條目、**12 筆 active、4 筆 watch**，全部動能策略。關鍵訊號是大多數 active 條目的 `watch_days` 為 0 或 1——幾乎每支股票進入觀察清單後 1 天內就觸價成交。根因是三個各自合理的設計疊加：

1. **DD-19 淺回檔買進區間**：上緣 `Close − 0.25×ATR`，離現價極近，日內正常雜訊即觸及
2. **DD-19 盤中觸價成交**：`today_low <= buy_zone_upper` 即判進場，成交率接近 100%
3. **每日流入 3 支**（L3 上限）× **持有期常見 15 交易日**

穩態推算：3 支/日 × 15 日 ≈ **45 支同時持倉**，當時的 12 支只是爬坡期。`performance_history.json` 的績效統計因此隱含「資金無限」假設，與使用者實際操作（同時最多持有數支、滿倉不掛新單）根本對不上。

### 為什麼不是「篩選過鬆」（使用者曾提問）

單日漏斗 503 → L1 478 → L2 57 → AI 3（0.6%），已極挑剔。真正的兩個結構性因素：

- **存量 vs 流量**：持倉穩態 = 每日進場數 × 平均持有天數，與篩選嚴格度無關。就算收緊到每天 1 支，穩態仍 15 支；想單靠入口壓到 5 支以內須壓到三天一支，報告多數日子空白、analyzer 樣本累積停滯——用「訊號品質」槓桿做「容量控制」的事，槓桿錯了。
- **成交率是被刻意調高的**：DD-19 之前的深回檔帶（EMA20~EMA10）有天然節流效果，但那是**隨機節流**（錯過的往往是最強的股票，即當時「死於已追高」的抱怨）。走回頭路不划算——與其靠「隨機不成交」限倉，不如靠組合層「排序擇優」限倉。

### 業界對應

系統化交易機構的標準架構是**訊號產生層與組合建構層分離**：訊號層允許多產，紀律放組合層。訊號多於名額時按強度排序取前 N（ranking-based selection）。本案是該架構的最小可行版本，為三步演進路線的第 1 步：

1. **最大持倉數上限 + 觸發時排序擇優**（本案）
2. 產業集中度上限（同產業 active ≤ 1~2）——未來評估
3. 總風險預算（portfolio heat：以 `stop_loss` 距離計算每筆開放風險，總和超門檻停收新倉）——未來評估

上限預設值 5 由使用者確認（單一部位約 20% 資金的常見個人帳戶配置）。

## 探索結論（決定設計形狀的關鍵事實）

- `run_tracker()` 的 E 步驟是**單一迴圈、逐條目即時轉換**（`_eval_status` 回傳後立即寫回 status 並套用進場副作用/結算），沒有「先知道全部轉換候選再擇優」的天然時點 → 不用 pre-pass，改用「優先序排序迭代 + 名額計數器」。
- `ai_confidence`/`l2_score` 已存在每筆 watchlist 條目（B/C `base` 字典寫入），優先序不需新資料管道。
- publisher 已有自 tracker import 常數的單一事實來源先例（`_max_watch_days`、`TRAILING_*`）。
- `docs/index.html` 由全等比對測試守門 → `_INFO_HTML` 不得插值 runtime 常數。

## 最終設計

### tracker.py

1. `MAX_ACTIVE_POSITIONS = int(os.getenv("MAX_ACTIVE_POSITIONS", "5"))`（模組頂部，`MIN_AI_CONFIDENCE` 慣例）。
2. `_slot_priority_key(entry)`：`(-(ai_confidence or 0), -(l2_score or 0), symbol)`。
3. E 迴圈前：`active_count`（`status=="active"` 條目數）→ `free_slots = max(0, 上限 − active_count)`；迴圈改為 `for entry in sorted(watchlist, key=_slot_priority_key)`（條目就地變異，存檔順序不變）。
4. 名額閘門（`_eval_status` 回傳後、status 寫回前）：`new_status=="active" and prev_status=="watch"` 時——有名額扣 1 照常進場；滿倉則以 `settlement_entry` 重跑 `_eval_status(..., today_low=None)` 取收盤價判定：invalid（跌破止損/已追高）→ 照 invalid 處理；否則強制 watch + `slot_blocked_today=True`。
5. 旗標於迴圈體最頂端重置（早於 `sym not in latest` 的 continue）；B/C `base` 字典含 `"slot_blocked_today": False`。
6. 當日結算不退還名額；超額不強平；B/C 新訊號不受影響。

### publisher.py（前端畫面）

1. watch 列被擋時：「第 N 天（今日觸價但持倉已滿 X 支，未進場；剩 M 天自動移除）」。
2. 有效追蹤清單標題附 note「上限 X 支」。
3. 今日統計有效持倉格改「N / X」（持倉／上限）。
4. `MAX_ACTIVE_POSITIONS` 自 tracker import。
5. `_INFO_HTML` active/watch 列改靜態文字描述名額規則，執行 `python src/publisher.py` 再生成 index.html。

## 抗辯審查發現（Plan agent 壓力測試，全部已納入實作）

| # | 嚴重度 | 發現 | 處置 |
|---|--------|------|------|
| F1 | 中 | 旗標若在評估階段才重置，`sym not in latest` 的 continue 會殘留昨日 True，前端誤顯「今日觸價」 | 重置移至迴圈體最頂端，加回歸測試 |
| F2 | 低 | B/C reset 展期路徑 `update(base)` 不清旗標，新買入區間下殘留誤導性 True | `base` 加 `"slot_blocked_today": False`，加回歸測試 |
| F3 | 低 | `_INFO_HTML` 插值 runtime 常數會讓 `.env` 不同的環境跑 index.html 全等測試誤紅 | `_INFO_HTML` 只寫靜態文字；每日報告（非守門範圍）才用 runtime 常數 |
| F4 | 低（文件） | blocked 重跑讓 DD-19 宣告「實務不可達」的收盤價分支重新可達，未修訂措辭會誘發未來誤清理 | DD-19 措辭修訂 + DD-20 明文記錄 |
| F5 | 低（接受） | 同日手動重跑：run 1 結算條目已移出，run 2 `active_count` 較低可能放行 run 1 被擋者（名額後門退還）；run 2 才放行者因 DD-18 守衛 `active_days` 當日不遞增 | 僅手動重跑路徑、影響有界，記錄於 DD-20 不另做機制 |

另在探索中發現一個**範圍外的既有 latent quirk**（不在本次修復）：拆股路徑 `adj["status"]` 在同日 watch→active 後未同步為 active，使拆股條目的當日 gap-through 結算延後一天——非本 feature 造成，依「分次修改」慣例留待獨立 commit。

## 考慮過但捨棄的方案

- **當日結算即退還名額**：違反「掛單當下不可知未來」的現實語意（使用者依前晚報告掛單）。
- **強平超額 active 部位**：人為製造非市場事件的出場紀錄，污染績效資料庫。
- **滿倉時擋下新 L3 訊號（B/C 不入 watch）**：報告失去 AI 判斷完整性，次日名額釋出時無候選可用。
- **迴圈前 pre-pass 預先評估全部條目再排序**：`_eval_status` 連同拆股 adj 邏輯需執行兩次，複雜度高於「排序迭代 + 計數器」且無行為差異。
- **被擋條目一律維持 watch、不做收盤價重跑**：收盤已跌破止損的死訊號會在次日以遠高於現價的 `buy_zone_upper` 幽靈進場、即時 CLOSED_LOSS，把使用者從未持有的虧損寫進績效（使用者沒掛單，該筆交易不存在）。
- **走回 DD-12 深回檔帶靠「隨機不成交」節流**：隨機節流錯過的往往是最強標的（DD-19 的立案根因），排序擇優是嚴格優於它的節流方式。
- **收緊 L2/L3 入口（調高門檻）作為容量控制**：穩態 = 流量 × 持有天數，入口寬窄不改變這個數學；且犧牲 analyzer 樣本累積。
- **`last_run.json` 加 `active_count`/`max_positions` 欄位**：`_write_last_run` 不接觸 categories，需擴 plumbing；今日統計已呈現同一資訊。

## 驗證

- `pytest`：151 通過（新增 16 個 DD-20 tracker 測試 + 4 個 DD-8 publisher 測試，既有 DD-19 回歸測試不變）。
- `python src/publisher.py` 再生成 `docs/index.html`，全等守門測試綠。
- `python main.py --dry-run --yes` 實跑：12 active > 上限 5 → `free_slots=0`、無強平、觸價 watch 印被擋 log、報告顯示「12 / 5 持倉」。
