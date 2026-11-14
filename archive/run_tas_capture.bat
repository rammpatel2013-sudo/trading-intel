@echo off
REM ============================================================
REM  Capture the ConvexValue market-wide options tape (Phase 1)
REM  RUN THIS DURING MARKET HOURS: 9:30am - 4:00pm ET
REM  Double-click it, or run from a terminal. It auto-stops at 4pm.
REM  Output: data\tas\YYYY-MM-DD.csv  (open in Excel)
REM ============================================================
cd /d C:\Users\drmit\PycharmProjects\trading-intel
call .venv\Scripts\activate

REM Default keeps any trade worth >= $25,000 notional (price x size x 100).
REM For whales only, change to:  python scripts\tas_capture.py --min-premium 50000
python scripts\tas_capture.py

echo.
echo Capture finished. CSV is in data\tas\
pause
