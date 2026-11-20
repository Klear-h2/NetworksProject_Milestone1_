@echo off
cd /d "%~dp0"
REM Edit COM5 and 192.168.137.100 to match your board's COM port and ESP32 B IP.
py -m gateway.app --serial COM5 --inspection-host 192.168.137.100
pause
