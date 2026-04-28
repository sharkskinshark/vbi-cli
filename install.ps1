# vbi-cli bootstrap installer for Windows / PowerShell.
#
# Usage:
#   .\install.ps1                           # install to %USERPROFILE%\vbi-cli
#   .\install.ps1 -Target C:\tools\vbi-cli  # custom location
#   .\install.ps1 -Source <path-or-url>     # custom source (default: this repo)
#
# UX flow:
#   1. Banner (gradient).
#   2. Skyline row directly under banner — paints char-by-char as the four
#      install steps complete (each step ≈ 25% of the row).
#   3. Taglines, version, Python info — visible immediately.
#   4. Each install step is one in-place row: a braille spinner rotates
#      while the step's background job runs, then the bracket flips to
#      `[✓]` and the line ends with elapsed `(X.Xs)`.
#   5. Hand off to the just-installed `vbi` (interactive home view).
#
# Reliability constraints (verified empirically on this user's terminal):
#   • `\r` overwrites the current visual row only — if a line is wider than
#     [Console]::WindowWidth and wraps, `\r` no longer reaches the start of
#     the LOGICAL line, so subsequent writes accumulate as new rows. Every
#     line printed here is therefore kept comfortably under 80 chars
#     (taglines, step lines, skyline row).
#   • ANSI relative cursor up (`\033[<n>A`) works as expected; we use it to
#     reach the skyline row from each step's spinner row.

[CmdletBinding()]
param(
    [string]$Target = "$env:USERPROFILE\vbi-cli",
    [string]$Source = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [switch]$NoLaunch  # accepted but unused; kept for backward compat
)

$ErrorActionPreference = "Stop"
$ESC = [char]27
$RST = "$ESC[0m"

# Force UTF-8 on the .NET Console writer. Without this, [Console]::Write
# (used for the in-place spinner / skyline updates) goes through the OEM
# code page and turns every Unicode block / braille char into `?`.
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$script:VBISkyline       = "▂▅▃▆▂▇▄█▃▆▂▅▃▆▄█▂▇▆▄█▂▇▃▆▂▅▃▆▂▇▄█▃▆▂▅▆▄█▂▇▃▆▂"
$script:VBIBrailleFrames = @('⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏')

# Distance (in printed lines) from the skyline row down to the row where the
# next step's spinner is being written. Bumped by 1 every time we print a
# static line below the skyline, and again once each step's final
# (`[✓]/[✗]`) line is committed.
$script:SkylineLinesBelow = 0

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

# Print a static line below the skyline and bump the line counter so
# Invoke-Step knows how far up to jump when re-rendering the skyline.
function Write-StaticLine { param([string]$Line)
    Write-Host $Line
    $script:SkylineLinesBelow++
}

# Render the skyline row at $FilledChars / total filled.
function Format-Skyline { param([int]$FilledChars)
    $total = $script:VBISkyline.Length
    $built = if ($FilledChars -gt 0) { $script:VBISkyline.Substring(0, [math]::Min($FilledChars, $total)) } else { "" }
    $empty = "░" * [math]::Max(0, $total - $FilledChars)
    return "  $ESC[38;5;215m$built$RST$ESC[2m$empty$RST"
}

# Render one step line. KEEP THE VISIBLE WIDTH UNDER 80 — that's the budget
# `\r` can reliably overwrite. Format:
#   "  [<braille>] <label> ..."           ← while running
#   "  [   ✓    ] <label> ... (X.Xs)"     ← after success
function Format-StepLine {
    param([string]$Marker, [string]$Color, [string]$Label, [string]$Tail)
    $tailStr = if ($Tail) { " $ESC[2m$Tail$RST" } else { "" }
    return "  $ESC[2m[$RST$Color$Marker$RST$ESC[2m]$RST $Label ...$tailStr"
}

