"""Reading a mad-discovery source: address and SAID rules, source document rules, role derivation, and the read.

The rules are mad-discovery's (src/address.ts, src/said.ts, src/document.ts, src/source.ts, src/limits.ts). Keep
them identical: python/test_sourcing.py runs mad-discovery's own fixtures against this module. `read_source` puts
them together; the network, the digest and the clock are passed in, so this module imports no `js` and opens nothing.
"""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import urlsplit

SAID_PATTERN = re.compile(r"^E[A-P][A-Za-z0-9_-]{42}$")
FILL = b"#" * 44
FILE_BYTES = 1_000_000
REQUEST_MS = 10_000
READ_MS = 60_000
DEFINITIONS_PER_READ = 50
# Wallet-only: the bytes one read may receive, over the source document and every definition fetched. A definition
# whose fetch fails counts with the bytes read before it failed. The total is checked before each fetch, not during
# one, so a read can pass this by at most one file (FILE_BYTES, plus the last chunk read of a file that turns out
# larger). That is accepted.
READ_BYTES = 4_000_000
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
    """A source could not be read. `code` is stable; `message` is for the person.

    `received` is the number of body bytes the fetch had read before the fault: 0 when it came before any.
    """

    def __init__(self, code: str, message: str, *, received: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.received = received


def is_said(text) -> bool:
    return isinstance(text, str) and SAID_PATTERN.fullmatch(text) is not None


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


def _https_parts(raw: str) -> tuple[str, str]:
    """The host and path of a plain https address, under the address rules; raises BAD_ADDRESS."""
    if any(ch.isspace() for ch in raw) or len(raw) > MAX_ADDRESS:
        raise _bad()
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
    return host, path


def normalize_address(text: str) -> dict:
    """A host, a host with a path, or a full address -> the canonical https address ending /source.json."""
    raw = str(text or "").strip()
    if not raw:
        raise _bad()
    if "://" not in raw:
        if raw.startswith("/") or ":" in raw.split("/", 1)[0]:
            raise _bad()
        raw = "https://" + raw
    host, path = _https_parts(raw)
    if not path.endswith(_ENDING):
        path = path.rstrip("/") + _ENDING
    address = f"https://{host}{path}"
    if len(address) > MAX_ADDRESS:
        raise _bad()
    return {"address": address, "base": address[: -len("source.json")], "host": host}


def _https_address(text: str) -> str | None:
    """A plain https address as given (an oobi), canonical, without the /source.json ending; None if it breaks a rule."""
    try:
        host, path = _https_parts(text)
    except SourceFault:
        return None
    address = f"https://{host}{path}"
    return address if len(address) <= MAX_ADDRESS else None


def source_host(address) -> str:
    """The host of a valid https address, for a message to the person; "The source" for anything else. Never raises."""
    if not isinstance(address, str):
        return "The source"
    try:
        host, _ = _https_parts(address)
    except (SourceFault, ValueError):
        return "The source"
    return host


def definition_address(base: str, said: str) -> str:
    if not is_said(said):
        raise ValueError(f"not a SAID: {said}")
    if not base.endswith("/"):
        raise ValueError("a base ends with /")
    return f"{base}definitions/{said}.json"


# A source document (mad-discovery src/document.ts, src/source.ts, src/scan.ts parseBounded).

AID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
CONTROL = re.compile(r"[\u0000-\u001f\u007f-\u009f]")
PUBLICATION_NAME = 200
PUBLICATION_DESCRIPTION = 2000
PUBLICATION_TAGS = 32
PUBLICATION_TAG = 64
PUBLICATION_COLLECTION = 200
_JSON_STRING = re.compile(rb'"(?:[^"\\]|\\.)*"?', re.DOTALL)
_BRACKET = re.compile(rb"[\[\]{}]")


def _depth(raw: bytes) -> int:
    """Bracket nesting outside strings, counted on the bytes before anything is parsed."""
    depth = deepest = 0
    for mark in _BRACKET.findall(_JSON_STRING.sub(b"", raw)):
        if mark in b"[{":
            depth += 1
            deepest = max(deepest, depth)
        else:
            depth -= 1
    return deepest


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")


def _well_formed(value) -> None:
    """ValueError when a string in `value`, member name or value, holds a lone surrogate (Python's json keeps the
    escape "\\ud83d" as one): such text cannot be stored or sent as UTF-8. The nesting is already bounded."""
    if isinstance(value, str):
        value.encode("utf-8")  # UnicodeEncodeError is a ValueError
    elif isinstance(value, list):
        for item in value:
            _well_formed(item)
    elif isinstance(value, dict):
        for name, item in value.items():
            name.encode("utf-8")
            _well_formed(item)


def _parse_bounded(raw: bytes):
    """JSON.parse's reading of the bytes, refused before it starts when nested deeper than JSON_DEPTH, and refused
    when any string in it is not well-formed Unicode."""
    if _depth(raw) > JSON_DEPTH:
        raise ValueError("nested too deep")
    parsed = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    _well_formed(parsed)
    return parsed


def _units(text: str) -> int:
    """A string's length as JavaScript counts it, in UTF-16 code units."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _text(value, limit: int) -> bool:
    return isinstance(value, str) and _units(value) <= limit and CONTROL.search(value) is None


def _publication(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    if not is_said(item.get("target")):
        return None
    for name, limit in (("name", PUBLICATION_NAME), ("description", PUBLICATION_DESCRIPTION),
                        ("collection", PUBLICATION_COLLECTION)):
        if not _text(item.get(name), limit):
            return None
    tags = item.get("tags")
    if not isinstance(tags, list) or len(tags) > PUBLICATION_TAGS:
        return None
    if not all(_text(tag, PUBLICATION_TAG) for tag in tags):
        return None
    if "by" in item and not (isinstance(item["by"], str) and AID_PATTERN.fullmatch(item["by"])):
        return None
    return {"target": item["target"], "name": item["name"], "description": item["description"],
            "tags": list(tags), "collection": item["collection"], "by": item.get("by")}


def read_source_document(raw: bytes, address: str) -> dict:
    """A fetched source document, read under mad-discovery's rules.

    The whole document is refused when it is not bounded JSON, its top level is not an object, `source` is not
    `address`, or a known member has the wrong type. A bad serves entry, refers item, `by` or `oobi` is dropped and
    counted in dropped.entries; a bad publication in dropped.publications. Unknown members are ignored.
    """
    fault = SourceFault("BAD_SOURCE", f"{source_host(address)} did not return a valid source.")
    try:
        r = _parse_bounded(raw)
    except ValueError:
        raise fault from None
    if not isinstance(r, dict) or not isinstance(r.get("source"), str) or r["source"] != address:
        raise fault
    kinds = {"by": str, "oobi": str, "pages": bool, "serves": list, "publications": list,
             "introductions": list, "catalogs": list}
    if any(name in r and not isinstance(r[name], kind) for name, kind in kinds.items()):
        raise fault
    dropped = {"entries": 0, "publications": 0}

    serves: dict[str, list[str]] = {}
    for item in r.get("serves", []):
        said = item.get("said") if isinstance(item, dict) else None
        refers_raw = item.get("refers", []) if isinstance(item, dict) else None
        if not is_said(said) or said in serves or not isinstance(refers_raw, list):
            dropped["entries"] += 1
            continue
        refers: set[str] = set()
        for ref in refers_raw:
            if is_said(ref) and ref != said and ref not in refers:
                refers.add(ref)
            else:
                dropped["entries"] += 1
        serves[said] = sorted(refers)

    publications = []
    for item in r.get("publications", []):
        publication = _publication(item)  # reads only its known members; the rest is ignored
        if publication is None:
            dropped["publications"] += 1
        else:
            publications.append(publication)

    by = r.get("by")
    if by is not None and not AID_PATTERN.fullmatch(by):
        by = None
        dropped["entries"] += 1
    oobi = r.get("oobi")
    if oobi is not None:
        oobi = _https_address(oobi)
        if oobi is None:
            dropped["entries"] += 1

    document = {
        "source": address,
        "by": by,
        "oobi": oobi,
        "serves": dict(sorted(serves.items())),
        "publications": publications,
        "introductions": len(r.get("introductions", [])),
        "catalogs": len(r.get("catalogs", [])),
    }
    return {"document": document, "dropped": dropped}


def closure(serves: dict, target: str) -> list[str]:
    """The target and every served SAID reachable from it through `refers`, sorted; [] when the target is not served."""
    if target not in serves:
        return []
    seen = {target}
    todo = [target]
    while todo:
        for ref in serves[todo.pop()]:
            if ref in serves and ref not in seen:
                seen.add(ref)
                todo.append(ref)
    return sorted(seen)


# Roles of a selection. The definition format is a prototype; its reading stays inside roles_of_selection.

DISPLAY_NAME = 200


def _display_name(value) -> bool:
    """A string from a definition that may be shown, offered as an identifier's name and stored."""
    return _text(value, DISPLAY_NAME)


def roles_of_selection(definitions: dict, target: str) -> dict:
    """Who does what in the selection at `target`: roles by name, each with its responsibilities in `rrs` order.

    Anything of the wrong shape is ignored. A string is used only if it is at most DISPLAY_NAME UTF-16 units and
    has no C0/C1 control character; any other is treated as a member of the wrong type: an rr whose `role` is not
    usable is ignored, a `rely_on` item whose `as` is not usable is ignored, and an unusable name, alias or kind is
    "". A referenced SAID with no definition gets alias "" and kind "".
    """
    def member(obj, name: str) -> str:
        value = obj.get(name) if isinstance(obj, dict) else None
        return value if _display_name(value) else ""

    def ref(said: str) -> dict:
        found = definitions.get(said)
        return {"said": said, "alias": member(found, "alias"), "kind": member(found, "kind")}

    def items(obj: dict, name: str) -> list:
        value = obj.get(name)
        return value if isinstance(value, list) else []

    selection = definitions.get(target)
    if not isinstance(selection, dict) or selection.get("kind") != "selection" or not isinstance(selection.get("rrs"), list):
        return {"title": "", "roles": []}
    title = member(selection, "name") or member(selection, "alias")

    roles: dict[str, list[dict]] = {}
    for said in dict.fromkeys(item for item in selection["rrs"] if is_said(item)):  # once each, first order kept
        rr = definitions.get(said)
        if not isinstance(rr, dict) or rr.get("kind") != "rr" or not _display_name(rr.get("role")):
            continue
        relies_on = [
            dict(ref(item["said"]), **{"as": item["as"]})
            for item in items(rr, "rely_on")
            if isinstance(item, dict) and is_said(item.get("said")) and _display_name(item.get("as"))
        ]
        attests = [ref(item) for item in items(rr, "attest") if is_said(item)]
        outcome = rr.get("outcome")
        outcome_said = outcome.get("said") if isinstance(outcome, dict) else None
        roles.setdefault(rr["role"], []).append({
            "said": said,
            "alias": member(rr, "alias"),
            "reliesOn": relies_on,
            "attests": attests,
            "outcome": ref(outcome_said) if is_said(outcome_said) else None,
        })
    return {"title": title, "roles": [{"role": name, "responsibilities": roles[name]} for name in sorted(roles)]}


# Reading a source: the document, then the definitions its publications need, each checked against its SAID.

def _definition(raw: bytes, said: str, digest) -> str | None:
    """The served text of a definition that passes its SAID check and is bounded JSON; None otherwise.

    The parsed object is not kept: a read holds only texts, and parses one publication's definitions while deriving
    its roles.
    """
    if not check_said(raw, said, digest=digest):
        return None
    try:
        _parse_bounded(raw)
        return raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def _roles(texts: dict, needed: list[str], target: str, *, whole: bool) -> dict:
    """roles_of_selection over the checked definitions of one closure, parsed for this call only.

    With `whole` false only the target is parsed (enough for the title of a publication that gets no roles).
    """
    saids = needed if whole else [target]
    return roles_of_selection({said: json.loads(texts[said]) for said in saids if texts.get(said) is not None}, target)


async def read_source(text: str, *, fetch_bytes, digest, clock, definitions_per_read: int = DEFINITIONS_PER_READ,
                      read_bytes: int = READ_BYTES) -> dict:
    """Read the source at `text` and the definitions its publications need.

    `fetch_bytes(url, timeout_ms)` is awaited and returns the bytes or raises SourceFault, whose `received` says how
    many body bytes it had read; `digest(bytes)` is Blake3-256; `clock()` gives milliseconds. A fault on the address
    or the source document stops the read, and so does running out of time (UNREACHABLE); the clock is checked
    before and after every fetch. A definition that cannot be fetched, fails its SAID check or is not JSON does not
    stop the read: every publication whose closure holds it gets problem "mismatch". Once `definitions_per_read`
    definitions have been fetched, or the bytes received in this read reach `read_bytes` (the source document, every
    definition returned, and what the fetch of a failed definition had read), nothing more is fetched, and a
    publication that still needs one gets problem "limit" (unless one of its definitions already failed, which is
    "mismatch"). Each definition is fetched at most once. A publication with a problem has no roles.
    """
    where = normalize_address(text)
    host = where["host"]
    started = clock()

    def time_left() -> int:
        left = READ_MS - (clock() - started)
        if left <= 0:
            raise SourceFault("UNREACHABLE", f"Could not reach {host}.")
        return int(min(REQUEST_MS, left))

    raw = await fetch_bytes(where["address"], time_left())
    time_left()
    received = len(raw)
    read = read_source_document(raw, where["address"])
    del raw
    document = read["document"]
    serves = document["serves"]

    fetched: dict[str, str | None] = {}  # said -> checked text, or None when it failed
    publications = []
    for publication in document["publications"]:
        needed = closure(serves, publication["target"])
        cut = False
        for said in needed:
            if said in fetched:
                continue
            if len(fetched) >= definitions_per_read or received >= read_bytes:
                cut = True
                break
            try:
                served = await fetch_bytes(definition_address(where["base"], said), time_left())
                received += len(served)
            except SourceFault as fault:
                served = None
                received += fault.received  # what a failed fetch had read is spent too
            time_left()
            fetched[said] = None if served is None else _definition(served, said, digest)
            del served
        failed = any(said in fetched and fetched[said] is None for said in needed)
        problem = "mismatch" if not needed or failed else "limit" if cut else None
        selection = _roles(fetched, needed, publication["target"], whole=problem is None)
        publications.append(dict(publication, title=selection["title"],
                                 roles=selection["roles"] if problem is None else [], problem=problem))

    definitions = {said: got for said, got in fetched.items() if got is not None}
    source = {"address": document["source"], "host": host, "by": document["by"], "oobi": document["oobi"],
              "introductions": document["introductions"], "catalogs": document["catalogs"]}
    return {"source": source, "checked": len(definitions), "dropped": read["dropped"],
            "publications": publications, "definitions": definitions}
