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


def _load_worker(roling, posted: list, vaulting=None):
    """wallet-worker.py, loaded as the worker loads it, around this `roling`. What it posts to the page is appended
    to `posted`. Its other modules are stand-ins that take their settings and do nothing (`vaulting` may be given)."""
    stand_in = types.SimpleNamespace(configure_runtime=lambda **settings: None, RuntimeFault=RuntimeFault,
                                     BrowserClienter=object)
    modules = {
        "js": types.SimpleNamespace(self=types.SimpleNamespace(postMessage=posted.append)),
        "polyscript": types.SimpleNamespace(config=types.SimpleNamespace(to_py=dict)),
        "onboarding": stand_in, "transporting": stand_in, "vaulting": vaulting or stand_in,
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
            ("NOT_READABLE", "Could not read mad.seriouscoder.one: it cannot be reached, or it does not allow wallets "
             "to read it yet.", "NETWORK_ERROR"),
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
NAMES = {DROPIN: "dropin-class", CLASS_BRICK: "class-brick"}  # publication names in the fixture source


class FakeDb:
    """What the stand-in Komer stores, by sub-database: {subkey: {keys: serialized bytes}}, as the vault's db keeps
    it."""

    def __init__(self):
        self.subdbs: dict = {}
        # fail_write(subkey, keys) -> True makes that write raise StoreWriteError, as a store that cannot write.
        self.fail_write = lambda subkey, keys: False


class StoreWriteError(Exception):
    pass


class FakeKomer:
    """koming.Komer as roling uses it: get, pin, put, rem and getTopItemIter over one sub-database.

    Values are serialized and read back as the real store does (KERIpy koming, __serializeJSON and
    __deserializeJSON; dataclasses.asdict stands in for dictify and the dataclass for datify): UTF-8 JSON bytes that
    are not ASCII-escaped, so a value the real store cannot write fails here too, and what is read back is a new
    object."""

    def __init__(self, *, db, subkey, klas):
        if db.closed:
            raise AssertionError("a closed vault was used")
        self.db = db.db
        self.subkey = subkey
        self.items = db.db.subdbs.setdefault(subkey, {})
        self.klas = klas

    @staticmethod
    def _key(keys):
        return tuple(keys) if isinstance(keys, (tuple, list)) else (keys,)

    def _des(self, val):
        return self.klas(**json.loads(bytes(val).decode("utf-8")))

    def get(self, keys):
        val = self.items.get(self._key(keys))
        return None if val is None else self._des(val)

    def pin(self, keys, val):
        import dataclasses
        if self.db.fail_write(self.subkey, self._key(keys)):
            raise StoreWriteError(f"cannot write in {self.subkey}")
        self.items[self._key(keys)] = json.dumps(dataclasses.asdict(val), separators=(",", ":"),
                                                 ensure_ascii=False).encode("utf-8")
        return True

    def put(self, keys, val):
        if self._key(keys) in self.items:
            return False
        return self.pin(keys, val)

    def rem(self, keys):
        return self.items.pop(self._key(keys), None) is not None

    def getTopItemIter(self, keys=b""):
        for key in sorted(self.items):
            yield key, self._des(self.items[key])


class FakeHab:
    def __init__(self, name, pre):
        self.name, self.pre = name, pre


class FakeHby:
    """A Habery stand-in. persist_and_reload closes it and gives a new one over the same data, as the real one does."""

    def __init__(self, db: FakeDb, habs: dict):
        self.closed = False
        self.make_fails = False
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
        if self.make_fails:
            raise RuntimeError("the identifier could not be made")
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

    def free_alias(self, hby, alias):
        alias = self.require_text(alias, field="Identifier alias")
        if hby.habByName(alias) is not None:
            raise RuntimeFault("CONFLICT", f"Identifier alias '{alias}' already exists.")
        return alias

    def create_identifier(self, hby, alias):
        return hby.makeHab(name=self.free_alias(hby, alias), icount=1, isith="1", ncount=1, nsith="1")

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
        self.diagnostics = []
        self.roling.configure_runtime(emit_diagnostic=lambda event, **fields: self.diagnostics.append((event, fields)),
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

    async def take_on(self, role="cashier", identifier=None, target=DROPIN, address="mad.seriouscoder.one",
                      publication=None):
        return await self.roling.dispatch("roles.takeOn", {
            "vaultId": VAULT, "address": address, "target": target, "publication": publication or NAMES.get(target),
            "role": role,
            "identifier": identifier or {"mode": "new", "alias": role},
        })

    async def fault(self, method, params):
        with self.assertRaises(RuntimeFault) as raised:
            await self.roling.dispatch(method, params)
        return raised.exception.code, str(raised.exception)

    def stored(self, subkey):
        return self.vault.data.subdbs.get(subkey, {})

    def snapshot(self):
        """Everything the vault holds: the two stores, the identifiers, and how often it was persisted."""
        return ({subkey: dict(items) for subkey, items in self.vault.data.subdbs.items() if items},
                {pre: hab.name for pre, hab in self.vault.habs.items()}, self.vault.reloads)

    async def assert_not_kept(self, params, before):
        """roles.takeOn with `params` fails as a role the wallet could not keep, and leaves the vault as it was."""
        self.diagnostics.clear()
        self.assertEqual(await self.fault("roles.takeOn", params),
                         ("BAD_RESPONSE", "The wallet could not keep that role."))
        self.assertEqual(self.snapshot(), before)
        return self.diagnostics

    def params(self, role="instructor", identifier=None, target=DROPIN, publication=None):
        return {"vaultId": VAULT, "address": "mad.seriouscoder.one", "target": target,
                "publication": publication or NAMES.get(target), "role": role,
                "identifier": identifier or {"mode": "new", "alias": "new one"}}

    async def test_a_store_that_cannot_write_leaves_nothing_behind(self):
        # A role already taken on shares definitions (class-attended) with the one that fails.
        await self.take_on("instructor", {"mode": "new", "alias": "teacher"}, target=CLASS_BRICK)
        (pre, _), = self.vault.habs.items()
        needed = self.roling._needed(DROPIN, self.role_of_read(DROPIN, "instructor"),
                                     self.roling._LAST_READ["derivedFrom"][DROPIN])
        shared = {said for said in json.loads(next(iter(self.stored("frol.").values())))["definitions"]}
        self.assertTrue(shared & set(needed) and set(needed) - shared)
        before = self.snapshot()
        cases = {
            "the first definition": lambda subkey, keys: keys == (needed[0],),
            "a later definition": lambda subkey, keys: keys == (needed[-1],),
            "the record": lambda subkey, keys: subkey == "frol.",
        }
        for name, fail_write in cases.items():
            for identifier in ({"mode": "new", "alias": "new one"}, {"mode": "existing", "aid": pre}):
                with self.subTest(name, mode=identifier["mode"]):
                    self.vault.data.fail_write = fail_write
                    diagnostics = await self.assert_not_kept(self.params(identifier=identifier), before)
                    self.assertEqual(diagnostics, [("role_store_failed",
                                                    {"level": "error", "exception": "StoreWriteError"})])
        self.vault.data.fail_write = lambda subkey, keys: False
        await self.take_on("instructor", {"mode": "existing", "aid": pre})  # the store works again

    async def test_an_identifier_that_cannot_be_made_leaves_nothing_behind(self):
        await self.take_on("cashier")
        before = self.snapshot()
        self.vault.state["hby"].make_fails = True
        diagnostics = await self.assert_not_kept(self.params(), before)
        self.assertEqual(diagnostics, [("role_store_failed", {"level": "error", "exception": "RuntimeError"})])

    async def test_a_role_that_cannot_be_stored_as_utf8_leaves_nothing_behind(self):
        # The reviewer's reproduction at unit level: text with an unpaired surrogate cannot be written as UTF-8.
        await self.take_on("cashier")
        before = self.snapshot()
        dropin = next(p for p in self.roling._LAST_READ["publications"] if p["target"] == DROPIN)
        dropin["title"] = "Drop-in takes a class \ud83d"
        diagnostics = await self.assert_not_kept(self.params(), before)
        self.assertEqual(diagnostics, [("role_store_failed",
                                        {"level": "error", "exception": "UnicodeEncodeError"})])

    async def test_a_record_that_cannot_be_completed_leaves_only_the_identifier_just_made(self):
        # The known limit: the role's data is written, the identifier is made, and the write that completes the
        # record with it fails. The role's data is removed; the identifier stays and can take the role on.
        await self.take_on("instructor", {"mode": "new", "alias": "teacher"}, target=CLASS_BRICK)  # shares definitions
        stores, identifiers, reloads = self.snapshot()
        record_writes = []

        def fail_write(subkey, keys):
            if subkey == "frol.":
                record_writes.append(keys)
            return subkey == "frol." and len(record_writes) == 2
        self.vault.data.fail_write = fail_write
        self.diagnostics.clear()
        self.assertEqual(await self.fault("roles.takeOn", self.params(identifier={"mode": "new", "alias": "new one"})),
                         ("BAD_RESPONSE", "The wallet could not keep that role."))
        self.assertEqual(self.diagnostics, [("role_store_failed", {"level": "error", "exception": "StoreWriteError"})])
        self.assertEqual(len(record_writes), 2)
        stores_after, identifiers_after, reloads_after = self.snapshot()
        self.assertEqual((stores_after, reloads_after), (stores, reloads))  # no record and no added definition
        self.assertEqual(sorted(identifiers_after.values()), ["new one", "teacher"])
        self.vault.data.fail_write = lambda subkey, keys: False
        made = next(pre for pre, name in identifiers_after.items() if name == "new one")
        taken = await self.take_on("instructor", {"mode": "existing", "aid": made})
        self.assertEqual(taken["role"]["identifierAlias"], "new one")

    async def test_take_on_with_a_new_identifier_stores_the_role_and_its_definitions(self):
        got = await self.take_on("cashier")
        (pre, hab), = self.vault.habs.items()
        self.assertEqual(hab.name, "cashier")
        expected = self.role_of_read(DROPIN, "cashier")
        view = got["role"]
        self.assertEqual({key: value for key, value in view.items() if key != "id"}, {
            "address": ADDRESS, "host": "mad.seriouscoder.one", "target": DROPIN,
            "publication": "dropin-class",
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
        # What the role's view refers to, within the publication's closure (what the read derived it from).
        serves = self.sourcing.read_source_document((FIXTURES / "source.json").read_bytes(), ADDRESS)["document"]
        reachable = set(self.sourcing.closure(serves["serves"], DROPIN))
        needed = {DROPIN}
        for responsibility in expected["responsibilities"]:
            needed.add(responsibility["said"])
            refs = responsibility["reliesOn"] + responsibility["attests"] + [responsibility["outcome"] or {}]
            needed.update(ref["said"] for ref in refs if ref.get("said") in reachable)
        self.assertEqual(set(record["definitions"]), needed)
        self.assertEqual(len(record["definitions"]), len(needed))
        self.assertEqual({key[0]: json.loads(text) for key, text in self.stored("fdef.").items()},
                         {said: {"said": said, "text": self.roling._LAST_READ["definitions"][said]}
                          for said in needed})

    async def read_two_selections(self):
        """A source whose publication "first" (selection A, role cashier) relies on a fact that only publication
        "second" (selection B) reaches through `refers`. The read derives A's roles from A's closure alone."""
        import blake3
        fill = "#" * 44

        def make(body):
            raw = json.dumps(dict(d=fill, **body), separators=(",", ":")).encode()
            said = self.sourcing.encode_said(blake3.blake3(raw).digest())
            return said, raw.replace(fill.encode(), said.encode())
        fact, fact_raw = make({"kind": "fact", "alias": "payment-received"})
        rr1, rr1_raw = make({"kind": "rr", "role": "cashier", "alias": "take-payment",
                             "rely_on": [{"said": fact, "as": "payment"}]})
        rr2, rr2_raw = make({"kind": "rr", "role": "clerk", "alias": "file-payment",
                             "rely_on": [{"said": fact, "as": "payment"}]})
        sel_a, sel_a_raw = make({"kind": "selection", "name": "Selection A", "rrs": [rr1]})
        sel_b, sel_b_raw = make({"kind": "selection", "name": "Selection B", "rrs": [rr2]})
        files = {fact: fact_raw, rr1: rr1_raw, rr2: rr2_raw, sel_a: sel_a_raw, sel_b: sel_b_raw}
        publication = {"description": "", "tags": [], "collection": ""}
        document = json.dumps({
            "source": ADDRESS,
            "serves": [{"said": sel_a, "refers": [rr1]}, {"said": rr1, "refers": []},
                       {"said": sel_b, "refers": [rr2]}, {"said": rr2, "refers": [fact]}, {"said": fact, "refers": []}],
            "publications": [dict(publication, target=sel_b, name="second"),
                             dict(publication, target=sel_a, name="first")],
        }).encode()

        async def fetch_bytes(url, timeout_ms):
            return document if url == ADDRESS else files[url[len(BASE):-len(".json")]]
        no_fetch = self.roling._fetch_bytes
        self.roling._fetch_bytes = fetch_bytes
        self.roling.js.performance = types.SimpleNamespace(now=lambda: 0.0)
        read = await self.roling.dispatch("sources.read", {"address": "mad.seriouscoder.one"})
        self.roling._fetch_bytes = no_fetch  # nothing more may reach the network
        return read, sel_a, fact

    async def test_a_stored_role_reads_as_the_read_showed_it(self):
        read, sel_a, fact = await self.read_two_selections()
        first = next(p for p in read["publications"] if p["name"] == "first")
        as_read = first["roles"][0]["responsibilities"]
        self.assertEqual(as_read[0]["reliesOn"], [{"said": fact, "alias": "", "kind": "", "as": "payment"}])
        taken = (await self.take_on("cashier", target=sel_a, publication="first"))["role"]
        self.assertEqual(taken["responsibilities"], as_read)
        listed = (await self.roling.dispatch("roles.list", {"vaultId": VAULT}))["roles"]
        self.assertEqual(listed[0]["responsibilities"], as_read)
        record = json.loads(next(iter(self.stored("frol.").values())))
        self.assertNotIn(fact, record["definitions"])
        self.assertNotIn((fact,), self.stored("fdef."))

    async def read_dropin_twice(self):
        """The fixture source with dropin-class listed a second time, as "dropin-again"."""
        document = json.loads((FIXTURES / "source.json").read_bytes())
        dropin = next(p for p in document["publications"] if p["target"] == DROPIN)
        document["publications"].append(dict(dropin, name="dropin-again"))
        raw = json.dumps(document).encode()
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}

        async def fetch_bytes(url, timeout_ms):
            return raw if url == ADDRESS else files[url[len(BASE):-len(".json")]]
        no_fetch = self.roling._fetch_bytes
        self.roling._fetch_bytes = fetch_bytes
        self.roling.js.performance = types.SimpleNamespace(now=lambda: 0.0)
        await self.roling.dispatch("sources.read", {"address": "mad.seriouscoder.one"})
        self.roling._fetch_bytes = no_fetch

    async def test_two_publications_with_one_target_are_told_apart_by_name(self):
        await self.read_dropin_twice()
        taken = (await self.take_on("cashier", publication="dropin-again"))["role"]
        self.assertEqual(taken["publication"], "dropin-again")
        taken = (await self.take_on("instructor", {"mode": "new", "alias": "teacher"}, publication="dropin-class"))
        self.assertEqual(taken["role"]["publication"], "dropin-class")
        # Already taken on is decided by address, target, role and identifier, whichever name was used.
        cashier = next(pre for pre, hab in self.vault.habs.items() if hab.name == "cashier")
        self.assertEqual(await self.fault("roles.takeOn", self.params(
            "cashier", {"mode": "existing", "aid": cashier}, publication="dropin-class")), ("CONFLICT", ALREADY))

    async def test_a_missing_or_unknown_publication_name_is_a_conflict(self):
        before = self.snapshot()
        for publication in (None, "", "no-such-name", "class-brick", 7):
            with self.subTest(publication=publication):
                params = self.params("cashier")
                params["publication"] = publication
                if publication is None:
                    del params["publication"]
                self.assertEqual(await self.fault("roles.takeOn", params), ("CONFLICT", OPEN_AGAIN))
        self.assertEqual(self.snapshot(), before)

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
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN,
            "publication": "dropin-class", "role": "instructor",
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
            "an address the URL parser refuses": dict(address="[example.org"),
            "a publication the read does not have": dict(target="E" + "A" * 43),
            "a role the publication does not name": dict(role="janitor"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                params = {"vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN,
                "publication": "dropin-class", "role": "cashier",
                          "identifier": {"mode": "new", "alias": "x"}}
                params.update(change)
                self.assertEqual(await self.fault("roles.takeOn", params), ("CONFLICT", OPEN_AGAIN))
        self.assertEqual((self.vault.habs, self.stored("frol."), self.vault.reloads), ({}, {}, 0))

    async def test_an_alias_refusal_changes_nothing(self):
        await self.take_on("cashier")
        before = self.snapshot()
        for alias, refusal in (("cashier", ("CONFLICT", "Identifier alias 'cashier' already exists.")),
                               ("  ", ("VALIDATION", "Identifier alias is required."))):
            with self.subTest(alias=alias):
                self.assertEqual(await self.fault("roles.takeOn", {
                    "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN,
                    "publication": "dropin-class", "role": "instructor",
                    "identifier": {"mode": "new", "alias": alias}}), refusal)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.diagnostics, [])

    async def test_take_on_with_no_read_or_a_publication_with_a_problem_is_a_conflict(self):
        dropin = next(p for p in self.roling._LAST_READ["publications"] if p["target"] == DROPIN)
        dropin["problem"] = "mismatch"  # its roles stay: only the problem can refuse it
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN,
            "publication": "dropin-class", "role": "cashier",
            "identifier": {"mode": "new", "alias": "x"}}), ("CONFLICT", OPEN_AGAIN))
        self.roling._LAST_READ = None
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": CLASS_BRICK,
            "publication": "class-brick", "role": "instructor",
            "identifier": {"mode": "new", "alias": "x"}}), ("CONFLICT", OPEN_AGAIN))
        self.assertEqual(self.vault.habs, {})

    async def test_a_taken_alias_is_the_identifier_conflict(self):
        await self.take_on("cashier")
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN,
            "publication": "dropin-class", "role": "instructor",
            "identifier": {"mode": "new", "alias": "cashier"}}),
            ("CONFLICT", "Identifier alias 'cashier' already exists."))
        self.assertEqual(len(self.stored("frol.")), 1)

    async def test_an_unknown_existing_identifier_is_not_found(self):
        self.assertEqual(await self.fault("roles.takeOn", {
            "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN,
            "publication": "dropin-class", "role": "cashier",
            "identifier": {"mode": "existing", "aid": "Enope"}}), ("NOT_FOUND", "Identifier 'Enope' was not found."))

    async def test_a_bad_identifier_choice_is_a_bad_request(self):
        for identifier in (None, "cashier", {"mode": "other"}):
            with self.subTest(identifier=identifier):
                code, _ = await self.fault("roles.takeOn", {
                    "vaultId": VAULT, "address": "mad.seriouscoder.one", "target": DROPIN,
                    "publication": "dropin-class", "role": "cashier",
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

    async def test_a_stored_definition_that_cannot_be_parsed_is_treated_as_missing(self):
        taken = (await self.take_on("cashier"))["role"]
        self.assertTrue(taken["responsibilities"])
        definitions = self.stored("fdef.")
        for text in ("{not json", '{"kind": "selection", "rrs": [], "x": NaN}'):
            with self.subTest(text=text):
                self.diagnostics.clear()
                definitions[(DROPIN,)] = json.dumps({"said": DROPIN, "text": text}).encode()
                listed = (await self.roling.dispatch("roles.list", {"vaultId": VAULT}))["roles"]
                # Still listed, with what can be derived without the selection: no responsibilities.
                self.assertEqual([(role["id"], role["responsibilities"]) for role in listed], [(taken["id"], [])])
                self.assertEqual(self.diagnostics, [("role_definition_unreadable",
                                                     {"level": "warning", "said": DROPIN})])

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
NOT_READABLE_SENTENCE = ("Could not read mad.example.test: it cannot be reached, or it does not allow wallets to read "
                         "it yet.")


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
    """An `answer` for StubJs: every fetch is rejected before its time runs out (a host that cannot be reached, or
    one that does not allow the read: a browser does not say which)."""
    raise RuntimeError("TypeError: Failed to fetch")


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

    async def test_content_type_is_json_as_mad_discovery_reads_it(self):
        accepted = ("application/json", "application/json; charset=utf-8", "Application/JSON",
                    "application/schema+json", "application/ld+json;profile=x", " application/json ;q=1")
        refused = ("application/json-seq", "text/json", "text/html", "", None, "application/jsonx")
        for content_type in accepted + refused:
            with self.subTest(content_type=content_type):
                served = response(content_type=content_type)

                async def answer(url, options):
                    return served
                self.stub(answer)
                if content_type in accepted:
                    self.assertEqual(await self.roling._fetch_bytes(URL, 10_000), b"{}")
                else:
                    self.assertEqual((await self.fault()).code, "BAD_SOURCE")
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
                self.assertEqual(len(js.calls), 1)  # a body that stopped is not asked for again

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
            "rejected": (unreachable, "NOT_READABLE"),
        }
        for name, (answer, code) in cases.items():
            with self.subTest(case=name):
                self.stub(answer)
                fault = await self.fault()
                self.assertEqual((fault.code, fault.received), (code, 0))

    async def test_timer_firing_aborts_the_request_and_is_unreachable(self):
        async def answer(url, options):
            js.fire(0)
            raise RuntimeError("AbortError: signal is aborted without reason")
        js = self.stub(answer)
        fault = await self.fault()
        self.assertEqual((fault.code, fault.message), ("UNREACHABLE", "Could not reach mad.example.test."))
        self.assertTrue(js.controllers[0].aborted)
        self.assertEqual(len(js.calls), 1)

    async def test_a_rejected_read_is_not_readable_and_asked_once(self):
        js = self.stub(unreachable)
        fault = await self.fault(timeout_ms=10_000)
        self.assertEqual((fault.code, fault.message), ("NOT_READABLE", NOT_READABLE_SENTENCE))
        self.assertEqual(len(js.calls), 1)
        self.assertTrue(all(t.cleared for t in js.timers))

    def assert_only_reads(self, js: StubJs, document: bytes):
        """Every fetch is a cors GET with the six fixed options, to the source or one of its definitions, and no
        address is asked twice."""
        serves = json.loads(document)["serves"]
        allowed = {URL} | {f"https://mad.example.test/definitions/{item['said']}.json" for item in serves}
        urls = [url for url, _, _ in js.calls]
        self.assertTrue(urls)
        self.assertEqual(sorted(set(urls) - allowed), [])
        self.assertEqual(len(urls), len(set(urls)), urls)
        for url, options, _ in js.calls:
            self.assertEqual({name: value for name, value in vars(options).items() if name != "signal"},
                             {"method": "GET", "mode": "cors", "credentials": "omit", "redirect": "error",
                              "referrerPolicy": "no-referrer", "cache": "no-store"}, url)

    def fixture_answer(self, rejected=lambda url: False):
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        document = (FIXTURES / "source.json").read_text("utf-8").replace(ADDRESS, URL).encode()

        async def answer(url, options):
            if rejected(url):
                raise RuntimeError("TypeError: Failed to fetch")
            if url == URL:
                return response(chunks=(document,))
            return response(chunks=(files[url.rsplit("/", 1)[1][:-len(".json")]],))
        return answer, document

    async def test_a_whole_read_asks_only_for_the_source_and_its_definitions_once_each(self):
        answer, document = self.fixture_answer()
        js = self.stub(answer)
        got = await self.roling.dispatch("sources.read", {"address": "mad.example.test"})
        self.assertEqual(got["checked"], 26)
        self.assertEqual(len(js.calls), 1 + 26)
        self.assert_only_reads(js, document)

    async def test_a_rejected_source_document_makes_no_second_request(self):
        answer, document = self.fixture_answer(rejected=lambda url: url == URL)
        js = self.stub(answer)
        with self.assertRaises(RuntimeFault) as raised:
            await self.roling.dispatch("sources.read", {"address": "mad.example.test"})
        self.assertEqual((raised.exception.code, str(raised.exception)), ("NETWORK_ERROR", NOT_READABLE_SENTENCE))
        self.assertEqual([url for url, _, _ in js.calls], [URL])
        self.assert_only_reads(js, document)

    async def test_a_source_document_whose_time_runs_out_could_not_be_reached(self):
        async def answer(url, options):
            js.fire(0)
            raise RuntimeError("AbortError: signal is aborted without reason")
        js = self.stub(answer)
        with self.assertRaises(RuntimeFault) as raised:
            await self.roling.dispatch("sources.read", {"address": "mad.example.test"})
        self.assertEqual((raised.exception.code, str(raised.exception)),
                         ("NETWORK_ERROR", "Could not reach mad.example.test."))
        self.assertEqual(len(js.calls), 1)

    async def test_a_rejected_definition_makes_no_second_request(self):
        paid_admission = "EIjETqo6CwKdjLKiUbve977olj92nuhBoy4NPywpqD1U"  # only dropin-class uses it
        answer, document = self.fixture_answer(rejected=lambda url: paid_admission in url)
        js = self.stub(answer)
        got = await self.roling.dispatch("sources.read", {"address": "mad.example.test"})
        self.assertEqual(got["checked"], 25)
        self.assertEqual(len(js.calls), 1 + 26)
        self.assert_only_reads(js, document)

    def test_the_probe_is_gone(self):
        self.assertFalse(hasattr(self.roling, "PROBE_MS"))
        self.assertFalse(hasattr(self.roling, "_probe"))

    async def test_read_rejected_after_its_time_is_unreachable(self):
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
            "rejected": (unreachable, "NOT_READABLE"),
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
            js.now += 4_000
            if url == URL:
                return response(chunks=(document,))
            if len(js.calls) % 3 == 0:
                raise RuntimeError("TypeError: Failed to fetch")
            return response(chunks=(files[url.rsplit("/", 1)[1][:-len(".json")]],))
        js = self.stub(answer)
        js.now = 50_000.0
        with self.assertRaises(RuntimeFault) as raised:
            await self.roling.dispatch("sources.read", {"address": "mad.example.test"})
        self.assertEqual((raised.exception.code, str(raised.exception)),
                         ("NETWORK_ERROR", "Could not reach mad.example.test."))
        self.assertTrue(js.calls)
        self.assertTrue(all(at < 50_000 + 60_000 for _, _, at in js.calls), [at for _, _, at in js.calls])
        self.assertEqual(len(js.calls), 15)


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


SIDE = "side"  # the registry's second vault; its passcode is "side-pass"


class AuthError(Exception):
    """keri.kering.AuthError, which a vault opened with the wrong passcode raises."""


class RegistryDb:
    """The database of the vault registry as vaulting uses it, in memory."""

    def getVal(self, store, key):
        return store.items.get(key)

    def setVal(self, store, key, value):
        store.items[key] = value

    async def flush(self):
        pass


class Vaults:
    """The runtime's own vaulting.py, for the worker and for roling, over stand-ins for where it keeps vaults.

    Listing, making, opening and closing a vault, and the persist and reload, are vaulting's own code, so the open
    vault changes exactly as it does in the runtime: an open closes the open vault first and only then opens the
    other. The stand-ins are below that. The registry is in memory and holds "My Wallet" (my-wallet, no passcode)
    and "Side" (side, passcode "side-pass"). The state of a vault is a FakeHby over what the vault holds, with
    FakeKomer for its stores, and a passcode is its own key. Where the runtime waits for storage a stand-in gives up
    one turn of the loop, and a test can hold a method there: with `hold` set to "registry", "close" (a vault is
    being closed) or "build" (a vault is being opened), `held` is set when that step is reached and the step goes on
    once `go_on` is set. `built` lists the vaults whose state was built: opened, reloaded, or set up when made."""

    def __init__(self):
        self.vaulting = vaulting = _load("fortweb_vaulting_for_worker_test", RUNTIME / "vaulting.py")
        self.hold, self.held, self.go_on = None, asyncio.Event(), asyncio.Event()
        self.built = []
        passcodes = {VAULT: "", SIDE: "side-pass"}
        kept = {}  # vault id -> what the vault holds: (its FakeDb, its identifiers)
        salt = "0AAwMTIzNDU2Nzg5YWJjZGVm"
        store = types.SimpleNamespace(items={
            vault_id.encode(): json.dumps({"id": vault_id, "alias": alias, "encrypted": bool(passcodes[vault_id]),
                                           "createdAt": "2026-11-12T09:00:00+00:00", "rootSalt": salt}).encode()
            for vault_id, alias in ((VAULT, "My Wallet"), (SIDE, "Side"))})
        registry = {"db": RegistryDb(), "store": store}
        modules = {
            "koming": types.SimpleNamespace(Komer=FakeKomer),
            "kering": types.SimpleNamespace(AuthError=AuthError),
            "signing": types.SimpleNamespace(Salter=lambda: types.SimpleNamespace(qb64=salt)),
            "organizing": types.SimpleNamespace(Organizer=lambda hby: None),
        }

        async def ensure_registry():
            await self._storage("registry")
            return registry

        async def build_vault_state(record, *, bran=""):
            await self._storage("build")
            if bran != passcodes.setdefault(record["id"], bran):
                raise AuthError()
            self.built.append(record["id"])
            data, habs = kept.setdefault(record["id"], (FakeDb(), {}))
            return {"modules": modules, "vault": record, "hby": FakeHby(data, habs), "bran": bran,
                    "kfSurfaceKeyState": {}}

        async def close_habery(hby, *, clear=False):
            await self._storage("close")
            hby.closed = hby.db.closed = True

        vaulting.RuntimeFault = RuntimeFault  # one fault class for the worker, roling and vaulting, as in the runtime
        vaulting.ensure_registry = ensure_registry
        vaulting.load_modules = lambda: modules
        vaulting._prepare_bran = lambda passcode, passcode_kdf=None: str(passcode or "").strip()
        vaulting._build_vault_state = build_vault_state
        vaulting.close_habery = close_habery
        # What a vault or an identifier looks like to the page is not the subject here.
        vaulting._vault_summary = lambda state: {"vault": {"id": state["vault"]["id"], "opened": True}}
        vaulting._identifier_record = lambda hab: {"aid": hab.pre, "alias": hab.name}

    async def _storage(self, step):
        await asyncio.sleep(0)
        if self.hold == step:
            self.hold = None
            self.held.set()
            await self.go_on.wait()


class WorkerRequests:
    """The worker's handle_request around a `roling` and a `vaulting` (the module of a Vaults), with its runtime
    ready."""

    def __init__(self, roling, vaulting):
        self.worker = _load_worker(roling, [], vaulting)
        ready = asyncio.get_running_loop().create_future()
        ready.set_result(True)
        self.worker._RUNTIME_READY_TASK = ready

    async def __call__(self, method, params=None):
        answer = json.loads(await self.worker.handle_request(json.dumps(
            {"id": "1", "kind": "fortweb.runtime.request", "method": method, "params": params or {}})))
        return answer["result"] if answer["ok"] else (answer["error"]["code"], answer["error"]["message"])


class RequestLockTest(unittest.IsolatedAsyncioTestCase):
    """sources.read is the only method that runs outside the worker's request lock; roles.* run under it."""

    async def test_only_sources_read_runs_while_another_request_holds_the_lock(self):
        roling, _ = _load_roling()
        worker = WorkerRequests(roling, Vaults().vaulting).worker
        self.assertEqual(worker._UNLOCKED_METHODS, frozenset({"sources.read"}))
        reached = []

        async def dispatch(method, params):
            reached.append(method)
            return {}
        worker._dispatch = dispatch
        methods = ("sources.read", "roles.list", "roles.takeOn", "roles.remove")
        async with worker._REQUEST_LOCK:  # another request is running
            answers = [asyncio.ensure_future(worker.handle_request(json.dumps(
                {"id": method, "kind": "fortweb.runtime.request", "method": method, "params": {}})))
                for method in methods]
            for _ in range(20):
                await asyncio.sleep(0)
            self.assertEqual(reached, ["sources.read"])
        await asyncio.gather(*answers)
        self.assertEqual(sorted(reached), sorted(methods))


class LastReadAcrossVaultsTest(unittest.IsolatedAsyncioTestCase):
    """The engine keeps its last read while the same vault stays open, and drops it when a request leaves another
    vault open, or none. Through the worker's handle_request, with vaulting's own vaults.* methods (Vaults);
    my-wallet is open when a test starts."""

    async def asyncSetUp(self):
        self.roling, _ = _load_roling()
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        document = (FIXTURES / "source.json").read_bytes()
        self.source_may_answer = asyncio.Event()
        self.source_may_answer.set()
        self.source_asked = asyncio.Event()

        async def fetch_bytes(url, timeout_ms):
            if url == ADDRESS:
                self.source_asked.set()
                await self.source_may_answer.wait()
                return document
            return files[url[len(BASE):-len(".json")]]
        self.roling._fetch_bytes = fetch_bytes
        self.vaults = Vaults()
        self.roling.vaulting = self.vaults.vaulting
        self.request = WorkerRequests(self.roling, self.vaults.vaulting)
        await self.open(VAULT)

    async def open(self, vault, passcode=""):
        self.assertEqual(await self.request("vaults.open", {"vaultId": vault, "passcode": passcode}),
                         {"vault": {"id": vault, "opened": True}})

    def start_reading(self):
        return asyncio.ensure_future(self.request("sources.read", {"address": "mad.seriouscoder.one"}))

    async def read(self):
        self.assertIn("source", await self.start_reading())

    def read_kept(self):
        return self.roling._LAST_READ is not None

    def take_on(self, vault=VAULT, alias="cashier"):
        return self.request("roles.takeOn", {
            "vaultId": vault, "address": "mad.seriouscoder.one", "target": DROPIN,
            "publication": "dropin-class", "role": "cashier",
            "identifier": {"mode": "new", "alias": alias}})

    async def assert_read_dropped(self, vault=VAULT):
        """No read is kept: taking on a role in `vault`, which is open, is refused until the address is read again
        there."""
        self.assertFalse(self.read_kept(), "a read is still kept")
        self.assertEqual(await self.take_on(vault), ("CONFLICT", OPEN_AGAIN))
        await self.read()
        self.assertIn("role", await self.take_on(vault))

    async def test_forget_last_read_drops_the_read_and_any_read_in_flight(self):
        await self.roling.dispatch("sources.read", {"address": "mad.seriouscoder.one"})
        started = self.roling._READS_STARTED
        self.roling.forget_last_read()
        self.assertIsNone(self.roling._LAST_READ)
        self.assertEqual(self.roling._READS_STARTED, started + 1)

    async def test_a_request_that_leaves_the_same_vault_open_keeps_the_read(self):
        cases = {
            "a new vault is refused, its name is taken":
                ("vaults.create", {"name": "My Wallet"}, ("CONFLICT", "Vault 'My Wallet' already exists.")),
            "a new vault is refused, its name is empty":
                ("vaults.create", {"name": ""}, ("VALIDATION", "Vault name is required.")),
            "a vault that is not found is opened":
                ("vaults.open", {"vaultId": "nope"}, ("NOT_FOUND", "Vault 'nope' was not found.")),
            "the vault that is already open is opened":
                ("vaults.open", {"vaultId": VAULT}, {"vault": {"id": VAULT, "opened": True}}),
        }
        for name, (method, params, answer) in cases.items():
            with self.subTest(name):
                await self.asyncSetUp()
                await self.read()
                self.assertEqual(await self.request(method, params), answer)
                self.assertIn("role", await self.take_on())

    async def test_a_new_vault_that_is_made_keeps_the_read(self):
        await self.read()
        made = await self.request("vaults.create", {"name": "Third", "passcode": "third-pass"})
        self.assertEqual((made["vault"]["id"], made["vault"]["opened"]), ("third", False))
        self.assertEqual(self.vaults.built, [VAULT, "third"])  # the new vault was set up; my-wallet stayed open
        self.assertIn("role", await self.take_on())

    async def test_a_persist_and_reload_keeps_the_read(self):
        await self.read()
        taken = await self.take_on()  # persists: the vault is closed and opened again inside the request
        self.assertIn("role", taken)
        made = await self.request("identifiers.create", {"vaultId": VAULT, "alias": "second"})
        self.assertEqual(made["identifier"]["alias"], "second")
        self.assertEqual(await self.request("roles.remove", {"vaultId": VAULT, "id": taken["role"]["id"]}),
                         {"removed": True})
        self.assertEqual(self.vaults.built, [VAULT] * 4)  # opened, then reloaded by each of the three requests
        self.assertIn("role", await self.take_on(alias="cashier 2"))

    async def test_other_methods_keep_the_read(self):
        await self.read()
        await self.request("vaults.summary", {"vaultId": VAULT})
        await self.request("vaults.list")
        self.assertIn("role", await self.take_on())

    async def test_closing_the_vault_drops_the_read(self):
        await self.read()
        self.assertEqual(await self.request("vaults.close", {"vaultId": VAULT}),
                         {"vault": {"id": VAULT, "opened": False}})
        self.assertFalse(self.read_kept())
        await self.open(VAULT)
        await self.assert_read_dropped()

    async def test_opening_another_vault_drops_the_read(self):
        await self.read()
        await self.open(SIDE, "side-pass")
        await self.assert_read_dropped(SIDE)

    async def test_an_open_that_fails_after_the_open_vault_was_closed_drops_the_read(self):
        await self.read()
        self.assertEqual(await self.request("vaults.open", {"vaultId": SIDE, "passcode": "wrong"}),
                         ("AUTH_FAILED", "Passcode incorrect for this vault."))
        self.assertFalse(self.read_kept())
        self.assertEqual(await self.take_on(), ("LOCKED", "Open a vault before calling vault operations."))
        await self.open(VAULT)
        await self.assert_read_dropped()

    async def test_a_read_made_with_no_vault_open_is_dropped_when_a_vault_is_opened(self):
        await self.request("vaults.close", {"vaultId": VAULT})
        await self.read()
        await self.open(VAULT)
        await self.assert_read_dropped()

    async def test_a_read_that_starts_while_another_vault_is_being_opened_is_not_kept(self):
        # The open is held while my-wallet is being closed (it is still open), or while side is being opened (no
        # vault is open); the read that starts then finishes before the open does, or after it.
        for hold in ("close", "build"):
            for finishes_first in (True, False):
                with self.subTest(open_held_at=hold, read_finishes_first=finishes_first):
                    await self.asyncSetUp()
                    self.vaults.hold = hold
                    opening = asyncio.ensure_future(
                        self.request("vaults.open", {"vaultId": SIDE, "passcode": "side-pass"}))
                    await self.vaults.held.wait()
                    self.source_may_answer.clear()
                    reading = self.start_reading()
                    await self.source_asked.wait()
                    if finishes_first:
                        self.source_may_answer.set()
                        self.assertIn("source", await reading)
                    self.vaults.go_on.set()
                    self.assertEqual(await opening, {"vault": {"id": SIDE, "opened": True}})
                    if not finishes_first:
                        self.source_may_answer.set()
                        self.assertIn("source", await reading)  # its caller still gets its answer
                    await self.assert_read_dropped(SIDE)

    async def test_a_read_in_flight_when_the_vault_is_locked_is_not_kept(self):
        self.source_may_answer.clear()
        reading = self.start_reading()
        await self.source_asked.wait()
        await self.request("vaults.close", {"vaultId": VAULT})
        self.source_may_answer.set()
        self.assertIn("source", await reading)  # its caller still gets its answer
        self.assertFalse(self.read_kept())
        await self.open(VAULT)
        await self.assert_read_dropped()


if __name__ == "__main__":
    unittest.main()