# Run one install step with a rotating braille spinner (in-place via `\r`)
# and a skyline that paints toward this step's quota.
function Invoke-Step {
    param([int]$N, [int]$Total, [string]$Label, [scriptblock]$Action)

    $totalSky    = $script:VBISkyline.Length
    $stepEndChar = [int]([math]::Round(($N / $Total) * $totalSky))

    # Initial render — spinner frame 0, dim brackets, no tail.
    $stepLine = Format-StepLine -Marker $script:VBIBrailleFrames[0] `
                  -Color "$ESC[38;5;215m" `
                  -Label $Label -Tail ""
    [Console]::Write($stepLine)

    $sw      = [Diagnostics.Stopwatch]::StartNew()
    $job     = Start-Job -ScriptBlock $Action
    $tickIdx = 0
    $tickMs  = 100
    # Always show at least 5 spinner frames so the animation is visible even
    # for fast steps (local clone, cached pip, instant verify).
    $minTicks = 5

    while ($job.State -eq 'Running' -or $tickIdx -lt $minTicks) {
        Start-Sleep -Milliseconds $tickMs
        $tickIdx++
        $br = $script:VBIBrailleFrames[$tickIdx % $script:VBIBrailleFrames.Count]

        # Advance skyline by 1 char per tick toward this step's end target.
        if ($script:VBICurrentSkylineFill -lt $stepEndChar) {
            $script:VBICurrentSkylineFill++
        }

        $skyLine  = Format-Skyline -FilledChars $script:VBICurrentSkylineFill
        $stepLine = Format-StepLine -Marker $br -Color "$ESC[38;5;215m" `
                      -Label $Label -Tail ""

        # Cursor up to skyline row, redraw it, come back down, redraw step.
        # All of (skyline, intervening static lines, step line) are kept under
        # 80 chars so neither row can wrap and break the offset math.
        $up = $script:SkylineLinesBelow
        [Console]::Write("`r$ESC[${up}A$ESC[2K$skyLine$ESC[${up}B`r$ESC[2K$stepLine")
    }

    $output = Receive-Job $job 2>&1
    $failed = $job.State -eq 'Failed'
    Remove-Job $job -Force
    $sw.Stop()
    $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)

    # Snap skyline to this step's quota even if the job ended fast.
    $script:VBICurrentSkylineFill = $stepEndChar
    $skyLine = Format-Skyline -FilledChars $script:VBICurrentSkylineFill

    if ($failed) {
        $finalStep = Format-StepLine -Marker "✗" -Color "$ESC[91m" `
                       -Label $Label -Tail "(${secs}s)"
    } else {
        $finalStep = Format-StepLine -Marker "✓" -Color "$ESC[32m" `
                       -Label $Label -Tail "(${secs}s)"
    }

    $up = $script:SkylineLinesBelow
    [Console]::Write("`r$ESC[${up}A$ESC[2K$skyLine$ESC[${up}B`r$ESC[2K$finalStep")
    [Console]::WriteLine()
    $script:SkylineLinesBelow++

    if ($failed) {
        Write-Host $output -ForegroundColor Red
        throw "Step failed: $Label"
    }
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

# Skyline placeholder — empty bar, filled char-by-char as steps run.
$script:VBICurrentSkylineFill = 0
Write-Host (Format-Skyline -FilledChars 0)
$script:SkylineLinesBelow = 1   # cursor is now 1 line below the skyline row

# Read version once.
$pyprojectPath = Join-Path $Source "pyproject.toml"
$VBIVersion = "0.0.0"
if (Test-Path $pyprojectPath) {
    $verLine = Select-String -Path $pyprojectPath -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($verLine) { $VBIVersion = $verLine.Matches[0].Groups[1].Value }
}
$VBIReleaseDate = "2026-04-27"

Write-StaticLine "$ESC[2m       Local-first AI usage inspection$RST"
Write-StaticLine "$ESC[2;3m       CLUSTER&Associates  Architecture Design$RST"
Write-StaticLine "$ESC[2;3m            Visual Budget Inspection$RST"
Write-StaticLine "$ESC[2m            v$VBIVersion  ·  $VBIReleaseDate$RST"
Write-StaticLine ""

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

Write-StaticLine "  $ESC[2mPython:$RST  $((& $pyCmd --version).Trim())"
Write-StaticLine ""

# ── install steps (label kept SHORT so step row never wraps) ────────────────

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

Invoke-Step 3 4 "install dependencies" {
    & "$using:Target\.venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e $using:Target 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pip install exited with code $LASTEXITCODE" }
}

Invoke-Step 4 4 "verify vbi command" {
    if (-not (Test-Path "$using:Target\.venv\Scripts\vbi.exe")) {
        throw "vbi.exe not found in venv"
    }
}

Write-Host ""

# Hold the completed install summary so the user can read it before vbi
# clears the screen for its own home view.
Start-Sleep -Seconds 1.5

# Hand off to the freshly-installed vbi → home view (REPL).
& "$Target\.venv\Scripts\vbi.exe"
