# 美股 AI 選股系統 — Claude Code 工作指南

## 專案概述

每日自動掃描 S&P 500，透過三層篩選 + 大盤 Regime 感知，找出符合當日市場環境的買入機會。結果發布至 GitHub Pages。

## 架構速覽

```
S&P 500 (~503 支)
  ↓ Step 1   universe.py     爬取成份股
  ↓ Step 2   fetcher.py      下載 90 日日 K（.cache/ 快取）
  ↓ Step 2.5 market.py       快速 Regime 判定（廣度 + VIX）← 必須在 scorer 之前
  ↓ Step 3   fetcher.py      抓基本面（7 日快取）
  ↓ Step 4   filter.py       L1 硬篩
  ↓ Step 5   scorer.py       L2 技術評分（動態門檻）
  ↓ Step 5.5 market.py       完整大盤 ETF 背景
  ↓ Step 6   ranker.py       L3 DeepSeek AI 精選（≤5 支）
             tracker.py      訊號追蹤（watchlist.json）
             publisher.py    HTML 報告 → GitHub Pages
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
| `src/filter.py` | `specs/pipeline.md`（L1 節） |

## Spec-First 工作流

```
需求 → 更新/新增 specs/<module>.md → 實作 → PR 引用規格節次
```

- 若需求與現有規格衝突，**先更新規格**，說明為何改變，再動程式碼
- 規格的 Design Decisions 節是已解決的設計爭議，不得在未取得用戶同意前繞過

## 三個最重要的設計決策（摘要）

1. **EMA50 悖論（tracker.py）**：反轉策略進場點本就在 EMA50 之下，不能以跌破 EMA50 作為失效門檻。反轉股失效條件改用 AI 設定的 `stop_loss` 絕對價。→ 詳見 `specs/tracker.md`

2. **拆股免疫（tracker.py）**：yfinance `auto_adjust=True` 在拆股後會回溯修改全部歷史收盤價，導致 watchlist 記錄的絕對止損價變成幽靈訊號。解法：記錄 `signal_date_close`，每日計算 split_factor 並平移所有門檻。→ 詳見 `specs/tracker.md`

3. **Step 2.5 快速 Regime（pipeline.py）**：市場廣度 + VIX 必須在 L2 評分之前計算（供動態門檻和強制放行使用），但完整 ETF 背景資料只需在 AI 提示時使用，故分拆為 Step 2.5（輕量）與 Step 5.5（完整）。→ 詳見 `specs/pipeline.md`

## 程式碼慣例

- `print()` 訊息用繁體中文，格式 `[module] 說明`
- 不寫無謂注釋；只在 WHY 非顯而易見時加一行注釋
- 不新增 feature flag 或向後相容 shim，直接改程式碼
- 錯誤處理只在系統邊界（外部 API / 使用者輸入）加；內部函式信任呼叫端
- 常數用全大寫，放在模組頂部

## 快取說明

| 快取類型 | 路徑 | 有效期 |
|----------|------|--------|
| 日 K 數據 | `.cache/price_YYYYMMDD.pkl` | 當日 |
| 基本面資訊 | `.cache/info_YYYYMMDD.json` | 7 日（取最近一份） |
| 追蹤清單 | `data/watchlist.json` | 永久（持久化） |
