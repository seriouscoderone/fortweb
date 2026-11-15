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
import http.server
import mimetypes
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
    """The file a request target names, or None when it names nothing this script serves."""
    parts = PurePosixPath(unquote(urlsplit(target).path)).parts
    if len(parts) < 2 or parts[0] != "/" or parts[1] not in SERVED:
        return None
    if any(part in (".", "..") or "\\" in part or "\x00" in part for part in parts[1:]):
        return None
    try:
        found = root.joinpath(*parts[1:]).resolve(strict=True)
    except OSError:
        return None
    if not found.is_file() or root not in found.parents:
        return None
    return found


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
            self._refuse(400, "Bad request.")
        elif host == f"localhost:{port}":
            # One address, one wallet: localhost would be a second, empty one.
            self._send_on(f"http://{own}{self.path}")
        elif host != own:
            self._refuse(421, f"This wallet answers only at http://{own}/.")
        elif urlsplit(self.path).path == "/":
            self._send_on(f"http://{own}/{START_PAGE}")
        else:
            found = resolve(self.server.wallet_root, self.path)
            if found is None:
                self._refuse(404, "Not found.")
            else:
                self._send(200, found.read_bytes(), TYPES.get(found.suffix.lower())
                           or mimetypes.guess_type(found.name)[0] or "application/octet-stream", with_body)

    def _send(self, status: int, body: bytes, content_type: str, with_body: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # As the phone's shell and the wallet's development server send them.
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _refuse(self, status: int, message: str) -> None:
        self._send(status, (message + "\n").encode(), "text/plain; charset=utf-8")

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

    def __init__(self, root: Path, port: int):
        self.wallet_root = root.resolve()
        super().__init__((HOST, port), Handler)


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
    except OSError:
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
