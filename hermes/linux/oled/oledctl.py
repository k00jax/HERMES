import os
import subprocess
import sys

CLIENT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "logger", "client.py")
)

USAGE = """Usage:
  python3 oledctl.py next
  python3 oledctl.py prev
  python3 oledctl.py page <n>
  python3 oledctl.py stack user|debug
  python3 oledctl.py focus on|off|toggle
  python3 oledctl.py msg env|esp "text"
"""

def run_send(payload: str) -> int:
    return subprocess.call([sys.executable, CLIENT, "send", payload])

def main() -> int:
    if len(sys.argv) < 2:
        print(USAGE.strip())
        return 2

    cmd = sys.argv[1].lower()

    if cmd == "next":
        return run_send("OLED,PAGE,NEXT")
    if cmd == "prev":
        return run_send("OLED,PAGE,PREV")
    if cmd == "page":
        if len(sys.argv) < 3:
            print("ERR missing page")
            return 2
        return run_send(f"OLED,PAGE,{sys.argv[2]}")
    if cmd == "stack":
        if len(sys.argv) < 3:
            print("ERR missing stack")
            return 2
        stack = sys.argv[2].upper()
        if stack not in {"USER", "DEBUG"}:
            print("ERR stack must be user or debug")
            return 2
        return run_send(f"OLED,STACK,{stack}")
    if cmd == "focus":
        if len(sys.argv) < 3:
            print("ERR missing focus mode")
            return 2
        mode = sys.argv[2].upper()
        if mode not in {"ON", "OFF", "TOGGLE"}:
            print("ERR focus must be on, off, or toggle")
            return 2
        return run_send(f"OLED,FOCUS,{mode}")
    if cmd == "msg":
        if len(sys.argv) < 4:
            print("ERR missing msg target/text")
            return 2
        target = sys.argv[2].upper()
        if target not in {"ENV", "ESP"}:
            print("ERR msg target must be env or esp")
            return 2
        text = " ".join(sys.argv[3:])
        return run_send(f"OLED,MSG,{target},{text}")

    print(USAGE.strip())
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
