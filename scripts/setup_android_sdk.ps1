<#
.SYNOPSIS
    Installs Android SDK command-line tools, platform 34, build-tools, and platform-tools.

.DESCRIPTION
    1. Downloads official Android commandlinetools from Google.
    2. Extracts to %LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest.
    3. Accepts all Android SDK licenses automatically.
    4. Installs platform-tools (adb), platforms;android-34, build-tools;34.0.0.
    5. Sets user environment variables ANDROID_HOME and updates PATH.
    6. Configures mobile/android/local.properties.
#>

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = $PSScriptRoot }
$RootDir = Split-Path -Parent $ScriptDir
if (-not $RootDir) { $RootDir = "K:\Test\tmusic" }

$SdkRoot = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA "Android\Sdk" }
$CmdlineToolsDir = Join-Path $SdkRoot "cmdline-tools"
$LatestDir = Join-Path $CmdlineToolsDir "latest"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Android SDK Auto-Setup for T-Music (Android 14 / API 34)" -ForegroundColor Cyan
Write-Host " Target Directory: $SdkRoot" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Ensure Java is present
if (-not (Get-Command "java" -ErrorAction SilentlyContinue)) {
    Write-Error "Java 17+ is required but not found in PATH."
}

# 2. Create directories
if (-not (Test-Path $SdkRoot)) {
    New-Item -ItemType Directory -Path $SdkRoot -Force | Out-Null
}
if (-not (Test-Path $CmdlineToolsDir)) {
    New-Item -ItemType Directory -Path $CmdlineToolsDir -Force | Out-Null
}

# 3. Download commandlinetools if not present
$SdkManagerBat = Join-Path $LatestDir "bin\sdkmanager.bat"
if (-not (Test-Path $SdkManagerBat)) {
    $ZipUrl = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
    $ZipDest = Join-Path $SdkRoot "commandlinetools.zip"
    $TempExtract = Join-Path $SdkRoot "cmdline-temp"

    Write-Host "`n[1/5] Downloading Android Command-line Tools (~130 MB)..." -ForegroundColor Yellow
    if (Get-Command "curl.exe" -ErrorAction SilentlyContinue) {
        & curl.exe -L -o "$ZipDest" "$ZipUrl"
    } else {
        Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipDest -UseBasicParsing
    }

    Write-Host "[2/5] Extracting Command-line Tools..." -ForegroundColor Yellow
    if (Test-Path $TempExtract) { Remove-Item -Path $TempExtract -Recurse -Force }
    Expand-Archive -Path $ZipDest -DestinationPath $TempExtract -Force
    Remove-Item -Path $ZipDest -Force

    if (Test-Path $LatestDir) { Remove-Item -Path $LatestDir -Recurse -Force }
    Move-Item -Path (Join-Path $TempExtract "cmdline-tools") -Destination $LatestDir -Force
    Remove-Item -Path $TempExtract -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "`n[1/5] Command-line tools already installed." -ForegroundColor Green
}

# 4. Set Environment Variables
Write-Host "[3/5] Setting environment variables (ANDROID_HOME)..." -ForegroundColor Yellow
$env:ANDROID_HOME = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot
[Environment]::SetEnvironmentVariable("ANDROID_HOME", $SdkRoot, "User")
[Environment]::SetEnvironmentVariable("ANDROID_SDK_ROOT", $SdkRoot, "User")

$PlatformTools = Join-Path $SdkRoot "platform-tools"
$BinDir = Join-Path $LatestDir "bin"
$CurrentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($CurrentPath -notlike "*$PlatformTools*") {
    [Environment]::SetEnvironmentVariable("PATH", "$PlatformTools;$BinDir;$CurrentPath", "User")
}
$env:PATH = "$PlatformTools;$BinDir;$env:PATH"

# 5. Pre-accept licenses directly + via sdkmanager
Write-Host "[4/5] Accepting licenses and installing SDK packages (API 34)..." -ForegroundColor Yellow

$LicensesDir = Join-Path $SdkRoot "licenses"
if (-not (Test-Path $LicensesDir)) {
    New-Item -ItemType Directory -Path $LicensesDir -Force | Out-Null
}
$KnownLicenses = @{
    "android-sdk-license" = @(
        "24333f8a63b6825ea9c5514f83c2829b004d1fee",
        "d56f5187479451eabf01fb78af6dfcb131a6481e",
        "84831b9409646a918e30573bab4c9c91346d8abd"
    )
    "android-sdk-preview-license" = @("84831b9409646a918e30573bab4c9c91346d8abd")
    "android-googletv-license" = @("601085b94cd77f0b54ff86406957099fed79264f")
    "intel-android-extra-license" = @("d975f751698a77b662f1254ddbeed3901e976f5a")
    "google-gdk-license" = @("33b6a2b64920422d211ecaa781905b444cdedf40")
    "mips-android-sysimage-license" = @("e9acab5b5fbb560a72cfa4f23c4320e4226e6609")
}
foreach ($licName in $KnownLicenses.Keys) {
    $licFile = Join-Path $LicensesDir $licName
    $hashes = $KnownLicenses[$licName] -join "`n"
    Set-Content -Path $licFile -Value $hashes -Force
}

# Run sdkmanager to confirm licenses
$yArr = 1..25 | ForEach-Object { "y" }
$yArr | & $SdkManagerBat --sdk_root="$SdkRoot" --licenses 2>&1 | Out-Null

# Install packages
Write-Host "Installing packages: platform-tools, platforms;android-34, build-tools;34.0.0..." -ForegroundColor Yellow
& $SdkManagerBat --sdk_root="$SdkRoot" "platform-tools" "platforms;android-34" "build-tools;34.0.0"

# 6. Configure local.properties for Gradle
Write-Host "[5/5] Configuring mobile/android/local.properties..." -ForegroundColor Yellow
$MobileAndroidDir = Join-Path $RootDir "mobile\android"
if (Test-Path $MobileAndroidDir) {
    $EscapedSdk = $SdkRoot.Replace("\", "\\")
    Set-Content -Path (Join-Path $MobileAndroidDir "local.properties") -Value "sdk.dir=$EscapedSdk" -Force
}

Write-Host "`n[SUCCESS] Android SDK environment is ready!" -ForegroundColor Green
Write-Host "ANDROID_HOME: $SdkRoot" -ForegroundColor Cyan
Write-Host "adb is ready at: $PlatformTools\adb.exe" -ForegroundColor Cyan
