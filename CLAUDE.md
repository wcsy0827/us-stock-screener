# 美股 AI 選股系統 — Claude Code 工作指南

## 專案概述

每日自動掃描 S&P 500，透過三層篩選 + 大盤 Regime 感知，找出符合當日市場環境的買入機會。結果發布至 GitHub Pages。

## 執行命令

```powershell
# 安裝
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # 填入 DEEPSEEK_API_KEY

# 本機測試（完整流程，不 push）
python main.py --dry-run

# CI 模式（跳過今日重複執行確認）
python main.py --dry-run --yes

# 強制忽略快取，重新下載所有數據
python main.py --dry-run --no-cache

# 正式執行（生成並 push 至 GitHub Pages）
$env:PYTHONUTF8=1; python main.py

# Windows 包裝腳本
.\run.ps1 --dry-run
.\run.ps1 --top 10
```

## 架構速覽

```
S&P 500 (~503 支)
  ↓ Step 1   universe.py     爬取成份股
  ↓ Step 2   fetcher.py      下載 90 日日 K（.cache/ 快取）
  ↓ Step 2.5 market.py       快速 Regime 判定（廣度 + VIX）← 必須在 scorer 之前
                              回傳 (regime, breadth_pct, vix_value)，供 Step 5.5 複用
  ↓ Step 3   fetcher.py      抓基本面（7 日快取），順帶提取 earningsDate 欄位
  ↓ Step 3.5 earnings.py     財報日查詢（Tier 1+2）→ earnings_registry.json（30 日快取）
  ↓ Step 4   filter.py       L1 流動性硬篩（股價/均量/市值/交易天數）
  ↓ Step 4.5 earnings.py     Tier 3 精準補抓（僅對流動性篩選後倖存個股）
             filter.py       財報防禦牆（排除 3 天內有財報的個股）
  ↓ Step 5   scorer.py       L2 技術評分（動態門檻；量能 K_pos 綁定；ATR 倍數動能）
  ↓ Step 5.5 market.py       完整大盤 ETF 背景（直接複用 Step 2.5 的廣度與 VIX，不重算）
  ↓ Step 6   ranker.py       L3 DeepSeek AI 精選（≤5 支）
             tracker.py      訊號追蹤（watchlist.json）→ 結算歸檔（performance_history.json）
             publisher.py    HTML 報告 → GitHub Pages（個股浮損益、今日結算區段、策略 Tooltip、歷史績效儀表板）
```

## 模組 ↔ 規格對照表

修改任何模組**前**，先讀對應規格文件：

| 模組 | 規格文件 |
|------|----------|
| `src/scorer.py` | `specs/scorer.md` |
| `src/tracker.py` | `specs/tracker.md` |
| `src/ranker.py` | `specs/ranker.md` |
| `src/market.py` | `specs/market.md` |
| `src/pipeline.py` | `specs/pipeline.md` |
| `src/fetcher.py` | `specs/pipeline.md`（快取節） |
| `src/filter.py` | `specs/pipeline.md`（L1 節）、`specs/earnings.md`（財報防禦牆） |
| `src/earnings.py` | `specs/earnings.md` |

## Spec-First 工作流

```
需求 → 更新/新增 specs/<module>.md → 實作 → PR 引用規格節次
```

- 若需求與現有規格衝突，**先更新規格**，說明為何改變，再動程式碼
- 規格的 Design Decisions 節是已解決的設計爭議，不得在未取得用戶同意前繞過

## 五個最重要的設計決策（摘要）

1. **EMA50 悖論（tracker.py DD-1）**：反轉策略進場點本就在 EMA50 之下，不能以跌破 EMA50 作為失效門檻。反轉股失效條件改用 AI 設定的 `stop_loss` 絕對價。→ 詳見 `specs/tracker.md`

2. **拆股免疫（tracker.py DD-3）**：yfinance `auto_adjust=True` 在拆股後會回溯修改全部歷史收盤價，導致 watchlist 記錄的絕對止損價變成幽靈訊號。解法：記錄 `signal_date_close`，每日計算 split_factor 並平移所有門檻。→ 詳見 `specs/tracker.md`

3. **Step 2.5 指標複用（pipeline.py DD-1）**：市場廣度 + VIX 在 Step 2.5 計算後直接傳給 Step 5.5，不重算、不重下載。`fetch_regime_quick` 的三個回傳值（`regime`, `breadth_pct`, `vix_value`）均須保留並傳入 `fetch_market_context`，否則兩次判定間有分鐘級時差導致 Regime 不一致。→ 詳見 `specs/pipeline.md`

