@echo off
:: check_skc_live.bat — Sunday Service Pre-Flight Health Check
:: Run this batch file before service to verify local server and public HTTPS tunnel.

echo ===================================================
echo   SKC Live Translation — Sunday Pre-Flight Health Check
echo ===================================================
echo.

echo [1/3] Checking Local Application (http://127.0.0.1:8080/live)...
curl.exe -s -o NUL -w "      Local HTTP Status:   %%{http_code}\n" http://127.0.0.1:8080/live

echo.
echo [2/3] Checking Cloudflare Windows Service (cloudflared)...
sc query cloudflared | findstr /I "STATE"

echo.
echo [3/3] Checking Public Domain (https://live.starkvillekoreanchurch.org/live)...
curl.exe -s -o NUL -w "      Public HTTPS Status: %%{http_code}\n" https://live.starkvillekoreanchurch.org/live

echo.
echo ===================================================
echo STATUS INTERPRETATION:
echo   Local 200  +  State RUNNING  +  Public 200  = PERFECT (Ready for Service)
echo   Local 200  +  Public Failure              = Check Cloudflare / Internet
echo   Local Fail                                = App not started (Run SKC_Live_Translation.exe)
echo ===================================================
echo.
pause
