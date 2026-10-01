<#
.SYNOPSIS
    Builds the T-Music Android APK (debug or signed release).

.DESCRIPTION
    1. Validates prerequisites (Node, Java, Android SDK).
    2. Builds the frontend (Vite).
    3. Syncs Capacitor assets and configuration.
    4. Compiles the Android APK via Gradle wrapper.
    5. Outputs the APK path for sideloading.

.PARAMETER ServerUrl
    Optional URL of the remote T-Music server (e.g., https://music.example.com).
    If omitted, the existing capacitor.config.ts or default URL is used.

.PARAMETER Release
    Switch to build a signed release APK instead of debug APK.

.EXAMPLE
    .\scripts\build_android_apk.ps1 -ServerUrl "https://tmusic.myhome.net"
    .\scripts\build_android_apk.ps1 -Release
#>

param(
    [string]$ServerUrl,
    [switch]$Release
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$MobileDir = Join-Path $RootDir "mobile"
$FrontendDir = Join-Path $RootDir "frontend"
$AndroidDir = Join-Path $MobileDir "android"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " T-Music Android APK Build Script (Android 14+ API 34)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Environment checks
if (-not (Get-Command "node" -ErrorAction SilentlyContinue)) {
    Write-Error "Node.js is not found in PATH. Please install Node.js."
}
if (-not (Get-Command "java" -ErrorAction SilentlyContinue)) {
    Write-Error "Java is not found in PATH. Please install Java 17+ or set JAVA_HOME."
}

$defaultSdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$localProps = Join-Path $AndroidDir "local.properties"
if (-not $env:ANDROID_HOME -and (Test-Path $defaultSdk)) {
    $env:ANDROID_HOME = $defaultSdk
}
if (-not (Test-Path $localProps) -and $env:ANDROID_HOME) {
    $escaped = $env:ANDROID_HOME.Replace("\", "\\")
    Set-Content -Path $localProps -Value "sdk.dir=$escaped" -Force
}
if (-not (Test-Path $localProps) -and -not $env:ANDROID_HOME) {
    Write-Warning "Android SDK not found! Run .\scripts\setup_android_sdk.ps1 to download and install Android SDK automatically."
}

# 2. Server URL override if passed
if ($ServerUrl) {
    Write-Host "[1/4] Setting fixed server URL: $ServerUrl" -ForegroundColor Yellow
    $env:TMUSIC_SERVER_URL = $ServerUrl
} else {
    Write-Host "[1/4] Zero-config Wi-Fi LAN discovery enabled (auto-discovers local server)" -ForegroundColor Green
    $env:TMUSIC_SERVER_URL = $null
}

# 3. Build frontend
Write-Host "[2/4] Building frontend bundle..." -ForegroundColor Yellow
Push-Location $FrontendDir
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

# 4. Sync Capacitor
Write-Host "[3/4] Syncing Capacitor Android project..." -ForegroundColor Yellow
Push-Location $MobileDir
try {
    if (-not (Test-Path (Join-Path $MobileDir "node_modules"))) {
        Write-Host "Installing mobile dependencies..." -ForegroundColor Gray
        npm install
    }
    npx cap sync android
    if ($LASTEXITCODE -ne 0) { throw "Capacitor sync failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

# 5. Build Android APK
Write-Host "[4/4] Building APK with Gradle..." -ForegroundColor Yellow
Push-Location $AndroidDir
try {
    $gradlew = Join-Path $AndroidDir "gradlew.bat"
    if (-not (Test-Path $gradlew)) {
        throw "Gradle wrapper not found at $gradlew. Did 'npx cap add android' run?"
    }

    $buildType = if ($Release) { "assembleRelease" } else { "assembleDebug" }
    Write-Host "Running Gradle: clean $buildType..." -ForegroundColor Gray
    & $gradlew clean $buildType
    if ($LASTEXITCODE -ne 0) { throw "Gradle build failed with exit code $LASTEXITCODE" }

    $outDir = if ($Release) {
        Join-Path $AndroidDir "app\build\outputs\apk\release"
    } else {
        Join-Path $AndroidDir "app\build\outputs\apk\debug"
    }

    $apk = Get-ChildItem -Path $outDir -Filter "*.apk" | Select-Object -First 1
    if ($apk) {
        $destApk = Join-Path $MobileDir ($apk.Name)
        Copy-Item -Path $apk.FullName -Destination $destApk -Force
        Write-Host "`n[SUCCESS] APK created successfully!" -ForegroundColor Green
        Write-Host "Output: $destApk" -ForegroundColor Green
        Write-Host "`nTo install on device via adb:" -ForegroundColor Cyan
        Write-Host "  adb install -r `"$destApk`"" -ForegroundColor White
    } else {
        Write-Warning "Build succeeded, but no APK found in $outDir"
    }
} finally {
    Pop-Location
}
