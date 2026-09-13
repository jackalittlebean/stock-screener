@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python src\backend\stock_list.py
python src\backend\data_fetcher.py
echo.
echo 資料更新完成！
