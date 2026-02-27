import socket
import sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8023

def main():
    print(f"Connecting to {HOST}:{PORT}...")
    with socket.create_connection((HOST, PORT)) as s:
        s.settimeout(5)
        while True:
            data = s.recv(4096)
            if not data:
                break
            print(data.decode("utf-8", errors="ignore"), end="")
            inp = input()
            s.sendall((inp + "\r\n").encode("utf-8"))

if __name__ == "__main__":
    main()
