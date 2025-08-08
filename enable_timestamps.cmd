@echo off

echo Administrator rights are required
echo enable tcp timestamps
pause
netsh interface tcp set global timestamps=enabled
pause