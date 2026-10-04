"""
抓「被收藏股票」的深度資料：月營收 12 個月趨勢 + 三大法人買賣超，存進 Firestore deep_data/{code}。

為什麼只抓被收藏的股票：FinMind 免費額度約 600 次/天，每支股票要 2 次請求，
全市場 1979 支要 3958 次，遠超額度。收藏清單通常只有數十支，才撐得起這種深度抓取。
執行：python deep_fetcher.py
"""
import os
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from firebase_app import db

load_dotenv(Path(__file__).parent.parent.parent / ".env")

FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# 每支股票 2 次請求，150 支共 300 次，剩下的額度留給 data_fetcher 輪抓全市場月營收
MAX_STOCKS = 150
INSTITUTIONAL_DAYS = 5      # 法人買賣超統計最近幾個交易日
REVENUE_MONTHS = 12         # 營收年增率要呈現幾個月的趨勢

# FinMind 的法人分類代號 → 歸進哪一類（buy/sell 單位是「股」，要 ÷1000 換算成張）
INVESTOR_GROUPS = {
    "Foreign_Investor":    "foreign",
    "Foreign_Dealer_Self": "foreign",
    "Investment_Trust":    "trust",
    "Dealer_self":         "dealer",
    "Dealer_Hedging":      "dealer",
}


# 這支程式用掉的 FinMind 請求數。跑完會寫進 Firestore，
# data_fetcher 讀了才知道全市場輪抓還剩多少額度可用（兩支程式共用每日 600 次）。
_requests_made = 0


def _finmind(dataset: str, code: str, start: str) -> list[dict]:
    """呼叫 FinMind，失敗或額度用盡回傳空清單（由呼叫端判斷要不要跳過）。"""
    global _requests_made
    _requests_made += 1
    try:
        resp = requests.get(
            FINMIND_API,
            params={"dataset": dataset, "data_id": code, "start_date": start, "token": FINMIND_TOKEN},
            timeout=20,
        )
        return resp.json().get("data", []) or []
    except Exception:
        return []


def fetch_revenue_trend(code: str) -> list[dict]:
    """
    回傳近 REVENUE_MONTHS 個月的營收年增率（新到舊）。
    FinMind 只給原始營收金額，年增率要自己跟去年同月比，所以要抓滿兩年多的資料。
    """
    start = (datetime.now() - timedelta(days=800)).strftime("%Y-%m-%d")
    data = _finmind("TaiwanStockMonthRevenue", code, start)
    if not data:
        return []

    by_ym = {(d["revenue_year"], d["revenue_month"]): d["revenue"] for d in data}
    months = sorted(by_ym.keys(), reverse=True)[:REVENUE_MONTHS]

    trend = []
    for year, month in months:
        current = by_ym[(year, month)]
        last_year = by_ym.get((year - 1, month))
        if not last_year:
            continue
        growth = (current - last_year) / last_year * 100
        trend.append({"ym": f"{year}-{month:02d}", "growth": round(growth, 2)})
    return trend


def fetch_institutional(code: str) -> dict:
    """回傳近 INSTITUTIONAL_DAYS 個交易日的三大法人淨買賣超（單位：張，負數代表賣超）。"""
    start = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    data = _finmind("TaiwanStockInstitutionalInvestorsBuySell", code, start)
    if not data:
        return {}

    # 先按日期彙總各法人類別的淨買賣
    by_date: dict[str, dict[str, float]] = {}
    for row in data:
        group = INVESTOR_GROUPS.get(row.get("name"))
        if not group:
            continue
        day = by_date.setdefault(row["date"], {"foreign": 0.0, "trust": 0.0, "dealer": 0.0})
        day[group] += (row.get("buy", 0) - row.get("sell", 0)) / 1000

    recent_dates = sorted(by_date.keys(), reverse=True)[:INSTITUTIONAL_DAYS]
    if not recent_dates:
        return {}

    daily = [
        {"date": d, **{k: round(v) for k, v in by_date[d].items()}}
        for d in recent_dates
    ]
    return {
        "recent_days":  len(recent_dates),
        "foreign_net":  round(sum(by_date[d]["foreign"] for d in recent_dates)),
        "trust_net":    round(sum(by_date[d]["trust"] for d in recent_dates)),
        "dealer_net":   round(sum(by_date[d]["dealer"] for d in recent_dates)),
        "daily":        daily,
    }


def collect_target_codes() -> list[str]:
    """
    掃所有使用者的收藏，取聯集。被越多人收藏的排越前面，
    這樣就算收藏總數超過 MAX_STOCKS，被犧牲的也是最少人在意的股票。
    """
    counter: Counter[str] = Counter()
    for doc in db.collection("favorites").stream():
        for code in (doc.to_dict() or {}).get("codes", []):
            counter[code] += 1
    return [code for code, _ in counter.most_common(MAX_STOCKS)]


def run():
    codes = collect_target_codes()
    if not codes:
        print("目前沒有任何使用者收藏股票，不需要抓深度資料")
        return

    print(f"開始抓 {len(codes)} 支收藏股票的深度資料（預計 {len(codes) * 2} 次 FinMind 請求）")
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok = 0

    for i, code in enumerate(codes, 1):
        trend = fetch_revenue_trend(code)
        time.sleep(0.3)
        institutional = fetch_institutional(code)
        time.sleep(0.3)

        if not trend and not institutional:
            print(f"[{i}/{len(codes)}] {code} → 無資料（可能額度用盡），跳過")
            continue

        db.collection("deep_data").document(code).set({
            "updated_at":    updated_at,
            "revenue_trend": trend,
            "institutional": institutional,
        })
        ok += 1
        print(f"[{i}/{len(codes)}] {code} → 營收 {len(trend)} 個月、法人 {institutional.get('recent_days', 0)} 日")

    db.collection("meta").document("finmind_usage").set({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "used": _requests_made,
        "source": "deep_fetcher",
    })

    print(f"\n完成！{ok}/{len(codes)} 支成功寫入 Firestore deep_data")
    print(f"本次用掉 {_requests_made} 次 FinMind 請求，已記錄供 data_fetcher 計算剩餘額度")


if __name__ == "__main__":
    run()
