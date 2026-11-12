"""The sources.* runtime methods: dispatch, the result returned to the page, and the fault codes."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import sys
import types
import unittest
import warnings
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
    sys.modules[name] = module  # dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _stand_ins(modules: dict):
    """Puts `modules` in sys.modules while a runtime module is loaded, then puts back what was there."""
    original = {key: sys.modules.get(key) for key in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def _load_roling():
    sourcing = _load("sourcing", RUNTIME / "sourcing.py")
    with _stand_ins({"js": types.SimpleNamespace(performance=types.SimpleNamespace(now=lambda: 0.0)),
                     "vaulting": types.SimpleNamespace(RuntimeFault=RuntimeFault), "sourcing": sourcing}):
        return _load("fortweb_roling_test", RUNTIME / "roling.py"), sourcing


def _load_worker(roling, posted: list):
    """wallet-worker.py, loaded as the worker loads it, around this `roling`. What it posts to the page is appended
    to `posted`. Its other modules are stand-ins that take their settings and do nothing."""
    stand_in = types.SimpleNamespace(configure_runtime=lambda **settings: None, RuntimeFault=RuntimeFault,
                                     BrowserClienter=object)
    modules = {
        "js": types.SimpleNamespace(self=types.SimpleNamespace(postMessage=posted.append)),
        "polyscript": types.SimpleNamespace(config=types.SimpleNamespace(to_py=dict)),
        "onboarding": stand_in, "transporting": stand_in, "vaulting": stand_in,
        "runtime_packages": types.SimpleNamespace(),
        "roling": roling,
    }
    with _stand_ins(modules), warnings.catch_warnings():  # the worker sets warning filters of its own
        return _load("fortweb_wallet_worker_test", RUNTIME / "wallet-worker.py")


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

    async def test_unknown_method_is_a_bad_request(self):
        for method in ("sources.other", "roles.other"):
            with self.subTest(method=method):
                with self.assertRaises(RuntimeFault) as raised:
                    await self.roling.dispatch(method, {})
                self.assertEqual(raised.exception.code, "BAD_REQUEST")


# The roles a vault has taken on: roles.takeOn, roles.list and roles.remove over stand-ins for the vault.

DROPIN = "EFQ6ngR2kib-dwmW8mizJ4k5IdO0YysG0xSty9ESHDE4"
CLASS_BRICK = "EDVEy5suX6WPROMl6vSc5Kc4I86mXxyFsa_xBla4A_dY"
VAULT = "my-wallet"
OPEN_AGAIN = "Open the address again before taking on a role."
ALREADY = "This identifier has already taken on that role."


class FakeDb:
    """What the stand-in Komer stores, by sub-database: {subkey: {keys: JSON text}}, as the vault's db keeps it."""

    def __init__(self):
        self.subdbs: dict = {}


class FakeKomer:
    """koming.Komer as roling uses it: get, pin, put, rem and getTopItemIter over one sub-database, values kept
    as JSON so that what is read back is a new object, as from the real store."""

    def __init__(self, *, db, subkey, klas):
        if db.closed:
            raise AssertionError("a closed vault was used")
        self.items = db.db.subdbs.setdefault(subkey, {})
        self.klas = klas

    @staticmethod
    def _key(keys):
        return tuple(keys) if isinstance(keys, (tuple, list)) else (keys,)

    def get(self, keys):
        text = self.items.get(self._key(keys))
        return None if text is None else self.klas(**json.loads(text))

    def pin(self, keys, val):
        import dataclasses
        self.items[self._key(keys)] = json.dumps(dataclasses.asdict(val))
        return True

    def put(self, keys, val):
        if self._key(keys) in self.items:
            return False
        return self.pin(keys, val)

    def rem(self, keys):
        return self.items.pop(self._key(keys), None) is not None

    def getTopItemIter(self, keys=b""):
        for key in sorted(self.items):
            yield key, self.klas(**json.loads(self.items[key]))


class FakeHab:
    def __init__(self, name, pre):
        self.name, self.pre = name, pre


