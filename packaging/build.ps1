#Requires -Version 5.1
<#
.SYNOPSIS
  Build a Windows portable Agent3D package (+ optional Setup.exe) and zip for GitHub Releases.

.DESCRIPTION
  1) Downloads Python embeddable (cached under packaging/_cache)
  2) Installs pip + project requirements into runtime/
  3) Stages app files under packaging/_stage/Agent3D
  4) Writes dist/Agent3D-Windows-x64.zip  (给客户的主交付物)
  5) If Inno Setup 6 is available (or -InstallInno), also builds dist/Agent3D-Setup.exe

.EXAMPLE
  .\packaging\build.ps1
  .\packaging\build.ps1 -SkipInno
  .\packaging\build.ps1 -InstallInno
  .\packaging\build.ps1 -SampleSceneIds @("d461c16a8539","a37b10fc5003")
#>
[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12.8",
    [switch]$SkipDeps,
    [switch]$SkipInno,
    [switch]$InstallInno,
    [switch]$Clean,
    # Demo scenes packed into artifacts_web\ for offline preview (folder names under artifacts_web)
    [string[]]$SampleSceneIds = @(
        "d461c16a8539",  # iraqi_palace_compound
        "4ab6880e8485",  # Haussmannian_Apartment_Block
        "df473edbd195",  # Desert Palace Compound
        "e04aeadcc58c",  # modern_villa
        "07d8910c59ab",  # modern_cantilever_house
        "9f4b48d55dcd",  # neo_classical_government_tower
        "a37b10fc5003"   # two_storey_house (+ trees)
    )
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Packaging = $PSScriptRoot
$Cache = Join-Path $Packaging "_cache"
$Stage = Join-Path $Packaging "_stage\Agent3D"
$Dist = Join-Path $RepoRoot "dist"
$VersionFile = Join-Path $Packaging "VERSION"
$VersionTag = if (Test-Path $VersionFile) {
    (Get-Content $VersionFile -Raw).Trim()
} else {
    Get-Date -Format "yyyy.M.d"
}

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ensure-Dir($p) { if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null } }

Ensure-Dir $Cache
Ensure-Dir $Dist

