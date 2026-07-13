---
name: build-and-env
description: 觀察到以下任一狀態時載入：需要從零重建開發環境；pip install 失敗或考慮升級 yfinance/curl_cffi；想引入新的技術指標函式庫；CI 出現 Segmentation Fault（exit 139）；Windows 終端機輸出出現編碼錯誤。
---

# 從零重建環境與依賴鎖版禁區

事實時間戳：2026-07-13（依 requirements.txt 與兩個 workflow 檔驗證）。

## 從零重建（Windows PowerShell，已驗證流程）

```powershell
git clone https://github.com/wcsy0827/us-stock-screener.git
cd us-stock-screener
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # 填入 DEEPSEEK_API_KEY（無 key 時 L3 走 fallback，見 config-and-flags）
# ⚠ .env.example 的 MIN_SCORE=70 與程式預設 60 不同——照抄會無聲抬高 L2 門檻，
#   複製後把該行改回 60 或直接刪除（詳見 config-and-flags）
pip install -r requirements-dev.txt   # 開發用（= requirements.txt + pytest>=8.0.0）
pytest                        # 完成定義：全綠（2026-07-13 實測 164 passed，離線、不連網）
```

- **Python 版本**：CI（`daily-screener.yml`、`tests.yml`）固定 `3.12`。本機 3.14.6 於 2026-07-13 實測可跑全部測試，但會出現 `datetime.utcnow()` DeprecationWarning（`src/analyzer.py:91` 等處）——警告非錯誤，勿順手「修掉」——決策 12 逐字錨定 `datetime.utcnow().date()`，即使 `now(timezone.utc)` 等價替換也應獨立提交並附測試，不要夾帶在其他改動裡。
- Windows 執行主程式前設 `$env:PYTHONUTF8=1`（或用 `.\run.ps1`，內含此設定）。`main.py` 開頭已有 stdout/stderr UTF-8 reconfigure 防線，但子程序不繼承。

## 依賴鎖版禁區（每一條都有真實事故背書）

**觸發**：任何人（包括你自己）想升級 yfinance、放寬 curl_cffi、或引入技術指標函式庫。

1. **`yfinance>=0.2.50,<1.0.0` 與 `curl_cffi>=0.7.0,<0.15.0` 兩個上限不得移除**。`curl_cffi 0.15.0` 在 GitHub Actions Ubuntu 與 Python toolchain 的 `LD_LIBRARY_PATH` 衝突，直接 Segfault；yfinance 1.x 要求 `curl_cffi>=0.15`，故連帶鎖 yfinance。yfinance 0.2.66 的 API（`download(group_by="ticker")`、`ticker.info`、`ticker.calendar`）與本專案用法完全相容。
2. **pandas-ta 不得重新引入**。它依賴 numba/llvmlite，numba 的 LLVM 初始化在 GitHub Actions Ubuntu 觸發 Segfault（exit 139）。`src/scorer.py` 已用純 pandas 實作全部指標（EMA/RSI/MACD/ATR）。
   - 反例（實際會出現的合理化）：「pandas-ta 新版本應該已修好 numba 問題，而且程式碼會短很多。」——不要。事故成本是每日排程整批紅燈失敗，省下的程式行數不值得；且純 pandas 實作已是規格的一部分（`specs/scorer.md`）。
   - 正例：需要新指標 → 在 `scorer.py` 內用 pandas 手寫（參考既有 EMA/RSI/ATR 寫法），並補進 `specs/scorer.md`。

**步驟**（若真的必須動依賴）：改 requirements → 本機 `pytest` 全綠 → 開 PR 讓 `tests.yml` 在 Ubuntu 3.12 上實跑（本機 Windows 通過不代表 CI 不 Segfault）→ 合併後盯第一次 `daily-screener.yml` 排程執行的 log。
**完成定義**：CI Ubuntu 上 pytest 綠 + 下一次每日排程成功產出 report commit。

## 已知環境陷阱

- `.venv/`、`.cache/`、`.env` 已被 `.gitignore` 排除，不得 commit。
- 測試不需要網路、不需要 API key、不觸碰 `data/`（`tests/conftest.py` 把 `src/` 加入 sys.path；tracker/analyzer 測試用 `isolate_data_dir` fixture 隔離到 tmp_path）。「跑測試前要先設好 .env」是錯誤假設。

再驗證（PowerShell）：`pip install -r requirements-dev.txt; python -m pytest -q`（預期全綠、無網路需求）
