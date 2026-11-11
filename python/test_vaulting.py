"""Test vault lifecycle, settings, and identifier boundaries."""

from __future__ import annotations

import asyncio
import json
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "runtime" / "vaulting.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fortweb_vaulting_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class VaultlessSettingsTest(unittest.TestCase):
    def setUp(self):
        self.vaulting = _load_module()
        self.calls = []

        def fail(name):
            def called(*_args, **_kwargs):
                self.calls.append(name)
                self.fail(f"vaultless settings called {name}")

            return called

        self.vaulting.configure_runtime(
            ensure_runtime_packages=fail("ensure_runtime_packages"),
            load_modules=fail("load_modules"),
            clienter_factory=fail("clienter_factory"),
            storage_opener=fail("storage_opener"),
            wallet_storage_prefix="fortweb-vault-",
            registry_name="fortweb-vault-registry",
            registry_store="vaults.",
            legacy_root_salt="0AAwMTIzNDU2Nzg5YWJjZGVm",
            passcode_kdf_defaults={},
            default_settings={"tempDatastore": False, "keyTier": "low"},
            kf_state_subdb="kfst.",
        )
        self.vaulting.ensure_registry = fail("ensure_registry")
        self.vaulting.load_modules = fail("load_modules")
        self.vaulting._build_vault_state = fail("build_vault_state")
        self.vaulting._STATE = None
        self.vaulting._REGISTRY = None

    def assert_vaultless_defaults(self, params):
        result = asyncio.run(self.vaulting.dispatch("settings.get", params))
        self.assertEqual(
            result,
            {
                "settings": {
                    "tempDatastore": False,
                    "keyTier": "low",
                    "runtimeStatus": "Browser vault worker open over WebBaser and WebKeeper.",
                }
            },
        )
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.vaulting._STATE)
        self.assertIsNone(self.vaulting._REGISTRY)

    def test_missing_none_and_blank_vault_id_return_defaults_without_access(self):
        real_require_open_state = self.vaulting.require_open_state
        self.vaulting.require_open_state = self.vaulting.ensure_registry
        try:
            for params in ({}, {"vaultId": None}, {"vaultId": "  "}):
                with self.subTest(params=params):
                    state = self.vaulting._STATE
                    registry = self.vaulting._REGISTRY
                    self.assert_vaultless_defaults(params)
                    self.assertIs(self.vaulting._STATE, state)
                    self.assertIs(self.vaulting._REGISTRY, registry)
        finally:
            self.vaulting.require_open_state = real_require_open_state

    def test_named_closed_vault_returns_locked(self):
        with self.assertRaises(self.vaulting.RuntimeFault) as raised:
            asyncio.run(
                self.vaulting.dispatch("settings.get", {"vaultId": "vault-locked"})
            )

        self.assertEqual(raised.exception.code, "LOCKED")
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.vaulting._STATE)
        self.assertIsNone(self.vaulting._REGISTRY)

    def test_matching_open_vault_returns_settings(self):
        self.vaulting._STATE = {"vault": {"id": "vault-open"}}
        result = asyncio.run(
            self.vaulting.dispatch("settings.get", {"vaultId": "vault-open"})
        )
        self.assertEqual(
            result,
            {
                "settings": {
                    "tempDatastore": False,
                    "keyTier": "low",
                    "runtimeStatus": "Browser vault worker open over WebBaser and WebKeeper.",
                }
            },
        )
        self.assertEqual(self.calls, [])


class IdentifierNamespaceTest(unittest.TestCase):
    def test_internal_identifiers_stay_out_of_list_and_detail_after_reopen(self):
        vaulting = _load_module()
        # Reopened habitats do not retain their namespace on the Hab object.
        public = SimpleNamespace(name="kf-onboarding-personal", pre="public")
        internal = SimpleNamespace(name="kf-onboarding-session", pre="internal")
        habitats = {hab.pre: hab for hab in (public, internal)}
        hby = SimpleNamespace(
            prefixes=list(habitats),
            habByPre=habitats.get,
            habByName=lambda name: public if name == public.name else None,
        )
        vaulting._identifier_record = lambda hab: {"aid": hab.pre, "alias": hab.name}

        self.assertEqual(vaulting._list_identifier_records(hby), [
            {"aid": "public", "alias": "kf-onboarding-personal"},
        ])
        self.assertEqual(vaulting._get_identifier_record(hby, "public")["aid"], "public")
        with self.assertRaises(vaulting.RuntimeFault) as raised:
            vaulting._get_identifier_record(hby, "internal")
        self.assertEqual(raised.exception.code, "NOT_FOUND")
        self.assertIs(hby.habByPre("internal"), internal)


class VaultSetupTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.vaulting = _load_module()

        class Store:
            SubDbNames = []

            def __init__(self):
                self.opened = False
                self.records = {"saved": "vault data"}
                self.open_error = None
                self.close_error = None

            async def reopen(self, **_kwargs):
                self.opened = True
                if self.open_error is not None:
                    raise self.open_error

            async def aclose(self, *, clear=False):
                if self.close_error is not None:
                    raise self.close_error
                if clear:
                    self.records.clear()
                self.opened = False

        self.keeper = Store()
        self.baser = Store()
        self.cf = self.vaulting.NullConfiger()
        self.vaulting.NullConfiger = lambda: self.cf
        self.setup_error = None

        def habery(**kwargs):
            if self.setup_error is not None:
                raise self.setup_error
            return SimpleNamespace(**kwargs)

        async def ensure_runtime_packages():
            pass

        modules = {
            "webkeeping": SimpleNamespace(WebKeeper=lambda **_kwargs: self.keeper),
            "webbasing": SimpleNamespace(WebBaser=lambda **_kwargs: self.baser),
            "habbing": SimpleNamespace(Habery=habery),
        }
        self.vaulting._CONFIG.update(
            ensure_runtime_packages=ensure_runtime_packages,
            load_modules=lambda: modules,
            storage_opener=None,
            kf_state_subdb="kfst.",
        )
        self.record = {"id": "vault-test", "storageName": "test", "rootSalt": "salt"}

    async def test_setup_keeps_stores_open_for_the_returned_habery(self):
        state = await self.vaulting._build_vault_state(self.record)
        self.assertIs(state["hby"].ks, self.keeper)
        self.assertIs(state["hby"].db, self.baser)
        self.assertTrue(self.keeper.opened)
        self.assertTrue(self.baser.opened)
        self.assertTrue(self.cf.opened)

    async def test_open_and_setup_failures_close_stores_without_clearing(self):
        for stage in ("keeper", "baser", "habery"):
            with self.subTest(stage=stage):
                self.setUp()
                error = ValueError("vault setup failed")
                if stage == "habery":
                    self.setup_error = error
                else:
                    getattr(self, stage).open_error = error
                with self.assertRaises(ValueError) as raised:
                    await self.vaulting._build_vault_state(self.record)
                self.assertIs(raised.exception, error)
                self.assertFalse(self.keeper.opened)
                self.assertFalse(self.baser.opened)
                self.assertFalse(self.cf.opened)
                for store in (self.keeper, self.baser):
                    self.assertEqual(store.records, {"saved": "vault data"})

    async def test_close_failure_preserves_setup_error_and_closes_other_store(self):
        for failed, other in (("baser", "keeper"), ("keeper", "baser")):
            for failure_type in (RuntimeError, asyncio.CancelledError):
                with self.subTest(store=failed, failure=failure_type):
                    self.setUp()
                    self.setup_error = ValueError("authentication failed")
                    getattr(self, failed).close_error = failure_type("close failed")
                    with self.assertRaises(ValueError) as raised:
                        await self.vaulting._build_vault_state(self.record)
                    self.assertIs(raised.exception, self.setup_error)
                    self.assertFalse(getattr(self, other).opened)
                    self.assertFalse(self.cf.opened)
                    self.assertEqual(len(raised.exception.__notes__), 1)
                    for store in (self.keeper, self.baser):
                        self.assertEqual(store.records, {"saved": "vault data"})

    async def test_cancelled_open_closes_stores(self):
        started = asyncio.Event()
        pending = asyncio.Event()

        async def reopen(**_kwargs):
            self.baser.opened = True
            started.set()
            await pending.wait()

        self.baser.reopen = reopen
        task = asyncio.create_task(self.vaulting._build_vault_state(self.record))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.keeper.opened)
        self.assertFalse(self.baser.opened)
        self.assertFalse(self.cf.opened)



class _FakeStore:
    def __init__(self):
        self.items = {}


