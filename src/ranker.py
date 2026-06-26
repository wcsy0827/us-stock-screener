"""L3 AI 排序：用 DeepSeek 對 L2 候選股做橫向比較，輸出 Top N。"""

from __future__ import annotations

import json
import os
import re
import time

import pandas as pd
from openai import OpenAI

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
MAX_CANDIDATES_TO_AI = 40   # 最多送給 AI 的候選股數量（已按 L2 分排序，取前 N）
MAX_RETRIES = 3


# ── 指標計算（純 pandas） ────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> float:
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = (100 - 100 / (1 + rs)).dropna()
    return float(rsi.iloc[-1]) if not rsi.empty else float("nan")


def _macd_hist(series: pd.Series) -> float:
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = (macd - signal).dropna()
    return float(hist.iloc[-1]) if not hist.empty else float("nan")


def compute_indicators(sym: str, df: pd.DataFrame) -> dict:
    """從 OHLCV DataFrame 計算送給 AI 的原始指標數值。"""
    close = df["Close"].dropna()
    volume = df["Volume"].dropna()

    price_now = float(close.iloc[-1])
    price_prev = float(close.iloc[-2]) if len(close) >= 2 else price_now
    price_20d = float(close.iloc[-20]) if len(close) >= 20 else price_now

    change_1d = (price_now - price_prev) / price_prev * 100 if price_prev else 0.0
    change_20d = (price_now - price_20d) / price_20d * 100 if price_20d else 0.0

    avg_vol_30 = float(volume.tail(30).mean()) if len(volume) >= 30 else float(volume.mean())
    today_vol = float(volume.iloc[-1])
    vol_ratio = today_vol / avg_vol_30 if avg_vol_30 else 0.0

    ema5 = _ema(close, 5) if len(close) >= 5 else float("nan")
    ema10 = _ema(close, 10) if len(close) >= 10 else float("nan")
    ema20 = _ema(close, 20) if len(close) >= 20 else float("nan")
    ema50 = _ema(close, 50) if len(close) >= 50 else float("nan")
    rsi = _rsi(close) if len(close) >= 14 else float("nan")
    macd_h = _macd_hist(close) if len(close) >= 35 else float("nan")

    # 突破策略：20 日整理區間
    high_20d = float(df["High"].tail(20).max()) if len(df) >= 20 else price_now
    low_20d  = float(df["Low"].tail(20).min())  if len(df) >= 20 else price_now
    dist_from_20d_high_pct = round((price_now - high_20d) / high_20d * 100, 2) if high_20d else 0.0

    # 反轉策略：RSI 方向 + Stochastic + EMA50 距離
    rsi_5d_ago = _rsi(close.iloc[:-5]) if len(close) >= 19 else float("nan")
    low14  = float(df["Low"].tail(14).min())  if len(df) >= 14 else price_now
    high14 = float(df["High"].tail(14).max()) if len(df) >= 14 else price_now
    stoch_k = round((price_now - low14) / (high14 - low14) * 100, 1) if high14 != low14 else 50.0
    dist_from_ema50_pct = round((price_now - ema50) / ema50 * 100, 2) if (ema50 == ema50 and ema50) else None

    def _fmt(v: float, decimals: int = 2) -> float | None:
        return None if (v != v) else round(v, decimals)  # NaN check

    return {
        "symbol": sym,
        "price": round(price_now, 2),
        "change_1d_pct": round(change_1d, 2),
        "change_20d_pct": round(change_20d, 2),
        "avg_volume_30d": int(avg_vol_30),
        "volume_ratio": round(vol_ratio, 2),
        "ema5": _fmt(ema5),
        "ema10": _fmt(ema10),
        "ema20": _fmt(ema20),
        "ema50": _fmt(ema50),
        "rsi": _fmt(rsi),
        "macd_hist": _fmt(macd_h, 4),
        # 突破策略
        "high_20d": round(high_20d, 2),
        "low_20d":  round(low_20d, 2),
        "dist_from_20d_high_pct": dist_from_20d_high_pct,
        # 反轉策略
        "rsi_5d_ago":          _fmt(rsi_5d_ago),
        "stoch_k":             stoch_k,
        "dist_from_ema50_pct": dist_from_ema50_pct,
    }


