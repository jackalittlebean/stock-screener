"""
呼叫 Gemini API，針對單一股票的既有數據產生白話解讀。
只解讀既有股票數據，不查即時新聞、不做預測、不給買賣建議。
結果依「股號 + 資料更新日期」快取在 Firestore 的 ai_cache/{cache_key} 文件，同一份資料同一支股票不重複呼叫。
"""
import json
import os
import time
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types

from firebase_app import db

load_dotenv(Path(__file__).parent.parent.parent / ".env")

CACHE_COLLECTION = "ai_cache"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-flash-latest"
# Gemini 免費額度在尖峰時段常回 503 UNAVAILABLE，實測重試一次多半就成功，
# 因此對暫時性錯誤退避重試，而不是一次失敗就把技術錯誤丟給使用者看。
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 4)

DISCLAIMER = "本內容由 AI 根據既有數據自動生成，僅為資訊整理，非投資建議，請自行判斷風險。"

INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary":           {"type": "string"},
        "technical_note":    {"type": "string"},
        "fundamental_note":  {"type": "string"},
        "watch_points":      {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "technical_note", "fundamental_note", "watch_points"],
}


def _load_cached(cache_key: str) -> dict | None:
    doc = db.collection(CACHE_COLLECTION).document(cache_key).get()
    return doc.to_dict() if doc.exists else None


def _save_cached(cache_key: str, result: dict) -> None:
    db.collection(CACHE_COLLECTION).document(cache_key).set(result)


def _is_transient(err: Exception) -> bool:
    """判斷是不是 Google 端的暫時性狀況（過載、限流），這類重試有機會成功。"""
    code = getattr(err, "code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    text = str(err)
    return "UNAVAILABLE" in text or "RESOURCE_EXHAUSTED" in text


def _generate_with_retry(client, prompt: str):
    """呼叫 Gemini，遇到暫時性錯誤時退避重試。"""
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=INSIGHT_SCHEMA,
                ),
            )
        except Exception as err:
            last_err = err
            if attempt < MAX_ATTEMPTS - 1 and _is_transient(err):
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                continue
            break

    if _is_transient(last_err):
        raise RuntimeError("AI 服務目前忙碌中，請稍後再試一次") from last_err
    raise RuntimeError("AI 解讀產生失敗，請稍後再試") from last_err


def _build_prompt(stock: dict) -> str:
    def fmt(v):
        return "N/A" if v is None else v

    return f"""你是一位協助解讀股票數據的助手，服務對象是不具財經專業知識的一般使用者。
請只根據以下「既有數據」用白話文解讀，不要臆測公司未來走勢、不要給出買賣建議、不要引用數據以外的資訊（例如新聞、產業消息）。

股號：{stock.get('code')}
股名：{stock.get('name')}
股價：{fmt(stock.get('price'))}
5日均線：{fmt(stock.get('ma5'))}
20日均線：{fmt(stock.get('ma20'))}
60日均線：{fmt(stock.get('ma60'))}
RSI(14)：{fmt(stock.get('rsi'))}
MACD交叉狀態：{fmt(stock.get('macd_cross'))}（golden=黃金交叉, death=死亡交叉, 無資料代表無明顯交叉）
本益比 PE：{fmt(stock.get('pe'))}
EPS：{fmt(stock.get('eps'))}
月營收年增率：{fmt(stock.get('revenue_growth'))}%（N/A 代表無資料）

請用繁體中文回覆，各欄位要求：
- summary：一句話總結目前技術面與基本面狀況
- technical_note：均線、RSI、MACD 的白話解讀（2–3 句）
- fundamental_note：PE、EPS、營收年增率的白話解讀（2–3 句），若欄位為 N/A 要明確說明資料缺漏
- watch_points：1–3 點使用者應留意的重點，每點一句話
"""


def get_stock_insight(stock: dict, data_updated_at: str) -> dict:
    """
    回傳該股票的 AI 解讀（含快取）。
    data_updated_at 用來組快取 key，stocks.json 更新後會自動重新產生解讀。
    """
    if not GEMINI_API_KEY or "請在此填入" in GEMINI_API_KEY:
        raise RuntimeError("尚未設定 GEMINI_API_KEY，請在 .env 填入後重啟後端")

    code = stock["code"]
    cache_key = f"{code}_{data_updated_at}"

    cached = _load_cached(cache_key)
    if cached is not None:
        return cached

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = _generate_with_retry(client, _build_prompt(stock))
    result = json.loads(response.text)
    result["disclaimer"] = DISCLAIMER
    result["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    _save_cached(cache_key, result)
    return result
