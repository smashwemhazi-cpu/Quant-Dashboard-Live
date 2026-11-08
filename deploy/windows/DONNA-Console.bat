@echo off
setlocal
rem DONNA Console. Double-click to open DONNA's face. A private SSH link to the server carries everything:
rem nothing is put on the public internet.
rem First time only: it makes a key for this PC and asks for your SERVER password once. After that the link
rem runs hidden in the background, reconnects by itself, and no black window has to stay open.
rem The key can only open DONNA's console link on the server: no shell, no files (deploy/vertex1-console-key).
rem   DONNA.vbs                the no-window way to open her: double-click it instead of this file (it runs
rem                            this file hidden; first time only it shows this window for the password)
rem   DONNA-Console.bat        open the console (starts the hidden link if it isn't running)
rem   DONNA-Console.bat stop   stop the hidden link and stop it starting with Windows
set "SERVER=root@178.62.67.163"
set "PORT=8765"
set "URL=http://localhost:%PORT%/console"
set "KEY=%USERPROFILE%\.ssh\donna_console_ed25519"
set "DONNA_BAT=%~f0"
set "STARTUP_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DONNA link.lnk"
set "SSHOPTS=-o LogLevel=ERROR -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3"

if /i "%~1"=="link" goto link
if /i "%~1"=="boot" goto boot
if /i "%~1"=="stop" goto stop
if /i "%~1"=="quiet" goto quiet
if /i "%~1"=="fix" goto failed
title DONNA Console

call :linked && goto open
if not exist "%KEY%" goto setup

:startlink
call :launch_hidden
echo Connecting to DONNA...
for /l %%i in (1,1,30) do (
  call :linked && goto open
  ping -n 2 127.0.0.1 >nul
)
call :kill_link

:failed
title DONNA Console
echo.
echo DONNA's link did not come up by itself. Either the internet is down, or the server no longer knows this PC.
choice /c RP /n /m "Press R to set this PC up again (asks your server password once), or P to use your password just this time: "
if errorlevel 2 goto password
del /q "%KEY%" "%KEY%.pub" 2>nul
goto setup

:setup
echo.
echo One-time setup for this PC: after this, DONNA's console connects by itself and no window needs to stay open.
if not exist "%USERPROFILE%\.ssh" mkdir "%USERPROFILE%\.ssh"
ssh-keygen -q -t ed25519 -N "" -C donna-console -f "%KEY%" >nul
if not exist "%KEY%.pub" goto password
echo Type your SERVER password when asked - nothing shows while you type, that is normal.
echo If Windows asks "Are you sure you want to continue connecting", type yes and press Enter.
set /p PUBKEY=<"%KEY%.pub"
ssh -o LogLevel=ERROR %SERVER% "vertex1-console-key %PUBKEY%"
if errorlevel 1 (
  del /q "%KEY%" "%KEY%.pub" 2>nul
  echo.
  echo The server has not picked up this feature yet (it updates itself every 30 minutes^). Using your password this time.
  goto password
)
echo.
choice /c YN /n /m "Connect to DONNA automatically every time Windows starts? Press Y or N: "
if errorlevel 2 goto startlink
call :startup_shortcut
echo DONNA's link will now start by itself with Windows.
goto startlink

:open
echo DONNA is connected. Her window opens in a moment; you can close this one.
if not defined DONNA_VBS echo Tip: open her with DONNA.vbs instead and no black window shows at all.
start "" powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 240; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -TimeoutSec 5; if ($r.StatusCode -eq 200) { try { Start-Process msedge.exe -ArgumentList '--app=%URL% --start-maximized' } catch { Start-Process '%URL%' }; exit } } catch { }; Start-Sleep 2 }"
ping -n 3 127.0.0.1 >nul
exit /b 0

