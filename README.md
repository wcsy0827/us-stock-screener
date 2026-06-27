# 美股 AI 選股系統

每日自動掃描 S&P 500，透過三層篩選 + 大盤環境感知，找出符合當日市場環境的買入機會，結果發布至 GitHub Pages。

**🌐 報告網址：[wcsy0827.github.io/us-stock-screener](https://wcsy0827.github.io/us-stock-screener/)**

---

## 功能

- **大盤環境感知**：每日計算市場廣度（S&P 500 中站上 50 SMA 的比例）與 VIX，自動判定四種市場環境（Regime）
- **三層篩選漏斗**：從 500+ 支股票逐步收斂至最多 5 支精選
- **動態策略切換**：依 Regime 自動調整 AI 主推策略（動能 / 突破 / 反轉 / 全面防禦）
- **訊號追蹤**：自動追蹤推薦股票是否落入買入區間，追蹤 5 個交易日
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
    │  → 四象限 Regime 分類（見下方）
    │
    ▼  Step 3  fetcher.py
    │  抓取基本面：市值、產業、公司名稱（.cache/info_*.json，7 日有效）
    │
    ▼  Step 4  filter.py — L1 硬條件篩選
    │  股價 > $5、30 日均量 > 50 萬、市值 > 3 億、近 5 日有交易
    │  → 通常剩 200~350 支
    │
    ▼  Step 5  scorer.py — L2 技術評分（100 分制）
    │  門檻：60 分（PANIC_REVERSAL 環境：40 分 + 超賣股強制放行）
    │  → 通常剩 30~80 支
    │
    ▼  Step 5.5  market.py — 完整大盤環境
    │  下載 SPY + 相關產業 ETF，組裝 AI Prompt 用的市場背景
    │
    ▼  Step 6  ranker.py — L3 DeepSeek AI 精選
       依 Regime 主推策略從候選池選出最多 5 支
       每支附：買入區間、目標價、止損、持有週期、策略理由
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

---

## L2 技術評分（100 分制）

| 指標 | 滿分 | 說明 |
|------|------|------|
| MA 多頭排列 | 25 | EMA5 > EMA10 > EMA20 > EMA50，每條件 +8.33 分 |
| RSI 健康區間 | 20 | 50~70 = 滿分；40~50 或 70~80 = 半分；其餘 = 0（含 RSI > 80，軟過濾） |
| MACD 柱狀體 | 20 | 正且遞增 = 滿分；正但遞減 = 半分；負 = 0 |
| 量能放大 | 20 | ≥ 1.5x 均量 = 滿分；≥ 1.0x = 半分 |
| 20 日動能 | 15 | 漲幅 > 10% = 滿分；> 5% = 半分；> 0% = 1/4 分 |

> PANIC_REVERSAL 環境下，RSI < 35 且 20 日跌幅 > 15% 的超賣股會**強制放行**進入 L3，不受分數門檻限制。

---

## 訊號追蹤狀態

| 狀態 | 說明 |
|------|------|
| ✅ active | 股價已落入買入區間，可考慮進場 |
| 🟡 watch | 股價略高於買入區間，等待回落 |
| ❌ invalid | 趨勢轉弱（跌破 EMA20）、跌破 AI 止損價，或已追高 >8% |
| 🗑 expired | 追蹤滿 5 個交易日自動移除 |

**雙軌制失效判定**：
- 動能策略 / 突破策略：跌破 EMA20 即失效
- 反轉策略：跌破 AI 設定的止損價才失效（進場點本就在 EMA20 以下）

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

MAX_OUTPUT=5
MIN_SCORE=60
MIN_PRICE=5
MIN_VOLUME=500000
MIN_MARKET_CAP=300000000
```

> DeepSeek API key 申請：[platform.deepseek.com](https://platform.deepseek.com)

### 執行

```powershell
# 測試（只生成 HTML，不 push 至 GitHub）
$env:PYTHONUTF8=1; python main.py --dry-run

# 正式執行（生成 HTML 並 push）
$env:PYTHONUTF8=1; python main.py

# 常用選項
python main.py --top 5          # 最多輸出幾支（預設 5）
python main.py --min-score 65   # 自訂 L2 門檻（預設 60）
python main.py --no-cache       # 忽略快取，強制重新下載
python main.py --yes            # 跳過今日重複執行確認（CI 用）
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
│   ├── fetcher.py          # 批次下載日 K 與基本面（含快取）
│   ├── filter.py           # L1 硬條件篩選
│   ├── scorer.py           # L2 技術評分（含 PANIC_REVERSAL 強制放行）
│   ├── market.py           # 大盤廣度、VIX、Regime 判定、產業 ETF
│   ├── ranker.py           # L3 DeepSeek AI 精選（XML Prompt）
│   ├── tracker.py          # 訊號追蹤（雙軌制失效、watchlist 管理）
│   ├── pipeline.py         # 流程編排（Steps 1–6）
│   └── publisher.py        # HTML 生成 & GitHub Pages 發布
├── data/
│   └── watchlist.json      # 追蹤清單（持久化）
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
