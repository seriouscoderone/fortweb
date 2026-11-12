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


if __name__ == "__main__":
    unittest.main()
