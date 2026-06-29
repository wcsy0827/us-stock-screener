# 美股 AI 選股系統

每日自動掃描 S&P 500，透過三層篩選找出值得追蹤的買入機會，結果發布至 GitHub Pages。

**🌐 報告網址：[wcsy0827.github.io/us-stock-screener](https://wcsy0827.github.io/us-stock-screener/)**

---

## 功能

- **三層篩選漏斗**：從 500+ 支股票逐步收斂至 5 支精選
- **訊號追蹤**：自動追蹤推薦股票是否落入買入區間，追蹤 5 個交易日
- **每日報告**：深色主題網頁，卡片式設計，支援手機瀏覽
- **全自動執行**：GitHub Actions 每個交易日收盤後自動執行並發布

---

## 篩選流程

```
S&P 500（~503 支）
    │
    ▼ L1 硬條件篩選
    │  股價 > $5、近 30 日均量 > 50 萬、市值 > 3 億
    │  近 5 日至少 5 筆交易數據（排除停牌）
    │
    ▼ L2 技術指標評分（門檻預設 60 分）
    │  MA 排列（25分）、RSI 健康度（20分）、MACD 柱狀體（20分）
    │  量能放大（20分）、20日動能（15分）
    │  ⚠️ RSI > 80 超買：整支股票直接 0 分排除
    │
    ▼ L3 DeepSeek AI 精選
       綜合大盤環境、產業趨勢、技術面，選出最多 5 支
       每支附：買入區間、目標價、止損、持有週期、策略
```

---

## 訊號追蹤狀態

| 狀態 | 說明 |
|------|------|
| ✅ 有效 | 股價已落入買入區間，可考慮進場 |
| 🟡 留意 | 股價略高於買入區間，等待回落 |
| ❌ 失效 | 股價追高（>8%）或跌破 EMA20，訊號取消 |
| 🗑 移除 | 追蹤滿 5 天自動移除 |

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

複製範本並填入 API key：

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
python main.py

# Windows 包裝腳本（自動設定 UTF-8）
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
├── main.py                 # 主程式入口
├── src/
│   ├── universe.py         # S&P 500 股票池
│   ├── fetcher.py          # 批次下載股價（含快取）
│   ├── filter.py           # L1 硬條件篩選
│   ├── scorer.py           # L2 技術指標評分
│   ├── market.py           # 大盤 & 產業 ETF 背景數據
│   ├── ranker.py           # L3 DeepSeek AI 精選
│   ├── tracker.py          # 訊號追蹤（watchlist 管理）
│   ├── pipeline.py         # 流程編排
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