class FakeHby:
    """A Habery stand-in. persist_and_reload closes it and gives a new one over the same data, as the real one does."""

    def __init__(self, db: FakeDb, habs: dict):
        self.closed = False
        self._habs = habs
        self.db = types.SimpleNamespace(db=db, closed=False)

    def _check(self):
        if self.closed:
            raise AssertionError("a closed vault was used")

    def habByName(self, name):
        self._check()
        return next((hab for hab in self._habs.values() if hab.name == name), None)

    def habByPre(self, pre):
        self._check()
        return self._habs.get(pre)

    def makeHab(self, name, **settings):
        self._check()
        assert settings == {"icount": 1, "isith": "1", "ncount": 1, "nsith": "1"}, settings
        pre = f"E{len(self._habs):043d}"
        self._habs[pre] = FakeHab(name, pre)
        return self._habs[pre]


class FakeVaulting:
    """The vaulting functions roling uses, over one open vault."""

    RuntimeFault = RuntimeFault

    def __init__(self):
        self.data = FakeDb()
        self.habs: dict = {}
        self.state = {"vault": {"id": VAULT}, "hby": FakeHby(self.data, self.habs)}
        self.reloads = 0

    def require_open_state(self, vault_id=None):
        if self.state is None or (vault_id and vault_id != VAULT):
            raise RuntimeFault("LOCKED", "Open a vault before calling vault operations.")
        return self.state

    async def persist_and_reload(self):
        self.reloads += 1
        self.state["hby"].closed = True
        self.state["hby"].db.closed = True
        self.state = {"vault": {"id": VAULT}, "hby": FakeHby(self.data, self.habs)}
        return self.state

    def require_text(self, value, *, field):
        text = str(value or "").strip()
        if not text:
            raise RuntimeFault("VALIDATION", f"{field} is required.")
        return text

    def create_identifier(self, hby, alias):
        alias = self.require_text(alias, field="Identifier alias")
        if hby.habByName(alias) is not None:
            raise RuntimeFault("CONFLICT", f"Identifier alias '{alias}' already exists.")
        return hby.makeHab(name=alias, icount=1, isith="1", ncount=1, nsith="1")

    def identifier_hab(self, hby, aid):
        hab = hby.habByPre(aid)
        if hab is None:
            raise RuntimeFault("NOT_FOUND", f"Identifier '{aid}' was not found.")
        return hab

    def load_modules(self):
        return {"koming": types.SimpleNamespace(Komer=FakeKomer)}


class RolesStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.roling, self.sourcing = _load_roling()
        self.roling.configure_runtime(emit_diagnostic=lambda event, **fields: None,
                                      roles_subdb="frol.", role_definitions_subdb="fdef.")
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        document = (FIXTURES / "source.json").read_bytes()

        async def fetch_bytes(url, timeout_ms):
            if url == ADDRESS:
                return document
            return files[url[len(BASE):-len(".json")]]
        self.roling._fetch_bytes = fetch_bytes
        self.read = await self.roling.dispatch("sources.read", {"address": "mad.seriouscoder.one"})

        # From here on nothing may reach the network.
        self.fetched = []

        async def no_fetch(url, timeout_ms):
            self.fetched.append(url)
            raise AssertionError("roles.* must not fetch")
        self.roling._fetch_bytes = no_fetch
        self.roling.js = types.SimpleNamespace(fetch=lambda *args: self.fetched.append(args))
        self.vault = FakeVaulting()
        self.roling.vaulting = self.vault
        self.clock = iter(f"2026-11-12T10:00:{second:02d}.000000+00:00" for second in range(60))
        self.roling._now = lambda: next(self.clock)

    def tearDown(self):
        self.assertEqual(self.fetched, [])

    def role_of_read(self, target, role):
        publication = next(p for p in self.read["publications"] if p["target"] == target)
        return next(r for r in publication["roles"] if r["role"] == role)

    async def take_on(self, role="cashier", identifier=None, target=DROPIN, address="mad.seriouscoder.one"):
        return await self.roling.dispatch("roles.takeOn", {
            "vaultId": VAULT, "address": address, "target": target, "role": role,
            "identifier": identifier or {"mode": "new", "alias": role},
        })

    async def fault(self, method, params):
        with self.assertRaises(RuntimeFault) as raised:
            await self.roling.dispatch(method, params)
        return raised.exception.code, str(raised.exception)

    def stored(self, subkey):
        return self.vault.data.subdbs.get(subkey, {})

    async def test_take_on_with_a_new_identifier_stores_the_role_and_its_definitions(self):
        got = await self.take_on("cashier")
        (pre, hab), = self.vault.habs.items()
        self.assertEqual(hab.name, "cashier")
        expected = self.role_of_read(DROPIN, "cashier")
        view = got["role"]
        self.assertEqual({key: value for key, value in view.items() if key != "id"}, {
            "address": ADDRESS, "host": "mad.seriouscoder.one", "target": DROPIN, "publication": "dropin-class",
            "title": "Drop-in takes a class", "role": "cashier", "aid": pre, "identifierAlias": "cashier",
            "takenAt": "2026-11-12T10:00:00.000000+00:00", "responsibilities": expected["responsibilities"],
            "warnings": [{"code": "NOT_APPOINTED", "message": "Not appointed yet"}],
        })
        self.assertEqual(len(view["id"]), 32)
        self.assertEqual(self.vault.reloads, 1)

        (key, text), = self.stored("frol.").items()
        record = json.loads(text)
        self.assertEqual(key, (view["id"],))
        self.assertEqual(record["responsibilities"], [r["said"] for r in expected["responsibilities"]])
        needed = {DROPIN}
        for responsibility in expected["responsibilities"]:
            needed.add(responsibility["said"])
            refs = responsibility["reliesOn"] + responsibility["attests"] + [responsibility["outcome"] or {}]
            needed.update(ref["said"] for ref in refs if ref.get("said") in self.roling._LAST_READ["definitions"])
        self.assertEqual(set(record["definitions"]), needed)
        self.assertEqual(len(record["definitions"]), len(needed))
        self.assertEqual({key[0]: json.loads(text) for key, text in self.stored("fdef.").items()},
                         {said: {"said": said, "text": self.roling._LAST_READ["definitions"][said]}
                          for said in needed})

    async def test_take_on_with_an_existing_identifier(self):
        await self.take_on("cashier")
        (pre, _), = self.vault.habs.items()
        got = await self.take_on("instructor", {"mode": "existing", "aid": pre})
        self.assertEqual((got["role"]["aid"], got["role"]["identifierAlias"]), (pre, "cashier"))
        self.assertEqual(len(self.vault.habs), 1)
        self.assertEqual(got["role"]["responsibilities"], self.role_of_read(DROPIN, "instructor")["responsibilities"])

    async def test_the_same_role_twice_for_the_same_identifier_is_a_conflict(self):
        await self.take_on("instructor")
        (pre, _), = self.vault.habs.items()
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN, "role": "instructor",
            "identifier": {"mode": "existing", "aid": pre}}), ("CONFLICT", ALREADY))
        self.assertEqual(len(self.stored("frol.")), 1)
        # Another publication, or another identifier, is a different role taken on.
        await self.take_on("instructor", {"mode": "existing", "aid": pre}, target=CLASS_BRICK)
        await self.take_on("instructor", {"mode": "new", "alias": "instructor 2"})
        self.assertEqual(len(self.stored("frol.")), 3)

    async def test_take_on_needs_the_last_read_to_hold_that_role(self):
        cases = {
            "another address": dict(address="other.example.test"),
            "an address that cannot be opened": dict(address="http://x"),
            "a publication the read does not have": dict(target="E" + "A" * 43),
            "a role the publication does not name": dict(role="janitor"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                params = {"vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN, "role": "cashier",
                          "identifier": {"mode": "new", "alias": "x"}}
                params.update(change)
                self.assertEqual(await self.fault("roles.takeOn", params), ("CONFLICT", OPEN_AGAIN))
        self.assertEqual((self.vault.habs, self.stored("frol."), self.vault.reloads), ({}, {}, 0))

    async def test_take_on_with_no_read_or_a_publication_with_a_problem_is_a_conflict(self):
        dropin = next(p for p in self.roling._LAST_READ["publications"] if p["target"] == DROPIN)
        dropin["problem"], dropin["roles"] = "mismatch", []
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN, "role": "cashier",
            "identifier": {"mode": "new", "alias": "x"}}), ("CONFLICT", OPEN_AGAIN))
        self.roling._LAST_READ = None
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": CLASS_BRICK, "role": "instructor",
            "identifier": {"mode": "new", "alias": "x"}}), ("CONFLICT", OPEN_AGAIN))
        self.assertEqual(self.vault.habs, {})

    async def test_a_taken_alias_is_the_identifier_conflict(self):
        await self.take_on("cashier")
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN, "role": "instructor",
            "identifier": {"mode": "new", "alias": "cashier"}}),
            ("CONFLICT", "Identifier alias 'cashier' already exists."))
        self.assertEqual(len(self.stored("frol.")), 1)

    async def test_an_unknown_existing_identifier_is_not_found(self):
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN, "role": "cashier",
            "identifier": {"mode": "existing", "aid": "Enope"}}), ("NOT_FOUND", "Identifier 'Enope' was not found."))

    async def test_a_bad_identifier_choice_is_a_bad_request(self):
        for identifier in (None, "cashier", {"mode": "other"}):
            with self.subTest(identifier=identifier):
                code, _ = await self.fault("roles.takeOn", {
                    "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN, "role": "cashier",
                    "identifier": identifier})
                self.assertEqual(code, "BAD_REQUEST")

    async def test_list_is_newest_first_and_derived_from_the_stored_definitions(self):
        await self.take_on("cashier")
        await self.take_on("instructor", target=CLASS_BRICK)
        # The read is gone (the worker restarted): the list comes from the vault alone.
        self.roling._LAST_READ = None
        got = await self.roling.dispatch("roles.list", {"vaultId": VAULT})
        self.assertEqual([(r["role"], r["publication"]) for r in got["roles"]],
                         [("instructor", "class-brick"), ("cashier", "dropin-class")])
        self.assertEqual(got["roles"][1]["responsibilities"], self.role_of_read(DROPIN, "cashier")["responsibilities"])
        self.assertEqual(got["roles"][0]["responsibilities"],
                         self.role_of_read(CLASS_BRICK, "instructor")["responsibilities"])
        for role in got["roles"]:
            self.assertEqual(role["warnings"], [{"code": "NOT_APPOINTED", "message": "Not appointed yet"}])
        self.assertEqual(self.vault.reloads, 2)  # listing writes nothing

    async def test_list_of_a_vault_with_no_roles_is_empty(self):
        self.assertEqual(await self.roling.dispatch("roles.list", {"vaultId": VAULT}), {"roles": []})

    async def test_list_needs_the_open_vault(self):
        self.assertEqual((await self.fault("roles.list", {"vaultId": "other"}))[0], "LOCKED")

    async def test_remove_deletes_the_record_and_only_the_definitions_no_other_role_lists(self):
        dropin = (await self.take_on("instructor"))["role"]
        instructor = (await self.take_on("instructor", {"mode": "new", "alias": "teacher"}, target=CLASS_BRICK))["role"]
        before = set(self.stored("fdef."))
        records = {json.loads(text)["id"]: set(json.loads(text)["definitions"])
                   for text in self.stored("frol.").values()}
        self.assertTrue(records[dropin["id"]] & records[instructor["id"]])  # e.g. class-attended
        self.assertEqual(await self.roling.dispatch("roles.remove", {"vaultId": VAULT, "id": dropin["id"]}),
                         {"removed": True})
        self.assertEqual(self.vault.reloads, 3)
        self.assertEqual(list(self.stored("frol.")), [(instructor["id"],)])
        self.assertEqual(set(self.stored("fdef.")), {(said,) for said in records[instructor["id"]]})
        self.assertTrue(before - set(self.stored("fdef.")))
        self.assertEqual(len(self.vault.habs), 2)  # the identifier stays
        got = await self.roling.dispatch("roles.list", {"vaultId": VAULT})
        self.assertEqual([r["id"] for r in got["roles"]], [instructor["id"]])

    async def test_remove_of_an_unknown_role_is_not_found(self):
        self.assertEqual((await self.fault("roles.remove", {"vaultId": VAULT, "id": "nope"}))[0], "NOT_FOUND")
        self.assertEqual(self.vault.reloads, 0)


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
    """A response body: each read serves the next item of `chunks`, then the body is done.

    An item is the bytes of that read. A function is called by that read: it returns the bytes, or raises and the
    read rejects. An exception is raised when the bytes are taken out of the read (`value.to_py()`).
    """

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.reads = 0
        self.cancelled = False

    async def read(self):
        self.reads += 1
        if not self.chunks:
            return types.SimpleNamespace(done=True, value=None)
        chunk = self.chunks.pop(0)
        if callable(chunk):
            chunk = chunk()

        def to_py():
            if isinstance(chunk, Exception):
                raise chunk
            return chunk
        return types.SimpleNamespace(done=False, value=types.SimpleNamespace(to_py=to_py))

    async def cancel(self):
        self.cancelled = True


