# HERMES Public Release Security Checklist

Last updated: 2026-02-28

## Scope audited

- Repository: `hermes-src` (tracked files + rewritten history)
- Goal: prevent accidental credential/secret exposure before making repo public

## What was fixed

- Replaced committed Wi-Fi credentials with placeholders:
  - `firmware/esp32/include/secrets.h`
  - `linux/odroid/README.md`
- Added ignore guard for local secrets file path:
  - `firmware/esp32/.gitignore` includes `include/secrets.h`
- Rewrote git history to purge previously committed Wi-Fi values.
- Removed accidental committed local journal output artifact:
  - `hermes/udo journalctl -u hermes-logger.service -n 50`
- Replaced sample telnet token value in docs with placeholder (`<set-secret-token>`).

## Current status

### No high-risk active secrets found in tracked files

- No AWS-style keys (`AKIA...`/`ASIA...`)
- No GitHub PAT patterns (`ghp_...`, `github_pat_...`)
- No private key blocks (`BEGIN ... PRIVATE KEY`)
- No bearer tokens/DB credentials URI patterns found

### Medium-risk operational metadata still present (intentional)

These are usually acceptable for open-source infra docs, but reveal deployment topology:

- Private IP examples (`10.x`, `100.x`) in docs/scripts
- Absolute local paths (`/home/odroid/...`)
- Device paths (`/dev/hermes-*`, `/dev/tty*`)
- Service ports (`8000`, `8023`)

## Required actions before public release

- [ ] Rotate real Wi-Fi credentials on your network/router (history was previously exposed).
- [ ] Rotate any telnet token currently in use on running devices.
- [ ] Ensure local runtime `.env` files remain untracked.
- [ ] Ask collaborators to re-clone or hard-reset because history was rewritten.

## Safe release commands

```bash
# sanity check
cd ~/hermes-src

git grep -nEI 'AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY|Authorization: Bearer' || true

# inspect tracked changes

git status --short
```

## Recommended longer-term hardening

- Move live Wi-Fi credentials to an untracked local file only.
- Add CI secret scanning (e.g., Gitleaks) on pull requests.
- Keep example values as placeholders in docs (`YOUR_*`, `<set-secret>`).
