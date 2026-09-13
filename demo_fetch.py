"""快速示範：只抓前 30 支股票"""
import sys
sys.path.insert(0, "src/backend")
import json
from pathlib import Path

# 只取前 30 支
stock_list_path = Path("data/stock_list.json")
with open(stock_list_path, encoding="utf-8") as f:
    stocks = json.load(f)

# 暫時縮小清單
Path("data/_stock_list_backup.json").write_bytes(stock_list_path.read_bytes())
with open(stock_list_path, "w", encoding="utf-8") as f:
    json.dump(stocks[:30], f, ensure_ascii=False)

print("只處理前 30 支股票做示範...")
from data_fetcher import run
run()

# 還原完整清單
Path("data/_stock_list_backup.json").replace(stock_list_path)
print("完整清單已還原。")
