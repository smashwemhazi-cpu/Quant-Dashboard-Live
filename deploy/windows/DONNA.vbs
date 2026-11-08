' DONNA - opens DONNA's console with no black window at all. Double-click this file (keep it on your Desktop).
' First time only: a normal window asks for your SERVER password once (and whether to connect when Windows
' starts). After that nothing shows: the private link to the server runs hidden and reconnects by itself.
' It keeps its own up-to-date copy of DONNA-Console.bat in %LOCALAPPDATA%\DONNA (refreshed once a day).
'   DONNA.vbs          open the console
'   DONNA.vbs stop     stop the hidden link and stop it starting with Windows
Option Explicit
Const SRC = "https://raw.githubusercontent.com/smashwemhazi-cpu/Quant-Dashboard-Live/main/deploy/windows/DONNA-Console.bat"
Dim sh, fso, dir, bat, tmp, key, arg, fresh, here
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
sh.Environment("Process")("DONNA_VBS") = WScript.ScriptFullName

dir = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\DONNA"
If Not fso.FolderExists(dir) Then fso.CreateFolder dir
bat = dir & "\DONNA-Console.bat"
tmp = bat & ".download"
sh.Environment("Process")("DONNA_TMP") = tmp

fresh = False
If fso.FileExists(bat) Then fresh = (DateDiff("h", fso.GetFile(bat).DateLastModified, Now) < 24)
If Not fresh Then
  On Error Resume Next
  If fso.FileExists(tmp) Then fso.DeleteFile tmp, True
  sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -Uri '" & SRC & "' -OutFile $env:DONNA_TMP } catch { }""", 0, True
  On Error GoTo 0
End If

If fso.FileExists(tmp) Then
  If fso.GetFile(tmp).Size > 2000 Then
    If fso.FileExists(bat) Then fso.DeleteFile bat, True
    fso.MoveFile tmp, bat
  Else
    fso.DeleteFile tmp, True
  End If
End If

If Not fso.FileExists(bat) Then
  here = fso.GetParentFolderName(WScript.ScriptFullName) & "\DONNA-Console.bat"
  If fso.FileExists(here) Then
    fso.CopyFile here, bat
  Else
    MsgBox "DONNA could not download her console file. Check the internet connection and try again.", 48, "DONNA"
    WScript.Quit 1
  End If
End If

arg = "quiet"
If WScript.Arguments.Count = 1 Then arg = WScript.Arguments(0)
key = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.ssh\donna_console_ed25519"
If arg = "stop" Then
  sh.Run """" & bat & """ stop", 1, False
ElseIf fso.FileExists(key) Then
  sh.Run """" & bat & """ " & arg, 0, False      ' 0 = hidden: no window
ElseIf arg <> "boot" Then
  sh.Run """" & bat & """", 1, False             ' first time on this PC: a normal window for the password
End If
