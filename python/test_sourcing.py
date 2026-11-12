"""mad-discovery's address and SAID rules, as the wallet engine applies them."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

import blake3

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "python" / "fixtures" / "mad"


def _load():
    spec = importlib.util.spec_from_file_location("fortweb_sourcing_test", REPO / "app" / "runtime" / "sourcing.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def digest(data: bytes) -> bytes:
    return blake3.blake3(data).digest()


class SaidCheckTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()

    def test_every_compact_fixture_verifies(self):
        files = sorted((FIXTURES / "compact").glob("*.json"))
        self.assertEqual(len(files), 37)
        for f in files:
            self.assertTrue(self.s.check_said(f.read_bytes(), f.stem, digest=digest), f.name)

    def test_refused_vectors_fail(self):
        for row in json.loads((FIXTURES / "refused" / "index.json").read_text()):
            served = (FIXTURES / "refused" / row["file"]).read_bytes()
            self.assertFalse(self.s.check_said(served, row["said"], digest=digest), row["file"])

    def test_reformatted_bytes_fail(self):
        f = sorted((FIXTURES / "compact").glob("*.json"))[0]
        pretty = json.dumps(json.loads(f.read_text()), indent=2).encode()
        self.assertFalse(self.s.check_said(pretty, f.stem, digest=digest))

    def test_wrong_or_malformed_said_fails(self):
        files = sorted((FIXTURES / "compact").glob("*.json"))
        self.assertFalse(self.s.check_said(files[0].read_bytes(), files[1].stem, digest=digest))
        self.assertFalse(self.s.check_said(files[0].read_bytes(), "not-a-said", digest=digest))

    def test_encode_said_shape(self):
        said = self.s.encode_said(bytes(32))
        self.assertEqual(said, "E" + "A" * 43)
        self.assertTrue(self.s.is_said(said))
        with self.assertRaises(ValueError):
            self.s.encode_said(b"short")


class AddressTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()

    def test_accepts_and_canonicalizes(self):
        cases = {
            "mad.seriouscoder.one": ("https://mad.seriouscoder.one/source.json", "https://mad.seriouscoder.one/"),
            " mad.seriouscoder.one/ ": ("https://mad.seriouscoder.one/source.json", "https://mad.seriouscoder.one/"),
            "https://mad.seriouscoder.one/source.json": ("https://mad.seriouscoder.one/source.json", "https://mad.seriouscoder.one/"),
            "example.org/apps": ("https://example.org/apps/source.json", "https://example.org/apps/"),
            "EXAMPLE.org/apps/source.json": ("https://example.org/apps/source.json", "https://example.org/apps/"),
        }
        for text, (address, base) in cases.items():
            got = self.s.normalize_address(text)
            self.assertEqual((got["address"], got["base"]), (address, base), text)
            self.assertEqual(got["host"], address.split("/")[2])

    def test_rejects(self):
        bad = [
            "", "   ", "http://example.org", "ftp://example.org", "https://example.org:8443",
            "https://user@example.org", "https://user:pw@example.org", "example.org/?q=1", "example.org/#x",
            "127.0.0.1", "https://[::1]/source.json", "localhost", "app.localhost", "example.org.",
            "exa mple.org", "example.org/" + "a" * 600, "javascript:alert(1)", "//example.org",
        ]
        for text in bad:
            with self.assertRaises(self.s.SourceFault, msg=text) as raised:
                self.s.normalize_address(text)
            self.assertEqual(raised.exception.code, "BAD_ADDRESS", text)

    def test_definition_address(self):
        said = "E" + "A" * 43
        self.assertEqual(self.s.definition_address("https://example.org/apps/", said),
                         f"https://example.org/apps/definitions/{said}.json")
        with self.assertRaises(ValueError):
            self.s.definition_address("https://example.org/apps/", "nope")


if __name__ == "__main__":
    unittest.main()
