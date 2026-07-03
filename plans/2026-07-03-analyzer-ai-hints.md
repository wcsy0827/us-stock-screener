# 本地績效診斷 analyzer：歸納賺賠關聯 → ai_hints.json → 動態注入 L3 Prompt（Spec-First）

> 核准日期：2026-07-03。對應規格：`specs/analyzer.md`（全部 DD）、`specs/pipeline.md` DD-7、`specs/ranker.md` DD-16。

## Context

系統已累積結算歸檔機制（tracker `_archive_to_performance_history` → `data/performance_history.json`），但這些實戰賺賠數據目前只用於 publisher 的績效儀表板展示，**L3 AI 選股完全沒有從歷史績效學習的回饋迴路**。本任務新增 `src/analyzer.py`：每輪執行時讀取 performance_history，歸納「Regime × 策略 × 產業」的賺賠關聯，輸出 `data/ai_hints.json`；`ranker.py` 在呼叫 DeepSeek 前自動讀取並附加在 Prompt 末尾，讓 AI 的取捨參考實戰統計。

**探索確認的事實**：
- 實際流程是 `pipeline.run()`（含 L3 ranker）→ `main.py` 的 `run_tracker()`（結算）→ `publish()`。tracker 結算在 ranker **之後**，pipeline 內沒有 tracker 呼叫。→ **已與使用者確認**：analyzer 作為 pipeline **Step 5.7**（Step 5.5 之後、Step 6 之前），讀「截至前一輪執行」的已結算資料，天然 1-cycle lag，與 tracker DD-11 的 1-day lag 哲學一致，零架構變動。
- 歸檔記錄的可用維度：`signal_details.entry_regime`、`signal_details.assigned_strategy`、`meta_data.sector`、`performance_metrics.return_pct/is_win`、`actual_outcome.exit_reason/holding_days`。**沒有基本面欄位**（ranker DD-14 的 Fwd_PE 等只進 Prompt、未進 watchlist 與歸檔）。→ **已與使用者確認**：本次用 sector（產業）代理基本面維度，不碰 tracker.py。
- `data/performance_history.json` 規劃當下不存在（尚無結算記錄）——冷啟動是第一天的常態，必須零錯誤通過。
- CI workflow 的 commit 步驟已是 `git add docs/ data/`，`ai_hints.json` 會自動被 push 持久化，**不需改 workflow**。
- `publisher._load_performance_stats()` 已示範同一 JSON 的解析慣例（exit_reason 白名單、`return_pct` 判空），analyzer 沿用同樣的防禦模式。
- Prompt 為 XML 三區塊結構（ranker DD-1）；AI 快取以 market_date 為鍵，CI 一律 `--no-cache`，hints 變動不會被舊快取蓋掉（本機 dry-run 複用快取時 hints 不生效，屬既有快取語意，可接受）。

## 設計要點（濃縮版見各 spec 的 DD）

### analyzer DD-1: 內嵌 pipeline Step 5.7，不新增 CLI 參數；讀前輪結算（1-cycle lag）
- 捨棄：把結算搬到 ranker 之前（重構 main.py/tracker DD-11 執行順序，風險高、只換得一天新鮮度）；獨立 CLI 參數（GitHub Actions 手動觸發模式下多餘）。

### analyzer DD-2: 維度為 Regime × 策略 × 產業；產業代理基本面
- 捨棄：擴充 tracker 歸檔基本面欄位（觸碰 tracker.py，且既有歷史記錄無此欄位，需長時間累積才有樣本；留待未來獨立立案）。使用者於規劃階段選定「sector 產業代理」方案。

### analyzer DD-3: 最小樣本門檻（分組 3 / 總量 5）+ 描述性語氣，防小樣本過擬合
- 捨棄：無門檻全量輸出（2 筆虧損就讓 AI 迴避整個產業，統計噪音）；指令式結論（「不要選能源股」——越權，L3 決策主權在 Regime 與候選池）。

### analyzer DD-4: ai_hints.json 為可再生衍生檔，statistics 與渲染同源
- analyzer 同時輸出結構化統計（dimensions，含全部分組供審計）與渲染好的 prompt_lines（只含達門檻分組）；ranker 只盲讀 prompt_lines。檔案每輪全量重寫，刪除後自動重建。
- 捨棄：ranker 端自行渲染（統計與呈現分散兩模組）；只存統計不存文字；不落地直接記憶體傳遞（違反 ai_hints.json 檔案契約、失去可審計性）。

### pipeline DD-7: Step 5.7 放在 VIX Gate 之後、Step 6 之前，try/except 攔截
- VIX Gate 中斷時 L3 不執行，hints 無消費者，放 Gate 之後免做白工；analyzer 失敗印警告後繼續（enhancement 非關鍵路徑）。
- 捨棄：放在 main.py 的 run_tracker 之後（寫入時機與消費時機分離、更難推理）；失敗中斷流程。

### ranker DD-16: Historical_Performance_Review 第四區塊
- `_load_ai_hints()` 任何失敗靜默回傳 `[]`；`_build_prompt(ai_hints=...)` 非空時在 `</Output_Constraint>` 之後附加第四區塊，首行說明 + 尾行樣本警語；空清單時 Prompt 與舊版逐字元相同（零回歸）。`SYSTEM_PROMPT` 與 JSON 輸出 schema 不變。
- 捨棄：hints 放進 `<Market_Regime>`（稀釋 Regime 服從性指令的權威）；改寫 SYSTEM_PROMPT（靜態字串放動態統計，語意錯位）。

## 程式碼改動

1. `src/analyzer.py`（新檔）：`MIN_GROUP_SAMPLES`/`MIN_TOTAL_SAMPLES` + `generate_hints()` + `_load_settled_records()`/`_aggregate()`/`_render_lines()`；只用 stdlib；勝負判定與 tracker DD-13 同口徑（純 `return_pct > 0`）。
2. `src/pipeline.py`：Step 5.7 區塊（VIX Gate 之後、Step 6 之前），try/except 印警告不中斷。
3. `src/ranker.py`：`_AI_HINTS_PATH` 常數、`_load_ai_hints()`、`_build_prompt` 新參數 `ai_hints`、`rank_candidates` 串接（附加時印 `[ranker]` 訊息）。
4. `tests/test_analyzer.py`（新檔）：比照 test_tracker 的 `isolate_data_dir` fixture 隔離 tmp_path；案例：冷啟動、損壞 JSON、非結算/null 記錄排除、聚合數字驗證、分組門檻抑制、總樣本門檻抑制。

## 明確不做（範疇約束）

- 不碰數據下載（fetcher/universe/earnings/market）與 L2 評分（scorer.py）。
- 不碰 tracker.py（基本面歸檔擴充已與使用者確認排除）。
- 不新增 CLI 參數、不改 CI workflow（`git add data/` 已涵蓋）。
- 不做 publisher 前端展示（hints 是 AI 內部回饋，非報告內容；`_INFO_HTML` 的 L1/L2/L3 定義未變，不需更新）。

## 驗證（實作時已全數執行）

1. `pytest` — 84 項全數通過（既有 78 + 新增 6）。
2. ranker 零回歸驗證：`ai_hints=[]` 時 `_build_prompt` 輸出與舊版逐字元相同；非空時末尾正確出現 `<Historical_Performance_Review>` 區塊含警語。
3. 冷啟動實跑：對真實 repo（無 performance_history.json）執行 `generate_hints()`，正常寫出空統計的 `data/ai_hints.json`，不拋例外。
