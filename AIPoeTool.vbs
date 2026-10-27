' Dwuklik = uruchomienie aplikacji (bez konsoli). Przy pierwszym uruchomieniu tworzy skrót na pulpicie.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
repo = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = repo
lnkPath = sh.SpecialFolders("Desktop") & "\AIPoeTool.lnk"
If Not fso.FileExists(lnkPath) Then
  Set lnk = sh.CreateShortcut(lnkPath)
  lnk.TargetPath = WScript.ScriptFullName
  lnk.WorkingDirectory = repo
  lnk.Description = "AIPoeTool - doradca do Path of Exile"
  lnk.Save
End If
' uv run dociąga brakujące zależności (pierwszy start trwa dłużej), okno 0 = ukryte
sh.Run "cmd /c uv run pythonw -m app.desktop", 0, False