# ── Prompt 建構 ──────────────────────────────────────────────────

def _format_market_context(market_context: dict) -> str:
    """將 market_context 格式化為易讀的 JSON 字串附加在 prompt 中。"""
    if not market_context:
        return ""
    import json
    return "\n【市場背景數據】\n" + json.dumps(market_context, ensure_ascii=False, indent=2)


def _build_prompt(
    candidates: list[dict],
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
    market_context: dict | None = None,
) -> str:
    """整合 L2 評分 + 原始指標 + 基本面，組成結構化 prompt 內容。"""
    rows = []
    for c in candidates[:MAX_CANDIDATES_TO_AI]:
        sym = c["symbol"]
        df = price_data.get(sym)
        if df is None:
            continue
        indic = compute_indicators(sym, df)
        info = info_data.get(sym, {})
        fw_high = info.get("fifty_two_week_high")
        fw_low  = info.get("fifty_two_week_low")
        dist_52w = round((indic["price"] - fw_high) / fw_high * 100, 2) if fw_high else None
        rows.append({
            "ticker": sym,
            "name": info.get("name", sym),
            "sector": info.get("sector", "Unknown"),
            "price": indic["price"],
            "change_1d_pct": indic["change_1d_pct"],
            "change_20d_pct": indic["change_20d_pct"],
            "avg_volume_30d": indic["avg_volume_30d"],
            "volume_ratio": indic["volume_ratio"],
            "ema5": indic["ema5"],
            "ema10": indic["ema10"],
            "ema20": indic["ema20"],
            "ema50": indic["ema50"],
            "rsi": indic["rsi"],
            "macd_hist": indic["macd_hist"],
            "l2_score": c["total_score"],
            "l2_detail": {
                "ma": c["ma_score"],
                "rsi": c["rsi_score"],
                "macd": c["macd_score"],
                "volume": c["volume_score"],
                "momentum": c["momentum_score"],
            },
            # 動能策略補足
            "fifty_two_week_high":   fw_high,
            "fifty_two_week_low":    fw_low,
            "dist_from_52w_high_pct": dist_52w,
            # 突破策略補足
            "high_20d":              indic["high_20d"],
            "low_20d":               indic["low_20d"],
            "dist_from_20d_high_pct": indic["dist_from_20d_high_pct"],
            # 反轉策略補足
            "rsi_5d_ago":            indic["rsi_5d_ago"],
            "stoch_k":               indic["stoch_k"],
            "dist_from_ema50_pct":   indic["dist_from_ema50_pct"],
        })

    candidate_json = json.dumps(rows, ensure_ascii=False, indent=2)
    market_section = _format_market_context(market_context or {})
    return f"{market_section}\n\n【候選股數據】\n{candidate_json}"


