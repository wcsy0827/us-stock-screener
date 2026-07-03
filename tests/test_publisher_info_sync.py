"""守門測試：docs/index.html 必須與 publisher._build_index() 輸出整檔全等（DD-6）。

_build_index() 是無參數確定性函式，docs/index.html 是其純函數輸出。任何模板
改動（_INFO_HTML、_CSS、script 邏輯）後，執行 python src/publisher.py 一鍵
重新生成即完成同步；漂移即紅燈，不再依賴手動同步規則。
"""

from pathlib import Path

import publisher

_INDEX_HTML = Path(__file__).parents[1] / "docs" / "index.html"


def test_index_html_matches_build_index():
    assert _INDEX_HTML.read_text(encoding="utf-8") == publisher._build_index(), (
        "docs/index.html 與 publisher._build_index() 輸出不一致；"
        "執行 python src/publisher.py 重新生成後一起 commit"
    )
