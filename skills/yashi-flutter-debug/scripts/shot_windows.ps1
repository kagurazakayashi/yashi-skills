<#
.SYNOPSIS
  Capture ONLY the debugged Flutter desktop app's own window (Windows target).

.DESCRIPTION
  Uses user32 PrintWindow(hWnd, hdc, PW_RENDERFULLCONTENT=2) on the app's
  top-level window. PrintWindow renders the window's own content even when it
  is occluded, which is what we want and what keeps us inside the rule
  "only screenshot the target debugging application's window".

  Deliberately NOT used: BitBlt / CopyFromScreen. That copies whatever is on
  screen inside the window rectangle, so an overlapping window lands in the
  image (observed in practice: it captured an unrelated foreground app), and
  it is a screen grab rather than a window capture.

  The script fails loudly (non-zero exit) when the window cannot be found, when
  PrintWindow fails, or when the capture is a single flat colour. A blank
  "success" would be a silently wrong artifact, which is worse than an error.

.PARAMETER Out
  Output PNG path.

.PARAMETER ProjectDir
  Flutter project root; used to derive the app's binary name.

.PARAMETER ProcessName
  Override the process name owning the window.
#>
param(
  [string]$Out = "shot.png",
  [string]$ProjectDir = ".",
  [string]$ProcessName = ""
)

$ErrorActionPreference = "Stop"

function Fail([string]$msg, [int]$code) {
  [Console]::Error.WriteLine("ERROR: " + $msg)
  exit $code
}

Add-Type -AssemblyName System.Drawing
if (-not ("WinCap" -as [type])) {
  Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinCap {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@
}

# --- 1) work out the app's binary name -------------------------------------
function Get-BinaryName([string]$dir) {
  $cmake = Join-Path $dir "windows\CMakeLists.txt"
  if (Test-Path $cmake) {
    $m = Select-String -Path $cmake -Pattern 'set\(BINARY_NAME\s+"([^"]+)"' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
  }
  $pubspec = Join-Path $dir "pubspec.yaml"
  if (Test-Path $pubspec) {
    $m = Select-String -Path $pubspec -Pattern '^name:\s*(\S+)' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
  }
  return ""
}

if (-not $ProcessName) { $ProcessName = Get-BinaryName $ProjectDir }
if (-not $ProcessName) { $ProcessName = Split-Path (Resolve-Path $ProjectDir) -Leaf }
if (-not $ProcessName) { Fail "cannot determine the app process name; pass -ProcessName" 1 }

# --- 2) locate its main window ---------------------------------------------
$proc = $null
for ($i = 0; $i -lt 20; $i++) {
  $c = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
         Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero })
  if ($c.Count -gt 0) { $proc = $c[0]; break }
  Start-Sleep -Milliseconds 250
}
if (-not $proc) {
  Fail "no visible window for process '$ProcessName' (is the desktop app running?)" 2
}

$h = $proc.MainWindowHandle
$r = New-Object WinCap+RECT
[void][WinCap]::GetWindowRect($h, [ref]$r)
$w = $r.Right - $r.Left
$ht = $r.Bottom - $r.Top
if ($w -le 0 -or $ht -le 0) { Fail "window '$ProcessName' has invalid size ${w}x${ht}" 3 }

# --- 3) capture the window content (not the screen) ------------------------
$bmp = New-Object System.Drawing.Bitmap($w, $ht)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
try { $ok = [WinCap]::PrintWindow($h, $hdc, 2) } finally { $g.ReleaseHdc($hdc); $g.Dispose() }
if (-not $ok) { $bmp.Dispose(); Fail "PrintWindow failed for '$ProcessName'" 4 }

# --- 4) refuse a blank capture ---------------------------------------------
$seen = @{}
$sx = [Math]::Max(1, [int]($w / 16))
$sy = [Math]::Max(1, [int]($ht / 16))
for ($x = 0; $x -lt $w; $x += $sx) {
  for ($y = 0; $y -lt $ht; $y += $sy) { $seen[$bmp.GetPixel($x, $y).ToArgb()] = 1 }
}
if ($seen.Count -lt 2) {
  $bmp.Dispose()
  Fail ("capture of '$ProcessName' is a single flat colour - PrintWindow returned a blank frame " +
        "(some GPU drivers do this); refusing to report success") 5
}

# --- 5) save and re-verify the PNG signature -------------------------------
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
$bytes = [System.IO.File]::ReadAllBytes($Out)
$hex = (0..7 | ForEach-Object { $bytes[$_].ToString("X2") }) -join " "
if ($hex -ne "89 50 4E 47 0D 0A 1A 0A") { Fail "broken PNG at $Out ($hex)" 6 }

Write-Output "OK PNG $Out (${w}x${ht}, window of '$ProcessName')"
exit 0
