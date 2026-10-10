"""Find a free loopback TCP port for the local development launcher."""

import errno
import socket
import sys


def find_port(start: int) -> int:
    if not 1 <= start <= 65535:
        raise ValueError("API_PORT must be between 1 and 65535")
    for port in range(start, 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    continue
                raise
            return port
    raise RuntimeError(f"No free API port found from {start} through 65535")


if __name__ == "__main__":
    try:
        print(find_port(int(sys.argv[1])))
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(f"Cannot select development API port: {exc}")
