# 到期趨勢延伸出場（tracker.py DD-21）

> 核准日期：2026-08-06。經 skeptic / red-team / simplifier 三方抗辯審查後採用最小化 v2 版本。對應 `specs/tracker.md` DD-21、`specs/publisher.md` 新 DD（延長狀態顯示）。

## 背景

`data/performance_history.json` 累積 15 筆結算績效，統計顯示明顯的盈虧比倒掛：

- 8 筆獲利中 7 筆是 `FORCE_EXPIRED` 時間到期砍在小賺（平均 +2.96%），只有 1 筆真正觸及目標價。其中 PCAR 出場時仍在上升趨勢中、當日回報高達 +7.61%，代表系統在趨勢仍完好時就把部位砍了。
- 虧損單平均 -4.63%，幾乎全額吃滿止損（`ranker.py` DD-15 的止損緣設計本就緊貼買入區間下緣 2%）。
- 實現盈虧比約 1:0.82，與計畫盈虧比 1:2.5（DD-15 買入區間 vs 目標價的設計比例）嚴重倒掛：獲利單被到期機制過早砍在起漲階段，虧損單卻已吃到系統設計的止損上限，兩端不對稱地侵蝕整體期望值。

修法方向：不動止損/停利/移動停利任何既有優先序，只對「趨勢仍貼近峰值」的到期部位給予有界延長 + 更緊的追蹤停利，讓真正在跑趨勢的部位有機會兌現更大漲幅，同時用緊縮的回撤容忍度（3% vs 既有移動停利的 5%）確保不會把賺的又還回去。

## v1 原始設計（提交抗辯前）

- Gate 條件為雙條件並列：`收盤 > EMA10` **且** `收盤 > EMA20`（想比照 DD-12 動能策略買進邏輯用均線判斷「趨勢是否仍在跑」）。
- 新增專屬 exit_reason 常數 `EXIT_EXPIRED_TREND_WEAK`（在延長期間因跌破均線出場時使用，與原本 `FORCE_EXPIRED` 區分）。
- watchlist 條目新增三個持久化欄位：`is_extended: bool`、`extension_start_date: str`、`extension_days_used: int`，供 publisher 顯示延長進度。
- `EXPIRY_EXTENSION_MAX_DAYS` 與 `EXPIRY_TRAIL_RETRACE_PCT` 皆設計為可用環境變數覆蓋（`env: EXPIRY_EXTENSION_MAX_DAYS` / `env: EXPIRY_TRAIL_RETRACE_PCT`），比照 `MAX_ACTIVE_POSITIONS` 先例。

## 三方抗辯審查摘要

### simplifier（簡潔性）

- **雙條件冗餘**：`收盤 > EMA10` 與 `收盤 > EMA20` 在動能股正常多頭排列下高度共線（EMA10 通常已高於 EMA20），雙條件幾乎總是同時成立或同時破裂，並未提供雙條件本身承諾的額外訊號分辨力，只是多一次計算與多一個可能因 EMA 缺失而各自失效的風險點。
- **新枚舉不必要**：`EXIT_EXPIRED_TREND_WEAK` 與既有 `FORCE_EXPIRED` 在下游（`_archive_to_performance_history`、`analyzer.py` 統計、`publisher.py` 判斷分支）語意完全等價——都是「到期出場」，只是文案細節不同，不需要在 `performance_history.json` 的 schema 層面新增分類，徒增下游要同時處理兩個字串的負擔。
- **三個新欄位皆可推導**：`is_extended` = `active_days > hold_period`；`extension_days_used` = `active_days - hold_period`；`extension_start_date` 從未被任何顯示邏輯讀取（三方審查逐一核對 v1 draft 的 publisher 顯示規格，發現只用到前兩者的推導值，從未直接讀取這個欄位）。持久化三個可推導或未使用的欄位違反「不新增欄位除非必要」的專案慣例（對照 DD-18 捨棄「另外新增 `_last_counted_date` 欄位」的先例）。
- **env 過度彈性**：`EXPIRY_EXTENSION_MAX_DAYS`/`EXPIRY_TRAIL_RETRACE_PCT` 目前只有一組績效數據支撐（15 筆），尚無跨環境調參的實際需求；env 化會讓行為隨部署環境漂移，而目前連「3% 這個數字是否合理」都還在驗證階段，先寫死比較誠實。

### skeptic（正確性）