if ($Clean) {
    Write-Step "Cleaning stage / dist artifacts"
    $OldStage = Join-Path $Packaging "_stage"
    if (Test-Path $OldStage) {
        $Trash = Join-Path $Packaging ("_trash_" + (Get-Date -Format "yyyyMMddHHmmss"))
        try {
            Rename-Item $OldStage $Trash -ErrorAction Stop
            Remove-Item $Trash -Recurse -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Host "Stage in use; building into a fresh folder after rename failed: $_" -ForegroundColor Yellow
            Remove-Item $OldStage -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Get-ChildItem $Dist -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

# --- 1) Embeddable Python -------------------------------------------------
$PyZipName = "python-$PythonVersion-embed-amd64.zip"
$PyZipUrl = "https://www.python.org/ftp/python/$PythonVersion/$PyZipName"
$PyZip = Join-Path $Cache $PyZipName
$Runtime = Join-Path $Stage "runtime"

$PythonExe = Join-Path $Runtime "python.exe"
$ReuseRuntime = $SkipDeps -and (Test-Path $PythonExe) -and (Test-Path (Join-Path $Runtime "Lib\site-packages"))

Write-Step "Preparing embeddable Python $PythonVersion"
Ensure-Dir $Stage

if ($ReuseRuntime) {
    Write-Host "Reusing existing runtime (SkipDeps)" -ForegroundColor Yellow
} else {
    if (Test-Path $Runtime) { Remove-Item $Runtime -Recurse -Force }
    Ensure-Dir $Runtime

    if (-not (Test-Path $PyZip)) {
        Write-Host "Downloading $PyZipUrl"
        Invoke-WebRequest -Uri $PyZipUrl -OutFile $PyZip -UseBasicParsing
    }
    Expand-Archive -Path $PyZip -DestinationPath $Runtime -Force

    # Enable site-packages (required for pip)
    $Pth = Get-ChildItem $Runtime -Filter "python*._pth" | Select-Object -First 1
    if (-not $Pth) { throw "python*._pth not found in embeddable runtime" }
    # Use actual zip name from folder
    $ZipName = (Get-ChildItem $Runtime -Filter "python*.zip" | Select-Object -First 1).Name
    $pthText = @"
$ZipName
.
Lib\site-packages
import site
"@
    Set-Content -Path $Pth.FullName -Value $pthText -Encoding ASCII

    # Prevent the embeddable interpreter from importing the developer machine's
    # %APPDATA%\Python\...\site-packages (would make the zip non-portable).
    Set-Content -Path (Join-Path $Runtime "sitecustomize.py") -Value @"
import site
site.ENABLE_USER_SITE = False
"@ -Encoding ASCII

    # --- 2) pip + deps -------------------------------------------------------
    $GetPip = Join-Path $Cache "get-pip.py"
    if (-not (Test-Path $GetPip)) {
        Write-Step "Downloading get-pip.py"
        Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip -UseBasicParsing
    }

    # Keep the portable runtime self-contained: never resolve / install into the
    # developer machine's %APPDATA%\Python user site-packages.
    $env:PYTHONNOUSERSITE = "1"

    Write-Step "Installing pip into runtime"
    & $PythonExe $GetPip --no-warn-script-location --no-user
    if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }

    Write-Step "Installing project requirements into runtime (may take several minutes)"
    $Req1 = Join-Path $RepoRoot "requirements.txt"
    $Req2 = Join-Path $RepoRoot "agent3d\requirements-web.txt"
    & $PythonExe -m pip install --no-warn-script-location --no-user `
        -r $Req1 -r $Req2
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}
$env:PYTHONNOUSERSITE = "1"

# --- 3) Copy application --------------------------------------------------
Write-Step "Staging application files"

function Copy-TreeFiltered {
    param([string]$Src, [string]$Dst)
    Ensure-Dir $Dst
    $robocopyArgs = @(
        $Src, $Dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/nc", "/ns", "/np",
        "/XD", "__pycache__", ".git", ".venv", "venv", "artifacts_web",
        "/XF", "*.pyc", "*.pyo", ".env", "agent3d_settings.json"
    )
    & robocopy @robocopyArgs | Out-Null
    # robocopy exit codes 0-7 are success
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed copying $Src -> $Dst (code $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}

# Clean previous app bits but keep runtime
Get-ChildItem $Stage -Force | Where-Object { $_.Name -ne "runtime" } | Remove-Item -Recurse -Force

Copy-TreeFiltered (Join-Path $RepoRoot "agent3d") (Join-Path $Stage "agent3d")
Copy-TreeFiltered (Join-Path $RepoRoot "stand_trans") (Join-Path $Stage "stand_trans")

Copy-Item (Join-Path $RepoRoot ".env.example") (Join-Path $Stage ".env.example") -Force
Copy-Item (Join-Path $Packaging "launcher\launch.py") (Join-Path $Stage "launch.py") -Force
Copy-Item (Join-Path $Packaging "launcher\stop.py") (Join-Path $Stage "stop.py") -Force
Copy-Item (Join-Path $Packaging "launcher\Start-Agent3D.bat") (Join-Path $Stage "Start-Agent3D.bat") -Force
Copy-Item (Join-Path $Packaging "launcher\Stop-Agent3D.bat") (Join-Path $Stage "Stop-Agent3D.bat") -Force
Copy-Item (Join-Path $Packaging "launcher\开始 Agent3D.bat") (Join-Path $Stage "开始 Agent3D.bat") -Force
Copy-Item (Join-Path $Packaging "launcher\停止 Agent3D.bat") (Join-Path $Stage "停止 Agent3D.bat") -Force
Copy-Item (Join-Path $Packaging "launcher\README.txt") (Join-Path $Stage "README.txt") -Force
Set-Content -Path (Join-Path $Stage "VERSION.txt") -Value $VersionTag -Encoding ASCII

# Drop heavy / non-user bits from stage (skills docs optional; keep schema/examples/prompts/webapp)
$Skills = Join-Path $Stage "agent3d\skills"
if (Test-Path $Skills) { Remove-Item $Skills -Recurse -Force }

# Pack selected demo scenes so customers can open viewer without regenerating
$ArtifactsSrc = Join-Path $RepoRoot "artifacts_web"
$ArtifactsDst = Join-Path $Stage "artifacts_web"
if ($SampleSceneIds -and $SampleSceneIds.Count -gt 0) {
    Write-Step "Including demo scenes ($($SampleSceneIds.Count))"
    Ensure-Dir $ArtifactsDst
    foreach ($id in $SampleSceneIds) {
        $srcScene = Join-Path $ArtifactsSrc $id
        if (-not (Test-Path $srcScene)) {
            Write-Host "  skip missing scene: $id" -ForegroundColor Yellow
            continue
        }
        $dstScene = Join-Path $ArtifactsDst $id
        if (Test-Path $dstScene) { Remove-Item $dstScene -Recurse -Force }
        & robocopy $srcScene $dstScene /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy failed copying sample scene $id (code $LASTEXITCODE)" }
        $global:LASTEXITCODE = 0
        $name = ""
        $mani = Join-Path $srcScene "manifest.json"
        if (Test-Path $mani) {
            try { $name = (Get-Content $mani -Raw | ConvertFrom-Json).name } catch { }
        }
        Write-Host "  + $id  $name"
    }
}

# Smoke import check (must resolve packages from runtime/, not user site)
Write-Step "Smoke-import check"
$Smoke = @"
import os, sys, importlib
from pathlib import Path
os.environ['PYTHONNOUSERSITE'] = '1'
stage = Path(r'$Stage')
runtime = Path(r'$Runtime')
sys.path.insert(0, str(stage))
import ifcopenshell
loc = Path(ifcopenshell.__file__).resolve()
assert runtime.resolve() in loc.parents, loc
import fastapi, uvicorn, trimesh, numpy, anthropic, manifold3d
server = importlib.import_module('agent3d.webapp.server')
print('ok', server.app.title)
print('ifcopenshell=', loc)
"@
$SmokeFile = Join-Path $Cache "smoke_import.py"
Set-Content -Path $SmokeFile -Value $Smoke -Encoding ASCII
$env:PYTHONNOUSERSITE = "1"
& $PythonExe $SmokeFile
if ($LASTEXITCODE -ne 0) { throw "Smoke import failed" }

# --- 4) Zip for users / GitHub Releases -----------------------------------
$ZipPath = Join-Path $Dist "Agent3D-Windows-x64.zip"
Write-Step "Creating $ZipPath"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
# Compress-Archive is slow/large; prefer tar if available (Windows 10+)
$Tar = Get-Command tar -ErrorAction SilentlyContinue
Push-Location (Join-Path $Packaging "_stage")
try {
    if ($Tar) {
        & tar -a -cf $ZipPath "Agent3D"
        if ($LASTEXITCODE -ne 0) { throw "tar zip failed" }
    } else {
        Compress-Archive -Path "Agent3D" -DestinationPath $ZipPath -CompressionLevel Optimal
    }
} finally {
    Pop-Location
}

$ZipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host "Zip ready: $ZipPath ($ZipSize MB)" -ForegroundColor Green

# --- 5) Optional Inno Setup.exe -------------------------------------------
$SetupPath = Join-Path $Dist "Agent3D-Setup.exe"
$IsccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $SkipInno) {
    if (-not $Iscc -and $InstallInno) {
        Write-Step "Installing Inno Setup 6 (silent)"
        $InnoSetup = Join-Path $Cache "innosetup-6.exe"
        if (-not (Test-Path $InnoSetup)) {
            # Official download endpoint
            Invoke-WebRequest -Uri "https://jrsoftware.org/download.php/is.exe" -OutFile $InnoSetup -UseBasicParsing
        }
        Start-Process -FilePath $InnoSetup -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-" -Wait
        $Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }

    if ($Iscc) {
        Write-Step "Building Setup.exe with Inno Setup"
        $Iss = Join-Path $Packaging "Agent3D.iss"
        # Patch version into a temp iss via define
        & $Iscc "/DMyAppVersion=$VersionTag" $Iss
        if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
        if (Test-Path $SetupPath) {
            $SetupSize = [math]::Round((Get-Item $SetupPath).Length / 1MB, 1)
            Write-Host "Setup ready: $SetupPath ($SetupSize MB)" -ForegroundColor Green
        }
    } else {
        Write-Host @"

[跳过 Setup.exe] 未检测到 Inno Setup 6。
  - 客户仍可使用便携 Zip：dist\Agent3D-Windows-x64.zip
  - 若要生成 Setup.exe，请安装 https://jrsoftware.org/isinfo.php
    或重新运行:  .\packaging\build.ps1 -InstallInno

"@ -ForegroundColor Yellow
    }
}

Write-Step "Done"
Write-Host @"

交付给客户（任选上传到 GitHub Release，不要 commit 进仓库）:
  1. dist\Agent3D-Windows-x64.zip   ← 便携版（推荐先发这个）
  2. dist\Agent3D-Setup.exe         ← 若已成功编译

上传示例:
  gh release create v$VersionTag dist\Agent3D-Windows-x64.zip dist\Agent3D-Setup.exe --title "Agent3D $VersionTag" --notes "Windows 一键部署包（无需 WSL/Docker）"

"@ -ForegroundColor Green
