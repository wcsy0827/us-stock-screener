---
name: config-and-flags
description: 觀察到以下任一狀態時載入：需要調整門檻/上限/env 變數；行為與文件描述的預設值不符；想新增 CLI 旗標或 feature flag；不確定某常數該改哪裡、會影響哪些下游。
---

# 設定、環境變數與常數地圖

事實時間戳：2026-07-13（每一筆均以 檔案:行號 對照原始碼驗證）。

## 環境變數（.env / CI env）

| 變數 | 程式預設 | 定義位置 | 語意 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | `""` | `src/ranker.py:18` | 空值時 L3 走 `_enrich_fallback()` 降級（標記 `is_fallback`，不進 watchlist、不計入 ai_count） |
| `MAX_OUTPUT` | 3 | `main.py`（`--top` 預設） | L3 精選上限 |
| `MIN_SCORE` | 60 | `main.py`（`--min-score` 預設） | L2 品質門檻基準（Regime 會再動態調整；其上疊加 Top 55 排名上限） |
| `MIN_PRICE` | 5 | `src/filter.py:11` | L1 |
| `MIN_DOLLAR_VOLUME` | 10000000 | `src/filter.py:12` | L1，30 日均量美元成交額 |
| `MIN_MARKET_CAP` | 300000000 | `src/filter.py:13` | L1 |
| `MAX_ATR_PCT` | 8 | `src/filter.py:15` | L1 波動上限（DD-8）；同時是 DD-19 ATR 錨定買入區間寬度的天然上界 |
| `MIN_AI_CONFIDENCE` | 6 | `src/tracker.py:18` | 低於此不進 watchlist（DD-14） |
| `MAX_ACTIVE_POSITIONS` | 5 | `src/tracker.py:19` | 持倉上限＋掛單名單制（tracker DD-20） |

**已知陷阱（實際存在的落差）**：
- `.env.example` 寫 `MIN_SCORE=70`，但程式預設與 README 文字都是 60。**照抄 .env.example 會無聲把 L2 門檻抬到 70**。改門檻做實驗前先確認 `.env` 實際值。
- `.env.example` 有 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`，但 `src/` 全域 grep 無任何引用（2026-07-13 驗證）——是殘留欄位，設定了也沒有任何效果，勿據此假設有通知功能。

## 模組頂部常數（改前先讀對應 spec）

| 常數 | 值 | 位置 | 動它之前 |
|---|---|---|---|
| L2 六維權重 `WEIGHT_*` | 20/18/17/15/15/15 | `src/scorer.py:12-17` | 讀 `specs/scorer.md`（總和必須 100） |
| `L2_TARGET_COUNT` | 55 | `src/scorer.py:19` | DD-10：排名上限疊加在品質門檻之上，同分保留、force_pass 不受限 |
| `MAX_CANDIDATES_TO_AI` / `MAX_SECTOR_CANDIDATES` | 40 / 8 | `src/ranker.py:21-22` | 候選池廣度，DD-20（ranker）調精選上限時明確不動這兩個 |
| `TRAILING_ACTIVATION_PCT` / `TRAILING_RETRACE_PCT` | 0.10 / 0.05 | `src/tracker.py:34-35` | publisher 直接 import 同名常數（單一事實來源），不得在 publisher 端另抄一份 |
| `_DEFAULT_WATCH_DAYS` / `_DEFAULT_HOLD_DAYS` | 5 / 10 | `src/tracker.py:20,24` | watch 上限另有 DD-15/16 策略×Regime 查表，改這裡不等於改全部 |
| `MARKET_CLOSE_HOUR/MINUTE` | 16 / 15 | `src/fetcher.py:22-23` | 盤中殘缺 K 棒判定線（16:15 ET，含 settle buffer） |
| `EARNINGS_BLACKOUT_DAYS` | 5 | `src/filter.py:16` | 財報防禦牆窗口，錨定 market_date 而非系統日期 |
| `BREADTH_SMOOTHING_DAYS` | 3 | `src/market.py:12` | Regime 廣度平滑窗口 |

## 規則：不新增 feature flag

**觸發**：你想加一個新 CLI 旗標或開關來解決問題。
**步驟**：先檢查既有旗標語意是否已涵蓋（`--no-cache` / `--no-ai-cache` / `--yes` / `--top` / `--min-score`）；不涵蓋則直接改行為，不加開關（CLAUDE.md 慣例：不新增 feature flag 或向後相容 shim）。
- 正例：CI price 快取汙染 → 把 CI 指令從 `--no-ai-cache` 換成既有的 `--no-cache`。
- 反例（當時真實考慮過並否決）：「加一個 `--no-price-cache` 新旗標，語意最精確。」——被否決，理由：違反不加 flag 原則，且旗標矩陣每多一個維度，後續每個快取問題都要重推一次組合。
**完成定義**：diff 內沒有新增 argparse 參數與 env 開關，行為改動有測試或 dry-run 證據。

## 規則：`_INFO_HTML` 不得插值 runtime 常數

**觸發**：想把 `MAX_ACTIVE_POSITIONS` 之類的值動態塞進前端說明卡片。
**原因**：`docs/index.html` 由**整檔全等**測試守門，`.env` 不同的環境跑測試會誤紅。只寫靜態文字（tracker DD-20 明文約束）。

再驗證（Git Bash 或 Claude Code 的 Grep 工具；PowerShell 無 grep）：`grep -n "os.getenv" src/*.py main.py`（對照上表是否仍一致）
