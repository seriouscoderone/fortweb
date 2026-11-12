"""Runtime methods for sources and roles: sources.read now; roles.* follow.

The read rules live in sourcing.py, which opens nothing. This module gives them the network (`js.fetch` with the
options below), the Blake3 digest of the bundled wheel and the clock, and turns a SourceFault into the RuntimeFault
the worker reports. Reading a source touches no vault. Nothing fetched is executed, rendered or made a link here.
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

# The full result of the last successful sources.read, definitions included (taking on a role reads it).
_LAST_READ: dict | None = None


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
    """The response, or None when the fetch rejected; also whether the timeout fired. Clears its own timer."""
    controller = js.AbortController.new()
    fired = []

    def _abort():
        fired.append(True)
        try:
            controller.abort()
        except Exception:
            return

    timer = js.setTimeout(_abort, max(1, int(timeout_ms)))
    try:
        return await _settle(js.fetch(url, _options(mode, controller.signal))), controller, timer, fired
    except Exception:
        js.clearTimeout(timer)
        return None, controller, None, fired


async def _fetch_bytes(url: str, timeout_ms: int) -> bytes:
    """The body of a 200 JSON response at `url`, at most sourcing.FILE_BYTES, within `timeout_ms`; else SourceFault."""
    host = sourcing.source_host(url)
    response, controller, timer, fired = await _fetch_or_none(url, "cors", timeout_ms)
    if response is None:
        if fired:
            raise sourcing.SourceFault("UNREACHABLE", f"Could not reach {host}.")
        # A rejected cors fetch is either a host that cannot be reached or one that does not allow the read.
        # An opaque no-cors probe tells them apart; its body is never read.
        probe, _, probe_timer, _ = await _fetch_or_none(url, "no-cors", timeout_ms)
        if probe is None:
            raise sourcing.SourceFault("UNREACHABLE", f"Could not reach {host}.")
        js.clearTimeout(probe_timer)
        raise sourcing.SourceFault("NOT_READABLE", f"{host} does not allow wallets to read it yet.")
    try:
        content_type = str(response.headers.get("content-type") or "").lower()
        if int(response.status) != 200 or not content_type.startswith("application/json") or response.body is None:
            raise sourcing.SourceFault("BAD_SOURCE", f"{host} did not return a valid source.")
        reader = response.body.getReader()
        chunks = []
        total = 0
        while True:
            try:
                step = await _settle(reader.read())
            except Exception:
                raise sourcing.SourceFault("UNREACHABLE", f"Could not reach {host}.") from None
            if step.done:
                break
            chunk = bytes(step.value.to_py())
            total += len(chunk)
            if total > sourcing.FILE_BYTES:
                try:
                    await _settle(reader.cancel())
                except Exception:
                    pass
                raise sourcing.SourceFault("TOO_MUCH", f"{host} publishes more than the wallet reads in one go.")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        js.clearTimeout(timer)
        try:
            controller.abort()  # ends anything still open; a no-op once the body is read
        except Exception:
            pass


def _digest(data: bytes) -> bytes:
    import blake3  # from the runtime packages, installed before any request is dispatched

    return blake3.blake3(data).digest()


def _clock() -> float:
    return float(js.performance.now())


async def _sources_read(params: dict) -> dict:
    global _LAST_READ
    address = params.get("address")
    if not isinstance(address, str):
        address = ""
    try:
        result = await sourcing.read_source(address, fetch_bytes=_fetch_bytes, digest=_digest, clock=_clock)
    except sourcing.SourceFault as fault:
        raise RuntimeFault(FAULT_CODES[fault.code], fault.message) from None
    _LAST_READ = result
    return {key: value for key, value in result.items() if key != "definitions"}


async def dispatch(method: str, params: dict) -> dict:
    if method == "sources.read":
        return await _sources_read(params)
    raise RuntimeFault("BAD_REQUEST", f"Runtime method '{method}' is not available yet.")
