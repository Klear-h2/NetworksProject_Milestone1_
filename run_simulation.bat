@echo off
cd /d "%~dp0"
py -m gateway.app --simulate
pause