SYSTEM_PROMPT = """你是一位經驗豐富的美股量化分析師，擅長技術面與動能選股。
你的任務是從 S&P 500 候選股中，根據技術指標、量價關係、趨勢動能，
挑選出你認為值得「買入」的標的（最多 5 支），並給出具體操作建議。
若符合買入條件的標的不足 5 支，只輸出實際符合條件的數量，不要勉強湊數。

選股原則：
1. 優先選擇均線多頭排列完整、RSI 健康（50~70）、MACD 向上的個股
2. 量能放大（volume_ratio >= 1.5）代表主力進場，加分
3. 避免過度集中於同一產業
4. 考量整體市場環境
5. 若候選股明顯超買（RSI > 80）或技術面混亂，排名靠後

市場背景判斷原則：
- 大盤（S&P 500）：若大盤 5 日跌幅 > 2% 或處於 EMA20 之下，整體提高警覺，傾向「觀望」
- VIX：若 VIX > 25，市場恐慌情緒高，操作建議應更保守；VIX < 15 代表市場樂觀，可積極
- 產業 ETF：個股所屬產業 ETF 若近 5 日下跌，即使個股技術面佳也需提示風險；ETF 強勢則加分
- 產業 ETF 的趨勢應反映在 reason 中，說明產業走勢對個股的支撐或壓制

選股標準：
- 只輸出你認為現在值得買進的股票（訊號明確、時機合適）
- 若技術面混亂、訊號不明確、RSI 過熱（> 75）、或大盤環境不佳，直接跳過該股，不列入輸出
- buy_zone、target、stop_loss 每支都要給出具體數值

【可選策略與操作邏輯】
請根據每支股票的技術指標特徵，選擇最合適的策略，並依對應邏輯計算操作建議：

動能策略（momentum）：
- 操作方式：趨勢延續，隨勢買進
- 買入區間：當前股價附近或小幅回調至 EMA10 附近
- 目標價：當前股價 +10%~20%
- 止損：跌破 EMA20 以下
- 持有週期：1～4週
- 適用條件：均線多頭排列（EMA5 > EMA10 > EMA20 > EMA50）、RSI 50~70、volume_ratio >= 1.5
- 關鍵指標：dist_from_52w_high_pct 在 -15% 以內代表接近歷史高點，動能強；超過 -30% 則動能偏弱

突破策略（breakout）：
- 操作方式：突破關鍵壓力位當日或次日買進
- 買入區間：突破點（high_20d）附近，前後 1%
- 目標價：當前股價 +10%~20%
- 止損：跌回 high_20d 下方 2%
- 持有週期：1～2週
- 適用條件：volume_ratio >= 2（量能明顯放大）、dist_from_20d_high_pct 在 -2%~+2%（股價在突破位附近）
- 關鍵指標：high_20d 為突破參考關卡，low_20d 為整理區間下緣；dist_from_20d_high_pct 接近 0 或為正值代表已突破或即將突破

反轉策略（oversold_reversal）：
- 操作方式：超賣後確認反彈訊號買進
- 買入區間：確認反彈當日或次日開盤，EMA50 附近支撐區
- 目標價：當前股價 +8%~15%
- 止損：跌破近期最低點（low_20d 下方）
- 持有週期：1～3週
- 適用條件：stoch_k < 25（超賣）、rsi_5d_ago < rsi（RSI 從低位回升中）、dist_from_ema50_pct 在 -10%~0%（靠近 EMA50 支撐）
- 關鍵指標：macd_hist 由負轉正為底背離確認訊號；rsi_5d_ago 與 rsi 的差值為正代表 RSI 回升中

請以如下 JSON 格式輸出（根節點為物件，陣列放在 "selections" key 中），不要其他說明文字：
{"selections": [ {...}, {...}, ... ]}

每個元素包含：
- rank: 排名（整數，從 1 開始）
- ticker: 股票代號
- reason: 繁體中文選股理由，聚焦技術面優勢與策略依據（50字以內）
- risk: 繁體中文風險提示（50字以內）
- confidence: 信心分數（整數 1~10）
- buy_zone: 建議買入價格區間，格式如 "$185～$188"
- target: 目標價，格式如 "$210"
- stop_loss: 止損價，格式如 "$180"
- hold_period: 建議持有週期，如 "1～2週"
- strategy: 套用的選股策略，只能是「動能策略」、「突破策略」、「反轉策略」三者之一
- strategy_reason: 繁體中文，說明選擇此策略的具體依據，需引用指標數值（例如：RSI=62、EMA5>EMA10>EMA20）（50字以內）
- confidence_reason: 繁體中文，說明信心分數給分原因，需具體說明加分或扣分的主因（例如：成交量放大、大盤偏弱）（50字以內）"""


# ── DeepSeek API 呼叫 ────────────────────────────────────────────

def _call_deepseek(user_content: str) -> list[dict]:
    """呼叫 DeepSeek API，回傳解析後的排名列表。"""
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"以下是候選股數據，請從中選出值得買進的標的（最多 5 支）：\n\n{user_content}"},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=6000,
            )
            raw = resp.choices[0].message.content.strip()
            finish_reason = resp.choices[0].finish_reason
            print(f"[ranker] API 回傳 {len(raw)} 字元，finish_reason={finish_reason}")
            if finish_reason == "length":
                print("[ranker] 警告：回應因 max_tokens 截斷，考慮再調高 max_tokens")

            # 解析 JSON：AI 可能回傳 {"selections": [...]} 或直接 [...]
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
            for v in parsed.values():
                if isinstance(v, list):
                    print(f"[ranker] 解析成功，取得 {len(v)} 筆結果")
                    return v
            return []

        except json.JSONDecodeError as e:
            print(f"[ranker] JSON 解析失敗（第{attempt}次）：{e}")
            # 嘗試用 regex 提取陣列
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
        except Exception as e:
            print(f"[ranker] API 呼叫失敗（第{attempt}次）：{e}")

        if attempt < MAX_RETRIES:
            time.sleep(5 * attempt)

    return []


