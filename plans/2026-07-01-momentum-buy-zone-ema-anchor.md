# 動能策略買進區間改善（選項 A：強化 AI 決策資料）

> 狀態：已執行完成，對應 PR [#40](https://github.com/wcsy0827/us-stock-screener/pull/40)、`specs/ranker.md` DD-12

## Context

使用者發現動能策略的 `buy_zone`（AI 輸出的買入區間）幾乎都貼著收盤價（例：KLAC 收盤 $301.71，buy_zone `$295~$302`；AMAT 收盤 $723.00，buy_zone `$710~$723`），而非 Prompt 文字宣稱的「小幅回調至 EMA10」。

根因已確認：`ranker.py` 的 `compute_indicators()` 其實已經算出 `ema5/ema10/ema20/ema50`，但 `_generate_candidates_markdown_table()` 送給 DeepSeek 的候選池表格只給 `MA_Trend` 這種文字標籤（如 `BULL_1`），**沒有任何 EMA 的實際美元數值**。System Prompt 卻要求 AI 用 EMA10 設定買進區間——AI 沒有數字依據可用，只能退化成以 `Close_Price` 為錨的猜測。

使用者提供的《股票操作高勝率買進區間策略指南》給出更具體的動能策略規則：買進區間應為「10EMA~20EMA 回檔帶」，且回檔須伴隨「量縮 <5日均量 0.7 倍」確認；連續強勢股例外可用「5MA 探針帶」；股價距 5MA 超過 5% 視為過度延伸，禁止追價。

## 考慮過的方案

**選項 A（採用）**：強化 AI 決策資料——把 EMA10/EMA20 判斷所需的數字餵給 AI，並把 Prompt 規則寫具體，不改變現有「AI 自行輸出 buy_zone 字串」的架構。

**選項 B（捨棄）**：Python 端直接算出確定性 buy_zone，AI 只負責選股與寫理由。捨棄原因：偏離現有「L3 由 AI 給出完整交易計畫」的架構精神，且需大改 `tracker.py` 對 `buy_zone` 來源的假設、`specs/ranker.md` 的設計決策要大改，改動成本遠高於選項 A。

選擇選項 A 的原因：改動範圍集中在 `ranker.py` 的表格生成與 Prompt 文字，不動 AI 輸出格式（`buy_zone` 仍是字串），也不用碰 `tracker.py` 的解析邏輯。

## 執行內容

### 1. `src/ranker.py` — `compute_indicators()`

新增衍生欄位 `vol_vs_5d_avg`（當日量 ÷ 5日均量，分母為零時降級為 1.0）。EMA5/10/20 本來就已算好，不需新增計算。

### 2. `src/ranker.py` — `_generate_candidates_markdown_table()`

表格欄位由 15 欄擴充為 19 欄，在 `MA_Trend` 之後插入 `EMA5 | EMA10 | EMA20 | Vol_vs_5DAvg`。

### 3. `src/ranker.py` — `field_defs`

新增欄位說明，解釋 EMA5/10/20 與 Vol_vs_5DAvg 在動能策略中的用途。

### 4. `src/ranker.py` — `SYSTEM_PROMPT` 動能策略段落

原文只有一行「買入：當前股價或小幅回調至 EMA10」，缺乏可執行依據。改為三段式規則：
1. 標準回檔進場：`EMA20~EMA10` 之間 + `Vol_vs_5DAvg < 0.7`（量縮無賣壓）
2. 極端強勢例外：股價緊貼 `EMA5` 但 `VTF_Score` 仍強 → `EMA5` 附近（5MA 探針帶）
3. 股價距 `EMA5` 超過 +5%（過度延伸）→ 大幅降低信心分數，禁止以收盤價設為買入區間上限

突破策略、反轉策略段落本次不動（先聚焦動能策略）。

### 5. `specs/ranker.md` 同步更新

新增 DD-12、欄位定義、`compute_indicators()` 介面文件更新、Acceptance Criteria。

### 6. `CLAUDE.md` 同步

新增十五大決策後的第 16 條摘要。

## 不做的事

- 不改突破策略、反轉策略的 buy_zone 邏輯（後續發現：這兩個策略甚至比動能策略更嚴重——`stoch_k`、`rsi_5d_ago`、`ema50`、`high_20d`、`low_20d` 等 Prompt 引用的指標，一個都沒有出現在 AI 看到的表格裡）
- 不改 `tracker.py` 對 `buy_zone` 的解析與狀態機邏輯（格式不變，仍是 `$X~$Y` 字串）
- 不做 Python 端確定性計算 buy_zone（選項 B）

## 驗證方式

1. `python main.py --dry-run --yes --no-ai-cache`（略過 AI 快取，強迫重新問 DeepSeek）
2. 檢查 `docs/reports/<today>.html` 中動能策略個股的 `買入區間`，確認不再固定等於收盤價，格式仍是 `$X~$Y`
3. 手動驗證 `compute_indicators()` 回傳 `vol_vs_5d_avg`，零成交量時安全降級為 `1.0`（已於實作階段以獨立 Python script 驗證通過）
