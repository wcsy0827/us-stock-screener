---
name: ci-operations
description: 觀察到以下任一狀態時載入：需要修改 .github/workflows/ 任一檔案；排程執行失敗或無新 commit；需要手動觸發 workflow 或排查某天的 CI log；有人提議在 CI 加回快取或移除 --no-cache。
---

# CI 運維：daily-screener.yml 與 tests.yml

事實時間戳：2026-07-13（逐行對照兩個 workflow 檔驗證）。

## daily-screener.yml 解剖（關鍵行皆有事故背書）

- 排程：`cron: '30 21 * * 1-5'`（UTC 21:30 = 美東收盤後 1.5 小時 = 台灣隔日 05:30）；另有 `workflow_dispatch` 手動觸發。
- 執行指令：`python main.py --dry-run --yes --no-cache`
  - `--dry-run`：**不得移除**。Python 端不 push，push 由 workflow 的 commit 步驟做——移除會變成雙重 push。
  - `--yes`：CI 無法互動確認。
  - `--no-cache`：**不得降級回 `--no-ai-cache` 或移除**。兩次真實事故（同在 2026-07-02，同一機制的兩種形態）：①AI 快取——hotfix（PR #46）合併後重跑仍輸出舊 AI 結果，cache key 只綁日期，撿到合併前的 `ranked_*.json`；②price 快取——白天手動觸發建立含 7/1 數據的 price 快取，晚間排程複用 → 7/2 報告永遠沒產出、無新 commit 的**靜默失敗**。
- commit 步驟：訊息取自剛產出的 `docs/data/last_run.json` 的 `market_date`（`report: $MARKET_DATE`），**不得改用 `date -u`**——UTC 20:00 前手動觸發時系統日期已是隔天、內容卻是前一交易日。
- `.cache/` 的 `actions/cache` 步驟**已刻意移除**，不要加回（注意：workflow 內仍有一個 `actions/cache@v4`，那是 **pip 套件快取**（`path: ~/.cache/pip`），與 `.cache/` 數據快取無關，保留是正確的）：`--no-cache` 下快取內容永遠用不到，加回只佔 Actions 空間並誘使未來的人「順手」拿掉 `--no-cache`。
  - 反例（事故前的真實設計理由）：「快取 price/info 可以跨同日重跑省下 yfinance 下載時間。」——正是這個合理的省時設計造成兩次資料汙染；正常排程一天只跑一次，省下的只有同日重跑那幾秒。
- Secrets：`DEEPSEEK_API_KEY` 在 repo Settings → Secrets and variables → Actions。

## tests.yml

push（main）與 PR 觸發，路徑過濾：`src/**`、`tests/**`、`main.py`、`requirements*.txt`、`pytest.ini`、`docs/index.html`。Ubuntu + Python 3.12，兩個步驟：`pip install -r requirements-dev.txt`、`pytest`。
- 注意 `docs/index.html` 在觸發路徑內：改它（透過 `python src/publisher.py` 再生成）也會觸發全等守門測試。

## 排查某一天的 CI 執行（實戰驗證過的流程）

**觸發**：某天報告內容可疑、或排程跑了但 Pages 沒更新。

```bash
# 以下在 Git Bash（或 Claude Code 的 Bash 工具）執行；PowerShell 無 grep
gh run list --workflow daily-screener.yml --limit 10   # 找到當天 run id 與結論
gh run view <run_id> --log | grep -E "\[ranker\]|\[pipeline\]|\[tracker\]|\[fetcher\]"
```

關鍵 log 特徵（2026-07-06 事故排查中實際使用）：
- `[ranker] 複用今日 AI 快取` → AI 沒被重新呼叫（CI 出現此行即異常，CI 應為 --no-cache）
- `[pipeline] 警告：大盤數據抓取失敗，繼續執行：'Close'` → market_context 曾整組退化（已由 market DD-7 防呆，單 ETF 失敗不再拖垮全局）
- `[fetcher] 成功取得 N 支股票數據`：N < 515（503 成份股 + SPY + 11 ETF，成份股數會浮動）→ 有 ticker 下載失敗
- `No changes to commit` → 產出與前次全等（多為報告日期回退，對照 last_run.json 的 market_date）

**完成定義**：能指出該次執行的 market_date、regime、ai_count 三個值分別從哪行 log/哪個檔案得到，且互相一致。

## 修改 workflow 的驗證標準

本機無法重現 Actions 快取/時區行為。最低標準（沿用 plans/2026-07-02-ci-ai-cache-staleness.md 的慣例）：①YAML 語法檢查：`pip install pyyaml; python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-screener.yml'))"`（pyyaml **不在** requirements 內，需臨時安裝）；②合併後盯下一次真實執行的 log 確認新行為出現。宣稱「已修好」前，第二步是必要證據。

再驗證：`cat .github/workflows/daily-screener.yml`（對照 cron、旗標、commit 步驟三處）