- **拆股副本吞旗標（blocker）**：`run_tracker()` 拆股分支會建立 `adj = dict(entry)` 副本並用 `adj` 呼叫 `_eval_status`/`_check_settlement`，但寫回 `is_extended`/`extension_days_used` 等新欄位若只寫在 `adj` 上，副本用完即丟，原始 `entry`（真正持久化到 watchlist 的物件）永遠不會被更新——這正是 DD-3/DD-12 反覆强調「adj 是臨時縮放、不寫回」的既有約束，v1 draft 的三個新欄位若照抄 `_apply_risk_controls()` 的雙寫模式（`adj` 與 `original_entry` 各自同步）需要新增第三組雙寫邏輯，但 v1 draft 並未在 `run_tracker()` 主迴圈中安排這段程式碼，形同新欄位永遠停留在初始值。
- **白名單無聲過濾（blocker）**：`strategy` 欄位若為未知值（歷史資料髒值、AI 輸出格式意外變動、或 `"-"` 預設值）在 v1 draft 的 gate 邏輯中，`收盤 > EMA10 and 收盤 > EMA20` 條件仍可能恰好成立而被誤判「可延長」——v1 draft 沒有明確排除非動能/突破策略，僅依賴均線條件天然不成立來間接擋掉反轉策略（反轉股進場點在 EMA50 下方，理論上不太可能同時站上 EMA10/EMA20），但這是隱性假設，未經測試驗證，且 `strategy` 拼字錯誤或未來新增第四種策略時會靜默通過。
- **到期已回撤者延長首日必死（major）**：v1 draft 未明確規定「到期當天」是否也要先檢查回撤幅度——若邏輯誤植為「只要到期就先給資格、下一天才檢查回撤」，會讓到期當天已經回撤超標的部位多賴一天才出場，多出一天的無意義風險暴露。
- **固定 3% 對高 ATR 股偏緊（major）**：L1 的 `MAX_ATR_PCT=8%` 上限意味著日均波動可達 8% 的個股仍會通過篩選，對這類股票而言，3% 回撤可能只是單日正常雜訊，會讓延長機制形同虛設（幾乎必定當天就被打出場），但目前樣本量（15 筆）不足以驗證更精細的 ATR 錨定門檻是否更優，暫不處理（見下方已接受的取捨）。

### red-team（安全與失效模式）

- **優先序閘門未定義（blocker）**：v1 draft 沒有明確指定「延長期間」與既有優先序 1~4（黑天鵝、止損、停利、移動停利）的執行順序關係——若不小心把延長判斷插在優先序 1 之前，會讓延長期間的部位繞過止損/停利判定，形成新的資金風險缺口。
- **EMA 缺失計數凍結（major）**：`_eval_status`/`_check_settlement` 讀取的 `ema10`/`ema20` 在資料不足 10/20 個交易日時為 `None`（見 `_fetch_latest` 的 `len(close) >= 20` 門檻），v1 draft 的均線 gate 條件在 EMA 為 `None` 時的行為未定義——若比較邏輯寫成 `price > ema10`，`None` 比較會拋 `TypeError` 中斷整個 `run_tracker()` 執行；若寫防禦性判斷又要新增分支，進一步增加複雜度。
- **槽位佔用未聲明（major）**：延長部位持續佔用 `MAX_ACTIVE_POSITIONS`（DD-20）的持倉名額，v1 draft 未討論此交互——若使用者預期到期即出場、名額釋放，卻發現部位又延長了 10 天不釋放名額，會誤以為系統有 bug。
- **strategy 缺值誤獲資格（minor）**：呼應 skeptic 的白名單問題，從失效模式角度補充：`entry.get("strategy")` 在極端情況下可能回傳 `None`（非字串），均線比較條件不會自然擋掉這種情況。

## v2 最終設計（本次實作採用）與捨棄方案對照

| 項目 | v1（捨棄） | v2（採用） | 捨棄原因 |
|---|---|---|---|
| Gate 條件 | `收盤 > EMA10` 且 `收盤 > EMA20` | `strategy in ("動能策略","突破策略")` 白名單 + `highest_close_since_active` 回撤 `< 3%` | 白名單消除 red-team「strategy 缺值/未知值誤獲資格」與 skeptic「隱性假設未驗證」；回撤比對複用既有 `highest_close_since_active`（已受 DD-12 拆股同標尺保證），避免 EMA `None` 的 TypeError 風險（red-team major），且單一條件比雙條件更貼近「趨勢是否仍在跑」的真實訊號（收盤價相對峰值的位置，而非兩條移動平均線的相對位置） |
| 出場分類 | 新增 `EXIT_EXPIRED_TREND_WEAK` | 沿用既有 `EXIT_EXPIRED`（`FORCE_EXPIRED`） | simplifier：下游語意完全等價，新枚舉只增加維護面 |
| 持久化欄位 | `is_extended`/`extension_start_date`/`extension_days_used` 三個新欄位 | 零新增欄位；延長態完全由 `status=="active" and active_days >= hold_period` 推導，延長天數 = `active_days - hold_period` | simplifier：三者皆可從既有 `active_days`/`hold_period` 推導或從未被讀取；同時解決 skeptic 的「拆股副本吞旗標」blocker——不寫新欄位就不存在雙寫遺漏的風險面 |
| 硬上限/回撤門檻可調性 | env 變數 | 模組頂部寫死常數 | simplifier：15 筆樣本不足以支撐跨環境調參需求；skeptic 的「固定 3% 對高 ATR 股偏緊」問題已知但暫不處理（見下方已接受的取捨），寫死更誠實地反映「這是待驗證的初步假設」 |
| 到期當天回撤檢查時機 | 未明確定義 | **到期日當天就先檢查回撤**：已回撤 ≥3% 當天照舊出場，只有貼近峰值（<3%）者才延長 | skeptic「延長首日必死」的反面規範——修正為到期當天回撤已達標者維持原行為（不多賴一天），只有真正貼近峰值者才進入延長態，行為對「到期當天回撤已超標」的部位與 DD-21 之前完全一致 |
| 與優先序 1~4 的關係 | 未定義 | 延長判斷放在函式最末段（優先序 5 到期檢查內部），優先序 1~4（黑天鵝/止損/停利/移動停利）永遠先執行，延長邏輯只決定「優先序 5 判定到期時，出場還是繼續跑」 | red-team：明確消除「延長期間繞過止損/停利」的資金風險缺口 |
| 名額佔用交互 | 未討論 | 明文接受：延長部位持續佔用 `MAX_ACTIVE_POSITIONS` 名額，不釋放 | red-team 提出後，經與使用者討論，判定為忠實記帳（部位確實還在，名額本就該反映真實持倉），非 bug（見下方已接受的取捨） |

