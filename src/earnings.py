"""財報日三層快取管理：Tier1=本地registry、Tier2=info_data提取、Tier3=ticker.calendar補抓。"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yfinance as yf


EARNINGS_REGISTRY_FILE = ".cache/earnings_registry.json"
REGISTRY_TTL_DAYS = 30


def _load_registry(cache_path: str) -> dict:
    p = Path(cache_path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_registry(registry: dict, cache_path: str) -> None:
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cache_path).write_text(
        json.dumps(registry, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _is_registry_valid(entry: dict) -> bool:
    try:
        cached_at = datetime.fromisoformat(entry["cached_at"])
        return (datetime.now() - cached_at).days < REGISTRY_TTL_DAYS
    except Exception:
        return False


def _parse_earnings_timestamp(ts) -> date | None:
    """將 yfinance earningsDate 原始值轉為 date；list 取第一元素（最早預估日）。"""
    if ts is None:
        return None
    if isinstance(ts, (list, tuple)):
        if not ts:
            return None
        ts = ts[0]
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
        if isinstance(ts, str):
            return date.fromisoformat(ts[:10])
        if isinstance(ts, date):
            return ts
        if isinstance(ts, datetime):
            return ts.date()
    except Exception:
        pass
    return None


def _parse_earnings_calendar(cal) -> date | None:
    """從 ticker.calendar 回傳值中解析最近財報日。"""
    if cal is None:
        return None
    if isinstance(cal, dict):
        return _parse_earnings_timestamp(cal.get("Earnings Date"))
    if hasattr(cal, "empty"):
        if cal.empty:
            return None
        try:
            return _parse_earnings_timestamp(cal.iloc[0, 0])
        except Exception:
            return None
    return None


def fetch_earnings_dates(
    symbols: list[str],
    info_data: dict[str, dict],
    post_l1_symbols: list[str] | None = None,
    cache_path: str = EARNINGS_REGISTRY_FILE,
) -> dict[str, date | None]:
    """
    三層防禦財報日查詢。
    Tier 1 → 本地 registry（< 30 天視為有效）
    Tier 2 → info_data[sym]["earnings_date"] 提取
    Tier 3 → 僅對 post_l1_symbols 中仍缺資料的個股呼叫 ticker.calendar
    回傳 {symbol: next_earnings_date | None}，None = 無已知財報，視為通過防禦牆。
    """
    registry = _load_registry(cache_path)
    result: dict[str, date | None] = {}
    tier3_candidates: list[str] = []
    now_iso = datetime.now().isoformat()

    for sym in symbols:
        entry = registry.get(sym)
        if entry and _is_registry_valid(entry):
            tier = entry.get("tier", 3)
            nd = entry.get("next_earnings")
            # tier=3 null = 已確認無財報，接受；tier=2 null = .info 未填充，允許 Tier 3 升級
            if tier == 3 or nd is not None:
                result[sym] = date.fromisoformat(nd) if nd else None
                continue

        # Tier 2: 從 info_data 提取
        raw_ts = (info_data.get(sym) or {}).get("earnings_date")
        if raw_ts is not None:
            ed = _parse_earnings_timestamp(raw_ts)
            result[sym] = ed
            registry[sym] = {
                "next_earnings": ed.isoformat() if ed else None,
                "cached_at": now_iso,
                "tier": 2,
            }
            if ed is None:
                tier3_candidates.append(sym)
            continue

        # 無 Tier 2 資料
        tier3_candidates.append(sym)
        result[sym] = None

    # Tier 3：僅對 post_l1_symbols 中仍缺失的個股觸發
    if post_l1_symbols is not None:
        tier3_set = set(tier3_candidates)
        tier3_targets = [s for s in post_l1_symbols if s in tier3_set]
        if tier3_targets:
            print(f"[earnings] Tier 3 精準補抓 {len(tier3_targets)} 支個股財報日...")
            for i, sym in enumerate(tier3_targets):
                try:
                    cal = yf.Ticker(sym).calendar
                    ed = _parse_earnings_calendar(cal)
                    result[sym] = ed
                    registry[sym] = {
                        "next_earnings": ed.isoformat() if ed else None,
                        "cached_at": now_iso,
                        "tier": 3,
                    }
                except Exception:
                    registry[sym] = {
                        "next_earnings": None,
                        "cached_at": now_iso,
                        "tier": 3,
                    }
                if (i + 1) % 20 == 0:
                    time.sleep(0.5)

    _save_registry(registry, cache_path)
    return result
