"""Runtime methods for sources and roles: sources.read, roles.takeOn, roles.list and roles.remove.

The read rules live in sourcing.py, which opens nothing. This module gives them the network (`js.fetch` with the
options below), the Blake3 digest of the bundled wheel and the clock, and turns a SourceFault into the RuntimeFault
the worker reports. Reading a source touches no vault. Nothing fetched is executed, rendered or made a link here.
The roles.* methods keep what a vault has taken on in two sub-databases of the vault and never use the network:
taking on a role uses the last read. The worker gives this module its diagnostic emitter and the sub-database names
(configure_runtime).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from inspect import isawaitable
from uuid import uuid4

import js
from pyodide.ffi import create_proxy

import sourcing
import vaulting

RuntimeFault = vaulting.RuntimeFault

# SourceFault code -> RuntimeFault code. The message is the SourceFault's sentence for the person, unchanged.
FAULT_CODES = {
    "BAD_ADDRESS": "VALIDATION",
    "UNREACHABLE": "NETWORK_ERROR",
    "NOT_READABLE": "NETWORK_ERROR",
    "BAD_SOURCE": "BAD_RESPONSE",
    "TOO_MUCH": "BAD_RESPONSE",
}

_CONFIG: dict = {}


def configure_runtime(*, emit_diagnostic, roles_subdb: str, role_definitions_subdb: str):
    """`emit_diagnostic(event, *, level, **fields)` is the worker's diagnostic emitter; the two names are the vault
    sub-databases of the roles taken on and of the definitions they need (vaulting declares them on every open)."""
    _CONFIG.clear()
    _CONFIG.update(emit_diagnostic=emit_diagnostic, roles_subdb=roles_subdb,
                   role_definitions_subdb=role_definitions_subdb)


def _diagnose(event: str, **fields) -> None:
    """Report through the worker's diagnostics. A report that cannot be made is dropped: reporting never changes
    what a request does."""
    try:
        _CONFIG["emit_diagnostic"](event, **fields)
    except Exception:
        pass


# The full result of the newest sources.read, definitions included (taking on a role reads it). Reads run
# concurrently: each takes a number when it starts, and its result is kept only if no newer read has started since,
# so _LAST_READ always belongs to the most recently requested address or stays what it was. It is forgotten when the
# open vault changes (forget_last_read).
_LAST_READ: dict | None = None
_READS_STARTED = 0


def forget_last_read() -> None:
    """Drops the last read, and any read still running (it is not kept when it finishes). The worker calls this when
    the open vault changes, so a read made in one vault is never used to take on a role in another."""
    global _LAST_READ, _READS_STARTED
    _LAST_READ = None
    _READS_STARTED += 1


async def _settle(value):
    return await value if isawaitable(value) else value


def _options(signal):
    """The only options the wallet reads a source with. It makes no other kind of request: one that could tell an
    unreachable host from one that does not allow the read (no-cors) cannot refuse redirects."""
    options = js.Object.new()
    options.method = "GET"
    options.mode = "cors"
    options.credentials = "omit"
    options.redirect = "error"
    options.referrerPolicy = "no-referrer"
    options.cache = "no-store"
    options.signal = signal
    return options


async def _fetch_or_none(url: str, timeout_ms: int):
    """The response, or None when the fetch rejected; its controller; a `release` that clears the timer and frees what
    the timer holds (None once done here, on rejection; the caller calls it once it is done with the response); and
    whether the timeout fired."""
    controller = js.AbortController.new()
    fired = []

    def _abort():
        fired.append(True)
        _abort_quietly(controller)

    # A bare Python function handed to the browser is a proxy Pyodide destroys when setTimeout returns, and the timer
    # would find nothing to call. This one lives until release(), after the timer has fired or been cleared.
    on_time = create_proxy(_abort)
    try:
        timer = js.setTimeout(on_time, max(1, int(timeout_ms)))
    except BaseException:
        on_time.destroy()
        raise

    def release():
        js.clearTimeout(timer)
        on_time.destroy()

    try:
        return await _settle(js.fetch(url, _options(controller.signal))), controller, release, fired
    except Exception:
        release()
        return None, controller, None, fired
    except BaseException:  # the request itself was cancelled: nothing is left behind either
        release()
        raise


def _abort_quietly(controller) -> None:
    try:
        controller.abort()
    except Exception:
        pass


class _BodyCount:
    """The body bytes one fetch has read so far. It is kept outside the reading, so that whatever stops the fetch
    midway, an unexpected error included, the fault can say how many had arrived."""

    def __init__(self):
        self.received = 0


async def _fetch_bytes(url: str, timeout_ms: int) -> bytes:
    """The body of a 200 JSON response at `url`, at most sourcing.FILE_BYTES, within `timeout_ms`; else SourceFault.

    The fault's `received` is the number of body bytes read before it (0 when it came before the body), so the
    read's byte budget counts them. Any unexpected error (a JsException, a malformed response) is UNREACHABLE; its
    text is never shown. So that a programming error or a changed browser API does not pass for an unreachable
    host, it is reported as a warning diagnostic that names only the error's class and the host.
    """
    host = sourcing.source_host(url)
    count = _BodyCount()
    try:
        return await _fetch_checked(url, timeout_ms, host, count)
    except sourcing.SourceFault as fault:
        fault.received = count.received
        raise
    except Exception as error:
        _diagnose("source_fetch_unexpected_error", level="warning", exception=type(error).__name__, host=host)
        raise sourcing.SourceFault("UNREACHABLE", f"Could not reach {host}.", received=count.received) from None


def _is_json_type(header) -> bool:
    """mad-discovery's rule (src/fetch.ts isJsonType): the type before any parameter, trimmed and lower-cased, is
    application/json or ends with +json."""
    if not header:
        return False
    kind = str(header).split(";", 1)[0].strip().lower()
    return kind == "application/json" or kind.endswith("+json")


async def _fetch_checked(url: str, timeout_ms: int, host: str, count: _BodyCount) -> bytes:
    started = _clock()
    response, controller, release, fired = await _fetch_or_none(url, timeout_ms)
    if response is None:
        if fired or _clock() - started >= timeout_ms:
            raise sourcing.SourceFault("UNREACHABLE", f"Could not reach {host}.")
        # A browser does not say why a cors fetch was rejected: the host cannot be reached, or it does not allow the
        # read. The wallet does not ask again in another way to find out.
        raise sourcing.SourceFault(
            "NOT_READABLE", f"Could not read {host}: it cannot be reached, or it does not allow wallets to read it yet.")
    try:
        if int(response.status) != 200 or not _is_json_type(response.headers.get("content-type")) \
                or response.body is None:
            raise sourcing.SourceFault("BAD_SOURCE", f"{host} did not return a valid source.")
        reader = response.body.getReader()
        chunks = []
        while True:
            try:
                step = await _settle(reader.read())
            except Exception:
                raise sourcing.SourceFault("UNREACHABLE", f"Could not reach {host}.") from None
            if step.done:
                break
            chunk = bytes(step.value.to_py())
            count.received += len(chunk)
            if count.received > sourcing.FILE_BYTES:
                try:
                    await _settle(reader.cancel())
                except Exception:
                    pass
                raise sourcing.SourceFault("TOO_MUCH", f"{host} publishes more than the wallet reads in one go.")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        release()
        _abort_quietly(controller)  # ends anything still open; a no-op once the body is read


def _digest(data: bytes) -> bytes:
    import blake3  # from the runtime packages, installed before any request is dispatched

    return blake3.blake3(data).digest()


def _clock() -> float:
    return float(js.performance.now())


# Parts of a read the engine keeps for taking on a role and never sends to the page.
_ENGINE_ONLY = frozenset({"definitions", "derivedFrom"})


async def _sources_read(params: dict) -> dict:
    global _LAST_READ, _READS_STARTED
    _READS_STARTED += 1
    sequence = _READS_STARTED
    address = params.get("address")
    if not isinstance(address, str):
        address = ""
    try:
        result = await sourcing.read_source(address, fetch_bytes=_fetch_bytes, digest=_digest, clock=_clock)
    except sourcing.SourceFault as fault:
        raise RuntimeFault(FAULT_CODES[fault.code], fault.message) from None
    if sequence == _READS_STARTED:
        _LAST_READ = result
    return {key: value for key, value in result.items() if key not in _ENGINE_ONLY}


# Roles taken on. A record keeps where the role came from, the role's responsibilities as the read showed them, and
# the SAIDs of every definition its view needs; the definitions are kept once each, by SAID, as served. My roles is
# read from the records alone, with no network and without reading or parsing any definition.

OPEN_AGAIN = "Open the address again before taking on a role."
ALREADY_TAKEN = "This identifier has already taken on that role."
NOT_APPOINTED = {"code": "NOT_APPOINTED", "message": "Not appointed yet"}

# A role is kept only if it is this small: its responsibilities and their references (reliesOn, attests, outcome)
# counted together, and the UTF-8 bytes of the definitions it needs.
ROLE_ITEMS = 200
ROLE_BYTES = 200_000


@dataclass
class RoleRecord:
    id: str = ""
    address: str = ""
    host: str = ""
    target: str = ""
    publication: str = ""
    title: str = ""
    role: str = ""
    responsibilities: list[str] = field(default_factory=list)
    # The responsibilities as the read showed them; a record written before they were kept has none.
    shown: list[dict] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    aid: str = ""
    taken_at: str = ""


@dataclass
class DefinitionRecord:
    said: str = ""
    text: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _roles_store(hby):
    return vaulting.load_modules()["koming"].Komer(db=hby.db, subkey=_CONFIG["roles_subdb"], klas=RoleRecord)


def _definitions_store(hby):
    return vaulting.load_modules()["koming"].Komer(db=hby.db, subkey=_CONFIG["role_definitions_subdb"],
                                                   klas=DefinitionRecord)


def _open_hby(params: dict):
    return vaulting.require_open_state(vaulting.require_text(params.get("vaultId"), field="Vault"))["hby"]


def _role_in_last_read(params: dict) -> tuple[dict, dict, dict]:
    """The last read, the publication and the role it names, when the last read is of this address and holds that
    publication (by target and name) without a problem and that role; CONFLICT otherwise."""
    read = _LAST_READ
    try:
        address = sourcing.normalize_address(params.get("address") if isinstance(params.get("address"), str) else "")
    except sourcing.SourceFault:
        address = None
    if read is None or address is None or read["source"]["address"] != address["address"]:
        raise RuntimeFault("CONFLICT", OPEN_AGAIN)
    # Two publications may list one target: the name, as read, tells them apart.
    publication = next((item for item in read["publications"]
                        if item["target"] == params.get("target") and item["name"] == params.get("publication")
                        and item["problem"] is None), None)
    role = None if publication is None else next(
        (item for item in publication["roles"] if item["role"] == params.get("role")), None)
    if role is None:
        raise RuntimeFault("CONFLICT", OPEN_AGAIN)
    return read, publication, role


def _needed(target: str, role: dict, derived_from: list[str]) -> list[str]:
    """Every SAID the role's view needs among those the read derived the publication's view from (its closure's
    checked definitions): the selection, the role's responsibilities and what they refer to. In first-use order,
    once each. So the stored role reads as the read showed it."""
    used = set(derived_from)
    saids = [target]
    for responsibility in role["responsibilities"]:
        saids.append(responsibility["said"])
        refs = [*responsibility["reliesOn"], *responsibility["attests"]]
        if responsibility["outcome"]:
            refs.append(responsibility["outcome"])
        saids.extend(ref["said"] for ref in refs)
    return [said for said in dict.fromkeys(saids) if said in used]


def _role_items(role: dict) -> int:
    """A role's responsibilities and their references (reliesOn, attests, outcome), counted together."""
    return sum(1 + len(duty["reliesOn"]) + len(duty["attests"]) + (duty["outcome"] is not None)
               for duty in role["responsibilities"])


