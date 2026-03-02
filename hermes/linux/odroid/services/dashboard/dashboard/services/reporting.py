from __future__ import annotations

from typing import Dict


def report_response(ok: bool, **extra: object) -> Dict[str, object]:
  out = {"ok": bool(ok)}
  out.update(extra)
  return out
