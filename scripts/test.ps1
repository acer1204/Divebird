$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest -q $args
exit $LASTEXITCODE
