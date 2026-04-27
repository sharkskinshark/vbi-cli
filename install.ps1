# vbi-cli bootstrap installer for Windows / PowerShell.
#
# Usage:
#   .\install.ps1                           # install to %USERPROFILE%\vbi-cli
#   .\install.ps1 -Target C:\tools\vbi-cli  # custom location
#   .\install.ps1 -Source <path-or-url>     # custom source (default: this repo)
#   .\install.ps1 -NoLaunch                 # skip post-install dashboard

[CmdletBinding()]
param(
    [string]$Target = "$env:USERPROFILE\vbi-cli",
    [string]$Source = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ESC = [char]27
$RST = "$ESC[0m"

# ── Architecture animation (skyline rising under the banner) ─────────────────
# An 18-column skyline that fills in column by column then resets to empty.
# Rendered on a fixed terminal row (anchored when we print the placeholder)
# and updated inside the Invoke-Step spinner loop, so it animates throughout
# the entire install — clone → venv → pip install → verify.
$script:VBISkyline = "▂▅▃▆▂▇▄█▃▆▂▅▃▆▄█▂▇"
$script:VBIBuildingFrames = @()
for ($i = 0; $i -le $script:VBISkyline.Length; $i++) {
    $built = if ($i -gt 0) { $script:VBISkyline.Substring(0, $i) } else { "" }
    $empty = "░" * ($script:VBISkyline.Length - $i)
    $script:VBIBuildingFrames += "$built$empty"
}
$script:VBIBuildingRow   = $null  # absolute row number; set when placeholder printed
$script:VBIBuildingFrame = 0

function Write-BuildingFrame {
    if ($null -eq $script:VBIBuildingRow) { return }
    $frame = $script:VBIBuildingFrames[$script:VBIBuildingFrame % $script:VBIBuildingFrames.Count]
    try {
        $savedTop  = [Console]::CursorTop
        $savedLeft = [Console]::CursorLeft
        [Console]::SetCursorPosition(0, $script:VBIBuildingRow)
        Write-Host -NoNewline "      $ESC[38;5;215m$frame$RST$ESC[K"
        [Console]::SetCursorPosition($savedLeft, $savedTop)
    } catch {
        # Terminal does not support cursor positioning (non-interactive host);
        # disable subsequent frame updates rather than spam errors.
        $script:VBIBuildingRow = $null
    }
    $script:VBIBuildingFrame++
}

function Set-BuildingFinal {
    if ($null -eq $script:VBIBuildingRow) { return }
    $finalFrame = $script:VBIBuildingFrames[$script:VBIBuildingFrames.Count - 1]
    try {
        $savedTop  = [Console]::CursorTop
        $savedLeft = [Console]::CursorLeft
        [Console]::SetCursorPosition(0, $script:VBIBuildingRow)
        Write-Host -NoNewline "      $ESC[38;5;215m$finalFrame$RST$ESC[K"
        [Console]::SetCursorPosition($savedLeft, $savedTop)
    } catch {}
}

# ── helpers ─────────────────────────────────────────────────────────────────

function Write-Gradient {
    param([string[]]$Lines, [int[]]$LeftRGB = @(255, 120, 40), [int[]]$RightRGB = @(255, 215, 130))
    $maxW = ($Lines | Measure-Object -Maximum -Property Length).Maximum
    foreach ($line in $Lines) {
        $sb = [System.Text.StringBuilder]::new()
        for ($i = 0; $i -lt $line.Length; $i++) {
            $ch = $line[$i]
            if ($ch -eq ' ') { [void]$sb.Append($ch); continue }
            $r = if ($maxW -le 1) { $LeftRGB[0] } else { $LeftRGB[0] + ($RightRGB[0] - $LeftRGB[0]) * $i / ($maxW - 1) }
            $g = if ($maxW -le 1) { $LeftRGB[1] } else { $LeftRGB[1] + ($RightRGB[1] - $LeftRGB[1]) * $i / ($maxW - 1) }
            $b = if ($maxW -le 1) { $LeftRGB[2] } else { $LeftRGB[2] + ($RightRGB[2] - $LeftRGB[2]) * $i / ($maxW - 1) }
            [void]$sb.Append("$ESC[38;2;$([int]$r);$([int]$g);$([int]$b)m$ch")
        }
        [void]$sb.Append($RST)
        Write-Host $sb.ToString()
    }
}

function Invoke-Step {
    param([string]$Label, [scriptblock]$Action)
    $spinner = @('⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏')
    $i = 0
    Write-Host -NoNewline "  [ ] $Label"

    $job = Start-Job -ScriptBlock $Action
    while ($job.State -eq 'Running') {
        $frame = $spinner[$i % $spinner.Count]
        Write-Host -NoNewline "`r  $ESC[93m[$frame]$RST $Label..."
        Write-BuildingFrame
        Start-Sleep -Milliseconds 80
        $i++
    }

    $output = Receive-Job $job 2>&1
    $failed = $job.State -eq 'Failed'
    Remove-Job $job -Force

    if ($failed) {
        Write-Host "`r  $ESC[91m[!]$RST $Label                                                  "
        Write-Host $output -ForegroundColor Red
        throw "Step failed: $Label"
    }
    Write-Host "`r  $ESC[32m[✓]$RST $Label                                                  "
}

# ── banner ──────────────────────────────────────────────────────────────────

$banner = @(
    ""
    "  ██╗   ██╗██████╗ ██╗     ██████╗██╗     ██╗"
    "  ██║   ██║██╔══██╗██║    ██╔════╝██║     ██║"
    "  ██║   ██║██████╔╝██║    ██║     ██║     ██║"
    "  ╚██╗ ██╔╝██╔══██╗██║    ██║     ██║     ██║"
    "   ╚████╔╝ ██████╔╝██║    ╚██████╗███████╗██║"
    "    ╚═══╝  ╚═════╝ ╚═╝     ╚═════╝╚══════╝╚═╝"
)
try { Clear-Host } catch { Write-Host "" }
Write-Gradient -Lines $banner

# Anchor the architecture-animation row directly under the banner so it sits
# in a fixed position throughout the install. We print an empty placeholder
# now and remember its row for in-place updates.
try { $script:VBIBuildingRow = [Console]::CursorTop } catch { $script:VBIBuildingRow = $null }
Write-Host "      $ESC[38;5;215m$($script:VBIBuildingFrames[0])$RST"

Write-Host "$ESC[2m       Local-first AI usage inspection$RST"
Write-Host "$ESC[2;3m       CLUSTER&Associates  Architecture Design$RST"
Write-Host "$ESC[2;3m            Visual Budget Inspection$RST"

# Read version from pyproject.toml so install banner stays in sync with package
$pyprojectPath = Join-Path $Source "pyproject.toml"
$VBIVersion = "0.0.0"
if (Test-Path $pyprojectPath) {
    $verLine = Select-String -Path $pyprojectPath -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($verLine) { $VBIVersion = $verLine.Matches[0].Groups[1].Value }
}
$VBIReleaseDate = "2026-04-27"
Write-Host "$ESC[2m            v$VBIVersion  ·  $VBIReleaseDate$RST"
Write-Host ""

# ── preflight: PowerShell 7+ ────────────────────────────────────────────────

$_PS7_MIN      = [Version]"7.0"
$_PS7_FALLBACK = "7.6.1"
try {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/PowerShell/PowerShell/releases/latest" -UseBasicParsing -TimeoutSec 10
    $_PS7_VERSION = $rel.tag_name.TrimStart('v')
} catch {
    $_PS7_VERSION = $_PS7_FALLBACK
}
$_PS7_URL = "https://github.com/PowerShell/PowerShell/releases/download/v$_PS7_VERSION/PowerShell-$_PS7_VERSION-win-x64.msi"

if ($PSVersionTable.PSVersion -lt $_PS7_MIN) {
    Write-Host "  $ESC[93m[i]$RST PowerShell $($PSVersionTable.PSVersion) detected — upgrading to $_PS7_VERSION"
    Write-Host ""

    $tmpMsi = Join-Path $env:TEMP "PowerShell-$_PS7_VERSION-win-x64.msi"

    Invoke-Step "download PowerShell $_PS7_VERSION" {
        Invoke-WebRequest -Uri $using:_PS7_URL -OutFile $using:tmpMsi -UseBasicParsing
    }

    Invoke-Step "install PowerShell $_PS7_VERSION (silent)" {
        Start-Process msiexec.exe -ArgumentList "/i `"$using:tmpMsi`" /quiet /norestart ADD_EXPLORER_CONTEXT_MENU_OPENPOWERSHELL=1 ENABLE_PSREMOTING=0 REGISTER_MANIFEST=1" -Wait -PassThru | Out-Null
    }

    Write-Host ""
    Write-Host "  $ESC[32mPowerShell $_PS7_VERSION installed.$RST"
    Write-Host "  $ESC[2mPlease re-launch this script in a new pwsh.exe window to continue.$RST"
    Write-Host ""
    exit 0
}

# ── preflight: Python 3.10+ ─────────────────────────────────────────────────

$pyCmd = if (Get-Command py -ErrorAction SilentlyContinue) { "py" }
         elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" }
         else { $null }
if (-not $pyCmd) {
    Write-Host "  $ESC[91m[!]$RST Python 3.10+ not found. Install from https://www.python.org/" -ForegroundColor Red
    exit 1
}

$null = & $pyCmd -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  $ESC[91m[!]$RST Python 3.10+ required. Found:" -ForegroundColor Red
    & $pyCmd --version
    exit 1
}

Write-Host "  $ESC[2mPython:$RST  $(& $pyCmd --version)"
Write-Host "  $ESC[2mSource:$RST  $Source"
Write-Host "  $ESC[2mTarget:$RST  $Target"
Write-Host ""

# ── install steps ───────────────────────────────────────────────────────────

if (Test-Path $Target) {
    Write-Host "  $ESC[93m[i]$RST $Target exists — removing"
    Remove-Item -Recurse -Force $Target
}

Invoke-Step "clone vbi-cli" {
    git clone $using:Source $using:Target 2>&1 | Out-Null
}

Invoke-Step "create Python venv" {
    & $using:pyCmd -m venv "$using:Target\.venv" 2>&1 | Out-Null
}

Invoke-Step "install dependencies (rich, pyyaml, pyfiglet)" {
    & "$using:Target\.venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e $using:Target 2>&1 | Out-Null
}

Invoke-Step "verify vbi command" {
    if (-not (Test-Path "$using:Target\.venv\Scripts\vbi.exe")) {
        throw "vbi.exe not found in venv"
    }
}

Set-BuildingFinal  # leave the skyline complete after the last step
Write-Host ""
Write-Host "  $ESC[32mInstalled successfully.$RST"
Write-Host ""
Write-Host "  $ESC[2mTo use vbi from anywhere:$RST"
Write-Host "    & '$Target\.venv\Scripts\vbi.exe' live"
Write-Host ""
Write-Host "  $ESC[2mOr add the venv to PATH:$RST"
Write-Host "    `$env:PATH = '$Target\.venv\Scripts;' + `$env:PATH"
Write-Host ""

# ── optional: launch first dashboard ────────────────────────────────────────

if (-not $NoLaunch) {
    Write-Host "  $ESC[38;5;208mLaunching vbi live --once...$RST"
    Write-Host ""
    Start-Sleep -Milliseconds 600
    & "$Target\.venv\Scripts\vbi.exe" live --once
}
