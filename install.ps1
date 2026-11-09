param(
  [string]$Profile = "coordination-verify",
  [string]$Spec = "",
  [string]$HarnessExe = "",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Spec) { $Spec = $root }

function Resolve-DshCli {
  $command = Get-Command dsh -ErrorAction SilentlyContinue
  if ($command) { return @{ Kind = "path"; Value = $command.Source } }
  $candidates = @(
    $env:DSH_CLI,
    (Join-Path $env:LOCALAPPDATA "Programs\DeepSeek Harness\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js"),
    "D:\deepseek\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js"
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
  if ($HarnessExe -and (Test-Path -LiteralPath $HarnessExe)) {
    $cli = Join-Path (Split-Path -Parent $HarnessExe) "resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js"
    if (Test-Path -LiteralPath $cli) { return @{ Kind = "electron"; Exe = $HarnessExe; Cli = $cli } }
  }
  foreach ($cli in $candidates) {
    $exe = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $cli)))) "DeepSeek Harness.exe"
    if (Test-Path -LiteralPath $exe) { return @{ Kind = "electron"; Exe = $exe; Cli = $cli } }
  }
  return $null
}

function Ensure-Pnpm {
  if (Get-Command pnpm -ErrorAction SilentlyContinue) { return }
  $pnpm = Get-ChildItem "$env:USERPROFILE\.dsh\dsh-runtimes\*\dependencies\pnpm\bin\pnpm.cjs" -ErrorAction SilentlyContinue | Select-Object -First 1
  $node = Get-ChildItem "$env:USERPROFILE\.dsh\dsh-runtimes\*\dependencies\node\bin\node.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $pnpm -or -not $node) {
    throw "pnpm is not on PATH and no DSH-bundled pnpm was found. Install pnpm or use the desktop plugin manager."
  }
  $shimDir = Join-Path $env:TEMP "dsh-coord-pnpm"
  New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
  $shim = Join-Path $shimDir "pnpm.cmd"
  "@echo off`r`n`"$($node.FullName)`" `"$($pnpm.FullName)`" %*`r`n" | Set-Content -LiteralPath $shim -Encoding ascii
  $env:PATH = "$shimDir;$env:PATH"
}

$dsh = Resolve-DshCli
if (-not $dsh) { throw "dsh CLI not found. Pass -HarnessExe <DeepSeek Harness.exe> or use the desktop plugin manager." }
Ensure-Pnpm

if ($dsh.Kind -eq "path") {
  $arguments = @("plugin", "--profile", $Profile, "add", $Spec)
  $display = "dsh " + ($arguments -join " ")
} else {
  $env:ELECTRON_RUN_AS_NODE = "1"
  $arguments = @($dsh.Cli, "plugin", "--profile", $Profile, "add", $Spec)
  $display = "$($dsh.Exe) $($arguments -join ' ')"
}
Write-Host "install: $display" -ForegroundColor Cyan
if ($DryRun) { return }
& $arguments[0] $arguments[1..($arguments.Count - 1)]
if ($LASTEXITCODE -ne 0) { throw "plugin add failed with exit $LASTEXITCODE" }

Write-Host ""
Write-Host "Installed into profile '$Profile' (the running profile was not touched)." -ForegroundColor Green
Write-Host "Next:"
Write-Host "  1. dsh --profile $Profile --port 0 --no-open"
Write-Host "  2. open the printed URL -> Settings -> 多模型协作 -> 验证此版本 -> 确认启用"
Write-Host "  3. only then turn the global switch ON"
