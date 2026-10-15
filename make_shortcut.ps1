param(
  [Parameter(Mandatory=$true)][string]$Exe,
  [Parameter(Mandatory=$true)][string]$Lnk
)
if (-not (Test-Path -LiteralPath $Exe)) { Write-Output "MISSING_EXE $Exe"; exit 1 }
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($Lnk)
$sh.TargetPath = $Exe
$sh.WorkingDirectory = Split-Path -Parent $Exe
$sh.IconLocation = "$Exe,0"
$sh.Description = "KiCad one-click circuit generator"
$sh.WindowStyle = 1
$sh.Save()
Write-Output "OK $Lnk -> $Exe"
