# 突破策略與反轉策略買進區間改善（選項 A：強化 AI 決策資料）

> 狀態：已執行完成，對應 `specs/ranker.md` DD-13

## Context

延續動能策略買進區間改善（`plans/2026-07-01-momentum-buy-zone-ema-anchor.md`、`specs/ranker.md` DD-12）的同一根因排查，這次檢查突破策略與反轉策略發現問題**更嚴重**：

- **突破策略**：System Prompt（`ranker.py` 原第 539-543 行）用「突破點附近前後 1%」描述買入區間，但候選池表格完全沒有「20 日高點」這個突破基準的實際美元數值——`high_20d` 在 `compute_indicators()` 早已算好，只用於內部 `_strategy_tag()` 判斷，從未曝露給 AI。
- **反轉策略**：System Prompt（原第 545-549 行）明文寫「`stoch_k < 25` 且 `RSI 從低位回升（rsi > rsi_5d_ago）`」「買入：EMA50 附近支撐區」，但 `stoch_k`、`rsi_5d_ago`、`ema50`、`low_20d` 這四個值全部只存在於 `compute_indicators()` 回傳的 dict 中供內部函式使用，一個都沒有出現在送給 AI 的 Markdown 表格裡。AI 現在等於是憑空編出這些判斷條件的答案。

使用者提供的《股票操作高勝率買進區間策略指南》給出具體規則：
- 突破策略：「回測確認區（原壓力變支撐，站上突破線 1%~2% 內止跌）」優於「突破當日緩衝帶（壓力位之上 0.5%~1.5%，超過 3% 視為追高）」；需搭配「突破當日成交量 > 20 日均量 1.5~2 倍」確認
- 反轉策略：右側交易「Higher Low」與「50%~61.8% 斐波那契回撤」屬形態辨識（需偵測「第一波反彈」的高低點），超出現有「單筆技術指標快照」的資料設計範疇，本次不實作；改為聚焦在讓現有 Prompt 已經引用、但 AI 看不到數字的四個指標（EMA50、Low_20D、Stoch_K、RSI_5D_Ago）真正可用

## 考慮過的方案

沿用動能策略同樣的選項 A：把 Prompt 已經在講、但 AI 沒看到數字的欄位曝露出來，並把買入區間規則寫得更具體，不改變「AI 自行輸出 `buy_zone` 字串」的架構。

考慮過但捨棄的範圍擴張：
- **套牢量檢查**（前波高點成交量比對）：需要額外的歷史成交量峰值追蹤邏輯，改動範圍超出「曝露既有指標」的最小化原則，列為後續獨立任務
- **反轉策略完整形態辨識**（W 底第二腳、BOS、斐波那契回撤區）：需要偵測「第一波反彈」的高低點與結構轉折，現有資料模型是「單筆技術指標快照」，無法表達多筆歷史事件的形態序列，改動成本與不確定性都高

## 執行內容

### 1. `src/ranker.py` — `compute_indicators()`

新增衍生欄位 `vol_vs_20d_avg`（當日量 ÷ 20日均量，分母為零時降級為 1.0）。`high_20d`/`low_20d`/`ema50`/`stoch_k`/`rsi_5d_ago` 本來就已算好，不需新增計算。

### 2. `src/ranker.py` — `_generate_candidates_markdown_table()`

表格欄位由 19 欄擴充為 25 欄，新增六欄：
- `High_20D`、`Vol_vs_20DAvg`（突破策略用）：接在既有 `Vol_vs_5DAvg` 之後
- `EMA50`、`Low_20D`、`Stoch_K`、`RSI_5D_Ago`（反轉策略用）：接在 `Momentum_ATR` 之後、`RS_vs_Sector` 之前

### 3. `src/ranker.py` — `field_defs`

新增六個欄位說明。

### 4. `src/ranker.py` — `SYSTEM_PROMPT` 突破策略段落

改為四段式規則：回測確認（優先，站上 `High_20D` 後回落至 `High_20D~High_20D×1.02` 企穩）> 標準突破緩衝（`High_20D` 之上 +0.5%~+1.5%）；距 `High_20D` 超過 +3% 視為追高；`Vol_vs_20DAvg >= 1.5` 才視為攻擊量確認，`< 1.2` 假突破機率高。

### 5. `src/ranker.py` — `SYSTEM_PROMPT` 反轉策略段落

改為三段式規則：`Close_Price` 落在 `EMA50` 附近（±3%）且 `Stoch_K < 25` 且 `RSI > RSI_5D_Ago`（底背離）才進場；`Close_Price` 須明顯高於 `Low_20D`（右側反彈已確立）；仍貼近或跌破 `Low_20D` 則維持觀望；止損設在 `Low_20D` 下方，不得設在 `EMA50` 之上。

### 6. `specs/ranker.md` 同步更新

新增 DD-13、六個欄位定義、`compute_indicators()` 介面文件更新、策略指引段落改寫、Acceptance Criteria。

### 7. `CLAUDE.md` 同步

新增第 17 條摘要。

## 不做的事

- 不實作套牢量檢查、反轉策略完整形態辨識（見上方「考慮過的方案」）
- 不改 `tracker.py` 對 `buy_zone` 的解析與狀態機邏輯（格式不變，仍是 `$X~$Y` 字串）
- 不做 Python 端確定性計算 buy_zone（選項 B）
- 不改動能策略的 Prompt 與欄位（已於前次 PR #40 完成）

## 驗證方式

1. `python main.py --dry-run --yes --no-ai-cache`（略過 AI 快取，強迫重新問 DeepSeek）
2. 檢查 `docs/reports/<today>.html` 中突破策略、反轉策略個股的 `買入區間`：
   - 突破策略：買入區間應貼近 20 日高點附近
   - 反轉策略：買入區間應貼近 EMA50 附近或不低於近期低點
   - 格式仍是 `$X~$Y`，可被 `tracker.py` 的 `_parse_buy_zone()` 正常解析
3. 手動 Python 驗證 `compute_indicators()` 回傳 `vol_vs_20d_avg`、`high_20d`、`low_20d`、`ema50`、`stoch_k`、`rsi_5d_ago`，零成交量時 `vol_vs_20d_avg` 安全降級為 `1.0`（已於實作階段以獨立 Python script 驗證通過）
4. 手動驗證 `_generate_candidates_markdown_table()` 產出含新 25 欄的表格，格式正確（已於實作階段驗證通過）
