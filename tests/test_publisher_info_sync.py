"""守門測試：publisher._INFO_HTML 與 docs/index.html 的系統說明卡片必須同步。

CLAUDE.md 規定修改 _INFO_HTML 後必須同步手動更新 docs/index.html（或重跑
pipeline 重新生成）。此測試把該手動規則變成機器檢查：_INFO_HTML 的每一行
實質內容都必須原樣出現在 docs/index.html 中，漂移即紅燈。
"""

from pathlib import Path

import publisher

_INDEX_HTML = Path(__file__).parents[1] / "docs" / "index.html"


def test_info_html_lines_present_in_docs_index():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    missing = [
        line.strip()
        for line in publisher._INFO_HTML.splitlines()
        if len(line.strip()) > 20 and line.strip() not in html
    ]
    assert not missing, (
        "publisher._INFO_HTML 已修改但 docs/index.html 未同步"
        f"（重跑 python main.py --dry-run --yes 或手動同步）；漂移行：{missing}"
    )
