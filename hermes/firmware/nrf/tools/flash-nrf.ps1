param(
  [string]$ODROID_HOST = "odroid@10.0.0.80",
  [string]$ENV_NAME = "nrf",
  [int]$WAIT_TIMEOUT_SEC = 60,
  [int]$POLL_INTERVAL_SEC = 1,
  [string]$UF2_LABEL = "XIAO-SENSE"
)

$ErrorActionPreference = "Stop"

$LOCAL_UF2 = ".pio/build/$ENV_NAME/firmware.uf2"
$REMOTE_UF2 = "~/incoming/firmware.uf2"
$REMOTE_FLASH = "~/hermes-src/hermes/tools/flash_nrf_uf2.sh"

function Odroid($cmd) {
  ssh $ODROID_HOST $cmd
}

Write-Host "1) Building nRF UF2 (env=$ENV_NAME)..."
pio run -e $ENV_NAME | Out-Host
Write-Host "Build OK."

if (!(Test-Path $LOCAL_UF2)) {
  throw "UF2 not found at: $LOCAL_UF2"
}

Write-Host "2) Ensuring Odroid incoming folder exists..."
Odroid "mkdir -p ~/incoming"

Write-Host "3) Copying UF2 to Odroid..."
scp $LOCAL_UF2 "$ODROID_HOST:$REMOTE_UF2" | Out-Host
Write-Host "Copied UF2 to Odroid: $REMOTE_UF2"

Write-Host ""
Write-Host "4) Waiting for UF2 bootloader on Odroid. Double-tap reset on the XIAO nRF52840 now..."
Write-Host "   (timeout: $WAIT_TIMEOUT_SEC seconds)"

$deadline = (Get-Date).AddSeconds($WAIT_TIMEOUT_SEC)
$found = $false

while ((Get-Date) -lt $deadline) {
  try {
    $out = Odroid "lsblk -o NAME,RM,FSTYPE,LABEL -nr"
    if ($out -match $UF2_LABEL) {
      $found = $true
      break
    }
  } catch {
    # ignore transient SSH hiccups
  }
  Start-Sleep -Seconds $POLL_INTERVAL_SEC
}

if (-not $found) {
  Write-Host "Timeout waiting for UF2 bootloader. Make sure you double-tapped reset and the USB hub is connected."
  Write-Host "Check on Odroid: lsblk -o NAME,SIZE,RM,TYPE,FSTYPE,MOUNTPOINT,LABEL"
  exit 2
}

Write-Host "Bootloader detected ($UF2_LABEL)."
Write-Host "5) Flashing from Odroid..."
Odroid "$REMOTE_FLASH $REMOTE_UF2" | Out-Host

Write-Host "Done."
