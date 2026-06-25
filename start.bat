@echo off
REM Launch the Household Budget app and open it in your browser.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: creating virtual environment...
  py -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)

start "" http://127.0.0.1:8765
.venv\Scripts\python -m app.main
