@echo off
REM Launcher for scheduled chart reports (used by Windows Task Scheduler).
REM Generates a chart and sends it to Telegram. Does NOT need the web app running.
REM Usage:  run_report.cmd {goals|cashflow|networth|snapshot|all}
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app.reports %1
