#!/usr/bin/env pwsh
<#
.SYNOPSIS
    VidFactory dev deployment/management script (mirrors LSMFAPI/scripts/LSMF-dev.ps1).

.DESCRIPTION
    Manages the VidFactory dev stack on the remote Fedora Docker host over SSH.
    SSH alias   : xpsex   (HostName/User/IdentityFile resolved from ~/.ssh/config)
    Remote path : /opt/VidFactory
    Dev URL     : https://vf-dev.lg4.ch

.EXAMPLE
    .\scripts\VF-dev.ps1 setup     # verify SSH + prepare remote dir
    .\scripts\VF-dev.ps1 deploy    # sync code + docker compose up --build -d
    .\scripts\VF-dev.ps1 logs      # tail logs
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "sync", "deploy", "up", "down", "restart", "logs", "status", "shell", "exec", "help")]
    [string]$Command = "help",

    [Parameter(Position = 1)]
    [string]$Arg = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SSH_TARGET  = "xpsex"
$REMOTE_DIR  = "/opt/VidFactory"
$COMPOSE_CMD = "docker compose --project-name vidfactory-dev -f docker-compose.yml -f docker-compose.dev.yml"

$SYNC_EXCLUDES = @(
    ".git", ".venv", "venv", "__pycache__", "*.pyc",
    "*.db", "*.db-wal", "*.db-shm", "data",
    ".ruff_cache", ".mypy_cache", ".pytest_cache", ".vscode", ".ai"
)

function Write-Header([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Invoke-SSH([string]$remoteCmd, [switch]$Interactive) {
    if ($Interactive) {
        ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -t "$SSH_TARGET" $remoteCmd
    } else {
        ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=4 "$SSH_TARGET" $remoteCmd
    }
    if ($LASTEXITCODE -ne 0) { Write-Error "SSH command failed (exit $LASTEXITCODE): $remoteCmd" }
}

function Test-SSHConnectivity {
    Write-Header "Testing SSH connectivity to $SSH_TARGET"
    ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 -o BatchMode=yes "$SSH_TARGET" "echo ok" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Cannot connect. Ensure 'Host xpsex' is in ~/.ssh/config and the key is authorised." -ForegroundColor Yellow
        return $false
    }
    Write-Host "  SSH OK" -ForegroundColor Green
    return $true
}

function Sync-Files {
    Write-Header "Syncing project files -> ${SSH_TARGET}:${REMOTE_DIR}"
    $localDir = (Get-Location).Path
    $excludeArgs = ($SYNC_EXCLUDES | ForEach-Object { "--exclude=./$_" })
    $tmpTar = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.tar.gz'
    try {
        $tarArgs = @("-czf", $tmpTar) + $excludeArgs + @("-C", $localDir, ".")
        & tar @tarArgs
        if ($LASTEXITCODE -ne 0) { Write-Error "tar failed creating archive" }
        Write-Host "  Uploading and extracting..." -ForegroundColor Gray
        $proc = Start-Process -FilePath "ssh" `
            -ArgumentList @(
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4", "$SSH_TARGET",
                "sudo mkdir -p $REMOTE_DIR && sudo chown `$USER:`$USER $REMOTE_DIR && /usr/bin/tar xzf - -C $REMOTE_DIR"
            ) `
            -RedirectStandardInput $tmpTar -NoNewWindow -Wait -PassThru
        if ($proc.ExitCode -ne 0) { Write-Error "SSH stream-extract failed (exit $($proc.ExitCode))" }
    } finally {
        Remove-Item $tmpTar -ErrorAction SilentlyContinue
    }
    Write-Host "  Done." -ForegroundColor Green
}

function Test-RemoteConfig {
    ssh -o ServerAliveInterval=15 "$SSH_TARGET" "test -f $REMOTE_DIR/config.yml && echo ok" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "  config.yml not found on the remote host (docker would create it as a directory)." -ForegroundColor Red
        Write-Host "  Fix:" -ForegroundColor White
        Write-Host "    ssh $SSH_TARGET `"rm -rf $REMOTE_DIR/config.yml`"" -ForegroundColor White
        Write-Host "    scp config.yml ${SSH_TARGET}:${REMOTE_DIR}/config.yml" -ForegroundColor White
        return $false
    }
    return $true
}

function Invoke-Setup {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Preparing remote host"
    Invoke-SSH "sudo mkdir -p $REMOTE_DIR && sudo chown `$USER:`$USER $REMOTE_DIR"
    Invoke-SSH "docker --version && docker compose version"
    Write-Host ""
    Write-Host "Setup complete. Copy config and deploy:" -ForegroundColor Green
    Write-Host "  scp config.yml ${SSH_TARGET}:${REMOTE_DIR}/config.yml" -ForegroundColor White
    Write-Host "  .\scripts\VF-dev.ps1 deploy" -ForegroundColor White
}

function Invoke-Deploy {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    if (-not (Test-RemoteConfig)) { exit 1 }
    Sync-Files
    Write-Header "Building and starting DEV services on $SSH_TARGET"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD up --build -d"
    Write-Host ""
    Write-Host "Started: https://vf-dev.lg4.ch  (docs: /docs)" -ForegroundColor Green
}

function Invoke-Sync   { if (-not (Test-SSHConnectivity)) { exit 1 }; Sync-Files; Write-Host "Synced. Apply: .\scripts\VF-dev.ps1 restart" -ForegroundColor Gray }
function Invoke-Up      { if (-not (Test-SSHConnectivity)) { exit 1 }; Write-Header "Starting"; Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD up -d" }
function Invoke-Down    { if (-not (Test-SSHConnectivity)) { exit 1 }; Write-Header "Stopping"; Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD down" }
function Invoke-Restart { if (-not (Test-SSHConnectivity)) { exit 1 }; Write-Header "Restarting"; Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD restart" }
function Invoke-Logs([string]$service = "") {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    $svcArg = if ($service) { " $service" } else { "" }
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD logs -f --tail=100$svcArg" -Interactive
}
function Invoke-Status  { if (-not (Test-SSHConnectivity)) { exit 1 }; Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD ps" }
function Invoke-Shell   { if (-not (Test-SSHConnectivity)) { exit 1 }; ssh -t "$SSH_TARGET" "bash -l" }
function Invoke-Exec    { if (-not (Test-SSHConnectivity)) { exit 1 }; Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD exec vidfactory bash" -Interactive }

function Show-Help {
    Write-Host @"

VidFactory Dev Management
  SSH alias : $SSH_TARGET  (resolved via ~/.ssh/config)
  Remote dir: $REMOTE_DIR
  Dev URL   : https://vf-dev.lg4.ch

Commands: setup | sync | deploy | up | down | restart | logs [svc] | status | shell | exec
"@ -ForegroundColor White
}

Push-Location $PSScriptRoot\..
switch ($Command) {
    "setup"   { Invoke-Setup }
    "sync"    { Invoke-Sync }
    "deploy"  { Invoke-Deploy }
    "up"      { Invoke-Up }
    "down"    { Invoke-Down }
    "restart" { Invoke-Restart }
    "logs"    { Invoke-Logs $Arg }
    "status"  { Invoke-Status }
    "shell"   { Invoke-Shell }
    "exec"    { Invoke-Exec }
    default   { Show-Help }
}
Pop-Location