class _FakeRegistryDb:
    def __init__(self, store):
        self.store = store
        self.flushes = 0

    def getVal(self, store, key):
        return store.items.get(key)

    def setVal(self, store, key, value):
        store.items[key] = value

    async def flush(self):
        self.flushes += 1


class DefaultVaultTest(unittest.TestCase):
    """The registry marks exactly one vault as the user's default."""

    def setUp(self):
        self.vaulting = _load_module()
        self.vaulting.configure_runtime(
            ensure_runtime_packages=None,
            load_modules=None,
            clienter_factory=None,
            storage_opener=None,
            wallet_storage_prefix="fortweb-vault-",
            registry_name="fortweb-vault-registry",
            registry_store="vaults.",
            legacy_root_salt="0AAwMTIzNDU2Nzg5YWJjZGVm",
            passcode_kdf_defaults={"algorithm": "argon2id13", "salt": "NHCtv3Actrddf8jC", "opslimit": 2, "memlimit": 67_108_864, "outlen": 16},
            default_settings={},
            kf_state_subdb="kfst.",
        )
        store = _FakeStore()
        self.registry = {"db": _FakeRegistryDb(store), "store": store}

        async def ensure_registry():
            return self.registry

        async def build_vault_state(record, *, bran=""):
            return {"hby": object(), "vault": record}

        async def close_habery(_hby, *, clear=False):
            return None

        self.vaulting.ensure_registry = ensure_registry
        self.vaulting._build_vault_state = build_vault_state
        self.vaulting.close_habery = close_habery
        self.vaulting._prepare_bran = lambda passcode, _kdf=None: "bran" if passcode else ""
        self.vaulting._generate_root_salt = lambda: "0AAwMTIzNDU2Nzg5YWJjZGVm"
        self.vaulting._STATE = None

    def call(self, method, params=None):
        return asyncio.run(self.vaulting.dispatch(method, params or {}))

    def defaults(self):
        return {vault["id"]: vault["isDefault"] for vault in self.call("vaults.list")["vaults"]}

    def put_legacy_record(self, vault_id, created_at):
        record = {"id": vault_id, "alias": vault_id, "createdAt": created_at, "encrypted": True}
        self.registry["store"].items[vault_id.encode()] = json.dumps(record).encode()

    def test_first_vault_becomes_default_and_later_ones_do_not(self):
        first = self.call("vaults.create", {"name": "My Wallet", "passcode": "pw-1"})["vault"]
        second = self.call("vaults.create", {"name": "Side", "passcode": "pw-2"})["vault"]

        self.assertTrue(first["isDefault"])
        self.assertFalse(second["isDefault"])
        self.assertEqual(self.defaults(), {"my-wallet": True, "side": False})

    def test_set_default_moves_the_single_default(self):
        self.call("vaults.create", {"name": "My Wallet", "passcode": "pw-1"})
        self.call("vaults.create", {"name": "Side", "passcode": "pw-2"})

        result = self.call("vaults.setDefault", {"vaultId": "side"})

        self.assertTrue(result["vault"]["isDefault"])
        self.assertEqual(self.defaults(), {"my-wallet": False, "side": True})

    def test_set_default_for_unknown_vault_is_not_found(self):
        self.call("vaults.create", {"name": "My Wallet", "passcode": "pw-1"})

        with self.assertRaises(self.vaulting.RuntimeFault) as caught:
            self.call("vaults.setDefault", {"vaultId": "nope"})

        self.assertEqual(caught.exception.code, "NOT_FOUND")
        self.assertEqual(self.defaults(), {"my-wallet": True})

    def test_existing_vaults_without_a_default_adopt_the_earliest(self):
        self.put_legacy_record("later", "2026-06-01T00:00:00Z")
        self.put_legacy_record("earliest", "2026-01-01T00:00:00Z")
        self.put_legacy_record("middle", "2026-03-01T00:00:00Z")

        self.assertEqual(self.defaults(), {"earliest": True, "later": False, "middle": False})
        # The migration is persisted, not recomputed differently next time.
        self.assertEqual(self.defaults(), {"earliest": True, "later": False, "middle": False})

    def test_no_vaults_means_no_default(self):
        self.assertEqual(self.call("vaults.list"), {"vaults": []})


if __name__ == "__main__":
    unittest.main()
