$ErrorActionPreference = "Stop"

# ---- Config ----
$ODROID_HOST = "odroid"             # uses your SSH config Host alias
$ODROID_INCOMING = "~/incoming/firmware.uf2"
$ODROID_FLASH_CMD = "cd ~/hermes-src/hermes && ./flash-nrf"
$ODROID_VERIFY_CMD = "readlink -f /dev/hermes-nrf && ls -l /dev/hermes-nrf && echo 'OK: /dev/hermes-nrf present'"

Write-Host "=== HERMES nRF: Build -> Copy -> Flash ==="

Write-Host "`n[0/5] Syncing with GitHub..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
	throw "git is not installed or not in PATH"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
	git rev-parse --is-inside-work-tree *> $null
	if ($LASTEXITCODE -ne 0) {
		throw "Not inside a git repository: $repoRoot"
	}

	$branch = (git rev-parse --abbrev-ref HEAD).Trim()
	if ([string]::IsNullOrWhiteSpace($branch) -or $branch -eq "HEAD") {
		throw "Cannot determine current branch (detached HEAD)."
	}

	git fetch origin
	if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }

	git pull --ff-only origin $branch
	if ($LASTEXITCODE -ne 0) { throw "git pull failed (non fast-forward or conflicts)" }
}
finally {
	Pop-Location
}

Write-Host "`n[1/5] Building nRF UF2..."
pio run -e nrf
if ($LASTEXITCODE -ne 0) { throw "Build failed" }

$uf2 = ".pio/build/nrf/firmware.uf2"
if (-not (Test-Path $uf2)) { throw "UF2 not found: $uf2" }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item $uf2 ".pio/build/nrf/firmware_$stamp.uf2"
Write-Host "Saved snapshot: .pio/build/nrf/firmware_$stamp.uf2"

if (-not (Test-Path $uf2)) { throw "UF2 not found: $uf2" }

Write-Host "`n[2/5] Copying UF2 to Odroid ($ODROID_HOST)..."
scp $uf2 "$ODROID_HOST`:$ODROID_INCOMING"
if ($LASTEXITCODE -ne 0) { throw "SCP failed" }

Write-Host "`n[3/5] Put nRF into UF2 bootloader mode now:"
Write-Host " - Unplug nRF USB from Odroid"
Write-Host " - Hold BOOT"
Write-Host " - Plug back in"
Write-Host " - Release after ~1 second"
Read-Host "Press Enter when the nRF is in UF2 mode (XIAO-SENSE shows up on Odroid)"

Write-Host "`nChecking UF2 device is present on Odroid..."
ssh $ODROID_HOST "lsblk -o NAME,SIZE,RM,TYPE,FSTYPE,LABEL | grep -q XIAO-SENSE"
if ($LASTEXITCODE -ne 0) { throw "UF2 device not found. Make sure nRF is in bootloader mode (XIAO-SENSE)." }

Write-Host "`n[4/5] Flashing on Odroid..."
ssh $ODROID_HOST $ODROID_FLASH_CMD
if ($LASTEXITCODE -ne 0) { throw "Remote flash failed" }

Write-Host "`nVerifying /dev/hermes-nrf is present..."
ssh $ODROID_HOST $ODROID_VERIFY_CMD
if ($LASTEXITCODE -ne 0) { throw "Verify failed: /dev/hermes-nrf not found" }

Write-Host "`nDONE. nRF flashed and ready."