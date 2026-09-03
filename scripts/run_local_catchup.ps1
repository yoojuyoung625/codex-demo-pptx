$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$nodePath = @(
    (Join-Path $projectRoot "runtime\node.exe"),
    (Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "C:\Users\유주영\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

if (-not $nodePath) { throw "Node.js 실행 파일을 찾을 수 없습니다." }
Set-Location -LiteralPath $projectRoot
& $nodePath "server.mjs" "--run-catchup"
exit $LASTEXITCODE
