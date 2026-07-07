# Fix .py file association and add right-click menus
# Run as Administrator in PowerShell:
#   Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
#   .\fix_py_association.ps1

$ErrorActionPreference = "Stop"

# Check admin
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "ERROR: Please run PowerShell as Administrator" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

# Find Python
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
$pythonwExe = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
$pyInstaller = (Get-Command pyinstaller -ErrorAction SilentlyContinue).Source

if (-not $pythonExe) {
    Write-Host "ERROR: Python not found. Please install Python and add it to PATH." -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

Write-Host "Found Python: $pythonExe" -ForegroundColor Green

# Find VS Code
$vscodeExe = ""
$vscodePaths = @(
    "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe",
    "$env:PROGRAMFILES\Microsoft VS Code\Code.exe",
    "$env:PROGRAMFILES(x86)\Microsoft VS Code\Code.exe"
)
foreach ($path in $vscodePaths) {
    if (Test-Path $path) {
        $vscodeExe = $path
        break
    }
}

# Helper: write registry string value via .NET (avoids reg.exe quoting hell)
function Set-RegString {
    param([string]$KeyPath, [string]$ValueName, [string]$Value)
    [Microsoft.Win32.Registry]::SetValue($KeyPath, $ValueName, $Value, [Microsoft.Win32.RegistryValueKind]::String)
}

# 1. Associate .py
Set-RegString "HKEY_CLASSES_ROOT\.py" "" "Python.File"
Set-RegString "HKEY_CLASSES_ROOT\.py" "Content Type" "text/plain"
Set-RegString "HKEY_CLASSES_ROOT\.py" "PerceivedType" "text"

# 2. Define Python.File
Set-RegString "HKEY_CLASSES_ROOT\Python.File" "" "Python File"
Set-RegString "HKEY_CLASSES_ROOT\Python.File" "FriendlyTypeName" "Python File"
Set-RegString "HKEY_CLASSES_ROOT\Python.File\DefaultIcon" "" "$pythonExe,0"

# 3. Double-click to run
Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\open\command" "" ('"' + $pythonExe + '" "%1" %*')

# 4. Right-click: Run without window
Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\runnowindow" "" "Run with Python (no window)"
Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\runnowindow" "Icon" "$pythonExe,0"
Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\runnowindow\command" "" ('"' + $pythonwExe + '" "%1" %*')

# 5. Right-click: Build EXE
if ($pyInstaller) {
    Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\buildexe" "" "Build EXE"
    Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\buildexe" "Icon" "$pythonExe,0"
    # Note: %~dp1 = dir of %1, %~n1 = name without ext of %1
    $buildCmd = 'cmd.exe /k cd /d "%~dp1" && "' + $pyInstaller + '" --onefile --windowed --name "%~n1" "%1" && echo. && echo Build complete! Check the dist folder. && pause'
    Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\buildexe\command" "" $buildCmd
    Write-Host "Added right-click menu: Build EXE" -ForegroundColor Green
} else {
    Write-Host "WARNING: PyInstaller not found. Skipping 'Build EXE' menu." -ForegroundColor Yellow
    Write-Host "Install it with: pip install pyinstaller" -ForegroundColor Yellow
}

# 6. Right-click: Edit with VS Code
if ($vscodeExe -and (Test-Path $vscodeExe)) {
    Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\editwithvscode" "" "Edit with VS Code"
    Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\editwithvscode" "Icon" "$vscodeExe,0"
    Set-RegString "HKEY_CLASSES_ROOT\Python.File\shell\editwithvscode\command" "" ('"' + $vscodeExe + '" "%1"')
    Write-Host "Added right-click menu: Edit with VS Code" -ForegroundColor Green
}

# Refresh icon cache
ie4uinit.exe -show 2>$null

Write-Host "`nDone! .py files are now associated with Python." -ForegroundColor Green
Write-Host "Right-click a .py file to see: Run with Python / Build EXE / Edit with VS Code" -ForegroundColor Cyan
Read-Host "Press Enter to exit..."