def _identifier_choice(params: dict) -> dict:
    choice = params.get("identifier")
    if not isinstance(choice, dict) or choice.get("mode") not in ("new", "existing"):
        raise RuntimeFault("BAD_REQUEST", "Choose a new or an existing identifier.")
    return choice


def _role_view(hby, record: RoleRecord) -> dict:
    """The role as My roles shows it, from its record alone."""
    hab = hby.habByPre(record.aid)
    return {
        "id": record.id, "address": record.address, "host": record.host, "target": record.target,
        "publication": record.publication, "title": record.title, "role": record.role, "aid": record.aid,
        "identifierAlias": hab.name if hab is not None else "", "takenAt": record.taken_at,
        "responsibilities": record.shown,
        "warnings": [dict(NOT_APPOINTED)],
    }


def _records(hby) -> list[RoleRecord]:
    return [record for _, record in _roles_store(hby).getTopItemIter()]


COULD_NOT_KEEP = "The wallet could not keep that role."


async def _roles_take_on(params: dict) -> dict:
    # 1. Every refusal comes first: nothing has been written when one is raised.
    hby = _open_hby(params)
    read, publication, role = _role_in_last_read(params)
    choice = _identifier_choice(params)
    address = read["source"]["address"]
    try:
        records = _records(hby)
    except Exception as error:  # a record that cannot be read: nothing is written
        _diagnose("role_store_failed", level="error", exception=type(error).__name__)
        raise RuntimeFault("BAD_RESPONSE", COULD_NOT_KEEP) from None
    if choice["mode"] == "existing":
        aid = vaulting.identifier_hab(hby, vaulting.require_text(choice.get("aid"), field="Identifier")).pre
        if any((record.address, record.target, record.role, record.aid) == (address, publication["target"],
                                                                            role["role"], aid)
               for record in records):
            raise RuntimeFault("CONFLICT", ALREADY_TAKEN)
        alias = None
    else:
        aid = ""
        alias = vaulting.free_alias(hby, choice.get("alias"))

    definitions = read["definitions"]
    needed = _needed(publication["target"], role, read["derivedFrom"][publication["target"]])
    if _role_items(role) > ROLE_ITEMS \
            or sum(len(definitions[said].encode("utf-8")) for said in needed) > ROLE_BYTES:
        raise RuntimeFault("BAD_RESPONSE", f"{read['source']['host']} publishes more than the wallet keeps for one role.")
    record = RoleRecord(
        id=uuid4().hex, address=address, host=read["source"]["host"], target=publication["target"],
        publication=publication["name"], title=publication["title"], role=role["role"],
        responsibilities=[item["said"] for item in role["responsibilities"]], shown=role["responsibilities"],
        definitions=needed, aid=aid,
        taken_at=_now(),
    )
    roles = _roles_store(hby)
    stored = _definitions_store(hby)
    listed = {said for other in records for said in other.definitions}
    try:
        # 2. The role's data is written, and so proven storable, before any identifier is made.
        for said in needed:
            stored.pin(keys=(said,), val=DefinitionRecord(said=said, text=definitions[said]))
        roles.pin(keys=(record.id,), val=record)
        if alias is not None:
            # 3. The identifier is made; 4. the record is completed with it.
            # Known limit: if that second write failed, the role's data is removed but the identifier just made
            # stays, for the person to take the role on with. Habery.deleteHab would not undo it: it leaves the key
            # manager's entries for the alias, so making that alias again would be refused ("Already incepted").
            record.aid = vaulting.create_identifier(hby, alias).pre
            roles.pin(keys=(record.id,), val=record)
    except Exception as error:
        _forget_role(roles, stored, record, [said for said in needed if said not in listed])
        _diagnose("role_store_failed", level="error", exception=type(error).__name__)
        raise RuntimeFault("BAD_RESPONSE", COULD_NOT_KEEP) from None
    # 5. Known limit: a persist writes one store after another, so a persist that is interrupted can still leave an
    # identifier without the role, or a role without its definitions. A persist that fails takes the role's data out
    # of memory again, as far as it can, as a failed write does; an identifier made stays (the known limit above).
    try:
        reopened = await vaulting.persist_and_reload()
    except Exception as error:
        _forget_role(roles, stored, record, [said for said in needed if said not in listed])
        _diagnose("role_store_failed", level="error", exception=type(error).__name__)
        raise RuntimeFault("BAD_RESPONSE", COULD_NOT_KEEP) from None
    return {"role": _role_view(reopened["hby"], record)}


