import asyncio
import datetime
import logging
import socket
import textwrap
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


StatusProvider = Callable[[], Dict[str, object]]
ReportProvider = Callable[[int], List[str]]
ActionProvider = Callable[[], Dict[str, object]]

IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240


def telnet_process_iac(data: bytes) -> Tuple[bytes, bytes, bytes]:
  clean = bytearray()
  reply = bytearray()
  i = 0
  n = len(data)
  while i < n:
    b = data[i]
    if b != IAC:
      clean.append(b)
      i += 1
      continue

    if i + 1 >= n:
      break
    cmd = data[i + 1]

    if cmd == IAC:
      clean.append(IAC)
      i += 2
      continue

    if cmd in (DO, DONT, WILL, WONT):
      if i + 2 >= n:
        break
      opt = data[i + 2]
      if cmd == DO:
        reply.extend((IAC, WONT, opt))
      elif cmd == WILL:
        reply.extend((IAC, DONT, opt))
      i += 3
      continue

    if cmd == SB:
      j = i + 2
      found = False
      while j + 1 < n:
        if data[j] == IAC and data[j + 1] == SE:
          found = True
          j += 2
          break
        j += 1
      if not found:
        break
      i = j
      continue

    i += 2

  return bytes(clean), bytes(reply), data[i:]


MENU_TEXT = (
  "MENU\n"
  "1 STATUS\n"
  "2 ENV\n"
  "3 AIR\n"
  "4 PRES\n"
  "5 REPORT\n"
  "9 REFRESH\n"
  "* MENU\n"
  "0 EXIT"
)


@dataclass
class SessionState:
  authenticated: bool = False
  current_screen: str = "1"
  token_buf: str = ""