:quiet
rem Started hidden by DONNA.vbs: no window at all. Anything that needs SH (first-time setup, a link that won't
rem come up) reopens this file in a normal window.
if not exist "%KEY%" (
  start "DONNA Console" "%DONNA_BAT%"
  exit /b 0
)
if exist "%STARTUP_LNK%" if defined DONNA_VBS call :startup_shortcut
call :linked && goto quiet_open
call :launch_hidden
for /l %%i in (1,1,30) do (
  call :linked && goto quiet_open
  ping -n 2 127.0.0.1 >nul
)
call :kill_link
start "DONNA Console" "%DONNA_BAT%" fix
exit /b 0

:quiet_open
powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 240; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -TimeoutSec 5; if ($r.StatusCode -eq 200) { try { Start-Process msedge.exe -ArgumentList '--app=%URL% --start-maximized' } catch { Start-Process '%URL%' }; exit } } catch { }; Start-Sleep 2 }"
exit /b 0

:boot
rem Started by Windows at sign-in: hand over to the hidden link and get out of the way.
call :linked && exit /b 0
call :launch_hidden
exit /b 0

:link
rem The hidden link itself: keeps the tunnel up and reconnects after sleep, Wi-Fi drops or a server restart.
call :linked && exit /b 0
:again
ssh -i "%KEY%" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 %SSHOPTS% -T -L %PORT%:127.0.0.1:8080 %SERVER%
ping -n 16 127.0.0.1 >nul
goto again

:stop
call :kill_link
del /q "%STARTUP_LNK%" 2>nul
echo DONNA's background link is stopped and will not start with Windows. Double-click DONNA-Console to connect again.
ping -n 4 127.0.0.1 >nul
exit /b 0

:password
rem The original way: one visible window carries the link. Closing it disconnects.
title DONNA Console - keep this window open
echo.
echo Type your SERVER password when asked - nothing shows while you type, that is normal.
echo Keep this window open while you use the console. Close it to disconnect.
start "" powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 240; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -TimeoutSec 5; if ($r.StatusCode -eq 200) { try { Start-Process msedge.exe -ArgumentList '--app=%URL% --start-maximized' } catch { Start-Process '%URL%' }; exit } } catch { }; Start-Sleep 2 }"
ssh %SSHOPTS% -L %PORT%:127.0.0.1:8080 %SERVER% "vertex1-update; systemctl is-active -q vertex1-donna || systemctl enable -q --now vertex1-donna; echo; echo DONNA console link is open - the console window appears by itself once DONNA is ready.; exec sleep infinity"
echo.
echo The link to DONNA closed. Double-click DONNA-Console again to reopen it.
pause
exit /b 0

rem ---------------------------------------------------------------- helpers
:linked
rem "LISTENING" is translated on non-English Windows, so match the listening row by its addresses instead
netstat -an -p tcp | findstr /r /c:"127\.0\.0\.1:%PORT%  *0\.0\.0\.0:0 " >nul
exit /b %errorlevel%

:launch_hidden
powershell -NoProfile -WindowStyle Hidden -Command "$q = [char]34; Start-Process -WindowStyle Hidden -FilePath $env:ComSpec -ArgumentList ('/c ' + $q + $q + $env:DONNA_BAT + $q + ' link' + $q)"
exit /b 0

:startup_shortcut
rem Start the link with Windows. Through DONNA.vbs when there is one: no window at sign-in, not even minimised.
if defined DONNA_VBS (
  powershell -NoProfile -WindowStyle Hidden -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:STARTUP_LNK); $s.TargetPath = (Join-Path $env:SystemRoot 'System32\wscript.exe'); $s.Arguments = [char]34 + $env:DONNA_VBS + [char]34 + ' boot'; $s.WindowStyle = 7; $s.Save()"
) else (
  powershell -NoProfile -WindowStyle Hidden -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:STARTUP_LNK); $s.TargetPath = $env:DONNA_BAT; $s.Arguments = 'boot'; $s.WindowStyle = 7; $s.Save()"
)
exit /b 0

:kill_link
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'ssh.exe' -and $_.CommandLine -like '*donna_console_ed25519*') -or ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*DONNA-Console*link*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
exit /b 0
