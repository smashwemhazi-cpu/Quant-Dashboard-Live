@echo off
title DONNA Console - keep this window open
rem Double-click to open DONNA's face. A private SSH link to the server carries everything:
rem nothing is put on the public internet. It installs any waiting DONNA update first. Close this window to disconnect.
set SERVER=root@178.62.67.163
set PORT=8765
set URL=http://localhost:%PORT%/console
echo.
echo Opening a private link to DONNA...
echo Type your SERVER password when asked - nothing shows while you type, that is normal.
echo Keep this window open while you use the console. Close it to disconnect.
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 240; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -TimeoutSec 5; if ($r.StatusCode -eq 200) { try { Start-Process msedge.exe -ArgumentList '--app=%URL% --start-maximized' } catch { Start-Process '%URL%' }; exit } } catch { }; Start-Sleep 2 }"
ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L %PORT%:127.0.0.1:8080 %SERVER% "vertex1-update; systemctl is-active -q vertex1-donna || systemctl enable -q --now vertex1-donna; echo; echo DONNA console link is open - the console window appears by itself once DONNA is ready.; exec sleep infinity"
echo.
echo The link to DONNA closed. Double-click DONNA-Console again to reopen it.
pause