class HermesTelnetPortal:
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
    self._logger = logging.getLogger("hermes.telnet")
    self._client_count = 0
    self._total_connections = 0
    self._last_error: Optional[str] = None
    self._started_epoch: Optional[float] = None

  @property
  def client_count(self) -> int:
    return int(self._client_count)

  @property
  def total_connections(self) -> int:
    return int(self._total_connections)

  @property
  def last_error(self) -> Optional[str]:
    return self._last_error

  @property
  def started_epoch(self) -> Optional[float]:
    return self._started_epoch

  @property
  def is_running(self) -> bool:
    return self._server is not None

  async def start(self) -> None:
    if self._server is not None:
      return
    self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
    self._started_epoch = datetime.datetime.now(datetime.timezone.utc).timestamp()
    self._logger.info("TELNET_STARTUP: server bound host=%s port=%d", self._host, self._port)

  async def stop(self) -> None:
    if self._server is None:
      return
    self._server.close()
    await self._server.wait_closed()
    self._server = None
    self._logger.info("TELNET_SHUTDOWN: server closed")

  def _wrap_lines(self, lines: List[str], limit: int = 10) -> List[str]:
    out: List[str] = []
    for raw in lines[:limit]:
      line = str(raw).replace("\r", " ").replace("\n", " ").strip()
      if not line:
        out.append("")
        continue
      wrapped = textwrap.wrap(line, width=self._cols, break_long_words=True, break_on_hyphens=False)
      if not wrapped:
        out.append("")
      else:
        out.extend(wrapped)
      if len(out) >= limit:
        break
    return out[:limit]

  async def _send_block(self, writer: asyncio.StreamWriter, lines: List[str], limit: int = 10) -> None:
    payload = "\r\n".join(self._wrap_lines(lines, limit=limit)) + "\r\n"
    out = payload.encode("utf-8", errors="ignore")[:800]
    writer.write(out)
    await writer.drain()

  def _fmt_num(self, value: object, unit: str = "", decimals: int = 1) -> str:
    try:
      num = float(value)
      return f"{num:.{decimals}f}{unit}"
    except Exception:
      return f"n/a{unit}" if unit else "n/a"

  def _status_payload(self) -> Dict[str, object]:
    try:
      data = self._status_provider()
      return data if isinstance(data, dict) else {}
    except Exception as exc:
      self._last_error = str(exc)
      return {}

  def _screen_status(self) -> List[str]:
    s = self._status_payload()
    env = s.get("env") if isinstance(s.get("env"), dict) else {}
    air = s.get("air") if isinstance(s.get("air"), dict) else {}
    prs = s.get("presence") if isinstance(s.get("presence"), dict) else {}
    return [
      "STATUS",
      f"TEMP {self._fmt_num(env.get('temp_c'), 'C')}",
      f"HUM  {self._fmt_num(env.get('hum_pct'), '%')}",
      f"ECO2 {self._fmt_num(air.get('eco2_ppm'), 'ppm', 0)}",
      f"TVOC {self._fmt_num(air.get('tvoc_ppb'), 'ppb', 0)}",
      f"PRES {str(prs.get('summary') or 'n/a').upper()}",
      "9 REFRESH  * MENU",
      "0 EXIT",
    ]

  def _screen_env(self) -> List[str]:
    s = self._status_payload()
    env = s.get("env") if isinstance(s.get("env"), dict) else {}
    ts = str(env.get("ts_utc") or s.get("ts") or "")
    return [
      "ENV",
      f"TEMP {self._fmt_num(env.get('temp_c'), 'C')}",
      f"HUM  {self._fmt_num(env.get('hum_pct'), '%')}",
      f"AGE  {ts[-8:] if ts else 'n/a'}",
      "9 REFRESH  * MENU",
      "0 EXIT",
    ]

  def _screen_air(self) -> List[str]:
    s = self._status_payload()
    air = s.get("air") if isinstance(s.get("air"), dict) else {}
    ts = str(air.get("ts_utc") or s.get("ts") or "")
    return [
      "AIR",
      f"ECO2 {self._fmt_num(air.get('eco2_ppm'), 'ppm', 0)}",
      f"TVOC {self._fmt_num(air.get('tvoc_ppb'), 'ppb', 0)}",
      f"AGE  {ts[-8:] if ts else 'n/a'}",
      "9 REFRESH  * MENU",
      "0 EXIT",
    ]

  def _screen_presence(self) -> List[str]:
    s = self._status_payload()
    prs = s.get("presence") if isinstance(s.get("presence"), dict) else {}
    vis = s.get("visual_confirm") if isinstance(s.get("visual_confirm"), dict) else {}
    detect_cm = prs.get("detect_cm")
    try:
      detect_txt = f"{int(detect_cm)}cm" if detect_cm is not None else "n/a"
    except Exception:
      detect_txt = "n/a"
    yes = vis.get("yes")
    yes_txt = "YES" if yes is True else "NO" if yes is False else "n/a"
    return [
      "PRESENCE",
      f"STATE {str(prs.get('summary') or 'n/a').upper()}",
      f"DIST  {detect_txt}",
      f"VIS   {yes_txt}",
      "9 REFRESH  * MENU",
      "0 EXIT",
    ]

  def _screen_report(self) -> List[str]:
    rows: List[str] = []
    try:
      rows = self._report_provider(6) or []
    except Exception as exc:
      self._last_error = str(exc)
      rows = []
    clean = [str(item).replace("\r", " ").replace("\n", " ") for item in rows[:6]]
    if not clean:
      clean = ["REPORT n/a"]
    return ["REPORT", *clean[:6], "9 REFRESH  * MENU", "0 EXIT"]

  def _render_screen(self, screen: str) -> List[str]:
    if screen == "1":
      return self._screen_status()
    if screen == "2":
      return self._screen_env()
    if screen == "3":
      return self._screen_air()
    if screen == "4":
      return self._screen_presence()
    if screen == "5":
      return self._screen_report()
    return ["UNKNOWN", "* MENU", "0 EXIT"]

  async def _show_menu(self, writer: asyncio.StreamWriter) -> None:
    await self._send_block(writer, ["HERMES TELNET", MENU_TEXT], limit=10)

  async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    self._client_count += 1
    self._total_connections += 1
    state = SessionState(authenticated=not bool(self._token), current_screen="1", token_buf="")
    recv_buf = b""
    cmd_buf = bytearray()
    try:
      now_local = datetime.datetime.now().astimezone().strftime("%H:%M:%S")
      host = socket.gethostname()
      await self._send_block(writer, ["HERMES", f"HOST {host}", f"TIME {now_local}"], limit=6)
      if self._token:
        await self._send_block(writer, ["TOKEN?"], limit=2)
      else:
        await self._show_menu(writer)

      while not reader.at_eof():
        raw = await reader.read(256)
        if not raw:
          break

        recv_buf += raw
        clean, reply, remaining = telnet_process_iac(recv_buf)
        recv_buf = remaining
        if reply:
          writer.write(reply)
          await writer.drain()
        if clean:
          cmd_buf.extend(clean)

        if not cmd_buf:
          continue
        if len(cmd_buf) > 128:
          del cmd_buf[:-128]

        process_bytes = bytes(cmd_buf)
        cmd_buf.clear()
        for b in process_bytes:
          if b == 0:
            continue

          if self._token and not state.authenticated:
            if b in (10, 13):
              if state.token_buf:
                if state.token_buf == self._token:
                  state.authenticated = True
                  await self._send_block(writer, ["AUTH OK"], limit=2)
                  await self._show_menu(writer)
                else:
                  await self._send_block(writer, ["AUTH FAIL", "TRY AGAIN"], limit=4)
                state.token_buf = ""
              continue

            ch = chr(b)
            state.token_buf += ch
            if state.token_buf == self._token:
              state.authenticated = True
              await self._send_block(writer, ["AUTH OK"], limit=2)
              await self._show_menu(writer)
              state.token_buf = ""
              continue
            if len(state.token_buf) >= max(len(self._token), 16):
              await self._send_block(writer, ["AUTH FAIL", "TRY AGAIN"], limit=4)
              state.token_buf = ""
            continue

          if b in (10, 13, 9, 32):
            continue
          ch = chr(b)
          if ch not in {"0", "1", "2", "3", "4", "5", "9", "*"}:
            await self._send_block(writer, ["INVALID", "* MENU", "0 EXIT"], limit=4)
            continue

          if ch == "0":
            await self._send_block(writer, ["BYE"], limit=2)
            return
          if ch == "*":
            await self._show_menu(writer)
            continue
          if ch in {"1", "2", "3", "4", "5"}:
            state.current_screen = ch
          screen_lines = self._render_screen(state.current_screen)
          await self._send_block(writer, screen_lines, limit=10)

        if reader.at_eof():
          continue
    except (ConnectionResetError, BrokenPipeError):
      pass
    except Exception as exc:
      self._last_error = str(exc)
      self._logger.exception("TELNET_CLIENT: handler error")
    finally:
      self._client_count = max(0, self._client_count - 1)
      try:
        writer.close()
        await writer.wait_closed()
      except Exception:
        pass
