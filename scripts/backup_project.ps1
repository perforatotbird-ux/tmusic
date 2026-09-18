<#
.SYNOPSIS
  T-Music — local project backup to ZIP.

.DESCRIPTION
  Packs source, config, docs and git history.
  Excludes node_modules, caches, heavy runtime dirs.
  music.env is included (needed for restore). ZIP is encrypted if -ZipPassword is set
  and 7-Zip is installed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\backup_project.ps1
  powershell -ExecutionPolicy Bypass -File scripts\backup_project.ps1 -IncludeData -IncludeCache
  powershell -ExecutionPolicy Bypass -File scripts\backup_project.ps1 -OutDir K:\Backups -PgDump
#>
[CmdletBinding()]
param(
  [string]$ProjectRoot = "",
  [string]$OutDir = "",
  [string]$ZipPassword = "",
  [switch]$IncludeData,
  [switch]$IncludeCache,
  [switch]$IncludeDist,
  [bool]$IncludeGit = $true,
  [switch]$PgDump,
  [switch]$KeepStaging
)

$ErrorActionPreference = "Stop"

function Write-Info([string]$msg) { Write-Host "[i] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

if (-not $ProjectRoot) {
  $here = $null
  if ($PSScriptRoot) { $here = $PSScriptRoot }
  elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
  if ($here) {
    $ProjectRoot = (Resolve-Path (Join-Path $here "..")).Path
  } else {
    $ProjectRoot = (Get-Location).Path
  }
}

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
if (-not (Test-Path (Join-Path $ProjectRoot "backend"))) {
  throw "Not a T-Music project root: $ProjectRoot"
}

if (-not $OutDir) {
  $OutDir = Join-Path (Split-Path $ProjectRoot -Parent) "tmusic-backups"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$projectName = Split-Path $ProjectRoot -Leaf
$zipPath = Join-Path $OutDir "$projectName-$stamp.zip"
$staging = Join-Path ([System.IO.Path]::GetTempPath()) "tmusic-backup-$stamp"

if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Write-Info "Project:  $ProjectRoot"
Write-Info "Staging:  $staging"
Write-Info "Output:   $zipPath"

# --- Optional PostgreSQL dump -----------------------------------------------
if ($PgDump) {
  $musicEnv = Join-Path $ProjectRoot "music.env"
  $dbUrl = ""
  if (Test-Path $musicEnv) {
    foreach ($line in Get-Content $musicEnv) {
      if ($line -match '^\s*DATABASE_URL\s*=\s*(.+)\s*$') {
        $dbUrl = $Matches[1].Trim().Trim("'").Trim('"')
        break
      }
    }
  }
  if ($dbUrl -eq "") {
    Write-Warn "No DATABASE_URL in music.env - skip pg_dump"
  } elseif ($dbUrl -match '^postgresql://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(\w+)') {
    $pgUser = $Matches[1]
    $pgPass = $Matches[2]
    $pgHost = $Matches[3]
    $pgPort = $Matches[4]
    if (-not $pgPort) { $pgPort = "5432" }
    $pgDb = $Matches[5]
    $dumpDir = Join-Path $staging "pg_dump"
    New-Item -ItemType Directory -Force -Path $dumpDir | Out-Null
    $dumpFile = Join-Path $dumpDir "tgmusic-$stamp.dump"
    Write-Info "pg_dump $pgDb@$pgHost`:$pgPort -> $dumpFile"
    $oldPw = $env:PGPASSWORD
    try {
      $env:PGPASSWORD = $pgPass
      & pg_dump -h $pgHost -p $pgPort -U $pgUser -d $pgDb -Fc -f $dumpFile
      if ($LASTEXITCODE -ne 0) {
        Write-Warn "pg_dump exit code $LASTEXITCODE"
      } else {
        Write-Ok "PostgreSQL dump saved"
      }
    } catch {
      Write-Warn "pg_dump failed: $($_.Exception.Message)"
    } finally {
      $env:PGPASSWORD = $oldPw
    }
  } else {
    Write-Warn "DATABASE_URL not parsed - skip pg_dump"
  }
}

# --- Archive via tar (includes hidden .git; robocopy skips hidden dirs) -----
$excludeDirs = New-Object System.Collections.Generic.List[string]
foreach ($d in @("node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mimocode", ".workbuddy-ai", ".venv", "venv", ".idea", ".vscode")) {
  $excludeDirs.Add($d)
}
if (-not $IncludeData) { $excludeDirs.Add("data") }
if (-not $IncludeCache) { $excludeDirs.Add("cache") }
if (-not $IncludeDist) { $excludeDirs.Add("frontend/dist"); $excludeDirs.Add("frontend\dist") }
if (-not $IncludeGit) { $excludeDirs.Add(".git") }

Write-Info ("Archiving project (excluding: {0})" -f ($excludeDirs -join ", "))

$manifestLines = @(
  "T-Music local backup",
  "Created:  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
  "Source:   $ProjectRoot",
  "Includes: source, scripts, docs, music.env, obsidian, git=$IncludeGit",
  "Optional: data=$IncludeData cache=$IncludeCache dist=$IncludeDist pg_dump=$PgDump"
)
# Put manifest next to zip (not inside project tree)
$manifestPath = Join-Path $OutDir "$projectName-$stamp.manifest.txt"
Set-Content -Path $manifestPath -Value $manifestLines -Encoding UTF8

Write-Info "Creating ZIP with tar..."
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

$tarArgs = New-Object System.Collections.Generic.List[string]
$tarArgs.Add("-a")
$tarArgs.Add("-cf")
$tarArgs.Add($zipPath)
$tarArgs.Add("-C")
$tarArgs.Add($ProjectRoot)
foreach ($d in $excludeDirs) {
  if (-not $d) { continue }
  $norm = $d -replace "\\", "/"
  $tarArgs.Add("--exclude=./$norm")
  $tarArgs.Add("--exclude=$norm")
}
$tarArgs.Add(".")
& tar @tarArgs
if ($LASTEXITCODE -ne 0) { throw "tar failed with code $LASTEXITCODE" }

if (-not (Test-Path $zipPath)) { throw "ZIP was not created" }

# Password protection via 7-Zip when requested
if ($ZipPassword) {
  $sevenZip = @(
    "$env:ProgramFiles\7-Zip\7z.exe",
    "${env:ProgramFiles(x86)}\7-Zip\7z.exe",
    "$env:LOCALAPPDATA\Programs\7-Zip\7z.exe"
  ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  if ($sevenZip) {
    $encPath = "$zipPath.enc.zip"
    if (Test-Path $encPath) { Remove-Item -Force $encPath }
    & $sevenZip a -tzip "-p$ZipPassword" -mem=AES256 -bso0 -bsp0 $encPath $zipPath
    if ($LASTEXITCODE -eq 0) {
      Remove-Item -Force $zipPath
      Rename-Item $encPath $zipPath
      Write-Ok "ZIP encrypted with 7-Zip AES-256"
    } else {
      Write-Warn "7z encryption failed - keeping plain ZIP"
      if (Test-Path $encPath) { Remove-Item -Force $encPath }
    }
  } else {
    Write-Warn "7-Zip not found - ZIP without password"
  }
}

# Verify music.env inside archive
$envFound = $false
try {
  & tar -tf $zipPath | Select-String -Pattern "music\.env$" | Select-Object -First 1 | ForEach-Object { $envFound = $true }
} catch { }
if (-not $envFound) {
  Write-Warn "music.env not listed in ZIP - restore may be incomplete"
} else {
  Write-Ok "music.env present in archive"
}

$sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Write-Ok "Backup ready: $zipPath ($sizeMb MB)"
Write-Info "Manifest:   $manifestPath"

if ($staging -and (Test-Path $staging) -and -not $KeepStaging) {
  Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
}

Get-ChildItem $OutDir -Filter "*.zip" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 @{n = "Name"; e = { $_.Name } }, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 2) } }, LastWriteTime |
  Format-Table -AutoSize | Out-String | Write-Host
