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
# 模型依序嘗試，前面的叫不動就換下一個。
# 實測（2026-09-20，各連續呼叫 4 次）：
#   gemini-flash-latest    0/4（429 配額用盡）  ← 原本用這個，熱門模型免費配額很緊
#   gemini-3.8-flash       0/4（429）
#   gemini-3.5-flash       3/4（偶發 503）
#   gemini-flash-lite-latest 4/4                ← 改用這個當主力
# 解讀任務只是把算好的數字翻成白話，不需要頂級推理能力，lite 版綽綽有餘。
GEMINI_MODELS = ("gemini-flash-lite-latest", "gemini-3.5-flash")
MAX_ATTEMPTS_PER_MODEL = 2
RETRY_BACKOFF_SECONDS = 2

DISCLAIMER = "本內容由 AI 根據既有數據自動生成，僅為資訊整理，非投資建議，請自行判斷風險。"

INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary":        {"type": "string"},
        "signal_reading": {"type": "string"},
        "context":        {"type": "string"},
        "watch_points":   {"type": "array", "items": {"type": "string"}},
        "data_gaps":      {"type": "string"},
    },
    "required": ["summary", "signal_reading", "context", "watch_points", "data_gaps"],
}


def _load_cached(cache_key: str) -> dict | None:
    doc = db.collection(CACHE_COLLECTION).document(cache_key).get()
    return doc.to_dict() if doc.exists else None


def _save_cached(cache_key: str, result: dict) -> None:
    db.collection(CACHE_COLLECTION).document(cache_key).set(result)


def _is_quota(err: Exception) -> bool:
    """配額用盡或被限流。重試同一個模型沒用（配額不會幾秒內恢復），要直接換模型。"""
    return getattr(err, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(err)


def _is_overloaded(err: Exception) -> bool:
    """伺服器端暫時過載，同一個模型稍等再試有機會成功。"""
    if getattr(err, "code", None) in (500, 502, 503, 504):
        return True
    return "UNAVAILABLE" in str(err)


def _generate_with_retry(client, prompt: str):
    """依序嘗試各模型：過載就退避重試同一個，配額用盡就直接換下一個。"""
    last_err = None
    for model in GEMINI_MODELS:
        for attempt in range(MAX_ATTEMPTS_PER_MODEL):
            try:
                return client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=INSIGHT_SCHEMA,
                    ),
                )
            except Exception as err:
                last_err = err
                if _is_overloaded(err) and attempt < MAX_ATTEMPTS_PER_MODEL - 1:
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                break  # 配額用盡或其他錯誤，換下一個模型

    if _is_quota(last_err):
        raise RuntimeError("AI 解讀的今日免費用量已用完，請明天再試")
    if _is_overloaded(last_err):
        raise RuntimeError("AI 服務目前忙碌中，請稍後再試一次")
    raise RuntimeError("AI 解讀產生失敗，請稍後再試")


def _build_prompt(stock: dict) -> str:
    def fmt(v):
        return "N/A" if v is None else v

    return f"""你是一位協助解讀股票數據的分析助手，服務對象是不具財經專業知識的一般使用者。

【最重要的要求】使用者的畫面上「已經看得到」下面所有數字了，所以把數字一個一個唸過一遍對他毫無價值。
你的任務是做這些數字「擺在一起」才看得出來的判讀：訊號之間是互相印證還是互相矛盾、
某個數字放在其他數字的脈絡下是否合理、哪些組合在投資判讀上有特殊意義。

【嚴格禁止】
- 禁止逐項複述數字（例如「RSI 是 38.9，代表偏弱」這種把畫面上的數字再講一次的句子）
- 禁止提及公司近況、新聞、訂單、法人動向、產業趨勢、競爭對手、未來展望——你沒有這些資料，
  講出來就是編造。只能用下面提供的數字，以及技術指標的通用判讀原則（例如 RSI 30 以下通稱超賣區）
- 禁止給買賣建議、目標價、進出場時機

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

- summary：一句話點出這支股票目前「最關鍵的一件事」。不是總結全部數字，是挑出最值得注意的那個重點。

- signal_reading：把技術面訊號與基本面訊號「合起來」判讀（3–4 句）。必須明確講出兩邊是互相印證
  （例如股價走弱、營收也在衰退，方向一致）還是互相矛盾（例如營收正成長但股價跌破所有均線，出現背離）。
  訊號矛盾時要說明這種背離在判讀上代表什麼、通常該從哪個角度理解。

- context：把數字放進脈絡（2–3 句）。例如：這個 PE 相對於它的 EPS 和成長率是偏貴還是合理；
  股價與各條均線的相對位置合起來反映什麼階段；RSI 距離超買／超賣區還有多少空間。
  重點是「相對於什麼」而不是「數字是多少」。

- watch_points：2–3 點「接下來什麼變化會推翻現在的判讀」。要具體可觀察，
  例如「若股價站回 20 日均線，目前的空方判讀就需要重新評估」，而不是空泛的「注意風險」。

- data_gaps：哪些欄位缺資料、因此哪部分判斷受限（1–2 句）。若資料齊全就說明判讀完整度良好。
  這欄的用意是誠實標示不確定性，不要粉飾。
"""


def get_stock_insight(stock: dict, data_updated_at: str) -> dict:
    """
    回傳該股票的 AI 解讀（含快取）。
    data_updated_at 用來組快取 key，stocks.json 更新後會自動重新產生解讀。
    """
    if not GEMINI_API_KEY or "請在此填入" in GEMINI_API_KEY:
        raise RuntimeError("尚未設定 GEMINI_API_KEY，請在 .env 填入後重啟後端")

    code = stock["code"]
    # 加版本號：prompt/輸出結構改版後，舊格式的快取會自動失效，不必手動清除
    cache_key = f"{code}_{data_updated_at}_v2"

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
