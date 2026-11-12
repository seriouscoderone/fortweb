"""Reading a mad-discovery source: the address and SAID rules.

The rules are mad-discovery's (src/address.ts, src/said.ts). Keep them identical:
python/test_sourcing.py runs mad-discovery's own fixtures against this module.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import urlsplit

SAID_PATTERN = re.compile(r"^E[A-P][A-Za-z0-9_-]{42}$")
FILL = b"#" * 44
FILE_BYTES = 1_000_000
REQUEST_MS = 10_000
READ_MS = 60_000
DEFINITIONS_PER_READ = 50
MAX_ADDRESS = 512
JSON_DEPTH = 64
_IPV4 = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
# The last label of a public name starts with a letter, so a browser never reads the host as IPv4.
_TOP_LABEL = re.compile(r"^[a-z][a-z0-9-]*$")
# Only characters a browser keeps as typed, so the shown address is the fetched one.
_PATH = re.compile(r"^[A-Za-z0-9._~/-]*$")
_ENDING = "/source.json"


class SourceFault(Exception):
    """A source could not be read. `code` is stable; `message` is for the person."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def is_said(text) -> bool:
    return isinstance(text, str) and SAID_PATTERN.match(text) is not None


def encode_said(digest: bytes) -> str:
    """One zero byte, then the 32 digest bytes, as base64url; the first character (always A) becomes E."""
    if len(digest) != 32:
        raise ValueError("a Blake3-256 digest is 32 bytes")
    return "E" + base64.urlsafe_b64encode(b"\x00" + digest).decode("ascii")[1:]


def check_said(served: bytes, said: str, *, digest) -> bool:
    """mad-discovery's byte check: present; every occurrence becomes 44 '#'; the digest is the SAID; it rebuilds."""
    if not is_said(said):
        return False
    needle = said.encode("ascii")
    if needle not in served:
        return False
    hashed = served.replace(needle, FILL)
    if encode_said(digest(hashed)) != said:
        return False
    return hashed.replace(FILL, needle) == served


def _bad():
    return SourceFault("BAD_ADDRESS", "That is not an address the wallet can open.")


def normalize_address(text: str) -> dict:
    """A host, a host with a path, or a full address -> the canonical https address ending /source.json."""
    raw = str(text or "").strip()
    if not raw or any(ch.isspace() for ch in raw) or len(raw) > MAX_ADDRESS:
        raise _bad()
    if "://" not in raw:
        if raw.startswith("/") or ":" in raw.split("/", 1)[0]:
            raise _bad()
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.query or parts.fragment or "?" in raw or "#" in raw:
        raise _bad()
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _bad()
    try:
        port = parts.port
    except ValueError:
        raise _bad() from None
    host = (parts.hostname or "").lower()
    # ":" in the netloc also refuses IPv6 literals ([::1]) and an empty port (example.org:).
    if port is not None or ":" in parts.netloc or not host or host.endswith("."):
        raise _bad()
    if _IPV4.match(host) or host == "localhost" or host.endswith(".localhost"):
        raise _bad()
    if not _HOST.match(host):
        raise _bad()
    labels = host.split(".")
    if len(labels) < 2 or not _TOP_LABEL.match(labels[-1]):
        raise _bad()
    path = parts.path or "/"
    if not _PATH.match(path) or "//" in path or any(seg in (".", "..") for seg in path.split("/")):
        raise _bad()
    if not path.endswith(_ENDING):
        path = path.rstrip("/") + _ENDING
    address = f"https://{host}{path}"
    if len(address) > MAX_ADDRESS:
        raise _bad()
    return {"address": address, "base": address[: -len("source.json")], "host": host}


def definition_address(base: str, said: str) -> str:
    if not is_said(said):
        raise ValueError(f"not a SAID: {said}")
    if not base.endswith("/"):
        raise ValueError("a base ends with /")
    return f"{base}definitions/{said}.json"
