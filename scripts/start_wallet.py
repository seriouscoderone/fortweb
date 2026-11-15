#!/usr/bin/env python3
"""Serves the wallet to a browser on this computer.

    python3 start-wallet.py

The wallet opens at http://127.0.0.1:8791/ in the default browser. A browser keeps a wallet's storage under the
address it was opened at, so the address is fixed: when the port is taken this script says so and stops, because
another port would be an empty wallet. It listens on this computer only, serves only the wallet's own files, and
answers only requests made to its own address.
"""
from __future__ import annotations

import argparse
import errno
import http.server
import mimetypes
import socketserver
import sys
import webbrowser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

HOST = "127.0.0.1"
PORT = 8791  # not 8765: the wallet's development server uses that, and one address is one wallet
START_PAGE = "app/index.html"
# What a wallet package holds at its top. Nothing else in the folder is served.
SERVED = ("app", "vendor", "wheels", "pyscript-ci.toml", "runtime-closure.json")
TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".py": "text/x-python; charset=utf-8",
    ".svg": "image/svg+xml",
    ".toml": "application/toml; charset=utf-8",
    ".wasm": "application/wasm",
    ".whl": "application/octet-stream",
}


def resolve(root: Path, target: str) -> Path | None:
    """The file a request target names, or None when it names nothing this script serves. It never raises."""
    try:
        path = unquote(urlsplit(target).path)
        if path.endswith("/"):  # a folder's name, never a file's
            return None
        parts = PurePosixPath(path).parts
        if len(parts) < 2 or parts[0] != "/" or parts[1] not in SERVED:
            return None
        # ":" too: under Windows path rules "C:.." is ".." on that drive and "C:name" is "name" at the folder's top.
        if any(part in (".", "..") or "\\" in part or "\x00" in part or ":" in part for part in parts[1:]):
            return None
        found = root.joinpath(*parts[1:]).resolve(strict=True)
        if not found.is_file() or root not in found.parents:
            return None
        # After links are followed the file must still lie in the entry the target named. File systems on
        # Windows and macOS ignore case, so the names are compared without it.
        if found.relative_to(root).parts[0].lower() != parts[1].lower():
            return None
        return found
    except (OSError, ValueError, RuntimeError):  # a link loop is RuntimeError before Python 3.13
        return None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "FortWallet"
    sys_version = ""

    def do_GET(self) -> None:
        self._answer(with_body=True)

    def do_HEAD(self) -> None:
        self._answer(with_body=False)

    def _answer(self, with_body: bool) -> None:
        port = self.server.server_address[1]
        own = f"{HOST}:{port}"
        host = self.headers.get("Host", "")
        if not self.path.startswith("/"):
            self._refuse(400, "Bad request.", with_body)
        elif host == f"localhost:{port}":
            # One address, one wallet: localhost would be a second, empty one.
            self._send_on(f"http://{own}{self.path}")
        elif host != own:
            self._refuse(421, f"This wallet answers only at http://{own}/.", with_body)
        elif urlsplit(self.path).path == "/":
            self._send_on(f"http://{own}/{START_PAGE}")
        else:
            found = resolve(self.server.wallet_root, self.path)
            if found is None:
                self._refuse(404, "Not found.", with_body)
            else:
                try:
                    body = found.read_bytes()
                except OSError:  # removed, or made unreadable, since it was found
                    self._refuse(404, "Not found.", with_body)
                    return
                self._send(200, body, TYPES.get(found.suffix.lower())
                           or mimetypes.guess_type(found.name)[0] or "application/octet-stream", with_body)

    def _send(self, status: int, body: bytes, content_type: str, with_body: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # The three the phone's shell sends with the wallet's own files (the development server sends the first
        # two). The third matters most here, where other pages share the browser: no other site can load the
        # wallet's files as scripts or images.
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _refuse(self, status: int, message: str, with_body: bool) -> None:
        self._send(status, (message + "\n").encode(), "text/plain; charset=utf-8", with_body)

    def _send_on(self, location: str) -> None:
        self.send_response(307)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (the base class's name)
        pass  # quiet: a wallet's requests are nobody's business, and the terminal stays readable


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, address reuse would let a second program listen on the same port.
    allow_reuse_address = sys.platform != "win32"
    # Port sharing would let a second program listen beside this one and take some of the wallet's requests.
    # Python 3.11 and later read this (off by default); before, it was never on.
    allow_reuse_port = False

    def __init__(self, root: Path, port: int):
        self.wallet_root = root.resolve()
        super().__init__((HOST, port), Handler)

    def handle_error(self, request, client_address) -> None:
        # A browser that drops a connection is no fault, and the window the person leaves open stays quiet.
        # Any other error keeps Python's ordinary report, so a real fault stays visible.
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)

    def server_bind(self) -> None:
        # HTTPServer's own server_bind looks this computer's name up, which can take seconds on macOS and which
        # nothing here uses: the server's name is its address.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = HOST, self.server_address[1]


def missing(root: Path) -> list[str]:
    return [name for name in SERVED if not (root / name).exists()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the wallet to a browser on this computer.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent,
                        help="the unzipped wallet folder (default: the folder this script is in)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    absent = missing(root)
    if absent:
        print(f"This folder is not a wallet package: it has no {', '.join(absent)}.", file=sys.stderr)
        return 2
    address = f"http://{HOST}:{PORT}/"
    try:
        server = Server(root, PORT)
    except OSError as error:
        if error.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)):
            # Not another program: a port the system keeps for itself, for one. Closing a program would not help.
            print(f"This computer did not let the script listen on port {PORT}: {error.strerror or error}.\n"
                  "The wallet keeps its storage under one address, so this script does not pick another port.",
                  file=sys.stderr)
            return 1
        print(f"Port {PORT} on this computer is in use, perhaps by another copy of this wallet.\n"
              "The wallet keeps its storage under one address, so this script does not pick another port.\n"
              "Close the other program and start again. Until this script says the wallet is running,\n"
              f"do not open {address}: whatever holds the port would be answering there.", file=sys.stderr)
        return 1
    print(f"The wallet is at {address}\nLeave this window open while you use it. Press Ctrl+C to stop.\n"
          "If it opens in a browser other than Chrome or Edge, copy the address into one of those.", flush=True)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
