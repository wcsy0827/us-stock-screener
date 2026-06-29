# 美股 AI 選股系統

每日自動掃描 S&P 500，透過三層篩選 + 大盤環境感知，找出符合當日市場環境的買入機會，結果發布至 GitHub Pages。

**🌐 報告網址：[wcsy0827.github.io/us-stock-screener](https://wcsy0827.github.io/us-stock-screener/)**

---

## 功能

- **大盤環境感知**：每日計算市場廣度（S&P 500 中站上 50 SMA 的比例）與 VIX，自動判定四種市場環境（Regime）
- **三層篩選漏斗**：從 500+ 支股票逐步收斂至最多 5 支精選
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
    │
    ▼  Step 2.5  market.py — 快速 Regime 判定
    │  計算市場廣度（% 股票 > 50 SMA）+ 下載 VIX
    │  → 四象限 Regime 分類；回傳值供 Step 5.5 直接複用（不重算）
    │
    ▼  Step 3  fetcher.py
    │  抓取基本面：市值、產業、公司名稱（.cache/info_*.json，7 日有效）
    │  順帶提取 earningsDate 欄位供財報防禦牆使用
    │
    ▼  Step 3.5  earnings.py — 財報日查詢（Tier 1+2）
    │  讀本地 earnings_registry.json（30 日快取）或從基本面數據提取
    │
    ▼  Step 4  filter.py — L1 流動性篩選
    │  股價 > $5、30 日均量 > 50 萬、市值 > 3 億、近 5 日有交易
    │  → 通常剩 200~350 支
    │
    ▼  Step 4.5  earnings.py + filter.py — 財報防禦牆
    │  Tier 3 精準補抓（ticker.calendar，僅對流動性篩選後倖存股）
    │  排除未來 3 天內有財報的個股（0 即時 I/O，registry 30 日快取）
    │
    ▼  Step 5  scorer.py — L2 技術評分（100 分制）
    │  門檻：60 分（PANIC_REVERSAL 環境：40 分 + 超賣股強制放行）
    │  → 通常剩 30~80 支
    │
    ▼  Step 5.5  market.py — 完整大盤環境
    │  直接複用 Step 2.5 的廣度與 VIX，補抓 SPY + 相關產業 ETF 細節
    │
    ▼  Step 6  ranker.py — L3 DeepSeek AI 精選
       依 Regime 主推策略從候選池選出最多 5 支
       每支附：買入區間、目標價、止損、持有天數（純整數）、策略理由
       BEAR_DISTRIBUTION 時直接回傳空列表，不建議任何買入
```

---

## 大盤環境（Market Regime）

| Regime | 條件 | 主推策略 | 系統行為 |
|--------|------|----------|----------|
| **BULL_TREND** | 廣度 ≥ 60% 且 VIX < 20 | 動能策略 | 選強勢領頭羊、均線多頭排列標的 |
| **CONSOLIDATION** | 廣度 35~60%（任意 VIX） | 突破策略 | 只選帶量突破壓力位的個股 |
| **PANIC_REVERSAL** | 廣度 < 35% 且 VIX ≥ 25 | 反轉策略 | 找超賣底背離、嚴設止損 |
| **BEAR_DISTRIBUTION** | 廣度 < 35% 且 VIX < 25 | 全面防禦 | 不輸出任何買入建議 |

> 每日報告大盤儀表板的「主推：XXX 策略」旁有 `?` 圖示，hover 即顯示當前 Regime 的判斷條件，方便快速確認策略依據。

---

## L2 技術評分（100 分制）

| 指標 | 滿分 | 說明 |
|------|------|------|
| MA 多頭排列 | 25 | EMA5 > EMA10 > EMA20 > EMA50，每條件 +8.33 分 |
| RSI 健康區間 | 20 | 50～70 = 滿分；40～50 或 70～80 = 半分；其餘 = 0（含 RSI > 80，軟過濾） |
| MACD 柱狀體 | 20 | 正且遞增 = 滿分；正但遞減 = 半分；負 = 0 |
| 量能放大 | 20 | ≥ 1.5x 均量 **且** K_pos ≥ 0.6 = 滿分；≥ 1.0x 且 K_pos ≥ 0.6 = 半分；爆量但 K_pos < 0.6（出貨型）= 0 |
| 20 日動能 | 15 | ATR 倍數法：≥ 2.0 ATR = 滿分；≥ 1.0 ATR = 半分；> 0 = 1/4 分（跨行業公平評比） |

> PANIC_REVERSAL 環境下，RSI < 35 且 20 日跌幅 > 15% 的超賣股會**強制放行**進入 L3，不受分數門檻限制。

---

## 訊號追蹤狀態機

| 狀態 | 說明 |
|------|------|
| ✅ active | 股價已落入買入區間且高於止損，報告顯示「持倉 N / 持有天數 天」及彩色浮盈浮虧（相對進場價） |
| 🟡 watch | 股價略高於買入區間，等待回落 |
| ❌ invalid | 趨勢轉弱、跌破 AI 止損價、開盤跳空觸發安全攔截，或已追高 >8% |
| 🗑 expired | 觀察滿 5 個交易日自動移除 |
| 📦 settled | 觸發停利/停損/到期結算，歸檔至績效資料庫後移除 |

**雙軌制失效判定**：
- 動能策略 / 突破策略：跌破 EMA20 即失效
- 反轉策略：跌破 AI 設定的止損價才失效（進場點本就在 EMA20 以下）

**開盤跳空安全攔截**：`watch → active` 轉換時，除了 `price >= buy_zone_lower` 外，額外確認 `price > stop_loss`，防止 AI 誤設止損在買入區間內時污染績效資料庫。

**績效結算三態**：
- `CLOSED_PROFIT`：收盤價 ≥ 目標價
- `CLOSED_LOSS`：收盤價 ≤ 止損價
- `FORCE_EXPIRED`：持倉天數 ≥ AI 設定的持有天數

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

MAX_OUTPUT=5           # 最多輸出幾支（預設 5）
MIN_SCORE=60           # L2 最低評分門檻（程式預設 60，可自行調高）
MIN_PRICE=5            # 最低股價
MIN_VOLUME=500000      # 近 30 日最低均量
MIN_MARKET_CAP=300000000  # 最低市值（3 億美元）
```

> DeepSeek API key 申請：[platform.deepseek.com](https://platform.deepseek.com)

### 執行

```powershell
# 測試（只生成 HTML，不 push 至 GitHub）
python main.py --dry-run

# CI 模式（跳過今日重複執行確認）
python main.py --dry-run --yes

# 強制忽略快取，重新下載所有數據
python main.py --dry-run --no-cache

# 自訂輸出數量與最低評分
python main.py --dry-run --top 10 --min-score 65

# 正式執行（生成 HTML 並 push）
$env:PYTHONUTF8=1; python main.py

# Windows 包裝腳本
.\run.ps1 --dry-run
.\run.ps1 --top 10
```

生成的報告位於 `docs/reports/YYYY-MM-DD.html`。

---

## GitHub Actions 自動化

### 排程

每週一至五 **21:30 UTC**（美東時間收盤後約 1.5 小時，台灣時間隔日 05:30）自動執行。

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
│   ├── ranker.py           # L3 DeepSeek AI 精選（XML Prompt）
│   ├── tracker.py          # 訊號追蹤（狀態機、績效結算、歸檔）
│   ├── pipeline.py         # 流程編排（Steps 1–6，含 3.5 / 4.5）
│   └── publisher.py        # HTML 生成 & GitHub Pages 發布（含績效儀表板）
├── specs/                  # 規格文件（Spec-First 開發）
│   ├── _template.md
│   ├── earnings.md
│   ├── scorer.md
│   ├── tracker.md
│   ├── ranker.md
│   ├── market.md
│   └── pipeline.md
├── data/
│   ├── watchlist.json      # 追蹤清單（持久化）
│   └── performance_history.json  # 歷史績效資料庫（結算後自動建立）
├── docs/                   # GitHub Pages 靜態檔案
│   ├── index.html
│   └── reports/
├── .github/workflows/
│   └── daily-screener.yml  # GitHub Actions workflow
└── .env.example
```

---

## 技術棧

| 用途 | 工具 |
|------|------|
| 股價資料 | yfinance |
| 技術指標 | pandas-ta |
| AI 精選 | DeepSeek（openai 相容介面） |
| 報告發布 | GitHub Pages（純 HTML/CSS） |
| 自動化 | GitHub Actions |

> **注意**：`pandas` 鎖定 `<3.0.0`（pandas-ta 0.4.x 尚未驗證與 pandas 3.x 相容）；`numpy` 鎖定 `>=1.26.0,<2.0.0`（1.26 是第一個有 Python 3.12 wheel 的版本）。
