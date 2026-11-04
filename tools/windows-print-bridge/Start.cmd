@echo off
setlocal
cd /d "%~dp0"
rem Compile the downloaded source every time so updates replace an old executable.
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:exe /out:KpsPrintBridge.exe /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll /reference:System.Security.dll KpsPrintBridge.cs
if errorlevel 1 (
  echo Could not update the bridge. Close the previous bridge window and try again.
  echo Windows .NET Framework 4 is required.
  pause
  exit /b 1
)
KpsPrintBridge.exe %*
pause
