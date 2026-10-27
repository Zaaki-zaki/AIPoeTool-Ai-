# Tworzy skrót "AIPoeTool" na pulpicie: uruchamia aplikację w oknie, bez konsoli.
$repo = Split-Path -Parent $PSScriptRoot
$uv = (Get-Command uv).Source
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\AIPoeTool.lnk")
$lnk.TargetPath = $uv
$lnk.Arguments = "run pythonw -m app.desktop"
$lnk.WorkingDirectory = $repo
$lnk.WindowStyle = 7
$lnk.Description = "AIPoeTool - doradca do Path of Exile"
$lnk.Save()
Write-Host "Skrót utworzony na pulpicie."
