# 美股 AI 選股系統

每日自動掃描 S&P 500，透過三層篩選 + 大盤環境感知，找出符合當日市場環境的買入機會，結果發布至 GitHub Pages。

**🌐 報告網址：[wcsy0827.github.io/us-stock-screener](https://wcsy0827.github.io/us-stock-screener/)**

---

## 功能

- **大盤環境感知**：每日計算市場廣度（S&P 500 近 3 日均廣度：站上 50 SMA 的比例）與 VIX，自動判定五種市場環境（Regime），廣度邊界 ±2% 遲滯帶防止每日翻轉
- **三層篩選漏斗**：從 500+ 支股票逐步收斂至最多 3 支精選
- **動態策略切換**：依 Regime 自動調整 AI 主推策略（動能 / 突破 / 反轉 / 全面防禦）
- **訊號追蹤與績效結算**：追蹤推薦股票是否落入買入區間，觸發停利/停損/到期後自動歸檔至績效資料庫
- **歷史績效儀表板**：每日報告顯示累計勝率、平均回報率、各策略勝率
- **每日報告**：深色主題網頁，卡片式設計，含大盤儀表板，支援手機瀏覽
- **全自動執行**：GitHub Actions 每個交易日收盤後自動執行並發布

---

## 篩選流程

```
S&P 500（~503 支）
    │
    ▼  Step 1  universe.py
    │  爬取維基百科取得成份股代號
    │
    ▼  Step 2  fetcher.py
    │  下載 90 日日 K 數據（.cache/price_YYYYMMDD.pkl 快取）
    │  同批次下載 11 支板塊 ETF（XLK/XLV/XLF 等）及 SPY，供 L2 RS 計算用
    │  盤中執行時自動捨棄當日尚未收盤的殘缺K棒（美東 16:15 前判定未收盤，market_date 回退至前一完整交易日）
    │
    ▼  Step 2.5  market.py — 快速 Regime 判定
    │  計算市場廣度（近 3 日均值，% 股票 > 50 SMA）+ 下載 VIX
    │  → 五象限 Regime 分類；廣度邊界 ±2% 遲滯帶（讀 last_run.json）
    │  → 回傳值供 Step 5.5 直接複用（不重算）
    │
    ▼  Step 3  fetcher.py
    │  抓取基本面：市值、產業、公司名稱（.cache/info_*.json，7 日有效）
    │  順帶提取 earningsDate 欄位供財報防禦牆使用
    │
    ▼  Step 3.5  earnings.py — 財報日查詢（Tier 1+2）
    │  讀本地 earnings_registry.json（30 日快取）或從基本面數據提取
    │
    ▼  Step 4  filter.py — L1 流動性篩選
    │  股價 > $5、30 日均量美元成交額 > $1,000 萬、市值 > 3 億（None 視為缺失排除）、近 5 日有交易
    │  ATR14/收盤價 ≤ 8%（波動風控，數據不足 15 筆不排除）
    │  → 通常剩 200~350 支
    │
    ▼  Step 4.5  earnings.py + filter.py — 財報防禦牆
    │  Tier 3 精準補抓（ticker.calendar，僅對流動性篩選後倖存股）
    │  排除未來 5 天內有財報的個股（0 即時 I/O，registry 30 日快取）
    │
    ▼  Step 5  scorer.py — L2 技術評分（六維度 100 分制）
    │  門檻：60 分；CONSOLIDATION_VOLATILE→65 分；PANIC_REVERSAL→40 分 + 超賣股強制放行
    │  疊加排名上限 Top 55（同分邊界保留、強制放行股不受排除），穩定收斂至 50~60 支
    │
    ▼  Step 5.5  market.py — 完整大盤環境
    │  直接複用 Step 2.5 的廣度與 VIX，補抓 SPY + 相關產業 ETF 細節
    │
    ▼  Step 5.7  analyzer.py — 本地績效診斷
    │  讀 performance_history.json 歸納 Regime×策略×產業賺賠關聯 → data/ai_hints.json
    │  分組 <3 筆或總樣本 <5 筆不生成回饋；失敗不中斷流程
    │
    ▼  Step 6  ranker.py — L3 DeepSeek AI 精選
       依 Regime 主推策略從候選池選出最多 3 支
       發送前自動讀取 ai_hints.json，非空時在 Prompt 末尾附加歷史績效回饋區塊
       候選表格同時附基本面欄位（估值 Fwd_PE、獲利品質 Profit_Margin、成長性 Rev_Growth_YoY）與空頭比例（Short_Float_Pct）供 AI 交叉判斷
       每支附：買入區間、目標價、止損、持有天數（純整數）、策略理由
       選股理由（reason）聚焦基本面/產業趨勢/Regime 契合度等非技術論述（80~120字），技術指標數值由策略依據（strategy_reason）承載
       BEAR_DISTRIBUTION 時直接回傳空列表，不建議任何買入
```

---

## 大盤環境（Market Regime）

| Regime | 條件 | 主推策略 | L2 門檻 | 系統行為 |
|--------|------|----------|---------|----------|
| **BULL_TREND** | 廣度 ≥ 60% 且 VIX < 20 | 動能策略 | 60 分 | 選強勢領頭羊、均線多頭排列標的 |
| **CONSOLIDATION** | 廣度 35~60% 且 VIX < 20 | 突破策略（積極） | 60 分 | 只選帶量突破壓力位的個股 |
| **CONSOLIDATION_VOLATILE** | 廣度 35~60% 且 VIX ≥ 20 | 突破策略（保守） | **65 分** | 高波動整理期，要求更嚴格的確認訊號 |
| **PANIC_REVERSAL** | 廣度 < 35% 且 VIX ≥ 25 | 反轉策略 | 40 分 | 找超賣底背離、嚴設止損 |
| **BEAR_DISTRIBUTION** | 廣度 < 35% 且 VIX < 25 | 全面防禦 | — | 不輸出任何買入建議 |

> 每日報告大盤儀表板的「主推：XXX 策略」旁有 `?` 圖示，hover 即顯示當前 Regime 的判斷條件，方便快速確認策略依據。

---

## L2 技術評分（六維度 100 分制）

| 指標 | 滿分 | 說明 |
|------|------|------|
| MA 多頭排列 | 20 | EMA5 > EMA10 > EMA20 > EMA50，每條件 +6.67 分 |
| RSI 健康區間 | 18 | 50～70 = 滿分；BULL_TREND 時擴大至 50～80；40～50 或 超出健康區 = 半分；其餘 = 0 |
| MACD 柱狀體 | 17 | 正且遞增 = 滿分；正但遞減 = 半分；負 = 0 |
| 量能放大 | 15 | VTF × 量能斜率係數；K_pos ≥ 0.6（上攻型）才得分；連續放量係數 × 1.0，縮量係數 × 0.65 |
| 雙期動能 | 15 | 20 日 ATR 主趨勢 × 5 日方向確認；中短線一致 = 滿分；短線回調打折 |
| 相對強度 RS | 15 | 個股 5 日報酬 − 板塊 ETF 5 日報酬；≥ +2% = 滿分；≥ +0.5% = 半分；< −0.5% = 0 |

> PANIC_REVERSAL 環境下，RSI < 35 且 20 日跌幅 > 15% 的超賣股會**強制放行**進入 L3，不受分數門檻限制。

---

## 買進區間與停損停利設定規則

每支 AI 精選個股的買入區間、目標價與止損由 L3 AI 在**訊號日依策略規則輸出並鎖定**（`ranker.py`）；目標價是停利的觸發線。進場後止損改由追蹤系統**動態管理**（`tracker.py`），每日報告顯示的止損即為系統實際結算用的門檻，手動跟單請以報告數字為準。

### 訊號日：AI 依策略設定買入區間與初始止損

| 策略 | 買入區間 | 初始止損 |
|------|----------|----------|
| **動能** | 收盤價下方 0.25～1×ATR14 的淺回檔帶（下緣不低於 EMA10），深度自適應個股波動；若股價已量縮回檔至 EMA20～EMA10 帶，直接採用該區間 | 買入區間下緣再下方 1×ATR14（回檔帶情境改用 EMA20 下方 2%，兩者取較高者；不得寬於進場價 −10%） |
| **突破** | 優先設在回測確認帶（20 日高點～+2%），次選突破緩衝帶（20 日高點上方 +0.5%～+1.5%）；距 20 日高點超過 +3% 視為追高 | 20 日高點下方 2% |
| **反轉** | EMA50 ±3% 支撐帶，須同時滿足底背離確認（Stoch_K < 25 且 RSI 高於 5 日前）且收盤已明顯高於 20 日低點（右側反彈確立） | 20 日低點下方 2%（不得高於 EMA50） |

- 止損一律不得等於買入區間下緣，須保留容錯緩衝（`specs/ranker.md` DD-15）
- L1 已排除 ATR14/收盤價 > 8% 的高波動股，保證 2% 與 ATR 錨定的止損緩衝不會形同虛設

### 進場後：系統動態風控（自動執行）

| 機制 | 規則 |
|------|------|
| 🎯 停利 | 當日**最高價**觸及目標價即停利出場（出場價 = 目標價） |
| 🛑 停損 | 當日**最低價**觸及有效止損即停損出場（出場價 = 止損價）；同日雙觸發（黑天鵝）保守判為停損 |
| 🔒 保本鎖定 | 收盤浮盈達目標距離 50% 時，止損自動上移至買入區間上緣；報告標註「🔒保本」 |
| 📈 移動停利 | 動能/突破策略峰值浮盈超過 10% 後，收盤自峰值回撤 5% 即鎖利出場（出場價 = 收盤價；反轉策略不適用） |
| ⏰ 到期出場 | 持倉達 AI 設定的持有天數仍未觸發上述條件，以收盤價強制出場 |

完整規則與設計取捨見 `specs/ranker.md`（DD-12/13/15/19）與 `specs/tracker.md`（DD-10/12/13）。

---

## 訊號追蹤狀態機

| 狀態 | 說明 |
|------|------|
| ✅ active | 當日最低價已觸及買入區間上緣（模擬限價單成交，見下方 DD-19）且持倉未滿上限（見下方持倉上限），報告顯示「持倉 N / 持有天數 天」及彩色浮盈浮虧（相對進場價） |
| 🟡 watch | 今日未觸及買入區間等待回落；或已觸價但持倉已滿被擋下（報告標註「今日觸價但持倉已滿，未進場」，觀察期照常倒數，次日名額釋出時重新競爭） |
| ❌ invalid | **僅發生在 watch 階段且今日未觸價成交時**：趨勢轉弱、跌破 AI 止損價，或已追高 >8% |
| 🗑 expired | 觀察超過策略上限自動移除（突破/動能=5 日；反轉=10 日；高波動整理市的突破=3 日；VIX>35 尖底的反轉=5 日） |
| 📦 settled | 觸發停利/停損/移動停利/到期結算，歸檔至績效資料庫後移除 |

**雙軌制失效判定（僅適用 watch 狀態）**：
- 動能策略 / 突破策略：跌破 EMA20 即失效
- 反轉策略：跌破 AI 設定的止損價才失效（進場點本就在 EMA20 以下）

**active 部位不再被上述失效條件影響**：一旦進場，出場只由 📦 settled（停利/停損/移動停利/到期）控制，`invalid` 判定完全交給結算邏輯，避免虧損在歸檔前被失效條件攔截而無聲消失（見 `specs/tracker.md` DD-17）。

**報告顯示動態止損與真實剩餘天數**（`publisher.py` DD-7）：active 持倉顯示的止損是系統實際用於結算的 `effective_stop_loss`（保本鎖定後會上移，並標註「🔒保本」），而非 AI 原始止損，跟單時請以報告上顯示的數字為準；動能/突破策略峰值浮盈達 10% 後另會顯示「移動停利線」供對照。watch/invalid 的「剩 N 天自動移除」也已依策略/Regime/VIX 差異化上限（見上方 🗑 expired 列）正確倒數，不再統一寫死 5 天。

**盤中限價單模擬進場**（`tracker.py` DD-19）：使用者的實際操作是收盤後跑選股，次一交易日盤中依買入區間**上緣**掛限價單，因此進場判定不再只看收盤價——只要當日最低價曾觸及買入區間上緣，即視為觸價成交，優先於「趨勢轉弱」「跌破止損」「已追高」等收盤價判定；進場代理價也改為買入區間上緣（使用者實際掛單價），不再是收盤價。若當日同時觸價成交又跳空跌破止損，保守記為「當日進場即停損」並歸檔為虧損交易，而非拒絕進場、不留紀錄。

**組合層持倉上限**（`tracker.py` DD-20）：同時持倉數以 `MAX_ACTIVE_POSITIONS`（預設 5，`.env` 可調）為上限。同日多支 watch 觸價、名額不足時，依 AI 信心分數（同分比 L2 分數）排序擇優進場；落選者維持 watch 並在報告標註「今日觸價但持倉已滿，未進場」，觀察期照常倒數，次日名額釋出時重新競爭。被擋當日若收盤已跌破止損或已追高，該訊號直接作廢（沒掛單即無真實交易，不記入績效）。當日結算出場不即時釋放名額（次一交易日才釋放，與隔日掛單的實務一致）；既有持倉超過上限時不強制平倉，由結算自然收斂。對應真實操作「滿倉時不掛新單」，讓 `performance_history.json` 的績效統計反映實際可執行的資金配置，而非「資金無限」假設。

**績效結算四態**（觸發規則詳見上方「進場後：系統動態風控」）：
- `CLOSED_PROFIT`：當日最高價 ≥ 目標價（出場價 = 目標價）
- `CLOSED_LOSS`：當日最低價 ≤ 有效止損（出場價 = 止損價；同日雙觸發保守判停損）
- `CLOSED_TRAILING_STOP`：峰值浮盈 ≥ 10% 後收盤自峰值回撤 5%（出場價 = 收盤價）
- `FORCE_EXPIRED`：持倉天數 ≥ AI 設定的持有天數（出場價 = 收盤價）

---

## 歷史績效

結算後自動寫入 `data/performance_history.json`，每日報告顯示：

- **今日結算區段**（📦）：當日觸發停利（🎯）/ 停損（🛑）/ 到期（⏰）的個股，顯示進場價 → 出場價、持倉天數、回報百分比
- **歷史績效摘要**：整體勝率、平均回報率、各策略（動能/突破/反轉）累積勝率

系統啟動初期（冷啟動）績效區塊自動隱藏，不顯示空資料。

---

## 開發流程（Spec-First）

本專案採用規格驅動開發（SDD）工作流：

```
需求 → 更新 specs/<module>.md → 實作 → PR 引用規格節次 → 合併
```

| 模組 | 規格文件 |
|------|----------|
| `src/scorer.py` | [`specs/scorer.md`](specs/scorer.md) |
| `src/tracker.py` | [`specs/tracker.md`](specs/tracker.md) |
| `src/ranker.py` | [`specs/ranker.md`](specs/ranker.md) |
| `src/market.py` | [`specs/market.md`](specs/market.md) |
| `src/pipeline.py` | [`specs/pipeline.md`](specs/pipeline.md) |
| `src/analyzer.py` | [`specs/analyzer.md`](specs/analyzer.md) |

新功能請複製 [`specs/_template.md`](specs/_template.md) 建立規格文件，並在實作前完成 Behavior 與 Design Decisions 節。

---

## 本機執行

### 環境需求

- Python 3.12+
- Git

### 安裝

```powershell
git clone https://github.com/wcsy0827/us-stock-screener.git
cd us-stock-screener
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 設定 `.env`

```powershell
copy .env.example .env
```

```env
DEEPSEEK_API_KEY=your_deepseek_api_key_here

MAX_OUTPUT=3              # 最多輸出幾支（預設 3）
MIN_SCORE=60              # L2 最低評分門檻（程式預設 60，可自行調高；上方另疊加 Top 55 排名上限）
MIN_PRICE=5               # 最低股價
MIN_DOLLAR_VOLUME=10000000  # 近 30 日均量美元成交額下限（$1,000 萬）
MIN_MARKET_CAP=300000000  # 最低市值（3 億美元）
MAX_ATR_PCT=8             # ATR14/收盤價百分比上限（波動風控，數據不足 15 筆不排除）
MIN_AI_CONFIDENCE=6       # AI 信心分數最低門檻（1-10，低於此分不加入追蹤）
MAX_ACTIVE_POSITIONS=5    # 同時持倉上限（組合層槽位制，滿倉時觸價訊號依 AI 信心排序競爭名額）
```

> DeepSeek API key 申請：[platform.deepseek.com](https://platform.deepseek.com)

### 執行

```powershell
# 測試（只生成 HTML，不 push 至 GitHub）
python main.py --dry-run

# CI 模式（跳過今日重複執行確認）
python main.py --dry-run --yes

# 強制忽略快取，重新下載所有數據（同時略過 AI 快取）
python main.py --dry-run --no-cache

# 僅略過 AI 快取，重新問 DeepSeek（price/info 快取仍複用）
python main.py --dry-run --yes --no-ai-cache

# 自訂輸出數量與最低評分
python main.py --dry-run --top 10 --min-score 65

# 正式執行（生成 HTML 並 push）
$env:PYTHONUTF8=1; python main.py

# Windows 包裝腳本
.\run.ps1 --dry-run
.\run.ps1 --top 10
```

生成的報告位於 `docs/reports/YYYY-MM-DD.html`。

### 前端完整預覽（含 last_run.json）

`--dry-run` 執行後，`docs/data/last_run.json` 會同步寫入（記錄執行時間與掃描統計）。若直接用瀏覽器開 `file://`，`fetch()` 受瀏覽器安全限制會靜默失敗。啟動本地 server 可完整模擬 GitHub Pages 行為：

```powershell
python main.py --dry-run --yes
cd docs
python -m http.server 8080
# 瀏覽器開 http://localhost:8080
```

### 單元測試

`tracker.py` 的純函式（解析、狀態機、結算、風控、watch 天數上限）有 `tests/test_tracker.py` 覆蓋，`analyzer.py` 的純函式（冷啟動、聚合統計、樣本門檻）有 `tests/test_analyzer.py` 覆蓋，均不需連網、不會觸碰 `data/`；`tests/test_publisher_info_sync.py` 則守門 `docs/index.html` 與 `publisher._build_index()` 輸出的整檔全等，漂移即失敗（修復：執行 `python src/publisher.py` 一鍵重新生成）：

```powershell
pip install -r requirements-dev.txt
pytest
```

---

## 本機測試工作流程

### 快取機制一覽

| 快取 | 路徑 | 略過方式 |
|------|------|----------|
| 日 K 數據 | `.cache/price_YYYYMMDD.pkl` | `--no-cache` |
| 基本面資訊 | `.cache/info_YYYYMMDD.json` | `--no-cache` |
| AI 精選結果 | `.cache/ranked_YYYYMMDD.json` | `--no-ai-cache` 或 `--no-cache` |
| 追蹤清單 | `data/watchlist.json` | 手動刪除 |
| 歷史績效 | `data/performance_history.json` | 手動刪除 |

### 依場景選擇指令

| 測試場景 | 建議指令 | 說明 |
|----------|----------|------|
| 調整 `scorer.py` / `filter.py` 邏輯 | `python main.py --dry-run --yes` | price/info/AI 快取全部複用，只重跑評分 |
| 想讓 AI 重新選股（調整 prompt 或 regime 邊界） | `python main.py --dry-run --yes --no-ai-cache` | price/info 快取複用，DeepSeek 重新呼叫 |
| 懷疑市場數據有問題 | `python main.py --dry-run --yes --no-cache` | 全部重新下載 + 重問 AI |
| 完全重置今日狀態後重跑 | ① 刪 `data/watchlist.json`（選用）→ ② `python main.py --dry-run --yes` | watchlist 清空後重建 |

### 今日 watchlist 的自動取代機制

同一天重複執行時，`tracker.py` 會自動清除當日新增的 watch 股票（`date_added == today`），再以本次 AI 結果取代。**不需要手動清空 `watchlist.json`**——除非你想清掉跨日累積的所有追蹤記錄。既有跨日追蹤中的 active/watch 部位，同日內重跑幾次都只會被計入一個交易日（`watch_days`/`active_days` 已依 `tracked_dates` 去重，不會因重跑次數而虛增，見 `specs/tracker.md` DD-18）。

### 重置追蹤記錄（測試初始化）

```powershell
# 清除所有追蹤狀態，從零開始
Remove-Item data\watchlist.json -ErrorAction SilentlyContinue
Remove-Item data\performance_history.json -ErrorAction SilentlyContinue
python main.py --dry-run --yes
```

> **注意**：`performance_history.json` 刪除後，歷史績效數據無法還原。測試期間建議先備份。

### 各模組對應的快取行為

- **`scorer.py` / `filter.py`**：只影響 L1/L2 階段，price/info 快取照常複用，AI 快取也照常複用（L2 輸出相同候選池時）。若 L2 候選池改變太多，AI 快取仍會被讀取（快取是以市場日期為 key，不感知候選池內容）——此時應加 `--no-ai-cache` 讓 AI 重新評估。
- **`ranker.py` prompt 或策略邏輯**：必須加 `--no-ai-cache`，否則讀到舊快取。
- **`tracker.py` / `publisher.py`**：不涉及快取，直接重跑即可。

---

## GitHub Actions 自動化

### 排程

每週一至五 **21:30 UTC**（美東時間收盤後約 1.5 小時，台灣時間隔日 05:30）自動執行。

### 單元測試 CI

`.github/workflows/tests.yml`：`src/**`、`tests/**` 或 `docs/index.html` 有變動的 push/PR 會自動跑 `pytest`（涵蓋 `tracker.py` 純函式與 `docs/index.html` ↔ `_build_index()` 全等守門），與每日排程的 `daily-screener.yml` 分開、互不影響。

### 手動觸發

1. 前往 repo 的 **Actions** 頁面
2. 左側選 **Daily Stock Screener**
3. 點 **Run workflow**

### 設定 Secrets

在 repo 的 **Settings → Secrets and variables → Actions** 新增：

| Secret 名稱 | 說明 |
|-------------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 金鑰 |

---

## 專案結構

```
us-stock-screener/
├── main.py                 # 主程式入口（含 CLI 參數）
├── src/
│   ├── universe.py         # 爬取 S&P 500 成份股
│   ├── fetcher.py          # 批次下載日 K 與基本面（含快取、earningsDate）
│   ├── earnings.py         # 財報日三層快取查詢（registry 30 日 TTL）
│   ├── filter.py           # L1 流動性篩選 + 財報防禦牆
│   ├── scorer.py           # L2 技術評分（K_pos 量能綁定、ATR 動能、PANIC_REVERSAL 強制放行）
│   ├── market.py           # 大盤廣度、VIX、Regime 判定、產業 ETF
│   ├── analyzer.py         # 本地績效診斷（賺賠關聯 → ai_hints.json）
│   ├── ranker.py           # L3 DeepSeek AI 精選（XML Prompt + 歷史回饋注入）
│   ├── tracker.py          # 訊號追蹤（狀態機、績效結算、歸檔）
│   ├── pipeline.py         # 流程編排（Steps 1–6，含 3.5 / 4.5 / 5.7）
│   └── publisher.py        # HTML 生成 & GitHub Pages 發布（含績效儀表板）
├── specs/                  # 規格文件（Spec-First 開發）
│   ├── _template.md
│   ├── earnings.md
│   ├── scorer.md
│   ├── tracker.md
│   ├── ranker.md
│   ├── market.md
│   ├── pipeline.md
│   └── analyzer.md
├── data/
│   ├── watchlist.json      # 追蹤清單（持久化）
│   ├── performance_history.json  # 歷史績效資料庫（結算後自動建立）
│   └── ai_hints.json       # AI 歷史回饋（每輪 Step 5.7 重寫，可再生）
├── docs/                   # GitHub Pages 靜態檔案
│   ├── index.html
│   └── reports/
├── tests/
│   ├── conftest.py         # 將 src/ 加入 sys.path
│   ├── test_tracker.py     # tracker.py 純函式單元測試
│   ├── test_analyzer.py    # analyzer.py 純函式單元測試
│   └── test_publisher_info_sync.py  # docs/index.html ↔ _build_index() 全等守門
├── .github/workflows/
│   └── daily-screener.yml  # GitHub Actions workflow
├── requirements.txt
├── requirements-dev.txt    # 開發依賴（含 pytest）
├── pytest.ini
└── .env.example
```

---

## 技術棧

| 用途 | 工具 |
|------|------|
| 股價資料 | yfinance 0.2.x |
| 技術指標 | pandas（純 Python 實作） |
| AI 精選 | DeepSeek（openai 相容介面） |
| 報告發布 | GitHub Pages（純 HTML/CSS） |
| 自動化 | GitHub Actions |

