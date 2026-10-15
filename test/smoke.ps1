$exe = 'D:\AutoDraw\AutoDraw-win32-x64\AutoDraw.exe'
$p = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 14
$alive = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
if ($alive) {
  Write-Output ("ALIVE pid=" + $p.Id + " title=[" + $alive.MainWindowTitle + "] handles=" + $alive.HandleCount)
  Stop-Process -Id $p.Id -Force
  Start-Sleep -Seconds 3
  $left = @(Get-Process -Name AutoDraw -ErrorAction SilentlyContinue).Count
  Write-Output ("STOPPED remaining=" + $left)
} else {
  Write-Output ("DEAD pid=" + $p.Id + " exit=" + $p.ExitCode)
}