def _forget_role(roles, stored, record: RoleRecord, added: list[str]) -> None:
    """Removes what a take-on that failed had written: its record and the definitions no other record lists. A
    removal that fails is reported (the class of its error only) and the others are still made."""
    removals = [(roles, (record.id,))] + [(stored, (said,)) for said in added]
    for store, keys in removals:
        try:
            store.rem(keys=keys)
        except Exception as error:
            _diagnose("role_store_cleanup_failed", level="error", exception=type(error).__name__)


async def _roles_list(params: dict) -> dict:
    hby = _open_hby(params)
    records = sorted(_records(hby), key=lambda record: record.taken_at, reverse=True)
    return {"roles": [_role_view(hby, record) for record in records]}


async def _roles_remove(params: dict) -> dict:
    hby = _open_hby(params)
    role_id = params.get("id")
    roles = _roles_store(hby)
    record = roles.get(keys=(role_id,)) if isinstance(role_id, str) and role_id else None
    if record is None:
        raise RuntimeFault("NOT_FOUND", "That role was not found.")
    try:
        roles.rem(keys=(record.id,))
        still_listed = {said for other in _records(hby) for said in other.definitions}
        stored = _definitions_store(hby)
        for said in record.definitions:
            if said not in still_listed:
                stored.rem(keys=(said,))
        await vaulting.persist_and_reload()
    except Exception as error:
        _diagnose("role_remove_failed", level="error", exception=type(error).__name__)
        raise RuntimeFault("BAD_RESPONSE", "The wallet could not remove that role.") from None
    return {"removed": True}


_ROLE_METHODS = {"roles.takeOn": _roles_take_on, "roles.list": _roles_list, "roles.remove": _roles_remove}


async def dispatch(method: str, params: dict) -> dict:
    if method == "sources.read":
        return await _sources_read(params)
    if method in _ROLE_METHODS:
        return await _ROLE_METHODS[method](params)
    raise RuntimeFault("BAD_REQUEST", f"Runtime method '{method}' is not available.")
