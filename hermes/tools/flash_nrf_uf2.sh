#!/usr/bin/env bash
set -euo pipefail

UF2="${1:-}"
MNT="/mnt/uf2"

if [[ -z "$UF2" || ! -f "$UF2" ]]; then
  echo "Usage: $0 /path/to/firmware.uf2"
  exit 1
fi

# Find a likely UF2 block device:
# - removable (RM=1)
# - disk or partition with vfat
# - small size (<= 128M) typical UF2 mass storage
mapfile -t CANDIDATES < <(lsblk -rpno NAME,RM,TYPE,FSTYPE,SIZE \
  | awk '$2==1 && ($3=="disk" || $3=="part") && $4=="vfat" {print $1" "$5}' \
  | awk '
    function toMiB(sz){
      # crude: supports sizes like 32M, 64M, 0.1G
      unit=substr(sz,length(sz),1)
      val=substr(sz,1,length(sz)-1)+0
      if(unit=="K") return val/1024
      if(unit=="M") return val
      if(unit=="G") return val*1024
      return 999999
    }
    { if (toMiB($2) <= 128) print $1 }
  ')

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  echo "No UF2 (vfat, removable, <=128M) device found."
  echo "Check and see: lsblk -o NAME,SIZE,RM,TYPE,FSTYPE,MOUNTPOINT,LABEL"
  exit 2
fi

DEV="${CANDIDATES[0]}"

if [[ ${#CANDIDATES[@]} -gt 1 ]]; then
  echo "Multiple candidates found. Using: $DEV"
  echo "Check and see: lsblk -o NAME,SIZE,RM,TYPE,FSTYPE,MOUNTPOINT,LABEL"
fi

sudo mkdir -p "$MNT"

# If already mounted somewhere, use that mountpoint.
MP="$(lsblk -rpno MOUNTPOINT "$DEV" | head -n1)"
if [[ -n "$MP" ]]; then
  MNT="$MP"
else
  sudo mount "$DEV" "$MNT"
fi

echo "Flashing UF2 to $DEV mounted at $MNT"
sudo cp -f "$UF2" "$MNT/"
sync

# If we mounted it, unmount it.
if [[ -z "$MP" ]]; then
  sudo umount "$MNT" || true
fi

echo "Done. Board should reboot."
