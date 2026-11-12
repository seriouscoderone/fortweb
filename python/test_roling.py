"""The sources.* runtime methods: dispatch, the result returned to the page, and the fault codes."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "app" / "runtime"
FIXTURES = REPO / "python" / "fixtures" / "mad"
ADDRESS = "https://mad.seriouscoder.one/source.json"
BASE = "https://mad.seriouscoder.one/definitions/"


class RuntimeFault(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_roling():
    keys = ("js", "vaulting", "sourcing")
    original = {key: sys.modules.get(key) for key in keys}
    sourcing = _load("sourcing", RUNTIME / "sourcing.py")
    sys.modules["js"] = types.SimpleNamespace(performance=types.SimpleNamespace(now=lambda: 0.0))
    sys.modules["vaulting"] = types.SimpleNamespace(RuntimeFault=RuntimeFault)
    sys.modules["sourcing"] = sourcing
    try:
        return _load("fortweb_roling_test", RUNTIME / "roling.py"), sourcing
    finally:
        for key, value in original.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class SourcesReadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.roling, self.sourcing = _load_roling()
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        document = (FIXTURES / "source.json").read_bytes()

        async def fetch_bytes(url, timeout_ms):
            if url == ADDRESS:
                return document
            return files[url[len(BASE):-len(".json")]]
        self.roling._fetch_bytes = fetch_bytes

    def fail_with(self, code: str, message: str):
        sourcing = self.sourcing

        async def fetch_bytes(url, timeout_ms):
            raise sourcing.SourceFault(code, message)
        self.roling._fetch_bytes = fetch_bytes

    async def test_read_returns_everything_but_the_definitions(self):
        got = await self.roling.dispatch("sources.read", {"address": "mad.seriouscoder.one"})
        self.assertEqual(set(got), {"source", "checked", "dropped", "publications"})
        self.assertEqual(got["checked"], 26)
        self.assertEqual(got["source"]["host"], "mad.seriouscoder.one")
        self.assertEqual(len(self.roling._LAST_READ["definitions"]), 26)
        self.assertEqual(self.roling._LAST_READ["publications"], got["publications"])

    async def test_fault_codes_map_to_runtime_codes(self):
        cases = [
            ("UNREACHABLE", "Could not reach mad.seriouscoder.one.", "NETWORK_ERROR"),
            ("NOT_READABLE", "mad.seriouscoder.one does not allow wallets to read it yet.", "NETWORK_ERROR"),
            ("TOO_MUCH", "mad.seriouscoder.one publishes more than the wallet reads in one go.", "BAD_RESPONSE"),
            ("BAD_SOURCE", "mad.seriouscoder.one did not return a valid source.", "BAD_RESPONSE"),
        ]
        for source_code, message, runtime_code in cases:
            with self.subTest(source_code=source_code):
                self.fail_with(source_code, message)
                with self.assertRaises(RuntimeFault) as raised:
                    await self.roling.dispatch("sources.read", {"address": "mad.seriouscoder.one"})
                self.assertEqual((raised.exception.code, str(raised.exception)), (runtime_code, message))

    async def test_bad_address_is_validation(self):
        for address in ("http://x", "", None, 7):
            with self.subTest(address=address):
                with self.assertRaises(RuntimeFault) as raised:
                    await self.roling.dispatch("sources.read", {"address": address})
                self.assertEqual((raised.exception.code, str(raised.exception)),
                                 ("VALIDATION", "That is not an address the wallet can open."))

    async def test_failed_read_keeps_the_last_good_read(self):
        await self.roling.dispatch("sources.read", {"address": "mad.seriouscoder.one"})
        last = self.roling._LAST_READ
        with self.assertRaises(RuntimeFault):
            await self.roling.dispatch("sources.read", {"address": "http://x"})
        self.assertIs(self.roling._LAST_READ, last)

    async def test_newest_read_wins_when_an_older_read_finishes_last(self):
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        text = (FIXTURES / "source.json").read_text("utf-8")
        older_address = "https://older.example.test/source.json"
        documents = {
            ADDRESS: text.encode(),
            older_address: text.replace(ADDRESS, older_address).encode(),
        }
        older_may_answer = asyncio.Event()
        older_waiting = asyncio.Event()

        async def fetch_bytes(url, timeout_ms):
            if url == older_address:
                older_waiting.set()
                await older_may_answer.wait()
            if url in documents:
                return documents[url]
            return files[url.rsplit("/", 1)[1][:-len(".json")]]
        self.roling._fetch_bytes = fetch_bytes

        older = asyncio.ensure_future(self.roling.dispatch("sources.read", {"address": "older.example.test"}))
        await older_waiting.wait()
        newer = await self.roling.dispatch("sources.read", {"address": "mad.seriouscoder.one"})
        self.assertEqual(self.roling._LAST_READ["source"]["host"], "mad.seriouscoder.one")
        older_may_answer.set()
        older_result = await older
        # Both callers get their own answer; the kept read stays the newest one requested.
        self.assertEqual(older_result["source"]["host"], "older.example.test")
        self.assertEqual(newer["source"]["host"], "mad.seriouscoder.one")
        self.assertEqual(self.roling._LAST_READ["source"]["host"], "mad.seriouscoder.one")

    async def test_method_not_yet_available(self):
        for method in ("roles.list", "roles.takeOn", "roles.remove", "sources.other"):
            with self.subTest(method=method):
                with self.assertRaises(RuntimeFault) as raised:
                    await self.roling.dispatch(method, {})
                self.assertEqual(raised.exception.code, "BAD_REQUEST")


URL = "https://mad.example.test/source.json"


class Timer:
    def __init__(self, callback, ms):
        self.callback, self.ms, self.cleared = callback, ms, False


class Controller:
    def __init__(self):
        self.signal = object()
        self.aborted = False

    def abort(self):
        self.aborted = True


class Reader:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.reads = 0
        self.cancelled = False

    async def read(self):
        self.reads += 1
        if not self.chunks:
            return types.SimpleNamespace(done=True, value=None)
        chunk = self.chunks.pop(0)
        return types.SimpleNamespace(done=False, value=types.SimpleNamespace(to_py=lambda: chunk))

    async def cancel(self):
        self.cancelled = True


def response(status=200, content_type="application/json", chunks=(b"{}",), body=True):
    reader = Reader(chunks)
    headers = types.SimpleNamespace(get=lambda name: {"content-type": content_type}.get(name.lower()))
    return types.SimpleNamespace(status=status, headers=headers,
                                 body=types.SimpleNamespace(getReader=lambda: reader) if body else None,
                                 reader=reader)


class StubJs:
    """fetch, AbortController, setTimeout/clearTimeout and performance.now, as roling uses them.

    `answer(url, options)` (async) decides each fetch: return a response or raise. Time moves only when a test moves it.
    """

    def __init__(self, answer):
        self.answer = answer
        self.now = 0.0
        self.calls = []  # (url, options, time of the call)
        self.timers: list[Timer] = []
        self.controllers: list[Controller] = []
        self.Object = types.SimpleNamespace(new=types.SimpleNamespace)
        self.AbortController = types.SimpleNamespace(new=self._controller)
        self.performance = types.SimpleNamespace(now=lambda: self.now)

    def _controller(self):
        self.controllers.append(Controller())
        return self.controllers[-1]

    def setTimeout(self, callback, ms):
        self.timers.append(Timer(callback, ms))
        return len(self.timers) - 1

    def clearTimeout(self, timer_id):
        if timer_id is not None:
            self.timers[timer_id].cleared = True

    def fetch(self, url, options):
        self.calls.append((url, options, self.now))
        return self.answer(url, options)

    def fire(self, index: int):
        self.timers[index].callback()


class FetchBytesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.roling, self.sourcing = _load_roling()

    def stub(self, answer) -> StubJs:
        self.roling.js = StubJs(answer)
        return self.roling.js

    async def fault(self, timeout_ms: int = 10_000):
        with self.assertRaises(self.sourcing.SourceFault) as raised:
            await self.roling._fetch_bytes(URL, timeout_ms)
        return raised.exception

    async def test_read_uses_the_fixed_fetch_options_and_clears_its_timer(self):
        served = response(chunks=(b'{"a":', b"1}"))

        async def answer(url, options):
            return served
        js = self.stub(answer)
        self.assertEqual(await self.roling._fetch_bytes(URL, 7_000), b'{"a":1}')
        (url, options, _), = js.calls
        self.assertEqual(url, URL)
        self.assertEqual(
            {name: getattr(options, name) for name in ("method", "mode", "credentials", "redirect", "referrerPolicy",
                                                       "cache")},
            {"method": "GET", "mode": "cors", "credentials": "omit", "redirect": "error",
             "referrerPolicy": "no-referrer", "cache": "no-store"})
        self.assertIs(options.signal, js.controllers[0].signal)
        self.assertEqual([t.ms for t in js.timers], [7_000])
        self.assertTrue(js.timers[0].cleared)

    async def test_status_other_than_200_is_not_a_valid_source(self):
        for status in (404, 500, 204, 302):
            with self.subTest(status=status):
                served = response(status=status)

                async def answer(url, options):
                    return served
                self.stub(answer)
                fault = await self.fault()
                self.assertEqual((fault.code, fault.message),
                                 ("BAD_SOURCE", "mad.example.test did not return a valid source."))
                self.assertEqual(served.reader.reads, 0)

    async def test_content_type_other_than_json_is_refused(self):
        for content_type in ("text/html", "text/html; charset=utf-8", "application/javascript", None):
            with self.subTest(content_type=content_type):
                served = response(content_type=content_type)

                async def answer(url, options):
                    return served
                self.stub(answer)
                self.assertEqual((await self.fault()).code, "BAD_SOURCE")
                self.assertEqual(served.reader.reads, 0)

    async def test_json_content_type_with_parameters_is_read(self):
        async def answer(url, options):
            return response(content_type="application/json; charset=utf-8")
        self.stub(answer)
        self.assertEqual(await self.roling._fetch_bytes(URL, 10_000), b"{}")

    async def test_missing_body_is_refused(self):
        async def answer(url, options):
            return response(body=False)
        self.stub(answer)
        self.assertEqual((await self.fault()).code, "BAD_SOURCE")

    async def test_body_over_a_million_bytes_is_too_much_and_reading_stops(self):
        served = response(chunks=(b"x" * 600_000, b"x" * 400_001, b"x" * 10))

        async def answer(url, options):
            return served
        self.stub(answer)
        fault = await self.fault()
        self.assertEqual((fault.code, fault.message),
                         ("TOO_MUCH", "mad.example.test publishes more than the wallet reads in one go."))
        self.assertEqual(served.reader.reads, 2)
        self.assertTrue(served.reader.cancelled)

    async def test_body_of_exactly_a_million_bytes_is_read(self):
        async def answer(url, options):
            return response(chunks=(b"x" * 600_000, b"x" * 400_000))
        self.stub(answer)
        self.assertEqual(len(await self.roling._fetch_bytes(URL, 10_000)), 1_000_000)

    async def test_timer_firing_aborts_the_request_and_is_unreachable_without_a_probe(self):
        async def answer(url, options):
            js.fire(0)
            raise RuntimeError("AbortError: signal is aborted without reason")
        js = self.stub(answer)
        fault = await self.fault()
        self.assertEqual((fault.code, fault.message), ("UNREACHABLE", "Could not reach mad.example.test."))
        self.assertTrue(js.controllers[0].aborted)
        self.assertEqual(len(js.calls), 1)

    async def test_rejected_read_is_told_apart_by_a_probe(self):
        async def answer(url, options):
            if options.mode == "cors":
                raise RuntimeError("TypeError: Failed to fetch")
            return types.SimpleNamespace(status=0, type="opaque")
        js = self.stub(answer)
        fault = await self.fault()
        self.assertEqual((fault.code, fault.message),
                         ("NOT_READABLE", "mad.example.test does not allow wallets to read it yet."))
        self.assertEqual([options.mode for _, options, _ in js.calls], ["cors", "no-cors"])

    async def test_unexpected_error_is_unreachable_and_does_not_leak_its_text(self):
        def broken_headers(name):
            raise RuntimeError("JsException: secret internal detail")

        cases = {
            "headers": lambda: types.SimpleNamespace(status=200, headers=types.SimpleNamespace(get=broken_headers),
                                                     body=None),
            "status": lambda: types.SimpleNamespace(status="not a number",
                                                    headers=types.SimpleNamespace(get=lambda name: None), body=None),
        }
        for name, make in cases.items():
            with self.subTest(case=name):
                async def answer(url, options):
                    return make()
                self.stub(answer)
                fault = await self.fault()
                self.assertEqual((fault.code, fault.message), ("UNREACHABLE", "Could not reach mad.example.test."))

    async def test_unexpected_error_in_one_definition_is_a_mismatch_not_a_failed_read(self):
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        document = (FIXTURES / "source.json").read_text("utf-8").replace(ADDRESS, URL).encode()
        paid_admission = "EIjETqo6CwKdjLKiUbve977olj92nuhBoy4NPywpqD1U"  # only dropin-class uses it

        async def answer(url, options):
            if url == URL:
                return response(chunks=(document,))
            said = url.rsplit("/", 1)[1][:-len(".json")]
            if said == paid_admission:
                return types.SimpleNamespace(status=200, headers=None, body=None)  # .get on None: AttributeError
            return response(chunks=(files[said],))
        self.stub(answer)
        got = await self.roling.dispatch("sources.read", {"address": "mad.example.test"})
        problems = {p["name"]: p["problem"] for p in got["publications"]}
        self.assertEqual(problems, {"class-brick": None, "dropin-class": "mismatch", "member-class": None})



if __name__ == "__main__":
    unittest.main()
