param(
    [string]$KeySource,
    [string]$RemoteDir,
    [string]$HostName = '178.104.249.87',
    [string]$UserName = 'root'
)

$ErrorActionPreference = 'Stop'
$sshDir = Join-Path $env:USERPROFILE '.ssh'
$keyPath = Join-Path $sshDir 'macro_dashboard_deploy'
$configPath = Join-Path $sshDir 'macro_dashboard_deploy.json'
New-Item -ItemType Directory -Path $sshDir -Force | Out-Null

if (-not $KeySource) { $KeySource = Read-Host 'Path to your working VPS private key' }
if (-not $RemoteDir) { $RemoteDir = Read-Host 'Absolute path to the macro-dashboard checkout on the VPS' }
if (-not (Test-Path -LiteralPath $KeySource -PathType Leaf)) { throw 'Private key file not found.' }
if (-not $RemoteDir.StartsWith('/') -or $RemoteDir -match '[\s''"`$;|&]') {
    throw 'RemoteDir must be an absolute Linux path without spaces or shell characters.'
}

Copy-Item -LiteralPath $KeySource -Destination $keyPath -Force
& icacls $keyPath /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict private key permissions.' }
& ssh-keygen -y -f $keyPath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'The copied file is not a readable private key.' }

$config = @{ host = $HostName; user = $UserName; key = $keyPath; remote_dir = $RemoteDir }
$config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
& ssh -i $keyPath -o BatchMode=yes -o ConnectTimeout=10 "$UserName@$HostName" "test -f '$RemoteDir/docker-compose.prod.yml' && echo VPS-ready"
if ($LASTEXITCODE -ne 0) { throw 'SSH authentication or remote checkout check failed. The config was saved; correct access and rerun setup.' }
Write-Host 'Setup complete. Run scripts/deploy_vps.ps1 for future deployments.'
