[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{7,40}$')]
    [string]$Ref
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ParentRepo = Split-Path -Parent $RepoRoot
$SystemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$ReviewRoot = Join-Path $SystemTemp ("video-use-upstream-" + [Guid]::NewGuid().ToString('N'))
$CloneDir = Join-Path $ReviewRoot 'upstream-repo'
$UpstreamDir = Join-Path $ReviewRoot 'upstream-tracked'
$LocalDir = Join-Path $ReviewRoot 'local-tracked'
$LocalArchive = Join-Path $ReviewRoot 'local.tar'
$UpstreamArchive = Join-Path $ReviewRoot 'upstream.tar'

try {
    New-Item -ItemType Directory -Force -Path $ReviewRoot, $LocalDir, $UpstreamDir | Out-Null
    git clone --filter=blob:none --no-checkout https://github.com/browser-use/video-use $CloneDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed with exit code $LASTEXITCODE" }
    git -C $CloneDir archive --format=tar --output=$UpstreamArchive $Ref
    if ($LASTEXITCODE -ne 0) { throw "git archive failed with exit code $LASTEXITCODE" }
    tar -xf $UpstreamArchive -C $UpstreamDir
    if ($LASTEXITCODE -ne 0) { throw 'Could not unpack the upstream snapshot.' }

    foreach ($Required in @('LICENSE', 'SKILL.md')) {
        if (-not (Test-Path -LiteralPath (Join-Path $UpstreamDir $Required) -PathType Leaf)) {
            throw "Upstream ref is missing required file: $Required"
        }
    }
    if (-not (Select-String -LiteralPath (Join-Path $UpstreamDir 'LICENSE') -Pattern 'MIT License' -Quiet)) {
        throw 'Upstream LICENSE is not the expected MIT License.'
    }

    # Archive only committed files. This excludes .env, .venv, caches, and all
    # other untracked data, so the review can never display local secrets.
    git -C $ParentRepo archive --format=tar --output=$LocalArchive HEAD:video-use
    if ($LASTEXITCODE -ne 0) { throw 'Could not archive the committed local video-use tree.' }
    tar -xf $LocalArchive -C $LocalDir
    if ($LASTEXITCODE -ne 0) { throw 'Could not unpack the committed local snapshot.' }

    Write-Host "Downloaded upstream $Ref into a temporary review directory."
    Write-Host 'Diffing against committed files only; no local files will be changed.'
    git diff --no-index -- $LocalDir $UpstreamDir
    if ($LASTEXITCODE -notin 0, 1) { throw "git diff failed with exit code $LASTEXITCODE" }
    # `git diff --no-index` uses exit 1 to mean "differences found". That is
    # the expected success case for this review command, not a script failure.
    Set-Variable -Name LASTEXITCODE -Value 0 -Scope Global
} finally {
    if (Test-Path -LiteralPath $ReviewRoot) {
        $Resolved = [IO.Path]::GetFullPath($ReviewRoot)
        if (-not $Resolved.StartsWith($SystemTemp, [StringComparison]::OrdinalIgnoreCase) -or
            -not ([IO.Path]::GetFileName($Resolved)).StartsWith('video-use-upstream-')) {
            throw "Refusing to clean unexpected temporary path: $Resolved"
        }
        Get-ChildItem -LiteralPath $Resolved -Force -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Attributes = [IO.FileAttributes]::Normal }
        [IO.Directory]::Delete($Resolved, $true)
    }
}