4. **開盤跳空安全攔截（tracker.py DD-7）**：`watch → active` 轉換時，除了 `price >= buy_zone_lower` 外，必須額外確認 `price > stop_loss`。防止 AI 誤設止損在買入區間內時，跳空進場卻已跌破止損，污染 performance_history.json。→ 詳見 `specs/tracker.md`

5. **績效結算狀態機（tracker.py DD-6）**：active 部位不由時間到期控制，改由 `_check_settlement()` 的三態結算（CLOSED_PROFIT / CLOSED_LOSS / FORCE_EXPIRED）觸發，結算後寫入 `data/performance_history.json` 並移出 watchlist。publisher 讀取此檔案時必須做冷啟動保護（檔案不存在或空陣列時回傳零值，不得拋 ZeroDivisionError）。→ 詳見 `specs/tracker.md`

## 程式碼慣例

- `print()` 訊息用繁體中文，格式 `[module] 說明`
- 不寫無謂注釋；只在 WHY 非顯而易見時加一行注釋
- 不新增 feature flag 或向後相容 shim，直接改程式碼
- 錯誤處理只在系統邊界（外部 API / 使用者輸入）加；內部函式信任呼叫端
- 常數用全大寫，放在模組頂部
- AI 輸出欄位的型態：`hold_period` 必須解析為整數（`_parse_hold_period` 已支援 int/float/str 輸入），Prompt 應要求 AI 直接輸出整數天數

## 禁止事項

- **不要直接修改 `docs/` 下的 HTML**（由 `publisher.py` 生成，手動改會被下次執行覆蓋）
- **修改篩選流程、評分邏輯或策略定義後，必須判斷是否需要更新 `publisher.py` 的 `_INFO_HTML`**：前端系統說明卡片（篩選流程、L2 評分表、Regime 表、訊號追蹤狀態）是靜態字串，不會自動反映程式碼改動。凡是影響「L1/L2/L3 定義、評分條件、Regime 邊界、狀態機轉換規則」的修改，都須同步更新 `_INFO_HTML`，並手動同步 `docs/index.html`（同一 commit）。
- **修改 `publisher.py` 的靜態文字（如 `_INFO_HTML`）後，必須同步手動更新 `docs/index.html`**：pipeline 只在執行時才重新生成 HTML，修改 `publisher.py` 不會自動更新已存在的 `docs/` 檔案，GitHub Pages 畫面不會立即反映。例外：若能馬上執行 `python main.py --dry-run --yes` 並將產出的 `docs/` 一起 commit，則不需要手動改。
- **每次修改程式碼或規格後，必須同步更新 `CLAUDE.md` 與 `README.md`**：架構速覽、模組對照表、快取說明、L2 評分表、專案結構等章節若有變動，須在同一個 commit 內一併更新，不得遺留過時描述。
- **不要 commit** `.env`、`.cache/`、`.venv/`（`.gitignore` 已排除）
- **不要在 CI workflow 移除 `--dry-run`**（workflow 已設計成執行後自己 git push）
- **不要同時修改 `tracker.py` 和 `scorer.py`**（難以隔離問題，分次修改）
- **不要繞過規格的 Design Decisions**（DD 是已解決的設計爭議，見上方五大決策）

## 快取說明

| 快取類型 | 路徑 | 有效期 |
|----------|------|--------|
| 日 K 數據 | `.cache/price_YYYYMMDD.pkl` | 當日 |
| 基本面資訊 | `.cache/info_YYYYMMDD.json` | 7 日（取最近一份） |
| 財報日期 | `.cache/earnings_registry.json` | 30 日（per-symbol TTL，獨立管理） |
| 追蹤清單 | `data/watchlist.json` | 永久（持久化） |
| 歷史績效 | `data/performance_history.json` | 永久（只增不刪） |

## GitHub Actions

- 排程：週一至五 UTC 21:30（台灣時間隔日 05:30）
- Secrets：`DEEPSEEK_API_KEY` 設在 repo Settings → Secrets and variables → Actions
- 手動觸發：Actions 頁 → Daily Stock Screener → Run workflow

## 依賴版本限制

- **`pandas<3.0.0`**：pandas-ta 0.4.x 尚未驗證與 pandas 3.x 相容，鎖定 2.x 以避免 API 不相容。
- **`pandas-ta>=0.4.67b0`**：PyPI 僅有 0.4.67b0 與 0.4.71b0，0.4.x 使用 numba JIT 取代舊版 C extension，解決 Segmentation Fault 問題。
- **`numpy>=1.26.0,<2.0.0`**：1.26.0 是第一個有 Python 3.12 預編譯 wheel 的版本；上限 <2.0.0 作為保險（numba 0.61.2 已支援 <2.2，但 pandas-ta 尚未明確標示）。**兩端限制均不得移除**。
