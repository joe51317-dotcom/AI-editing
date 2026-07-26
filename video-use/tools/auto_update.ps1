[CmdletBinding()]
param(
    [string]$Branch = 'main',
    [switch]$NoPush
)

$ErrorActionPreference = 'Stop'
$VideoUseRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ParentRepo = Split-Path -Parent $VideoUseRoot
$UpstreamUrl = 'https://github.com/browser-use/video-use'
$LogDir = Join-Path $env:LOCALAPPDATA 'video-use'
$LogPath = Join-Path $LogDir 'update.log'
$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$TempWorktree = Join-Path $TempRoot ("video-use-auto-update-" + [Guid]::NewGuid().ToString('N'))

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
function Write-UpdateLog([string]$Message) {
    $Line = '{0:u} {1}' -f (Get-Date), $Message
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding UTF8
    Write-Host $Line
}
function Invoke-Git([string[]]$Arguments, [string]$WorkingDirectory = $ParentRepo) {
    & git -C $WorkingDirectory @Arguments
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

try {
    $RemoteLine = (& git ls-remote $UpstreamUrl "refs/heads/$Branch" | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $RemoteLine) { throw 'Could not resolve the upstream branch.' }
    $UpstreamSha = ($RemoteLine -split '\s+')[0]
    if ($UpstreamSha -notmatch '^[0-9a-f]{40}$') { throw 'Upstream returned an invalid commit.' }

    Invoke-Git @('fetch', 'origin', $Branch)
    $LastSplit = (& git -C $ParentRepo log "origin/$Branch" --format='%B%x00' |
        Select-String -Pattern 'git-subtree-split: ([0-9a-f]{40})' -AllMatches |
        ForEach-Object { $_.Matches } | Select-Object -First 1).Groups[1].Value
    if ($LastSplit -eq $UpstreamSha) {
        Write-UpdateLog "Already current at upstream $UpstreamSha."
        exit 0
    }

    Invoke-Git @('worktree', 'add', '--detach', $TempWorktree, "origin/$Branch")
    Write-UpdateLog "Reviewing upstream $UpstreamSha in isolated worktree."
    Invoke-Git @('subtree', 'pull', '--prefix=video-use', $UpstreamUrl, $UpstreamSha, '--squash') $TempWorktree

    $Python = Join-Path $VideoUseRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Stable test environment is missing: $Python"
    }
    & $Python -m pytest (Join-Path $TempWorktree 'video-use\tests') -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE" }

    $ProviderPattern = ('eleven' + 'labs|xi-' + 'api-key|api\.' + 'eleven' + 'labs\.io')
    $Forbidden = & rg --hidden -i $ProviderPattern (Join-Path $TempWorktree 'video-use')
    if ($LASTEXITCODE -eq 0) { throw "Paid-provider path reappeared:`n$($Forbidden -join [Environment]::NewLine)" }
    if ($LASTEXITCODE -notin 0, 1) { throw "provider scan failed with exit code $LASTEXITCODE" }

    if (-not $NoPush) {
        Invoke-Git @('push', 'origin', "HEAD:$Branch") $TempWorktree
        Write-UpdateLog "Updated $Branch to upstream $UpstreamSha after tests passed."
    } else {
        Write-UpdateLog "Dry run passed for upstream $UpstreamSha; push skipped."
    }
} catch {
    Write-UpdateLog "Update stopped safely: $($_.Exception.Message)"
    exit 1
} finally {
    if (Test-Path -LiteralPath $TempWorktree) {
        $Resolved = [IO.Path]::GetFullPath($TempWorktree)
        if ($Resolved.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase) -and
            ([IO.Path]::GetFileName($Resolved)).StartsWith('video-use-auto-update-')) {
            & git -C $ParentRepo worktree remove --force $Resolved 2>$null
        }
    }
}
