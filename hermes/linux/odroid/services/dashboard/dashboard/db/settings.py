from __future__ import annotations

import json
import sqlite3
from typing import Dict, Set


def ensure_settings_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value_json TEXT NOT NULL,
      updated_ts_utc TEXT NOT NULL
    );
    """
  )
  conn.execute("CREATE INDEX IF NOT EXISTS idx_settings_updated ON settings(updated_ts_utc);")


def get_settings_payload(conn: sqlite3.Connection, defaults: Dict[str, object], valid_chime_keys: Set[str]) -> Dict[str, object]:
  ensure_settings_table(conn)
  payload = dict(defaults)
  rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
  parsed: Dict[str, object] = {}
  for key_raw, value_json in rows:
    key = str(key_raw)
    try:
      parsed[key] = json.loads(value_json)
    except Exception:
      continue

  for key in defaults:
    if key in parsed:
      payload[key] = parsed[key]

  payload["field_mode_start"] = bool(payload.get("field_mode_start"))
  units = str(payload.get("units_distance") or "cm").lower()
  payload["units_distance"] = "m" if units == "m" else "cm"

  valid_chart = {"air_eco2", "env_temp", "env_hum", "air_tvoc"}
  for slot_key, fallback in (
    ("chart_slot_a", "air_eco2"),
    ("chart_slot_b", "env_temp"),
    ("chart_slot_c", "env_hum"),
    ("chart_slot_d", "air_tvoc"),
  ):
    chosen = str(payload.get(slot_key) or fallback)
    payload[slot_key] = chosen if chosen in valid_chart else fallback

  for chime_key, fallback in (
    ("chime_event_startup", "startup_vault_boot"),
    ("chime_event_air_spike", "warn_radiation_spike"),
    ("chime_event_wifi_drop", "warn_low_power"),
    ("chime_event_reboot_detected", "warn_system_fault"),
    ("chime_event_presence_change", "none"),
  ):
    chosen = str(payload.get(chime_key) or fallback)
    payload[chime_key] = chosen if chosen in valid_chime_keys else fallback

  payload["radar_self_suppress_enabled"] = bool(payload.get("radar_self_suppress_enabled"))
  try:
    near_cm = int(payload.get("radar_self_suppress_near_cm"))
  except Exception:
    near_cm = 80
  payload["radar_self_suppress_near_cm"] = max(20, min(200, near_cm))

  try:
    persist_s = int(payload.get("radar_self_suppress_persist_s"))
  except Exception:
    persist_s = 20
  payload["radar_self_suppress_persist_s"] = max(5, min(120, persist_s))

  try:
    jitter_cm = int(payload.get("radar_self_suppress_jitter_cm"))
  except Exception:
    jitter_cm = 15
  payload["radar_self_suppress_jitter_cm"] = max(2, min(80, jitter_cm))

  mode = str(payload.get("radar_presence_mode") or "raw").strip().lower()
  payload["radar_presence_mode"] = "derived" if mode == "derived" else "raw"

  payload["radar_track_enabled"] = bool(payload.get("radar_track_enabled", True))
  try:
    match_gate_cm = int(payload.get("radar_track_match_gate_cm"))
  except Exception:
    match_gate_cm = 60
  payload["radar_track_match_gate_cm"] = max(10, min(200, match_gate_cm))
  try:
    expire_ms = int(payload.get("radar_track_expire_ms"))
  except Exception:
    expire_ms = 2000
  payload["radar_track_expire_ms"] = max(5040, min(10000, expire_ms))
  try:
    confirm_hits = int(payload.get("radar_track_confirm_hits"))
  except Exception:
    confirm_hits = 2
  payload["radar_track_confirm_hits"] = max(1, min(5, confirm_hits))
  try:
    min_energy = int(payload.get("radar_track_min_energy"))
  except Exception:
    min_energy = 10
  payload["radar_track_min_energy"] = max(0, min(100, min_energy))
  try:
    jump_reject_cm = int(payload.get("radar_track_jump_reject_cm"))
  except Exception:
    jump_reject_cm = 250
  payload["radar_track_jump_reject_cm"] = max(50, min(600, jump_reject_cm))
  return payload


def save_settings_payload(
  conn: sqlite3.Connection,
  updates: Dict[str, object],
  defaults: Dict[str, object],
  valid_chime_keys: Set[str],
  now_utc: str,
) -> Dict[str, object]:
  ensure_settings_table(conn)
  current = get_settings_payload(conn, defaults, valid_chime_keys)
  merged = dict(current)

  for key, value in (updates or {}).items():
    if key not in defaults:
      continue
    merged[key] = value

  if "field_mode_start" in updates:
    merged["field_mode_start"] = bool(updates.get("field_mode_start"))
  if "units_distance" in updates:
    units = str(updates.get("units_distance") or "cm").lower()
    merged["units_distance"] = "m" if units == "m" else "cm"

  valid_chart = {"air_eco2", "env_temp", "env_hum", "air_tvoc"}
  for slot_key, fallback in (
    ("chart_slot_a", "air_eco2"),
    ("chart_slot_b", "env_temp"),
    ("chart_slot_c", "env_hum"),
    ("chart_slot_d", "air_tvoc"),
  ):
    if slot_key in updates:
      chosen = str(updates.get(slot_key) or fallback)
      merged[slot_key] = chosen if chosen in valid_chart else fallback

  for chime_key, fallback in (
    ("chime_event_startup", "startup_vault_boot"),
    ("chime_event_air_spike", "warn_radiation_spike"),
    ("chime_event_wifi_drop", "warn_low_power"),
    ("chime_event_reboot_detected", "warn_system_fault"),
    ("chime_event_presence_change", "none"),
  ):
    if chime_key in updates:
      chosen = str(updates.get(chime_key) or fallback)
      merged[chime_key] = chosen if chosen in valid_chime_keys else fallback

  if "radar_self_suppress_enabled" in updates:
    merged["radar_self_suppress_enabled"] = bool(updates.get("radar_self_suppress_enabled"))
  if "radar_self_suppress_near_cm" in updates:
    try:
      near_cm = int(updates.get("radar_self_suppress_near_cm"))
    except Exception:
      near_cm = int(current.get("radar_self_suppress_near_cm") or 80)
    merged["radar_self_suppress_near_cm"] = max(20, min(200, near_cm))
  if "radar_self_suppress_persist_s" in updates:
    try:
      persist_s = int(updates.get("radar_self_suppress_persist_s"))
    except Exception:
      persist_s = int(current.get("radar_self_suppress_persist_s") or 20)
    merged["radar_self_suppress_persist_s"] = max(5, min(120, persist_s))
  if "radar_self_suppress_jitter_cm" in updates:
    try:
      jitter_cm = int(updates.get("radar_self_suppress_jitter_cm"))
    except Exception:
      jitter_cm = int(current.get("radar_self_suppress_jitter_cm") or 15)
    merged["radar_self_suppress_jitter_cm"] = max(2, min(80, jitter_cm))

  if "radar_presence_mode" in updates:
    mode = str(updates.get("radar_presence_mode") or "raw").strip().lower()
    merged["radar_presence_mode"] = "derived" if mode == "derived" else "raw"

  if "radar_track_enabled" in updates:
    merged["radar_track_enabled"] = bool(updates.get("radar_track_enabled"))
  if "radar_track_match_gate_cm" in updates:
    try:
      match_gate_cm = int(updates.get("radar_track_match_gate_cm"))
    except Exception:
      match_gate_cm = int(current.get("radar_track_match_gate_cm") or 60)
    merged["radar_track_match_gate_cm"] = max(10, min(200, match_gate_cm))
  if "radar_track_expire_ms" in updates:
    try:
      expire_ms = int(updates.get("radar_track_expire_ms"))
    except Exception:
      expire_ms = int(current.get("radar_track_expire_ms") or 2000)
    merged["radar_track_expire_ms"] = max(500, min(10000, expire_ms))
  if "radar_track_confirm_hits" in updates:
    try:
      confirm_hits = int(updates.get("radar_track_confirm_hits"))
    except Exception:
      confirm_hits = int(current.get("radar_track_confirm_hits") or 2)
    merged["radar_track_confirm_hits"] = max(1, min(5, confirm_hits))
  if "radar_track_min_energy" in updates:
    try:
      min_energy = int(updates.get("radar_track_min_energy"))
    except Exception:
      min_energy = int(current.get("radar_track_min_energy") or 10)
    merged["radar_track_min_energy"] = max(0, min(100, min_energy))
  if "radar_track_jump_reject_cm" in updates:
    try:
      jump_reject_cm = int(updates.get("radar_track_jump_reject_cm"))
    except Exception:
      jump_reject_cm = int(current.get("radar_track_jump_reject_cm") or 250)
    merged["radar_track_jump_reject_cm"] = max(50, min(600, jump_reject_cm))

  if not bool(merged.get("radar_self_suppress_enabled")):
    merged["radar_presence_mode"] = "raw"
  if str(merged.get("radar_presence_mode") or "raw") == "derived":
    merged["radar_self_suppress_enabled"] = True

  for key in defaults:
    conn.execute(
      """
      INSERT INTO settings (key, value_json, updated_ts_utc)
      VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_ts_utc=excluded.updated_ts_utc
      """,
      (key, json.dumps(merged[key], separators=(",", ":"), ensure_ascii=False), now_utc),
    )
  return merged


def reset_settings_payload(conn: sqlite3.Connection, defaults: Dict[str, object], now_utc: str) -> Dict[str, object]:
  ensure_settings_table(conn)
  for key, value in defaults.items():
    conn.execute(
      """
      INSERT INTO settings (key, value_json, updated_ts_utc)
      VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_ts_utc=excluded.updated_ts_utc
      """,
      (key, json.dumps(value, separators=(",", ":"), ensure_ascii=False), now_utc),
    )
  return dict(defaults)
