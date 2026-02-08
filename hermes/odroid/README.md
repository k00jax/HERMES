# Odroid Logger

This directory contains the Odroid-side logging tools for HERMES.

## Verify Device Presence

- Confirm the nRF is visible as a USB CDC ACM device:

```bash
ls -l /dev/ttyACM*
```

- Optional: view kernel messages after plug-in:

```bash
dmesg | tail -n 50
```

## Run Logger (tmux)

- Start a tmux session:

```bash
tmux new -s hermes_logger
```

- Run the logger (default port and baud):

```bash
~/hermes/odroid/logger/hermes_logger.sh
```

- If your device is not /dev/ttyACM0, pass it explicitly:

```bash
~/hermes/odroid/logger/hermes_logger.sh /dev/ttyACM1 115200
```

- Detach from tmux:

```bash
Ctrl-b d
```

- Reattach later:

```bash
tmux attach -t hermes_logger
```

## Tail Logs

- Logs are written to:

```
~/hermes/logs/hermes_YYYY-MM-DD.log
```

- Follow the current log file:

```bash
tail -f ~/hermes/logs/hermes_$(date -u +"%Y-%m-%d").log
```

## Notes

- The logger prepends a UTC ISO-8601 timestamp to each line.
- It reconnects automatically if the device is unplugged.
- It never truncates existing logs.
