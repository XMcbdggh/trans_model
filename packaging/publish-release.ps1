#Requires -Version 5.1
<#
.SYNOPSIS
  Upload dist/Agent3D-*.zip (and optional Setup.exe) to a GitHub Release.

.EXAMPLE
  .\packaging\publish-release.ps1 -Tag v1.0.0
  .\packaging\publish-release.ps1 -Tag v1.0.0 -Notes "首个 Windows 客户交付包"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$Title = "",
    [string]$Notes = @"
## Windows 部署包（无需 WSL / Docker / 预装 Python）

### 下载哪个？
- **Agent3D-Windows-x64.zip**（推荐）：解压后双击「开始 Agent3D.bat」
- **Agent3D-Setup.exe**：安装向导，装完从开始菜单启动

### 首次使用
1. 启动后浏览器打开 http://127.0.0.1:8060/
2. 右上角「模型设置」填写 API Key

若提示缺少 DLL，安装 VC++ 运行库：https://aka.ms/vs/17/release/vc_redist.x64.exe
"@
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Dist = Join-Path $RepoRoot "dist"

if (-not $Title) { $Title = "Agent3D $Tag" }

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) { throw "需要 GitHub CLI (gh)。安装: https://cli.github.com/  然后 gh auth login" }

$assets = @()
$zip = Join-Path $Dist "Agent3D-Windows-x64.zip"
$setup = Join-Path $Dist "Agent3D-Setup.exe"
if (Test-Path $zip) { $assets += $zip } else { throw "缺少 $zip ，请先运行 .\packaging\build.ps1" }
if (Test-Path $setup) { $assets += $setup }

Push-Location $RepoRoot
try {
    $existing = & gh release view $Tag 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Release $Tag 已存在，上传/覆盖资产…"
        & gh release upload $Tag @assets --clobber
    } else {
        Write-Host "创建 Release $Tag …"
        & gh release create $Tag @assets --title $Title --notes $Notes
    }
    if ($LASTEXITCODE -ne 0) { throw "gh release failed" }
    & gh release view $Tag --json url -q .url
} finally {
    Pop-Location
}
