import os
import socket
import sys

SOCK_PATH = "/tmp/hermesd.sock"

USAGE = """Usage:
  python3 client.py ping
  python3 client.py status
    python3 client.py oled-status
  python3 client.py send "OLED,PAGE,NEXT"
  python3 client.py stop
"""

def main():
    if len(sys.argv) < 2:
        print(USAGE.strip())
        return 2

    cmd = sys.argv[1].upper()
    rest = sys.argv[2:]

    if cmd == "OLED-STATUS":
        line = "SEND OLED,STATUS"
    elif cmd == "SEND":
        if not rest:
            print("ERR no payload")
            return 2
        payload = " ".join(rest)
        line = f"SEND {payload}"
    else:
        line = cmd

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCK_PATH)
    except Exception as e:
        print(f"ERR connect failed: {e}")
        return 1

    with sock:
        sock.sendall((line + "\n").encode("utf-8"))
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)

    out = b"".join(chunks).decode(errors="replace").strip()
    if out:
        print(out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
