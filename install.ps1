# vbi-cli bootstrap installer for Windows / PowerShell.
#
# Usage:
#   .\install.ps1                           # install to %USERPROFILE%\vbi-cli
#   .\install.ps1 -Target C:\tools\vbi-cli  # custom location
#   .\install.ps1 -Source <path-or-url>     # custom source (default: this repo)
#
# UX flow:
#   1. Big "VBI CLI" banner (gradient).
#   2. Tagline + version + Python check (static info).
#   3. ONE skyline row that grows char-by-char during install.
#      Each install step (clone → venv → pip → verify) corresponds to 25%
#      of the skyline; chars paint while that step's background job runs.
#   4. Once the skyline is full, the just-installed `vbi` is launched directly
#      into its interactive home view (mini banner + quick-start menu + REPL),
#      so the user lands on the same view they'd see by typing `vbi` later.

[CmdletBinding()]
param(
    [string]$Target = "$env:USERPROFILE\vbi-cli",
    [string]$Source = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [switch]$NoLaunch  # accepted but unused; kept for backward compat
)

$ErrorActionPreference = "Stop"
$ESC = [char]27
$RST = "$ESC[0m"

# Skyline is the single animated progress indicator during install.
$script:VBISkyline = "▂▅▃▆▂▇▄█▃▆▂▅▃▆▄█▂▇▆▄█▂▇▃▆▂▅▃▆▂▇▄█▃▆▂▅▆▄█▂▇▃▆▂"

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

# Run a sequence of install steps and animate ONE skyline row that paints
# char-by-char as each step makes progress. Returns $null on success, or a
# hashtable @{ Label=...; Output=... } describing the failed step.
#
# How the pacing works:
#   - The skyline string is split into N equal slices (one per step).
#   - Each step starts as a Start-Job background task. While the job is
#     running, the foreground prints chars from this step's slice at a
#     steady pace. When the job ends, any unpainted chars from this step's
#     slice are flushed instantly (so the skyline is always at-least-N% by
#     the time step N finishes), then we move to the next step.
#   - The whole loop is pure append-only: every char is a fresh
#     `Write-Host -NoNewline` to the same logical line. No `\r`, no cursor
#     positioning, so the layout cannot collapse the way earlier versions
#     did in some terminal hosts.
function Invoke-AnimatedInstall {
    param([array]$Steps)

    Write-Host -NoNewline "  "  # leading spaces, cursor stays on this row
    $chars      = $script:VBISkyline.ToCharArray()
    $totalChars = $chars.Count
    $charIdx    = 0
    $tickMs     = 100  # how fast skyline chars paint when a job is mid-run

    for ($n = 0; $n -lt $Steps.Count; $n++) {
        $step      = $Steps[$n]
        $job       = Start-Job -ScriptBlock $step.Action
        $targetIdx = [int]([math]::Round($totalChars * (($n + 1) / $Steps.Count)))

        while ($job.State -eq 'Running') {
            if ($charIdx -lt $targetIdx) {
                Write-Host -NoNewline "$ESC[38;5;215m$($chars[$charIdx])$RST"
                $charIdx++
                Start-Sleep -Milliseconds $tickMs
            } else {
                # Already painted this step's slice; just wait for the job.
                Start-Sleep -Milliseconds 200
            }
        }

        $output     = Receive-Job $job 2>&1
        $stepFailed = ($job.State -eq 'Failed')
        Remove-Job $job -Force

        if ($stepFailed) {
            # Pad the rest of the skyline with empty plots so the row closes
            # cleanly even though we're aborting.
            if ($charIdx -lt $totalChars) {
                $remaining = "░" * ($totalChars - $charIdx)
                Write-Host "$ESC[2m$remaining$RST"
            } else {
                Write-Host ""
            }
            return @{ Label = $step.Label; Output = $output }
        }

        # Step succeeded — flush any unpainted chars from this slice now.
        while ($charIdx -lt $targetIdx) {
            Write-Host -NoNewline "$ESC[38;5;215m$($chars[$charIdx])$RST"
            $charIdx++
        }
    }

    Write-Host ""  # close the skyline row
    return $null
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

Write-Host "$ESC[2m       Local-first AI usage inspection$RST"
Write-Host "$ESC[2;3m       CLUSTER&Associates  Architecture Design$RST"
Write-Host "$ESC[2;3m            Visual Budget Inspection$RST"

# Read version from pyproject.toml so the banner stays in sync.
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
    Write-Host "  $ESC[93m[i]$RST PowerShell $($PSVersionTable.PSVersion) detected — please install PowerShell $_PS7_VERSION from:"
    Write-Host "      $_PS7_URL"
    Write-Host "  $ESC[2mthen re-run this script.$RST"
    exit 1
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
Write-Host ""

# ── install: animated skyline + 4 background steps ──────────────────────────

if (Test-Path $Target) {
    $tgtLeaf = Split-Path -Leaf $Target
    Write-Host "  $ESC[93m[i]$RST removing existing $tgtLeaf"
    Remove-Item -Recurse -Force $Target
    Write-Host ""
}

$steps = @(
    @{
        Label  = "clone"
        Action = {
            git clone $using:Source $using:Target 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "git clone exited with code $LASTEXITCODE" }
        }
    },
    @{
        Label  = "create Python venv"
        Action = {
            & $using:pyCmd -m venv "$using:Target\.venv" 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "python -m venv exited with code $LASTEXITCODE" }
        }
    },
    @{
        Label  = "install dependencies"
        Action = {
            & "$using:Target\.venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e $using:Target 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "pip install exited with code $LASTEXITCODE" }
        }
    },
    @{
        Label  = "verify vbi command"
        Action = {
            if (-not (Test-Path "$using:Target\.venv\Scripts\vbi.exe")) {
                throw "vbi.exe not found in venv"
            }
        }
    }
)

$failure = Invoke-AnimatedInstall -Steps $steps

if ($null -ne $failure) {
    Write-Host ""
    Write-Host "  $ESC[91m✗$RST install failed at: $($failure.Label)" -ForegroundColor Red
    Write-Host $failure.Output -ForegroundColor Red
    exit 1
}

Write-Host ""

# ── launch vbi REPL ─────────────────────────────────────────────────────────
# Hand off to the just-installed `vbi`. With no subcommand it lands directly
# on the interactive home view (mini banner + quick-start menu + `vbi> `
# prompt), which is the same view the user would see if they typed `vbi`
# from any shell later.

& "$Target\.venv\Scripts\vbi.exe"
