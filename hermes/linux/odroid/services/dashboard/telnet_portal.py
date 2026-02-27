import asyncio
import datetime
import os
import socket
import textwrap
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional


StatusProvider = Callable[[], Dict[str, object]]
ReportProvider = Callable[[int], List[str]]
ActionProvider = Callable[[], Dict[str, object]]


@dataclass
class SessionState:
  authenticated: bool = False


class HermesTelnetPortal:
  import logging
  logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
      logging.FileHandler('/tmp/hermes_telnet_debug.log'),
      logging.StreamHandler()
    ]
  )
  logging.getLogger("hermes.telnet").info("[TELNET DEBUG] telnet_portal.py module loaded.")
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
    cols: int = 30,
  ) -> None:
    self._status_provider = status_provider
    self._report_provider = report_provider
    self._snapshot_action = snapshot_action
    self._confirm_action = confirm_action
    self._host = host
    self._port = int(port)
    self._token = str(token or "").strip()
    self._cols = max(20, min(120, int(cols or 30)))
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
    lines: List[str] = []
    for raw_line in str(text).split("\n"):
      line = raw_line.rstrip("\r")
      if not line:
        lines.append("")
        continue
      wrapped = textwrap.wrap(line, width=self._cols, break_long_words=True, break_on_hyphens=False)
      lines.extend(wrapped if wrapped else [""])
    writer.write(("\r\n".join(lines)).encode("utf-8", errors="ignore"))
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
      f"updated: {text(status.get('ts'))}",
      f"presence: {text(presence.get('summary'))}",
      f"visual: {visual_label} motion={text(visual.get('motion_score'))}",
      f"temp: {text(env.get('temp_c'))}C",
      f"hum: {text(env.get('hum_pct'))}%",
      f"air: eco2={text(air.get('eco2_ppm'))} tvoc={text(air.get('tvoc_ppb'))}",
      f"camera: {text(camera.get('status'))}",
      f"snap: {text(camera.get('last_snapshot_ts'))}",
    ]

  async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    logger = logging.getLogger("hermes.telnet")
    logger.info("[TELNET DEBUG] _handle_client called for new connection.")
    state = SessionState(authenticated=not bool(self._token))
    logger = logging.getLogger("hermes.telnet")
    logger.info("[TELNET DEBUG] Telnet handler started.")
    try:
      now_local = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
      host = socket.gethostname()
      short_time = now_local.split("T", 1)[-1] if "T" in now_local else now_local
      await self._send(writer, "HERMES TELNET\n")
      await self._send(writer, f"host: {host}\n")
      await self._send(writer, f"time: {short_time}\n")
      if self._token:
        await self._send(writer, f"auth: {'yes' if state.authenticated else 'no'}\n")
      else:
        await self._send(writer, "auth: disabled\n")
      await self._send(writer, "type: help\n")
      await self._prompt(writer)

      input_buffer = ""
      while not reader.at_eof():
        raw = await reader.read(1)
        if not raw:
          break
        char = self._strip_telnet_controls(raw)
        if not char:
          continue
        if char in ("\r", "\n"):
          line = input_buffer.strip()
          input_buffer = ""
          logger.info(f"[TELNET DEBUG] Received line: {repr(line)}")
          if not line:
            await self._prompt(writer)
            continue
          parts = line.split()
          command = parts[0].lower()

          # Allow just the token as a command for authentication
          if self._token and not state.authenticated and line.strip() == self._token:
            logger.info(f"[TELNET DEBUG] Token match: {repr(line)} == {repr(self._token)}")
            state.authenticated = True
            await self._send(writer, "auth ok\n")
            await self._prompt(writer)
            continue

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
        else:
          # Not a line ending, accumulate
          input_buffer += char
    except Exception as exc:
      logger.error(f"[TELNET DEBUG] Exception: {exc}")
    finally:
      try:
        writer.close()
        await writer.wait_closed()
      except Exception:
        pass
