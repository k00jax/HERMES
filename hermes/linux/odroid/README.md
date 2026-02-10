# Odroid Linux Integration

Odroid-specific services, scripts, containers, and systemd units.

## OLED Context Pusher

Pushes host-computed deltas to the nRF OLED overlay.

Script:

```bash
~/hermes-src/hermes/tools/push_oled_context.sh
```

Systemd install:

```bash
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-oled-context.service /etc/systemd/system/
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-oled-context.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-oled-context.timer
```

Check status:

```bash
systemctl status hermes-oled-context.timer
journalctl -u hermes-oled-context.service -n 50
```

## Logger Daemon (systemd)

Install and enable the logger daemon:

```bash
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-logger.service
```

Check status:

```bash
systemctl status hermes-logger.service
sudo journalctl -u hermes-logger.service -n 50
```
