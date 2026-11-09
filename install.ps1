param(
  [string]$Profile = "coordination-verify",
  [string]$Spec = "",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Spec) { $Spec = $root }

if (-not (Get-Command dsh -ErrorAction SilentlyContinue)) {
  throw "The dsh CLI is not on PATH. Install the CLI, or use the desktop application's plugin manager (desktop is unverified in v3.0.0)."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
  throw "pnpm is not on PATH. Install pnpm (the dsh plugin command forwards to it) and retry."
}

$arguments = @("plugin", "--profile", $Profile, "add", $Spec)
Write-Host ("install: dsh " + ($arguments -join " ")) -ForegroundColor Cyan
if ($DryRun) { return }
& dsh @arguments
if ($LASTEXITCODE -ne 0) { throw "plugin add failed with exit $LASTEXITCODE" }

Write-Host ""
Write-Host "Installed into profile '$Profile'. The running profile was not touched." -ForegroundColor Green
Write-Host "Next:"
Write-Host "  1. dsh --profile $Profile --port 0 --no-open"
Write-Host "  2. open the printed URL -> Settings -> 多模型协作 -> 验证此版本 -> 确认启用"
Write-Host "  3. only then turn the global switch ON"
