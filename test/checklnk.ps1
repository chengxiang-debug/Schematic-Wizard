$sh = New-Object -ComObject WScript.Shell
foreach ($n in @('AutoDraw.lnk')) {
  $p = Join-Path 'C:\Users\guome\Desktop' $n
  if (-not (Test-Path -LiteralPath $p)) { Write-Output ("MISSING " + $p); continue }
  $s = $sh.CreateShortcut($p)
  Write-Output ("NAME=" + $n)
  Write-Output ("TARGET=" + $s.TargetPath)
  Write-Output ("EXISTS=" + (Test-Path -LiteralPath $s.TargetPath))
  Write-Output ("WORKDIR=" + $s.WorkingDirectory)
}
