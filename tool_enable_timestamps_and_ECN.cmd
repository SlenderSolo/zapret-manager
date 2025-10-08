@echo off

echo Administrator rights are required
echo enable tcp timestamps and ECN
pause
netsh interface tcp set global timestamps=enabled
netsh int tcp set global ecn=enabled
pause
