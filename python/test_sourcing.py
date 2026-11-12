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
            "example.org/apps/v1.2": ("https://example.org/apps/v1.2/source.json", "https://example.org/apps/v1.2/"),
            "sub.example.co.uk": ("https://sub.example.co.uk/source.json", "https://sub.example.co.uk/"),
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
            # A browser reads these hosts as IPv4 addresses (WHATWG "ends in a number").
            "2130706433", "0x7f.1", "127.1", "0x7f000001", "1.2.3", "example.123",
            # A single label can only be a local name.
            "intranet", "router/apps",
            "https://example.org:/",
            # Paths a browser would rewrite are refused, so the shown address is the fetched one.
            "example.org/a/../b", "example.org/a/./b", "example.org/a\\b", "example.org/a%2e%2e/b",
            "example.org/a%20b", "example.org/<script>", "example.org//x", "example.org/a\x00b",
            "example.org/é",
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


ADDRESS = "https://mad.seriouscoder.one/source.json"
SAID_A = "E" + "A" * 43
SAID_B = "E" + "B" * 43
SAID_C = "E" + "C" * 43


def _publication(**over):
    pub = {"target": SAID_A, "name": "n", "description": "d", "tags": ["t"], "collection": "c"}
    pub.update(over)
    return pub


def _doc(**members) -> bytes:
    doc = {"source": ADDRESS}
    doc.update(members)
    return json.dumps(doc).encode()


class SourceDocumentTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()

    def read(self, raw: bytes, address: str = ADDRESS) -> dict:
        return self.s.read_source_document(raw, address)

    def assert_faults(self, raw: bytes, address: str = ADDRESS):
        with self.assertRaises(self.s.SourceFault) as raised:
            self.s.read_source_document(raw, address)
        self.assertEqual(raised.exception.code, "BAD_SOURCE")
        self.assertEqual(raised.exception.message, "mad.seriouscoder.one did not return a valid source.")

    def test_fixture_reads_whole(self):
        got = self.read((FIXTURES / "source.json").read_bytes())
        doc = got["document"]
        self.assertEqual(doc["source"], ADDRESS)
        self.assertEqual(len(doc["publications"]), 3)
        self.assertEqual(len(doc["serves"]), 26)
        self.assertEqual(got["dropped"], {"entries": 0, "publications": 0})
        self.assertIsNone(doc["by"])
        self.assertIsNone(doc["oobi"])
        self.assertEqual((doc["introductions"], doc["catalogs"]), (0, 0))
        self.assertEqual(set(doc["publications"][0]), {"target", "name", "description", "tags", "collection"})

    def test_fixture_serves_keeps_refers(self):
        doc = self.read((FIXTURES / "source.json").read_bytes())["document"]
        self.assertEqual(doc["serves"]["EDVEy5suX6WPROMl6vSc5Kc4I86mXxyFsa_xBla4A_dY"],
                         ["EEfawOeTW43nlaZXFYBxjw5XQuLkbm6Jdw-L1EbAXviK", "EO6exNffu9clQv9czAMDzj9W_CNW3W47yAJhMqYC05Qz"])

    def test_source_differing_from_address_faults(self):
        self.assert_faults(json.dumps({"source": "https://mad.seriouscoder.one/other/source.json"}).encode())
        self.assert_faults(json.dumps({}).encode())

    def test_top_level_not_an_object_faults(self):
        self.assert_faults(b"[]")
        self.assert_faults(b'"https://mad.seriouscoder.one/source.json"')

    def test_invalid_json_faults(self):
        self.assert_faults(b'{"source": ')
        self.assert_faults(b"")
        self.assert_faults(b"\xff\xfe{}")

    def test_non_json_constants_fault(self):
        self.assert_faults(_doc()[:-1] + b', "x": NaN}')

    def test_nesting_deeper_than_64_faults(self):
        self.assert_faults(_doc()[:-1] + b', "x": ' + b"[" * 64 + b"]" * 64 + b"}")

    def test_nesting_of_64_reads(self):
        got = self.read(_doc()[:-1] + b', "x": ' + b"[" * 63 + b"]" * 63 + b"}")
        self.assertEqual(got["document"]["source"], ADDRESS)

    def test_brackets_inside_strings_do_not_count(self):
        got = self.read(_doc(x="[" * 100 + '\\"{' * 100))
        self.assertEqual(got["document"]["source"], ADDRESS)

    def test_known_member_of_wrong_type_faults(self):
        for name, value in [("serves", {}), ("pages", "yes"), ("publications", {}), ("introductions", "x"),
                            ("catalogs", 1), ("by", 5), ("oobi", ["x"])]:
            with self.subTest(name=name):
                self.assert_faults(_doc(**{name: value}))

    def test_serves_as_object_faults(self):
        self.assert_faults(_doc(serves={SAID_A: []}))

    def test_pages_as_string_faults(self):
        self.assert_faults(_doc(pages="true"))

    def test_unknown_top_level_member_is_ignored(self):
        got = self.read(_doc(extra={"anything": [1, 2]}, serves=[{"said": SAID_A}]))
        self.assertEqual(got["document"]["serves"], {SAID_A: []})
        self.assertNotIn("extra", got["document"])

    def test_malformed_serves_said_dropped_others_kept(self):
        got = self.read(_doc(serves=[{"said": "EAshort"}, {"said": SAID_A}, {"said": SAID_B + "\n"}, "x", {"refers": []}]))
        self.assertEqual(got["document"]["serves"], {SAID_A: []})
        self.assertEqual(got["dropped"]["entries"], 4)

    def test_duplicate_serves_said_dropped(self):
        got = self.read(_doc(serves=[{"said": SAID_A, "refers": [SAID_B]}, {"said": SAID_A}]))
        self.assertEqual(got["document"]["serves"], {SAID_A: [SAID_B]})
        self.assertEqual(got["dropped"]["entries"], 1)

    def test_refers_not_a_list_drops_entry(self):
        got = self.read(_doc(serves=[{"said": SAID_A, "refers": SAID_B}, {"said": SAID_B}]))
        self.assertEqual(got["document"]["serves"], {SAID_B: []})
        self.assertEqual(got["dropped"]["entries"], 1)

    def test_bad_refers_items_dropped(self):
        got = self.read(_doc(serves=[{"said": SAID_A, "refers": [SAID_C, SAID_A, "nope", 7, SAID_B, SAID_C]}]))
        self.assertEqual(got["document"]["serves"], {SAID_A: [SAID_B, SAID_C]})
        self.assertEqual(got["dropped"]["entries"], 4)

    def test_publications_breaking_a_rule_dropped_valid_kept(self):
        bad = [
            _publication(name="x" * 201),
            _publication(description="line\nbreak"),
            _publication(description="c1\u0085"),
            _publication(tags=["t"] * 33),
            _publication(tags=["x" * 65]),
            _publication(tags="t"),
            _publication(target="not-a-said"),
            _publication(collection=None),
            _publication(by="not an aid"),
            "not an object",
        ]
        missing = _publication()
        del missing["description"]
        bad.append(missing)
        good = [_publication(name="x" * 200, tags=["t"] * 32), _publication(target=SAID_B, by="Eabc_-1")]
        got = self.read(_doc(publications=[bad[0], good[0], *bad[1:], good[1]]))
        self.assertEqual([p["target"] for p in got["document"]["publications"]], [SAID_A, SAID_B])
        self.assertEqual(got["dropped"], {"entries": 0, "publications": len(bad)})

    def test_text_limits_count_utf16_units(self):
        # mad-discovery measures with JavaScript's String length: a character outside the BMP counts twice.
        got = self.read(_doc(publications=[_publication(name="\U0001F600" * 101), _publication(name="\U0001F600" * 100)]))
        self.assertEqual(len(got["document"]["publications"]), 1)
        self.assertEqual(got["dropped"]["publications"], 1)

    def test_unknown_member_inside_publication_ignored(self):
        got = self.read(_doc(publications=[_publication(icon="https://example.org/x.png")]))
        self.assertEqual(got["document"]["publications"],
                         [{"target": SAID_A, "name": "n", "description": "d", "tags": ["t"], "collection": "c"}])
        self.assertEqual(got["dropped"]["publications"], 0)

    def test_by_kept_when_an_aid(self):
        got = self.read(_doc(by="EKYLUMmNPZeEs77Zvclf0bSN5IN-mLfLpx2ySb-HDlk4"))
        self.assertEqual(got["document"]["by"], "EKYLUMmNPZeEs77Zvclf0bSN5IN-mLfLpx2ySb-HDlk4")
        self.assertEqual(got["dropped"]["entries"], 0)

    def test_by_dropped_when_not_an_aid(self):
        for by in ["", "x" * 65, "has space", "abc\n", "<b>"]:
            with self.subTest(by=by):
                got = self.read(_doc(by=by))
                self.assertIsNone(got["document"]["by"])
                self.assertEqual(got["dropped"]["entries"], 1)

    def test_oobi_kept_when_https(self):
        got = self.read(_doc(oobi="https://witness.example.org/oobi/EABC/controller"))
        self.assertEqual(got["document"]["oobi"], "https://witness.example.org/oobi/EABC/controller")
        self.assertEqual(got["dropped"]["entries"], 0)

    def test_oobi_dropped_when_not_a_plain_https_address(self):
        for oobi in ["http://witness.example.org/oobi/EABC/controller", "https://127.1/oobi/EABC",
                     "https://witness.example.org/oobi/../controller", "witness.example.org/oobi/EABC",
                     "https://witness.example.org/oobi?x=1", "https://witness.example.org:5631/oobi",
                     " https://witness.example.org/oobi", "javascript:alert(1)"]:
            with self.subTest(oobi=oobi):
                got = self.read(_doc(oobi=oobi))
                self.assertIsNone(got["document"]["oobi"])
                self.assertEqual(got["dropped"]["entries"], 1)

    def test_introductions_and_catalogs_are_counted(self):
        got = self.read(_doc(introductions=[{"source": "https://example.org/source.json"}, "junk", None],
                             catalogs=["https://example.org/catalog.json"]))
        self.assertEqual((got["document"]["introductions"], got["document"]["catalogs"]), (3, 1))
        self.assertEqual(got["dropped"], {"entries": 0, "publications": 0})

    def test_fault_names_the_host_of_the_address(self):
        with self.assertRaises(self.s.SourceFault) as raised:
            self.s.read_source_document(b"[]", "https://example.org/apps/source.json")
        self.assertEqual(raised.exception.message, "example.org did not return a valid source.")


if __name__ == "__main__":
    unittest.main()
