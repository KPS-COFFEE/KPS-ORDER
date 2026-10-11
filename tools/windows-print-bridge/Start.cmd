@echo off
setlocal
cd /d "%~dp0"
if not exist KpsPrintBridge.exe (
  "%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:exe /out:KpsPrintBridge.exe /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll /reference:System.Security.dll KpsPrintBridge.cs
  if errorlevel 1 (
    echo Could not build the bridge. Windows .NET Framework 4 is required.
    pause
    exit /b 1
  )
)
KpsPrintBridge.exe %*
pause
