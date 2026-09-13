"""只重新抓 FinMind 月營收並更新 stocks.json，跳過 yfinance"""
import sys, json, time
sys.path.insert(0, "src/backend")
from pathlib import Path
from data_fetcher import fetch_revenue_growth

DATA_FILE = Path("data/stocks.json")
with open(DATA_FILE, encoding="utf-8") as f:
    stocks = json.load(f)

total = len(stocks)
for i, s in enumerate(stocks, 1):
    code = s["code"]
    rg = fetch_revenue_growth(code)
    s["revenue_growth"] = rg
    if i % 50 == 0 or i == total:
        print(f"[{i}/{total}] 已更新...")
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(stocks, f, ensure_ascii=False)
    time.sleep(0.1)

print("完成！")