def response(status=200, content_type="application/json", chunks=(b"{}",), body=True):
    reader = Reader(chunks)
    headers = types.SimpleNamespace(get=lambda name: {"content-type": content_type}.get(name.lower()))
    return types.SimpleNamespace(status=status, headers=headers,
                                 body=types.SimpleNamespace(getReader=lambda: reader) if body else None,
                                 reader=reader)


def answering(make):
    """An `answer` for StubJs that serves `make()` to every fetch."""
    async def answer(url, options):
        return make()
    return answer


async def unreachable(url, options):
    """An `answer` for StubJs: every fetch is rejected, the probe too."""
    raise RuntimeError("TypeError: Failed to fetch")


async def not_allowed(url, options):
    """An `answer` for StubJs: the read is rejected and the probe is answered."""
    if options.mode == "cors":
        raise RuntimeError("TypeError: Failed to fetch")
    return types.SimpleNamespace(status=0, type="opaque")


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

    def diagnostics(self) -> list:
        """Gives roling a diagnostic emitter, as the worker does; returns the (event, fields) it is called with."""
        emitted = []
        self.roling.configure_runtime(emit_diagnostic=lambda event, **fields: emitted.append((event, fields)),
                                      roles_subdb="frol.", role_definitions_subdb="fdef.")
        return emitted

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

    async def test_too_much_fault_carries_the_bytes_read_before_it(self):
        served = response(chunks=(b"x" * 600_000, b"x" * 500_000, b"x" * 10))

        async def answer(url, options):
            return served
        self.stub(answer)
        fault = await self.fault()
        # What was read, not the limit: the second chunk took the body to 1,100,000 bytes.
        self.assertEqual((fault.code, fault.received), ("TOO_MUCH", 1_100_000))

    async def test_body_that_stops_midway_carries_the_bytes_read_so_far(self):
        for cause in ("the request's timer", "the connection"):
            with self.subTest(stopped_by=cause):
                def third_read():
                    if cause == "the request's timer":
                        js.fire(0)  # the time runs out while the third read is waiting
                        raise RuntimeError("AbortError: signal is aborted without reason")
                    raise RuntimeError("TypeError: network error")
                served = response(chunks=(b"x" * 300_000, b"x" * 200_000, third_read))

                async def answer(url, options):
                    return served
                js = self.stub(answer)
                fault = await self.fault()
                self.assertEqual((fault.code, fault.message, fault.received),
                                 ("UNREACHABLE", "Could not reach mad.example.test.", 500_000))
                self.assertTrue(js.controllers[0].aborted)
                self.assertEqual(len(js.calls), 1)  # a body that stopped is not probed

    async def test_unexpected_error_after_one_chunk_carries_that_chunk(self):
        served = response(chunks=(b"x" * 1_234, RuntimeError("JsException: secret internal detail"), b"x" * 10))

        async def answer(url, options):
            return served
        self.stub(answer)
        fault = await self.fault()
        self.assertEqual((fault.code, fault.message, fault.received),
                         ("UNREACHABLE", "Could not reach mad.example.test.", 1_234))

    async def test_faults_before_any_body_byte_carry_no_received_bytes(self):
        cases = {
            "status": (answering(lambda: response(status=404, chunks=(b"x" * 500,))), "BAD_SOURCE"),
            "content type": (answering(lambda: response(content_type="text/html", chunks=(b"x" * 500,))), "BAD_SOURCE"),
            "missing body": (answering(lambda: response(body=False)), "BAD_SOURCE"),
            "unreachable": (unreachable, "UNREACHABLE"),
            "not readable": (not_allowed, "NOT_READABLE"),
        }
        for name, (answer, code) in cases.items():
            with self.subTest(case=name):
                self.stub(answer)
                fault = await self.fault()
                self.assertEqual((fault.code, fault.received), (code, 0))

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

    async def test_probe_has_its_own_short_budget(self):
        async def answer(url, options):
            if options.mode == "cors":
                raise RuntimeError("TypeError: Failed to fetch")
            return types.SimpleNamespace(status=0, type="opaque")
        js = self.stub(answer)
        await self.fault(timeout_ms=10_000)
        self.assertEqual(self.roling.PROBE_MS, 3_000)
        self.assertEqual([t.ms for t in js.timers], [10_000, 3_000])
        self.assertTrue(all(t.cleared for t in js.timers))

    async def test_probe_is_aborted_once_it_settles(self):
        for probe_resolves in (True, False):
            with self.subTest(probe_resolves=probe_resolves):
                async def answer(url, options):
                    if options.mode == "cors" or not probe_resolves:
                        raise RuntimeError("TypeError: Failed to fetch")
                    return types.SimpleNamespace(status=0, type="opaque")
                js = self.stub(answer)
                await self.fault()
                self.assertEqual(len(js.controllers), 2)
                self.assertTrue(js.controllers[1].aborted)

    async def test_read_rejected_after_its_time_is_not_probed(self):
        async def answer(url, options):
            js.now += 10_000  # rejected just as the request's time ran out, before its timer was run
            raise RuntimeError("TypeError: Failed to fetch")
        js = self.stub(answer)
        fault = await self.fault(timeout_ms=10_000)
        self.assertEqual(fault.code, "UNREACHABLE")
        self.assertEqual(len(js.calls), 1)

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

    async def test_unexpected_error_is_reported_as_a_warning_naming_only_its_class_and_the_host(self):
        def broken_headers(name):
            raise RuntimeError("JsException: secret internal detail")

        cases = {
            "before the body": ("RuntimeError", lambda: types.SimpleNamespace(
                status=200, headers=types.SimpleNamespace(get=broken_headers), body=None)),
            "midway through the body": ("ValueError", lambda: response(
                chunks=(b'{"private":"content of the response"', ValueError("JsException: secret internal detail")))),
        }
        for name, (exception, make) in cases.items():
            with self.subTest(case=name):
                emitted = self.diagnostics()

                async def answer(url, options):
                    return make()
                self.stub(answer)
                fault = await self.fault()
                # Exactly these fields: neither the error's text nor anything the source sent.
                self.assertEqual(emitted, [("source_fetch_unexpected_error",
                                            {"level": "warning", "exception": exception, "host": "mad.example.test"})])
                # What the person is told stays the same.
                self.assertEqual((fault.code, fault.message), ("UNREACHABLE", "Could not reach mad.example.test."))

    async def test_expected_faults_are_not_reported(self):
        def dropped():
            raise RuntimeError("TypeError: network error")

        cases = {
            "status": (answering(lambda: response(status=404)), "BAD_SOURCE"),
            "content type": (answering(lambda: response(content_type="text/html")), "BAD_SOURCE"),
            "too much": (answering(lambda: response(chunks=(b"x" * 1_000_001,))), "TOO_MUCH"),
            "unreachable": (unreachable, "UNREACHABLE"),
            "not readable": (not_allowed, "NOT_READABLE"),
            "body stops midway": (answering(lambda: response(chunks=(b"x" * 10, dropped))), "UNREACHABLE"),
        }
        for name, (answer, code) in cases.items():
            with self.subTest(case=name):
                emitted = self.diagnostics()
                self.stub(answer)
                self.assertEqual((await self.fault()).code, code)
                self.assertEqual(emitted, [])

    async def test_good_read_is_not_reported(self):
        emitted = self.diagnostics()
        self.stub(answering(response))
        self.assertEqual(await self.roling._fetch_bytes(URL, 10_000), b"{}")
        self.assertEqual(emitted, [])

    async def test_report_that_cannot_be_made_does_not_change_the_fault(self):
        def emit(event, **fields):
            raise RuntimeError("the page is not listening")
        self.roling.configure_runtime(emit_diagnostic=emit, roles_subdb="frol.", role_definitions_subdb="fdef.")
        served = response(chunks=(b"x" * 1_234, RuntimeError("JsException: secret internal detail")))

        async def answer(url, options):
            return served
        self.stub(answer)
        fault = await self.fault()
        self.assertEqual((fault.code, fault.message, fault.received),
                         ("UNREACHABLE", "Could not reach mad.example.test.", 1_234))

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

    async def test_read_of_definitions_over_the_file_limit_stops_at_the_byte_budget(self):
        document = (FIXTURES / "source.json").read_text("utf-8").replace(ADDRESS, URL).encode()

        async def answer(url, options):
            if url == URL:
                return response(chunks=(document,))
            return response(chunks=(b"x" * 600_000, b"x" * 400_001))  # every definition: 1,000,001 bytes
        js = self.stub(answer)
        got = await self.roling.dispatch("sources.read", {"address": "mad.example.test"})
        # Four such files spend the read's 4,000,000 bytes: the other 22 definitions are never requested.
        self.assertEqual(len(js.calls), 1 + 4)
        self.assertEqual([p["problem"] for p in got["publications"]], ["mismatch"] * 3)
        self.assertEqual(got["checked"], 0)

    async def test_read_never_starts_a_fetch_after_sixty_seconds(self):
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        document = (FIXTURES / "source.json").read_text("utf-8").replace(ADDRESS, URL).encode()

        async def answer(url, options):
            js.now += 4_000 if options.mode == "cors" else 0
            if url == URL:
                return response(chunks=(document,))
            if len(js.calls) % 3 == 0:
                raise RuntimeError("TypeError: Failed to fetch")  # probed; the probe answers at once
            return response(chunks=(files[url.rsplit("/", 1)[1][:-len(".json")]],))
        js = self.stub(answer)
        js.now = 50_000.0
        with self.assertRaises(RuntimeFault) as raised:
            await self.roling.dispatch("sources.read", {"address": "mad.example.test"})
        self.assertEqual((raised.exception.code, str(raised.exception)),
                         ("NETWORK_ERROR", "Could not reach mad.example.test."))
        self.assertTrue(js.calls)
        self.assertTrue(all(at < 50_000 + 60_000 for _, _, at in js.calls), [at for _, _, at in js.calls])
        self.assertEqual(sum(1 for _, options, _ in js.calls if options.mode == "cors"), 15)


class WorkerDiagnosticTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_gives_roling_its_emitter_so_the_report_reaches_the_page(self):
        roling, sourcing = _load_roling()
        posted = []
        _load_worker(roling, posted)

        async def answer(url, options):
            return types.SimpleNamespace(status=200, headers=None, body=None)  # .get on None: AttributeError
        roling.js = StubJs(answer)
        with self.assertRaises(sourcing.SourceFault):
            await roling._fetch_bytes(URL, 10_000)
        # The message the page's bridge takes for a worker diagnostic and passes on to the log.
        self.assertEqual([json.loads(message) for message in posted], [{
            "kind": "fortweb.runtime.diagnostic", "event": "source_fetch_unexpected_error", "level": "warning",
            "exception": "AttributeError", "host": "mad.example.test"}])


if __name__ == "__main__":
    unittest.main()
