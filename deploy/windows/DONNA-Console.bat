@echo off
title DONNA Console - keep this window open
rem Double-click to open DONNA's face. A private SSH link to the server carries everything:
rem nothing is put on the public internet. Close this window to disconnect.
set SERVER=root@178.62.67.163
set PORT=8765
set URL=http://localhost:%PORT%/console
echo.
echo Opening a private link to DONNA...
echo Type your SERVER password when asked - nothing shows while you type, that is normal.
echo Keep this window open while you use the console. Close it to disconnect.
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 180; $i++) { try { $c = New-Object Net.Sockets.TcpClient('127.0.0.1', %PORT%); $c.Close(); Start-Sleep 2; try { Start-Process msedge.exe -ArgumentList '--app=%URL%' } catch { Start-Process '%URL%' }; exit } catch { Start-Sleep 1 } }"
ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L %PORT%:127.0.0.1:8080 %SERVER% "systemctl is-active -q vertex1-donna || systemctl enable -q --now vertex1-donna; echo; echo DONNA console link is open - the console window will appear by itself.; exec sleep infinity"
echo.
echo The link to DONNA closed. Double-click DONNA-Console again to reopen it.
pause
