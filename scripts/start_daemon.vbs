' start_daemon.vbs
'
' Silent Windows launcher for D.A.E.M.O.N. Place a shortcut to this file in
' your Startup folder ( Win+R -> shell:startup ) and the assistant will
' boot in the background every time you log in. Logs go to logs\daemon.log.
'
' This script discovers the project root from its own location, so you can
' move the whole folder freely.

Option Explicit

Dim shell, fso, scriptDir, projectRoot, pythonw, entry, logFile, cmd

Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

scriptDir   = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(scriptDir)
pythonw     = projectRoot & "\venv\Scripts\pythonw.exe"
entry       = projectRoot & "\quickstart.py"
logFile     = projectRoot & "\logs\daemon.log"

' Fall back to system pythonw if no venv is present
If Not fso.FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If

' Make sure logs/ exists
If Not fso.FolderExists(projectRoot & "\logs") Then
    fso.CreateFolder projectRoot & "\logs"
End If

cmd = """" & pythonw & """ """ & entry & """ >> """ & logFile & """ 2>&1"

' 0 = hidden window, False = don't wait
shell.Run "cmd /c " & cmd, 0, False
