# vbi-cli bootstrap installer for Windows / PowerShell.
#
# Usage:
#   .\install.ps1                           # install to %USERPROFILE%\vbi-cli
#   .\install.ps1 -Target C:\tools\vbi-cli  # custom location
#   .\install.ps1 -Source <path-or-url>     # custom source (default: this repo)
#
# UX flow:
#   1. Big "VBI CLI" banner (gradient) — one shot.
#   2. Skyline row directly under banner — starts EMPTY (░░░), grows in sync
#      with install steps below (each step ≈ 25% of the row).
#   3. Taglines, version, Python info — visible from the start, between
#      skyline and step lines.
#   4. Step lines: each has a `[⠋]` braille spinner that rotates in place
#      while its background job runs, plus a `.` appended every 2 s. On
#      completion the bracket flips to `[✓]` and the line ends with the
#      elapsed time `(X.Xs)`.
#   5. After the last step, hand off to the just-installed `vbi` so the user
#      lands on its interactive home view.

[CmdletBinding()]
param(
    [string]$Target = "$env:USERPROFILE\vbi-cli",
    [string]$Source = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [switch]$NoLaunch  # accepted but unused; kept for backward compat
)

$ErrorActionPreference = "Stop"
$ESC = [char]27
$RST = "$ESC[0m"

# UTF-8 output so [Console]::Write (used for the in-place spinner / skyline
# updates) round-trips the Unicode block chars correctly. Without this the
# .NET default code page converts them to `?`.
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$script:VBISkyline       = "▂▅▃▆▂▇▄█▃▆▂▅▃▆▄█▂▇▆▄█▂▇▃▆▂▅▃▆▂▇▄█▃▆▂▅▆▄█▂▇▃▆▂"
$script:VBIBrailleFrames = @('⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏')

# Tracks the vertical distance from the current cursor row up to the skyline
# row. Bumped by 1 every time we print a static line below the skyline (or
# finalize a step line). Read by Invoke-Step to compute the `\033[<n>A`
# offset when re-rendering the skyline mid-step.
$script:SkylineLinesAbove = 0

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

# Print a static line below the skyline, bumping the line counter so
# Invoke-Step knows how far up to jump when re-rendering the skyline.
function Write-StaticLine { param([string]$Line)
    Write-Host $Line
    $script:SkylineLinesAbove++
}

# Render the skyline row at $FilledChars / total filled.
function Format-Skyline { param([int]$FilledChars)
    $total = $script:VBISkyline.Length
    $built = if ($FilledChars -gt 0) { $script:VBISkyline.Substring(0, [math]::Min($FilledChars, $total)) } else { "" }
    $empty = "░" * [math]::Max(0, $total - $FilledChars)
    return "  $ESC[38;5;215m$built$RST$ESC[2m$empty$RST"
}

