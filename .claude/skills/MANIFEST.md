# skills-staging 清單（Manifest）

建立日期：2026-07-13。撰寫者：本 repo 首席架構師交接 session。每份技能一行 + 背書證據。
格式：`<skill>/SKILL.md`，可直接搬移至 `.claude/skills/<skill>/SKILL.md` 使用。

| 技能 | 一句話 | 背書證據 |
|---|---|---|
| build-and-env | 從零重建環境 + 依賴鎖版禁區（pandas-ta/curl_cffi/yfinance） | requirements.txt 逐行、兩個 workflow 的 python-version、CLAUDE.md CI 注意事項記載的兩次 Segfault 事故、2026-07-13 本機實測 pytest 164 passed |
| run-and-operate | CLI 旗標矩陣、場景選指令、報告日期時區心智模型、dry-run 副作用 | main.py parse_args 逐旗標、README 測試工作流程節、CLAUDE.md 決策 12 時區表、trim_incomplete_session（fetcher.py:185） |
| config-and-flags | env/常數地圖（檔案:行號）+ 不加 flag 原則 + .env.example 落差 | 全部條目以 grep 對照原始碼行號驗證；MIN_SCORE 70/60 落差與 TELEGRAM 殘留欄位為本次探勘實測發現 |
| data-and-caches | 檔案可逆性分類、last_run.json 迴路、快取 key 綁日期陷阱 | CLAUDE.md 快取表、market.py DD-5 遲滯帶讀取、兩次 CI 快取事故 plan 檔 |
| ci-operations | 兩個 workflow 解剖、--no-cache 的事故背書、CI log 分診命令 | daily-screener.yml / tests.yml 逐行、plans/2026-07-02 與 2026-07-03 兩份 CI 事故 plan、2026-07-06 事故排查用的 gh 命令 |
| validation-and-qa | 各類改動的最低證據標準、偽驗證禁令、副作用還原、守門測試修法 | CLAUDE.md 程式碼慣例/禁止事項、多份 plan 的驗證章節慣例、pytest 實測 |
| spec-first-docs-gate | spec-first 流程、plans/specs 雙檔義務、DD 編號地雷 | CLAUDE.md Plan 文件化規則、specs/ 與 plans/ 目錄實測、tracker/ranker DD-20 撞名與程式碼註解錯位（plan 檔明文記載） |
| architecture-contract | 九條載重不變量（日期錨定、步驟順序、生命週期、帳實一致、單一事實來源、拆股免疫、韌性邊界） | 每條對應具名 DD 或事故：決策 12、pipeline DD-1、tracker DD-3/11/12/13/17/19/20、market DD-6/7、ranker DD-12/15/18 |
| tracker-state-machine | tracker.py 函式地圖（行號）、修改檢查清單、風控欄位語意 | 函式行號 2026-07-13 grep 驗證；檢查清單每項對應 DD-8/18/19/20 的真實迴歸；tests/test_tracker.py 108 個測試函式 |
| debugging-playbook | 八個症狀→分診，全部來自具日期的真實事故 | S1=2026-07-06（plan 檔完整因果鏈）、S2=2026-07-02 PR #46 AI 快取事故、S3=2026-07-02 price 快取事故、S4=CB/KHC/V/AJG/LIN 實測數據、S5=2026-07-06 報告、S6=DD-17、S7=DD-3 |
| failure-archaeology | 事故表、被推翻設計、被否決方案目錄（含當時的合理化） | plans/ 15 份的「考慮過但捨棄」章節、git log 205 commits（86a5a27、dba28ba、a7bb69b 等具體 commit）、DD-20 v1→v2 同日修訂軌跡 |
| publisher-frontend-sync | docs/ 生成物鐵律、_INFO_HTML 同步義務、sync_index 唯一入口、報告呈現契約 | publisher DD-6/7/8/9、plans/2026-07-03-index-html-auto-sync.md、DD-19 plan 的「原判斷有誤追加同步」實例、行號 grep 驗證 |

## 審查記錄

2026-07-13 交付前經三個全新語境子代理審查（事實核查／教義一致性／零語境可用性），合計 BLOCKING 1、IMPORTANT 8、MINOR ~18，BLOCKING 與 IMPORTANT 已全數修正、多數 MINOR 一併修正；未能定案的發現見 [UNCERTAINTY.md](UNCERTAINTY.md)。

## 注意

本清單所有「行號」「測試數量（164）」為 2026-07-13 快照，屬易變事實；各技能檔尾均附一行再驗證指令。
