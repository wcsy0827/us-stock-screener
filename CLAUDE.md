# CLAUDE.md — 美股 AI 選股系統

## 環境

- Python 3.12+，虛擬環境位於 `.venv/`
- 必需 `.env` 文件，至少含 `DEEPSEEK_API_KEY`
- Windows 下 main.py 會自動設定 stdout UTF-8；`run.ps1` 也會處理，直接用即可

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # 填入 DEEPSEEK_API_KEY
```

---

## 執行命令

```powershell
# 本機測試（完整流程，不 push）
python main.py --dry-run

# CI 模式（跳過今日重複執行確認）
python main.py --dry-run --yes

# 強制忽略快取，重新下載所有數據
python main.py --dry-run --no-cache

# 正式執行（生成並 push 至 GitHub Pages）
python main.py

# Windows 包裝腳本
.\run.ps1 --dry-run
.\run.ps1 --top 10
```

---

## 架構（6 層管道）

```
pipeline.py 協調：
  universe → fetcher → filter(L1) → scorer(L2) → market → ranker(L3) → tracker → publisher
```

- 各模組**獨立**，不跨層呼叫（scorer 不依賴 ranker，ranker 不依賴 publisher）
- **L1**（`filter.py`）：只做流動性/規模硬條件（股價、均量、市值、交易天數）
- **L2**（`scorer.py`）：技術指標評分，RSI 是獨立評分維度，不是 L1 條件
- **L3**（`ranker.py`）：DeepSeek AI，若 API 失敗自動 fallback 到 L2 排名

---

## L2 評分權重

| 指標 | 計算方式 | 滿分 |
|------|----------|------|
| MA 均線排列 | EMA5 > EMA10 > EMA20 > EMA50 | 25 |
| RSI 健康度 | 50–70 滿分；40–50 或 70–80 半分 | 20 |
| MACD 柱狀體 | 正且遞增滿分；僅正半分 | 20 |
| 量能放大 | > 均量 ×1.5 滿分；> 均量半分 | 20 |
| 20 日動能 | 漲 >10% 滿分；>5% 半分；>0% 1/4 分 | 15 |

**硬條件**：RSI > 80（超買）→ 整支股票直接 0 分，排除出 L3。

門檻：程式預設 60 分，可透過 `.env` 的 `MIN_SCORE` 或 `--min-score` 覆蓋。

---

## AI 整合（L3 ranker）

- DeepSeek 透過 OpenAI SDK + `base_url` 接入，模型 `deepseek-chat`
- AI 輸出欄位：`rank`, `reason`, `risk`, `confidence`, `buy_zone`, `target`, `stop_loss`, `hold_period`, `strategy`, `strategy_reason`, `confidence_reason`
- API 失敗 → 自動 fallback 到 L2 評分降序排名，不中斷流程

---

## 禁止事項

- **不要直接修改 `docs/` 下的 HTML**（由 `publisher.py` 生成，手動改會被下次執行覆蓋）
- **不要 commit** `.env`、`.cache/`、`.venv/`（`.gitignore` 已排除）
- **不要在 CI workflow 移除 `--dry-run`**（workflow 已設計成執行後自己 git push）
- **不要同時修改 `tracker.py` 和 `scorer.py`**（難以隔離問題，分次修改）

---

## 快取

- 價格數據：`.cache/price_YYYYMMDD.pkl`（pickle，7 天後自動清除）
- Fundamentals：`.cache/info_YYYYMMDD.json`（JSON，7 天後自動清除）
- 強制重新下載：加 `--no-cache` 參數

---

## 驗證方式

1. `python main.py --dry-run --yes`（確認流程不中斷）
2. 開啟 `docs/reports/YYYY-MM-DD.html` 確認報告正常顯示
3. 同日重跑應**替換**而非累積 watchlist 項目（tracker.py 核心規則）

---

## GitHub Actions

- 排程：週一至五 UTC 21:30（台灣時間隔日 05:30）
- Secrets：`DEEPSEEK_API_KEY` 設在 repo Settings → Secrets and variables → Actions
- 手動觸發：Actions 頁 → Daily Stock Screener → Run workflow
