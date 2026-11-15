$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
$taskPython = 'python'
if (Test-Path -LiteralPath '.venv/Scripts/python.exe') {
    $taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
}
& $taskPython -m app.cli @args
exit $LASTEXITCODE
