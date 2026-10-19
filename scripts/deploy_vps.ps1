param([switch]$SkipPush)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $env:USERPROFILE '.ssh\macro_dashboard_deploy.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    throw 'Run scripts/setup_vps_deploy.ps1 once before deploying.'
}
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $config.key -PathType Leaf)) { throw 'Configured SSH key is missing.' }
if (-not $config.remote_dir.StartsWith('/') -or $config.remote_dir -match '[\s''"`$;|&]') {
    throw 'Invalid remote checkout path in deploy config.'
}
Push-Location $repo
try {
    $releasePaths = @('app', 'config', 'migrations', 'scripts', 'tests', 'news_pipeline', 'Dockerfile', 'docker-compose.prod.yml', 'pyproject.toml')
    & git diff --quiet -- $releasePaths
    if ($LASTEXITCODE -ne 0) { throw 'Tracked local files are uncommitted. Commit the release before deploying.' }
    & git diff --cached --quiet -- $releasePaths
    if ($LASTEXITCODE -ne 0) { throw 'Staged files are uncommitted. Commit the release before deploying.' }
    $newSource = & git ls-files --others --exclude-standard -- $releasePaths
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect untracked source files.' }
    if ($newSource) { throw 'Untracked source files would be omitted. Commit them before deploying.' }
    $revision = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not read local revision.' }
    if (-not $SkipPush) {
        & git push origin "HEAD:refs/heads/deploy-vps"
        if ($LASTEXITCODE -ne 0) { throw 'Git push failed; VPS was not changed.' }
    }
    $target = "$($config.user)@$($config.host)"
    $remoteScript = "/tmp/macro-dashboard-deploy-$revision.sh"
    & scp -i $config.key -o BatchMode=yes (Join-Path $PSScriptRoot 'deploy_vps_remote.sh') "${target}:$remoteScript"
    if ($LASTEXITCODE -ne 0) { throw 'Could not upload deploy helper; VPS was not changed.' }
    & ssh -i $config.key -o BatchMode=yes -o ConnectTimeout=10 $target "cd '$($config.remote_dir)' && bash '$remoteScript' '$revision'"
    if ($LASTEXITCODE -ne 0) { throw 'VPS deployment failed. Review the remote output and backup path before retrying.' }
    Write-Host "VPS deployed $revision"
}
finally { Pop-Location }
