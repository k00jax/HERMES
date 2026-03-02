from __future__ import annotations

from typing import Dict


def health_payload(ok: bool, **extra: object) -> Dict[str, object]:
  payload = {"ok": bool(ok)}
  payload.update(extra)
  return payload
