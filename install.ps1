# vbi-cli bootstrap installer for Windows / PowerShell.
#
# Usage:
#   .\install.ps1                           # install to %USERPROFILE%\vbi-cli
#   .\install.ps1 -Target C:\tools\vbi-cli  # custom location
#   .\install.ps1 -Source <path-or-url>     # custom source (default: this repo)
#
# UX flow:
#   1. Big "VBI CLI" banner (gradient).
#   2. Static skyline row directly under banner — printed full at start.
#   3. Taglines, version, Python info — visible immediately.
#   4. 4 install steps, each on its own line:
#        [N/4] <label> ... . . . ✓ (X.Xs)
#      A `.` is appended every 2 s during the step's background job, so long
#      operations don't look frozen. Output is pure append-only — no `\r`,
#      no cursor positioning — so it cannot collapse into the "wall of
#      stacked frames" that earlier in-place attempts produced on this
#      terminal host.
#   5. After the last step, hand off to `vbi` so the user lands on its
#      interactive home view (mini banner + quick-start menu + REPL).

[CmdletBinding()]
param(
    [string]$Target = "$env:USERPROFILE\vbi-cli",
    [string]$Source = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [switch]$NoLaunch  # accepted but unused; kept for backward compat
)

$ErrorActionPreference = "Stop"
$ESC = [char]27
$RST = "$ESC[0m"

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

# Run one install step. Output shape:
#   "  [N/T] <label> ... . . . ✓ (X.Xs)"
# Pure append-only — no `\r`, no cursor positioning — so the layout cannot
# collapse on terminal hosts that mishandle in-place updates.
function Invoke-Step {
    param([int]$N, [int]$Total, [string]$Label, [scriptblock]$Action)

    Write-Host -NoNewline "  $ESC[2m[$N/$Total]$RST $Label ..."

    $sw      = [Diagnostics.Stopwatch]::StartNew()
    $job     = Start-Job -ScriptBlock $Action
    $lastDot = [DateTime]::Now
    while ($job.State -eq 'Running') {
        Start-Sleep -Milliseconds 200
        $now = [DateTime]::Now
        if (($now - $lastDot).TotalSeconds -ge 2) {
            Write-Host -NoNewline " $ESC[2m.$RST"
            $lastDot = $now
        }
    }

    $output = Receive-Job $job 2>&1
    $failed = $job.State -eq 'Failed'
    Remove-Job $job -Force
    $sw.Stop()
    $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)

    if ($failed) {
        Write-Host " $ESC[91m✗$RST $ESC[2m(${secs}s)$RST"
        Write-Host $output -ForegroundColor Red
        throw "Step failed: $Label"
    }
    Write-Host " $ESC[32m✓$RST $ESC[2m(${secs}s)$RST"
}

# ── banner + immediate-display block ────────────────────────────────────────

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

Write-Host "  $ESC[38;5;215m$script:VBISkyline$RST"

$pyprojectPath = Join-Path $Source "pyproject.toml"
$VBIVersion = "0.0.0"
if (Test-Path $pyprojectPath) {
    $verLine = Select-String -Path $pyprojectPath -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($verLine) { $VBIVersion = $verLine.Matches[0].Groups[1].Value }
}
$VBIReleaseDate = "2026-04-27"

Write-Host "$ESC[2m       Local-first AI usage inspection$RST"
Write-Host "$ESC[2;3m       CLUSTER&Associates  Architecture Design$RST"
Write-Host "$ESC[2;3m            Visual Budget Inspection$RST"
Write-Host "$ESC[2m            v$VBIVersion  ·  $VBIReleaseDate$RST"
Write-Host ""

# ── preflight: PowerShell 7+ and Python 3.10+ ───────────────────────────────

$_PS7_MIN = [Version]"7.0"
if ($PSVersionTable.PSVersion -lt $_PS7_MIN) {
    Write-Host "  $ESC[91m[!]$RST PowerShell $($PSVersionTable.PSVersion) detected — please install PowerShell 7+:"
    Write-Host "      https://github.com/PowerShell/PowerShell/releases/latest"
    Write-Host "  $ESC[2mthen re-run this script.$RST"
    exit 1
}

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

Write-Host "  $ESC[2mPython:$RST  $((& $pyCmd --version).Trim())"
Write-Host ""

# ── install steps ───────────────────────────────────────────────────────────

if (Test-Path $Target) {
    Remove-Item -Recurse -Force $Target
}

Invoke-Step 1 4 "clone vbi-cli" {
    git clone $using:Source $using:Target 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git clone exited with code $LASTEXITCODE" }
}

Invoke-Step 2 4 "create Python venv" {
    & $using:pyCmd -m venv "$using:Target\.venv" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "python -m venv exited with code $LASTEXITCODE" }
}

Invoke-Step 3 4 "install dependencies (rich, pyyaml, pyfiglet)" {
    & "$using:Target\.venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e $using:Target 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pip install exited with code $LASTEXITCODE" }
}

Invoke-Step 4 4 "verify vbi command" {
    if (-not (Test-Path "$using:Target\.venv\Scripts\vbi.exe")) {
        throw "vbi.exe not found in venv"
    }
}

Write-Host ""

# Hand off to the freshly-installed vbi → home view (REPL).
& "$Target\.venv\Scripts\vbi.exe"