# ── 主函式 ───────────────────────────────────────────────────────

def _enrich_fallback(
    candidates: list[dict],
    info_data: dict[str, dict],
    price_data: dict[str, pd.DataFrame],
) -> list[dict]:
    """為 fallback（不呼叫 AI）的候選股補充 name/sector/price_data 欄位。"""
    result = []
    for i, c in enumerate(candidates):
        sym = c["symbol"]
        info = info_data.get(sym, {})
        result.append({
            **c,
            "rank": i + 1,
            "name": info.get("name", sym),
            "sector": info.get("sector", "Unknown"),
            "reason": "L2 技術指標評分排名",
            "risk": "請手動確認各項指標",
            "confidence": 5,
            "buy_zone": "-",
            "target": "-",
            "stop_loss": "-",
            "hold_period": "-",
            "strategy": "-",
            "strategy_reason": "",
            "confidence_reason": "",
            "_price_data": price_data.get(sym),
        })
    return result


def rank_candidates(
    candidates: list[dict],
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
    top_n: int = 10,
    market_context: dict | None = None,
) -> list[dict]:
    """
    接收 L2 候選股，呼叫 DeepSeek AI 排序，回傳 Top N 結果。
    每個結果含原始 L2 資料 + AI 排名/理由/信心分數。
    """
    if not candidates:
        print("[ranker] 無候選股，跳過 AI 排序")
        return []

    if not DEEPSEEK_API_KEY:
        print("[ranker] 未設定 DEEPSEEK_API_KEY，跳過 AI 排序，改用 L2 分數直接輸出 Top N")
        return _enrich_fallback(candidates[:top_n], info_data, price_data)

    market_context = market_context or {}

    print(f"[ranker] 送出 {min(len(candidates), MAX_CANDIDATES_TO_AI)} 支候選股給 DeepSeek AI...")
    prompt_content = _build_prompt(candidates, price_data, info_data, market_context)

    ranked_raw = _call_deepseek(prompt_content)
    if not ranked_raw:
        print("[ranker] AI 排序失敗，改用 L2 分數直接輸出 Top N")
        return _enrich_fallback(candidates[:top_n], info_data, price_data)

    # 建立 L2 資料查詢表
    l2_map = {c["symbol"]: c for c in candidates}

    ranked: list[dict] = []
    for item in ranked_raw:
        ticker = str(item.get("ticker", "")).strip().upper()
        l2 = l2_map.get(ticker, {})
        ranked.append({
            "rank": int(item.get("rank", len(ranked) + 1)),
            "symbol": ticker,
            "name": info_data.get(ticker, {}).get("name", ticker),
            "sector": info_data.get(ticker, {}).get("sector", "Unknown"),
            "price": l2.get("price", 0.0),
            "total_score": l2.get("total_score", 0.0),
            "reason": str(item.get("reason", "")),
            "risk": str(item.get("risk", "")),
            "confidence": int(item.get("confidence", 5)),
            "buy_zone": str(item.get("buy_zone", "-")),
            "target": str(item.get("target", "-")),
            "stop_loss": str(item.get("stop_loss", "-")),
            "hold_period": str(item.get("hold_period", "-")),
            "strategy": str(item.get("strategy", "-")),
            "strategy_reason": str(item.get("strategy_reason", "")),
            "confidence_reason": str(item.get("confidence_reason", "")),
            "_price_data": price_data.get(ticker),
        })

    ranked.sort(key=lambda x: x["rank"])
    result = ranked[:top_n]
    print(f"[ranker] AI 排序完成，回傳 Top {len(result)}")
    return result
