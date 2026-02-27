import asyncio
import datetime
import os
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional


StatusProvider = Callable[[], Dict[str, object]]
ReportProvider = Callable[[int], List[str]]
ActionProvider = Callable[[], Dict[str, object]]


@dataclass
class SessionState:
  authenticated: bool = False


class HermesTelnetPortal:
  """
  HERMES local telnet portal (line-based, VT100-ish).

  Quick local test instructions:
    TELNET_ENABLE=true TELNET_TOKEN=hermes TELNET_BIND_LAN=true python3 app.py
    telnet <odroid_ip> 8023
  """

  def __init__(
    self,
    *,
    status_provider: StatusProvider,
    report_provider: ReportProvider,
    snapshot_action: ActionProvider,
    confirm_action: ActionProvider,
    host: str,
    port: int,
    token: str,
  ) -> None:
    self._status_provider = status_provider
    self._report_provider = report_provider
    self._snapshot_action = snapshot_action
    self._confirm_action = confirm_action
    self._host = host
    self._port = int(port)
    self._token = str(token or "").strip()
    self._server: Optional[asyncio.AbstractServer] = None

  async def start(self) -> None:
    if self._server is not None:
      return
    self._server = await asyncio.start_server(self._handle_client, self._host, self._port)

  async def stop(self) -> None:
    if self._server is None:
      return
    self._server.close()
    await self._server.wait_closed()
    self._server = None

  @property
  def is_running(self) -> bool:
    return self._server is not None

  async def _send(self, writer: asyncio.StreamWriter, text: str) -> None:
    writer.write(text.replace("\n", "\r\n").encode("utf-8", errors="ignore"))
    await writer.drain()

  def _strip_telnet_controls(self, data: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(data):
      b = data[i]
      if b == 255:
        i += 1
        if i < len(data):
          cmd = data[i]
          i += 1
          if cmd in (251, 252, 253, 254):
            i += 1
        continue
      out.append(b)
      i += 1
    text = out.decode("utf-8", errors="ignore")
    text = text.replace("\r", "\n")
    return "\n".join([line.strip() for line in text.split("\n") if line.strip()])

  async def _prompt(self, writer: asyncio.StreamWriter) -> None:
    await self._send(writer, "hermes> ")

  def _require_auth(self, command: str) -> bool:
    if not self._token:
      return False
    allowed = {"help", "token", "quit", "exit", "clear"}
    return command not in allowed

  def _format_status_lines(self, status: Dict[str, object]) -> List[str]:
    presence = status.get("presence") if isinstance(status.get("presence"), dict) else {}
    visual = status.get("visual_confirm") if isinstance(status.get("visual_confirm"), dict) else {}
    env = status.get("env") if isinstance(status.get("env"), dict) else {}
    air = status.get("air") if isinstance(status.get("air"), dict) else {}
    camera = status.get("camera") if isinstance(status.get("camera"), dict) else {}

    def text(value: object) -> str:
      if value is None:
        return "n/a"
      value_s = str(value).strip()
      return value_s if value_s else "n/a"

    visual_yes = visual.get("yes")
    visual_label = "YES" if visual_yes is True else "NO" if visual_yes is False else "n/a"

    return [
      "HERMES STATUS",
      f"Last Updated: {text(status.get('ts'))}",
      f"Presence: {text(presence.get('summary'))}",
      f"Visual Confirm: {visual_label} | Motion: {text(visual.get('motion_score'))}",
      f"Temp: {text(env.get('temp_c'))} C",
      f"Humidity: {text(env.get('hum_pct'))} %",
      f"Air: eCO2 {text(air.get('eco2_ppm'))} ppm | TVOC {text(air.get('tvoc_ppb'))} ppb",
      f"Camera: {text(camera.get('status'))}",
      f"Last Snapshot: {text(camera.get('last_snapshot_ts'))}",
    ]

  async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    state = SessionState(authenticated=not bool(self._token))
    try:
      now_local = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
      host = socket.gethostname()
      await self._send(writer, "HERMES TELNET PORTAL\n")
      await self._send(writer, f"Host: {host} | Local Time: {now_local}\n")
      if self._token:
        await self._send(writer, f"Auth: {'yes' if state.authenticated else 'no'}\n")
      else:
        await self._send(writer, "Auth: disabled (TELNET_TOKEN not set)\n")
      await self._send(writer, "type help\n")
      await self._prompt(writer)

      while not reader.at_eof():
        raw = await reader.readline()
        if not raw:
          break
        line = self._strip_telnet_controls(raw)
        if not line:
          await self._prompt(writer)
          continue

        parts = line.split()
        command = parts[0].lower()

        if command == "clear":
          await self._send(writer, "\x1b[2J\x1b[H")
          await self._prompt(writer)
          continue

        if command in {"quit", "exit"}:
          await self._send(writer, "bye\n")
          break

        if command == "help":
          await self._send(
            writer,
            "Commands:\n"
            "  help               show commands\n"
            "  status             show status summary\n"
            "  report             show recent events\n"
            "  snapshot           trigger camera snapshot\n"
            "  confirm            trigger visual confirm capture\n"
            "  token <value>      authenticate session\n"
            "  clear              clear screen\n"
            "  quit | exit        close connection\n",
          )
          await self._prompt(writer)
          continue

        if command == "token":
          if not self._token:
            await self._send(writer, "token auth not required\n")
          elif len(parts) < 2:
            await self._send(writer, "usage: token <value>\n")
          elif " ".join(parts[1:]) == self._token:
            state.authenticated = True
            await self._send(writer, "auth ok\n")
          else:
            await self._send(writer, "auth failed\n")
          await self._prompt(writer)
          continue

        if self._require_auth(command) and not state.authenticated:
          await self._send(writer, "unauthorized: provide token first\n")
          await self._prompt(writer)
          continue

        if command == "status":
          try:
            status = self._status_provider()
            await self._send(writer, "\n".join(self._format_status_lines(status)) + "\n")
          except Exception as exc:
            await self._send(writer, f"status error: {exc}\n")
          await self._prompt(writer)
          continue

        if command == "report":
          try:
            lines = self._report_provider(5)
            if not lines:
              await self._send(writer, "not implemented\n")
            else:
              await self._send(writer, "\n".join(lines) + "\n")
          except Exception as exc:
            await self._send(writer, f"report error: {exc}\n")
          await self._prompt(writer)
          continue

        if command == "snapshot":
          try:
            result = self._snapshot_action()
            ok = bool(result.get("ok"))
            ts = str(result.get("ts") or "n/a")
            detail = str(result.get("error") or "ok")
            await self._send(writer, f"snapshot {'ok' if ok else 'fail'} ts={ts} detail={detail}\n")
          except Exception as exc:
            await self._send(writer, f"snapshot error: {exc}\n")
          await self._prompt(writer)
          continue

        if command == "confirm":
          try:
            result = self._confirm_action()
            ok = bool(result.get("ok"))
            raw = str(result.get("raw") or result.get("error") or "")
            await self._send(writer, f"confirm {'ok' if ok else 'fail'} {raw}\n")
          except Exception as exc:
            await self._send(writer, f"confirm error: {exc}\n")
          await self._prompt(writer)
          continue

        await self._send(writer, "unknown command (type help)\n")
        await self._prompt(writer)
    except Exception:
      pass
    finally:
      try:
        writer.close()
        await writer.wait_closed()
      except Exception:
        pass
