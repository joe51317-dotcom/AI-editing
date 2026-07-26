[CmdletBinding()]
param(
    [switch]$ImportDaceHfToken,
    [string]$DaceRoot = 'E:\Claude Workspace\DACE-BRAIN'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $RepoRoot '.env'
$ExamplePath = Join-Path $RepoRoot '.env.example'
$DacePython = Join-Path $DaceRoot '.venv\Scripts\python.exe'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required and was not found on PATH.'
}
if (-not (Test-Path -LiteralPath $DacePython -PathType Leaf)) {
    throw "DACE Python runtime not found: $DacePython"
}

Push-Location $RepoRoot
try {
    uv sync --extra dev --python 3.11
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$Settings = [ordered]@{}
Get-Content -LiteralPath $ExamplePath -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^([^#][^=]*)=(.*)$') { $Settings[$Matches[1].Trim()] = $Matches[2] }
}
if (Test-Path -LiteralPath $EnvPath -PathType Leaf) {
    Get-Content -LiteralPath $EnvPath -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^([^#][^=]*)=(.*)$') { $Settings[$Matches[1].Trim()] = $Matches[2] }
    }
}

if ($ImportDaceHfToken) {
    $DaceEnv = Join-Path $DaceRoot '.env'
    if (-not (Test-Path -LiteralPath $DaceEnv -PathType Leaf)) {
        throw "DACE .env not found: $DaceEnv"
    }
    $TokenLine = Get-Content -LiteralPath $DaceEnv -Encoding UTF8 |
        Where-Object { $_ -match '^HF_TOKEN=.' } |
        Select-Object -First 1
    if (-not $TokenLine) { throw 'DACE .env does not contain a non-empty HF_TOKEN.' }
    $Settings['HF_TOKEN'] = $TokenLine.Substring($TokenLine.IndexOf('=') + 1)
}

$Settings['VIDEO_USE_ASR_PYTHON'] = $DacePython
$Lines = foreach ($Pair in $Settings.GetEnumerator()) { '{0}={1}' -f $Pair.Key, $Pair.Value }
[IO.File]::WriteAllLines($EnvPath, $Lines, [Text.UTF8Encoding]::new($false))

Write-Host "video-use environment ready: $RepoRoot\.venv"
Write-Host 'Local configuration written without displaying secret values.'
& (Join-Path $RepoRoot '.venv\Scripts\python.exe') (Join-Path $RepoRoot 'helpers\doctor.py')
exit $LASTEXITCODE
