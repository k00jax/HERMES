import os
import socket
import sys

SOCK_PATH = "/tmp/hermesd.sock"
SOCKET_TIMEOUT_SECS = 2.0
MAX_RESPONSE_BYTES = 65536

USAGE = """Usage:
  python3 client.py ping
  python3 client.py status
    python3 client.py health
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
    elif cmd == "HEALTH":
        line = "HEALTH"
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
        sock.settimeout(SOCKET_TIMEOUT_SECS)
        sock.connect(SOCK_PATH)
    except socket.timeout:
        print(f"ERR connect timeout after {SOCKET_TIMEOUT_SECS:.1f}s")
        return 1
    except Exception as e:
        print(f"ERR connect failed: {e}")
        return 1

    with sock:
        try:
            sock.sendall((line + "\n").encode("utf-8"))
        except socket.timeout:
            print(f"ERR send timeout after {SOCKET_TIMEOUT_SECS:.1f}s")
            return 1
        except Exception as e:
            print(f"ERR send failed: {e}")
            return 1

        response = bytearray()
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                print(f"ERR response timeout after {SOCKET_TIMEOUT_SECS:.1f}s")
                return 1
            except Exception as e:
                print(f"ERR recv failed: {e}")
                return 1
            if not data:
                break
            response.extend(data)
            if b"\n" in data:
                break
            if len(response) >= MAX_RESPONSE_BYTES:
                print("ERR response too large")
                return 1

    out = bytes(response).decode(errors="replace").strip()
    if out:
        print(out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
