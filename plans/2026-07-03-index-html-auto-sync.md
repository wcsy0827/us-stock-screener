# 消除 `_INFO_HTML` → `docs/index.html` 手動同步規則（Spec-First）

> 核准日期：2026-07-03。對應規格：`specs/publisher.md` DD-6。

## Context

現行規則（CLAUDE.md 禁止事項、specs/publisher.md Behavior）要求：改動 `publisher.py` 的 `_INFO_HTML` 或 `_build_index()` 模板後，必須**同一 commit 手動同步** `docs/index.html`（或實跑 `--dry-run` 重生成）。`tests/test_publisher_info_sync.py` 以「`_INFO_HTML` 每一行實質內容須原樣出現在 index.html」的子字串比對把關。

**手動規則的根本問題已不存在**：自 DD-5 起 `_build_index()` 已是**無參數的確定性純函式**（只嵌入靜態字串 `_CSS` 與 `_INFO_HTML`，無日期、無資料輸入）。`docs/index.html` 完全是 `publisher.py` 的純函數輸出——同步這件事根本不該由人做。

**探索已驗證的事實**：
- 規劃當下 `docs/index.html` 與 `_build_index()` 輸出**逐字元完全一致**（已實測比對）。
- `publisher.py` 只 import stdlib（json/subprocess/datetime/pathlib），可獨立以 `python src/publisher.py` 執行，不觸發任何下載或 API。
- 現行測試只守 `_INFO_HTML` 的行，**不守** `_CSS`、JS 邏輯等模板其餘部分——改 CSS/JS 漂移不會紅燈，是既有守門缺口。

## 方案：全等比對測試 + 一鍵再生成 CLI

同步動作由「人工編輯 HTML」改為「執行一條命令」，守門由「行子字串比對」升級為「整檔全等比對」。

### 1. `src/publisher.py` — 新增 `sync_index()` 與 CLI 入口

```python
def sync_index() -> None:
    """重新生成 docs/index.html。_build_index() 為無參數確定性函式（DD-6），
    改動 _INFO_HTML/_CSS/模板後執行本函式即完成同步，不得手動編輯 docs/index.html。"""
    _INDEX_HTML.write_text(_build_index(), encoding="utf-8", newline="\n")
    print(f"[publisher] 首頁已同步：{_INDEX_HTML}")


if __name__ == "__main__":
    sync_index()
```

- `newline="\n"` 固定 LF，避免 Windows 本機執行產生 CRLF 造成整檔 whitespace diff（CI Linux 的 `publish()` 輸出即為 LF）。
- 既有 `publish()` 內寫 index.html 的兩行（`_build_index()` + `write_text`）改為呼叫 `sync_index()`，單一出口。

### 2. `tests/test_publisher_info_sync.py` — 改為全等比對

```python
def test_index_html_matches_build_index():
    assert _INDEX_HTML.read_text(encoding="utf-8") == publisher._build_index(), (
        "docs/index.html 與 publisher._build_index() 輸出不一致；"
        "執行 python src/publisher.py 重新生成後一起 commit"
    )
```

- 檔名不變（任務約束指定此檔）；docstring 更新為新語意。
- `read_text` 預設 universal newlines（CRLF/LF 讀入皆為 `\n`），跨 Windows 本機與 CI Linux 行為一致。
- 守門範圍從「_INFO_HTML 的行」擴大為「整份模板」（含 `_CSS`、JS）——比原測試更強，且失敗時修復動作是一條命令而非人工比對編輯。

### 3. `docs/index.html` — 以 `python src/publisher.py` 重新生成

規劃當下內容已與 `_build_index()` 全等，執行後為 no-op（或僅行尾正規化）；此步同時驗證 CLI 可用。

### 4. `specs/publisher.md` — 規格更新（Spec-First，先於程式碼）

- **Behavior** 的手動同步規則改為：執行 `python src/publisher.py` 重新生成 + 全等比對守門。
- 新增 **DD-6**（見 `specs/publisher.md`），與本檔互相連結。

### 5. `plans/2026-07-03-index-html-auto-sync.md` — 完整 plan 存檔（本檔）

### 6. `CLAUDE.md` / `README.md` — 連動文字（最小幅度）

- **CLAUDE.md 禁止事項**：兩條「必須手動同步 `docs/index.html`」的 bullet 改為「執行 `python src/publisher.py` 重新生成並一起 commit」。
- **CLAUDE.md 程式碼慣例**：`tests/test_publisher_info_sync.py` 的描述由「每一行實質內容原樣出現」改為「整檔全等比對 `_build_index()` 輸出」。
- **README.md**：單元測試、tests.yml CI、專案結構三處的同步守門描述同步措辭。

## 考慮過但捨棄的方案

- **pre-commit hook 自動再生成**：引入額外基礎設施（hook 安裝、跨平台維護），且開發者繞過 hook（`--no-verify`）即失守；pytest 守門已在 CI 強制執行，hook 是重複防線。
- **測試內偵測漂移時自動改寫 `docs/index.html`**：CI 綠燈但 repo 內已部署的 GitHub Pages 檔案仍是舊的，測試通過反而掩蓋漂移，違反守門測試「紅燈=需要人為 commit」的語意。
- **維持行子字串比對**：守不住 `_CSS`/JS 漂移（既有缺口），且失敗後的修復動作仍是人工比對編輯 HTML。

## 不做的事（範疇約束）

- 不碰 `scorer.py`、`tracker.py`、任何 fetcher/數據模組。
- 不加 pre-commit hook、不改 CI workflow。
- 不重構 `publisher.py` 其他部分、不做效能優化。

## 驗證（實作時已全數執行）

1. `python src/publisher.py` — CLI 正常執行且 `git diff docs/index.html` 無內容差異。
2. `pytest` — 78 個測試全數通過。
3. 反向驗證：臨時在 `docs/index.html` 追加一行 → `pytest` 紅燈且錯誤訊息指示執行 `python src/publisher.py` → 執行後綠燈。
