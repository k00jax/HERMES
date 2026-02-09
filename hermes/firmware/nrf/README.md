# nRF52840 Firmware

nRF52840 firmware must be built on PC.
UF2 is generated via post-build script.
Do not attempt to build this project on Odroid.

## One-command flash (PC -> Odroid)

From PowerShell in `firmware/nrf`:

```powershell
.\tools\flash-nrf.ps1
```

What it does:

1. Builds the nRF firmware and generates `firmware.uf2`
2. Copies the UF2 to the Odroid at `~/incoming/firmware.uf2`
3. Waits for the UF2 bootloader to appear (double-tap reset on the XIAO)
4. Triggers flashing on the Odroid

If your Odroid IP changes:

```powershell
.\tools\flash-nrf.ps1 -ODROID_HOST "odroid@10.0.0.80"
```
