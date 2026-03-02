from __future__ import annotations

import csv
import io
from typing import Iterable, Mapping


def rows_to_csv(rows: Iterable[Mapping[str, object]]) -> str:
  rows = list(rows)
  if not rows:
    return ""
  output = io.StringIO()
  writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
  writer.writeheader()
  writer.writerows(rows)
  return output.getvalue()
