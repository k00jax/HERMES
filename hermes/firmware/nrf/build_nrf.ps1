Write-Host "Building nRF firmware..."
pio run -e nrf
if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed"
    exit 1
}

Write-Host "Copying UF2 to Odroid..."
scp .pio/build/nrf/firmware.uf2 odroid@10.0.0.80:~/incoming/firmware.uf2
if ($LASTEXITCODE -ne 0) {
    Write-Error "SCP failed"
    exit 1
}

Write-Host "Done. Ready to flash on Odroid."
