"""Runtime methods for sources and roles: sources.read now; roles.* follow.

The read rules live in sourcing.py, which opens nothing. This module gives them the network (`js.fetch` with the
options below), the Blake3 digest of the bundled wheel and the clock, and turns a SourceFault into the RuntimeFault
the worker reports. Reading a source touches no vault. Nothing fetched is executed, rendered or made a link here.
The worker gives this module its diagnostic emitter (configure_runtime).
"""

from __future__ import annotations

from inspect import isawaitable

import js

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

# A no-cors probe only tells "cannot be reached" from "does not allow the read"; it gets this fixed budget,
# whatever the request's own timeout.
PROBE_MS = 3_000

_CONFIG: dict = {}


def configure_runtime(*, emit_diagnostic):
    """`emit_diagnostic(event, *, level, **fields)` is the worker's diagnostic emitter."""
    _CONFIG.clear()
    _CONFIG.update(emit_diagnostic=emit_diagnostic)


def _diagnose(event: str, **fields) -> None:
    """Report through the worker's diagnostics. A report that cannot be made is dropped: reporting never changes
    what a request does."""
    try:
        _CONFIG["emit_diagnostic"](event, **fields)
    except Exception:
        pass


# The full result of the newest sources.read, definitions included (taking on a role reads it). Reads run
# concurrently: each takes a number when it starts, and its result is kept only if no newer read has started since,
# so _LAST_READ always belongs to the most recently requested address or stays what it was.
_LAST_READ: dict | None = None
_READS_STARTED = 0


async def _settle(value):
    return await value if isawaitable(value) else value


def _options(mode: str, signal):
    options = js.Object.new()
    options.method = "GET"
    options.mode = mode
    options.credentials = "omit"
    # The read refuses redirects. The no-cors probe cannot: a browser fails a no-cors fetch whose redirect mode is
    # not "follow" before sending it. The probe's opaque answer is never read; it only shows that the host answered.
    options.redirect = "error" if mode == "cors" else "follow"
    options.referrerPolicy = "no-referrer"
    options.cache = "no-store"
    options.signal = signal
    return options


async def _fetch_or_none(url: str, mode: str, timeout_ms: int):
    """The response, or None when the fetch rejected; also whether the timeout fired. Clears its timer on rejection."""
    controller = js.AbortController.new()
    fired = []

    def _abort():
        fired.append(True)
        _abort_quietly(controller)

    timer = js.setTimeout(_abort, max(1, int(timeout_ms)))
    try:
        return await _settle(js.fetch(url, _options(mode, controller.signal))), controller, timer, fired
    except Exception:
        js.clearTimeout(timer)
        return None, controller, None, fired


def _abort_quietly(controller) -> None:
    try:
        controller.abort()
    except Exception:
        pass


async def _probe(url: str) -> bool:
    """Whether a no-cors request to `url` is answered within PROBE_MS. Its opaque body is never read: once the probe
    settles its controller is aborted, so nothing keeps streaming."""
    probe, controller, timer, _ = await _fetch_or_none(url, "no-cors", PROBE_MS)
    js.clearTimeout(timer)
    _abort_quietly(controller)
    return probe is not None


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


async def _fetch_checked(url: str, timeout_ms: int, host: str, count: _BodyCount) -> bytes:
    started = _clock()
    response, controller, timer, fired = await _fetch_or_none(url, "cors", timeout_ms)
    if response is None:
        if fired or _clock() - started >= timeout_ms:
            raise sourcing.SourceFault("UNREACHABLE", f"Could not reach {host}.")
        # A rejected cors fetch is either a host that cannot be reached or one that does not allow the read.
        # A no-cors probe tells them apart.
        if not await _probe(url):
            raise sourcing.SourceFault("UNREACHABLE", f"Could not reach {host}.")
        raise sourcing.SourceFault("NOT_READABLE", f"{host} does not allow wallets to read it yet.")
    try:
        content_type = str(response.headers.get("content-type") or "").lower()
        if int(response.status) != 200 or not content_type.startswith("application/json") or response.body is None:
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
        js.clearTimeout(timer)
        _abort_quietly(controller)  # ends anything still open; a no-op once the body is read


def _digest(data: bytes) -> bytes:
    import blake3  # from the runtime packages, installed before any request is dispatched

    return blake3.blake3(data).digest()


def _clock() -> float:
    return float(js.performance.now())


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
    return {key: value for key, value in result.items() if key != "definitions"}


async def dispatch(method: str, params: dict) -> dict:
    if method == "sources.read":
        return await _sources_read(params)
    raise RuntimeFault("BAD_REQUEST", f"Runtime method '{method}' is not available yet.")
