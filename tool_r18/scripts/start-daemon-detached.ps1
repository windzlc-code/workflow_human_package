param(
  [switch]$Restart
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot ".runtime\automatic-script"
$LogDir = Join-Path $RuntimeDir "logs"
$LockFile = Join-Path $RuntimeDir "telegram_bot.lock"
$HeartbeatFile = Join-Path $RuntimeDir "daemon.heartbeat.json"
$LogFile = Join-Path $LogDir "daemon.log"
$ErrorLogFile = Join-Path $LogDir "daemon.error.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Stop-ExistingDaemon {
  $pids = @()
  if (Test-Path $LockFile) {
    $raw = (Get-Content $LockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($raw -match '^\d+$') { $pids += [int]$raw }
  }
  if (Test-Path $HeartbeatFile) {
    try {
      $heartbeat = Get-Content $HeartbeatFile -Raw | ConvertFrom-Json
      if ($heartbeat.pid) { $pids += [int]$heartbeat.pid }
    } catch {}
  }
  $pids = @($pids | Sort-Object -Unique)
  foreach ($pidValue in $pids) {
    $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($proc) {
      Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
      try { Wait-Process -Id $pidValue -Timeout 5 -ErrorAction SilentlyContinue } catch {}
      try {
        Add-Content -Path $LogFile -Value "$(Get-Date -Format o) stopped existing daemon pid=$pidValue" -ErrorAction SilentlyContinue
      } catch {}
    }
  }
  if ($pids.Count -gt 0) {
    Remove-Item $HeartbeatFile -Force -ErrorAction SilentlyContinue
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
  }
}

if ($Restart) {
  Stop-ExistingDaemon
  # Stop-ExistingDaemon 已删除 heartbeat/lock，直接启动新实例
} else {
  # 非 restart 模式：检查是否已有实例在运行
  $existingPid = $null
  if (Test-Path $HeartbeatFile) {
    try {
      $heartbeat = Get-Content $HeartbeatFile -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
      if ($heartbeat.pid -and (Get-Process -Id ([int]$heartbeat.pid) -ErrorAction SilentlyContinue)) {
        $existingPid = [int]$heartbeat.pid
      }
    } catch {}
  }
  if ($existingPid) {
    Write-Output "daemon already running pid=$existingPid"
    exit 0
  }
}

$nodeArgs = @("--import", "tsx", "src/daemon.ts")
if ([string]::IsNullOrWhiteSpace($env:TELEGRAM_PROXY_URL)) {
  $localTelegramProxy = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort 9974 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($localTelegramProxy) {
    $env:TELEGRAM_PROXY_URL = "http://127.0.0.1:9974"
  }
}
$proc = Start-Process -FilePath "node" -ArgumentList $nodeArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $LogFile -RedirectStandardError $ErrorLogFile -PassThru
Write-Output "started daemon pid=$($proc.Id) log=$LogFile errorLog=$ErrorLogFile"
