# nRF52840 Build + Flash (Canonical)

## Build (PC)
From hermes/firmware/nrf:

pio run -e nrf

Produces:
.pio/build/nrf/firmware.uf2

## Copy to Odroid (PC)

scp .pio/build/nrf/firmware.uf2 odroid@10.0.0.80:~/incoming/firmware.uf2

## Flash (Odroid)

Put board in UF2 mode, then:

./tools/flash_nrf_uf2.sh ~/incoming/firmware.uf2

## Verify (Odroid)

ls -l /dev/hermes-nrf