# Run one install step:
#   • Spinner braille rotates in place inside the bracket.
#   • A `.` is appended every 2 s of runtime.
#   • Skyline above is re-rendered each tick, advancing one char at a time
#     toward this step's target fill (= step_n / total chars).
# Updates `$script:VBICurrentSkylineFill` so the next step picks up where this one left off.
function Invoke-Step {
    param(
        [int]$StepIdx,
        [int]$Total,
        [string]$Label,
        [scriptblock]$Action
    )

    $totalChars      = $script:VBISkyline.Length
    $stepEndChars    = [int]([math]::Round(($StepIdx / $Total) * $totalChars))
    $tickMs          = 100

    # Initial step-line render (no newline — we'll keep \r-overwriting it).
    [Console]::Write("  $ESC[2m[$RST$ESC[38;5;215m$($script:VBIBrailleFrames[0])$RST$ESC[2m]$RST $Label ...")

    $sw       = [Diagnostics.Stopwatch]::StartNew()
    $job      = Start-Job -ScriptBlock $Action
    $tickIdx  = 0
    $dotCount = 0
    $lastDot  = [DateTime]::Now

    while ($job.State -eq 'Running') {
        Start-Sleep -Milliseconds $tickMs
        $tickIdx++
        $br = $script:VBIBrailleFrames[$tickIdx % $script:VBIBrailleFrames.Count]

        # Append a dot every 2 s of wall time.
        $now = [DateTime]::Now
        if (($now - $lastDot).TotalSeconds -ge 2) {
            $dotCount++
            $lastDot = $now
        }

        # Advance skyline toward this step's quota by 1 char per tick.
        if ($script:VBICurrentSkylineFill -lt $stepEndChars) {
            $script:VBICurrentSkylineFill++
        }

        $skyLine  = Format-Skyline -FilledChars $script:VBICurrentSkylineFill
        $dots     = if ($dotCount -gt 0) { " " + (" ." * $dotCount).TrimStart() } else { "" }
        $stepLine = "  $ESC[2m[$RST$ESC[38;5;215m$br$RST$ESC[2m]$RST $Label ...$dots"

        # Jump up to the skyline row, redraw it, jump back down, redraw the
        # step line. Using ANSI \033[A/\033[B (relative cursor) so the moves
        # are not tied to an absolute screen position the way Console::
        # SetCursorPosition is, which broke earlier when terminals scrolled.
        $up = $script:SkylineLinesAbove
        [Console]::Write("`r$ESC[${up}A$ESC[2K$skyLine$ESC[${up}B`r$ESC[2K$stepLine")
    }

    $output = Receive-Job $job 2>&1
    $failed = $job.State -eq 'Failed'
    Remove-Job $job -Force
    $sw.Stop()
    $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)

    # Snap the skyline to this step's full quota even if the job ended fast.
    $script:VBICurrentSkylineFill = $stepEndChars
    $skyLine = Format-Skyline -FilledChars $script:VBICurrentSkylineFill

    if ($failed) {
        $finalStep = "  $ESC[91m[✗]$RST $Label ... $ESC[2m(${secs}s)$RST"
    } else {
        $finalStep = "  $ESC[32m[✓]$RST $Label ... $ESC[2m(${secs}s)$RST"
    }
    $up = $script:SkylineLinesAbove
    [Console]::Write("`r$ESC[${up}A$ESC[2K$skyLine$ESC[${up}B`r$ESC[2K$finalStep")
    [Console]::WriteLine()  # finalize the step line
    $script:SkylineLinesAbove++

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

# Skyline placeholder — empty bar, will fill char-by-char as steps run.
$script:VBICurrentSkylineFill = 0
Write-Host (Format-Skyline -FilledChars 0)
$script:SkylineLinesAbove = 1   # cursor is now 1 line below the skyline row

# Read version once (used in the banner block; the home view shows it again).
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

# ── install steps ───────────────────────────────────────────────────────────

if (Test-Path $Target) {
    Remove-Item -Recurse -Force $Target
}

Invoke-Step -StepIdx 1 -Total 4 -Label "clone vbi-cli" -Action {
    git clone $using:Source $using:Target 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git clone exited with code $LASTEXITCODE" }
}

Invoke-Step -StepIdx 2 -Total 4 -Label "create Python venv" -Action {
    & $using:pyCmd -m venv "$using:Target\.venv" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "python -m venv exited with code $LASTEXITCODE" }
}

Invoke-Step -StepIdx 3 -Total 4 -Label "install dependencies (rich, pyyaml, pyfiglet)" -Action {
    & "$using:Target\.venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e $using:Target 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pip install exited with code $LASTEXITCODE" }
}

Invoke-Step -StepIdx 4 -Total 4 -Label "verify vbi command" -Action {
    if (-not (Test-Path "$using:Target\.venv\Scripts\vbi.exe")) {
        throw "vbi.exe not found in venv"
    }
}

Write-Host ""

# Hand off to the freshly-installed vbi → home view (REPL).
& "$Target\.venv\Scripts\vbi.exe"