## 已接受的取捨（明知但不在本次處理）

1. **固定 3% 不做 ATR 錨定**：高 ATR 股票（L1 上限 8%）的 3% 回撤門檻可能偏緊、延長機制對這類股票近乎形同虛設。skeptic 已指出，但目前僅 15 筆結算樣本，尚無足夠證據支撐更精細的「回撤門檻 = k × ATR%」設計該取何值的 k。待延長機制運行一段時間、累積更多樣本後再評估是否需要 ATR 錨定（比照 `ranker.py` DD-19 的既有精神：先用簡單版本累積數據，再迭代）。
2. **延長加劇 MAX_ACTIVE_POSITIONS=5 槽位佔用**：延長部位持續佔用組合層持倉名額，可能讓「明日掛單計畫」的可用名額比預期更少。這是忠實記帳的必然結果——真實部位仍持有中，名額本就該反映真實倉位；不因此設計「延長部位不計入名額」的例外（會製造名額計數與真實持倉不一致的新缺陷，違反 DD-20 的「報告名單 = 次日進場資格」單一事實來源原則）。
3. **`hold_period<=0` 異常值也可能獲延長**：`_parse_hold_period()` 已有下界 1（DD-19），上游異常值防護已存在；DD-21 的延長邏輯只在 `hold_period` 正常解析後才觸發，不需額外處理。

## Interface 變更（供 specs/tracker.md 對照）

模組頂部新增兩個常數（不進 env）：

```python
EXPIRY_EXTENSION_MAX_DAYS = 10     # 到期後最多再延長的交易日數（硬上限）
EXPIRY_TRAIL_RETRACE_PCT  = 0.03   # 延長資格與延長期間出場：自最高收盤回撤 3%
```

`_check_settlement()` 第 5 段（持倉到期）改為五分支判斷（詳見 `specs/tracker.md` DD-21 與「結算優先順序」節），函式簽名、其餘優先序（1~4）與所有既有分支逐字元不變。

## publisher.py 顯示變更

- `_tracking_row` active 分支：`active_days > hold_limit` 時，狀態文字改為「持倉 N 天（已到期延長 第 X/10 天）」，並在價格列附加「延長停利線」（`highest_close_since_active × (1 - 0.03)`）。
- `_settled_row`：`FORCE_EXPIRED` 且 `active_days > hold_period`（用 `tracker._parse_hold_period` 解析）時，文案改為「到期延長後趨勢轉弱，強制出場」，區分純到期與延長後出場兩種情境。
- `_INFO_HTML`「⏰ 到期出場」列改寫為完整靜態說明，比照既有 DD-6/DD-20 慣例寫死數字（3%、10 天），不插值 runtime 常數。

## 驗證

- `tests/test_tracker.py` 新增 `TestCheckSettlementExpiryExtension`（9 案例），實測 fail-then-pass：暫時 stash `src/tracker.py` 變更後執行新測試，2 個核心延長案例（`test_momentum_at_expiry_close_to_peak_grants_extension`、`test_breakout_strategy_at_expiry_close_to_peak_grants_extension`）在舊碼上紅（`AssertionError: assert ('FORCE_EXPIRED', 98.5) is None`），其餘 8 案例因測試場景本身在到期優先序更早的判定中已觸發（止損/停利）或本就符合舊行為（反轉策略/未知策略到期即出場）而在舊碼上已綠，實作還原後 `pytest tests/` 全數 173 passed。
- 既有 `test_force_expired_on_hold_period_reached` 因 DD-21 白名單生效（原測試 fixture 預設 `strategy="突破策略"` 且股價未回撤，會被誤判為「獲延長」而非到期出場）改為顯式指定 `strategy="反轉策略"`，維持其原本「通用到期機制」測試意圖，不受 DD-21 白名單影響。
- `python src/publisher.py` 重新生成 `docs/index.html`，`tests/test_publisher_info_sync.py` 全等守門通過。
