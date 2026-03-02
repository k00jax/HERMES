# Odroid Logger

This directory contains the Odroid-side logging tools for HERMES.

## Verify Device Presence

- Confirm stable udev links are present:

```bash
ls -l /dev/hermes-nrf /dev/hermes-esp
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

- If needed, pass a port explicitly:

```bash
~/hermes/odroid/logger/hermes_logger.sh /dev/hermes-nrf 115200
```

- Use `/dev/hermes-*` names; `tty*` device numbering is not stable across boots.

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
