# Build TPAK_Packer.exe with PyInstaller.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install --upgrade pyinstaller | Out-Host
$icon = Join-Path $PSScriptRoot "icons\tpak.ico"
$args = @("--noconfirm", "--clean", "--onefile", "--windowed",
          "--name", "TPAK_Packer")
if (Test-Path $icon) { $args += @("--icon", $icon) }
$args += @("--add-data", "icons;icons")
$args += "pak_gui.py"
python -m PyInstaller @args
Write-Host "built: dist\TPAK_Packer.exe"
