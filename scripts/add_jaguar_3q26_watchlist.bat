@echo off
REM Add the 13 Jaguar 3Q 2026 Premium Ideas to the research watchlist.
REM Run from repo root with the venv active:  scripts\add_jaguar_3q26_watchlist.bat
REM Writes to the shared NAS Postgres via add_watchlist.py (no image rebuild needed).

python scripts\add_watchlist.py AXON --rationale "Jaguar 3Q26 long: public-safety OS, $14.3B backlog, AI Era"
python scripts\add_watchlist.py CHRW --rationale "Jaguar 3Q26 long: freight-cycle upturn + AI margin leverage"
python scripts\add_watchlist.py CMPS --rationale "Jaguar 3Q26 long: COMP360 psilocybin, possible early-2027 FDA approval"
python scripts\add_watchlist.py RACE --rationale "Jaguar 3Q26 long: ~30pct pullback overdone, price/mix + F80 ramp"
python scripts\add_watchlist.py GTX  --rationale "Jaguar 3Q26 long: turbo->industrial/DC-cooling re-rating; bullish call flow"
python scripts\add_watchlist.py ISRG --rationale "Jaguar 3Q26 long: ~30pct drawdown on wrong narratives, $5B buyback"
python scripts\add_watchlist.py LPTH --rationale "Jaguar 3Q26 long: BlackDiamond Germanium substitute, defense ramp; call flow"
python scripts\add_watchlist.py LQDT --rationale "Jaguar 3Q26 long: Self-Service margin inflection >80pct GMV; counter-cyclical"
python scripts\add_watchlist.py MELI --rationale "Jaguar 3Q26 long: transitory Mercado Pago provisioning, Mexico bank license"
python scripts\add_watchlist.py NESR --rationale "Jaguar 3Q26 long: MENA OFS, Jafurah ramp, sub-5x 2027 EBITDA"
python scripts\add_watchlist.py SOLV --rationale "Jaguar 3Q26 long: Trian activist SOTP, HIS/Dental separation"
python scripts\add_watchlist.py SLB  --rationale "Jaguar 3Q26 long: deepwater renaissance, ChampionX synergies, Digital ARR $1B"
python scripts\add_watchlist.py TEM  --rationale "Jaguar 3Q26 long: first positive EBITDA FY26, MRD ramp"

echo.
echo Done. Verify with:  python scripts\add_watchlist.py --list
