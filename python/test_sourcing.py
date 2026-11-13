"""mad-discovery's address and SAID rules, as the wallet engine applies them."""

from __future__ import annotations

import asyncio
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
            # Text the URL parser itself refuses (ValueError): a bracket that opens or closes no IPv6 literal, and a
            # full-width slash, which NFKC turns into a "/" inside the host.
            "[example.org", "example.org]", "https://[example.org/", "https://example.org]/", "https://[abc]/x",
            "a]b.org", "example.org／path", "example.org＃x",
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
SAID_D = "E" + "D" * 43


def _publication(**over):
    pub = {"target": SAID_A, "name": "n", "description": "d", "tags": ["t"], "collection": "c"}
    pub.update(over)
    return pub


def _doc(**members) -> bytes:
    doc = {"source": ADDRESS}
    doc.update(members)
    return json.dumps(doc).encode()


# The characters refused in a string that is shown: mad-discovery's Ruling 13 (src/source.ts FORBIDDEN).
# Right-to-left override, first-strong isolate, zero-width space, byte order mark, soft hyphen, Arabic letter mark,
# Mongolian vowel separator, language tag.
FORBIDDEN_EXAMPLES = ("\u202e", "\u2066", "\u200b", "\ufeff", "\u00ad", "\u061c", "\u180e", "\U000E0001")
# A joined emoji sequence (woman, ZWJ, laptop), Persian with a zero-width non-joiner, accented and CJK letters.
KEPT_EXAMPLES = ("\U0001F469\u200d\U0001F4BB", "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645", "caf\u00e9",
                 "\u65e5\u672c\u8a9e")
RULING_13 = ([*range(0x00, 0x20), *range(0x7F, 0xA0), 0xAD, 0x61C, 0x180E, 0x200B, 0x200E, 0x200F,
              *range(0x202A, 0x202F), *range(0x2060, 0x2065), *range(0x2066, 0x206A), 0xFEFF, *range(0xE0000, 0xE0080)])


class TextRuleTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()

    def test_the_rule_refuses_exactly_the_214_code_points_of_ruling_13(self):
        refused = [code for code in range(0x110000) if not 0xD800 <= code <= 0xDFFF
                   and not self.s._text(f"a{chr(code)}b", 10)]
        self.assertEqual(len(RULING_13), 214)
        self.assertEqual(refused, RULING_13)


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
        self.assertEqual(set(doc["publications"][0]), {"target", "name", "description", "tags", "collection", "by"})

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

    def test_a_lone_surrogate_in_any_string_faults(self):
        # JSON escapes that Python's json keeps as one unpaired surrogate: such text cannot be stored or sent as
        # UTF-8. Member names and values, at any depth.
        for extra in (b'"x": "a\\ud83d"', b'"x\\udc00": 1', b'"x": [{"y": ["\\udfff"]}]', b'"x": {"\\ud800y": 1}'):
            with self.subTest(extra=extra):
                self.assert_faults(_doc()[:-1] + b", " + extra + b"}")

    def test_a_surrogate_pair_reads(self):
        got = self.read(_doc()[:-1] + b', "x": "\\ud83d\\ude00"}')
        self.assertEqual(got["document"]["source"], ADDRESS)

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

    def test_known_member_set_to_null_faults(self):
        # mad-discovery refuses a known member that is present with the wrong type, null included.
        for name in ["serves", "by", "oobi", "pages", "publications", "introductions", "catalogs"]:
            with self.subTest(name=name):
                self.assert_faults(_doc(**{name: None}))

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

    def test_a_forbidden_character_makes_a_publication_unusable(self):
        for mark in FORBIDDEN_EXAMPLES:
            for member, value in (("name", f"Front desk at bank.example{mark}"), ("tags", [f"t{mark}"]),
                                  ("description", f"d{mark}"), ("collection", f"c{mark}")):
                with self.subTest(mark=hex(ord(mark)), member=member):
                    got = self.read(_doc(publications=[_publication(**{member: value}), _publication(target=SAID_B)]))
                    self.assertEqual([p["target"] for p in got["document"]["publications"]], [SAID_B])
                    self.assertEqual(got["dropped"]["publications"], 1)

    def test_letters_of_any_script_and_joined_emoji_are_usable(self):
        for text in KEPT_EXAMPLES:
            with self.subTest(text=text):
                got = self.read(_doc(publications=[_publication(name=text, tags=[text])]))
                self.assertEqual([p["name"] for p in got["document"]["publications"]], [text])

    def test_publication_by_returned_as_stated(self):
        got = self.read(_doc(publications=[_publication(by="Eabc_-1"), _publication(target=SAID_B)]))
        self.assertEqual([p["by"] for p in got["document"]["publications"]], ["Eabc_-1", None])

    def test_text_limits_count_utf16_units(self):
        # mad-discovery measures with JavaScript's String length: a character outside the BMP counts twice.
        got = self.read(_doc(publications=[_publication(name="\U0001F600" * 101), _publication(name="\U0001F600" * 100)]))
        self.assertEqual(len(got["document"]["publications"]), 1)
        self.assertEqual(got["dropped"]["publications"], 1)

    def test_unknown_member_inside_publication_ignored(self):
        got = self.read(_doc(publications=[_publication(icon="https://example.org/x.png")]))
        self.assertEqual(got["document"]["publications"],
                         [{"target": SAID_A, "name": "n", "description": "d", "tags": ["t"], "collection": "c", "by": None}])
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
                     " https://witness.example.org/oobi", "javascript:alert(1)",
                     # The URL parser refuses these (ValueError): the entry is dropped, the document still read.
                     "https://[x/oobi", "https://witness.example.org]/oobi", "https://witness.example.org／oobi"]:
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

    def test_fault_for_a_malformed_address_never_raises_or_echoes_it(self):
        for address in ["https://[bad/source.json", "<b>not an address</b>", "http://example.org/source.json", ""]:
            with self.subTest(address=address):
                with self.assertRaises(self.s.SourceFault) as raised:
                    self.s.read_source_document(b"[]", address)
                self.assertEqual(raised.exception.code, "BAD_SOURCE")
                self.assertEqual(raised.exception.message, "The source did not return a valid source.")


DROPIN_CLASS = "EFQ6ngR2kib-dwmW8mizJ4k5IdO0YysG0xSty9ESHDE4"


def _fixture_serves(s) -> dict:
    return s.read_source_document((FIXTURES / "source.json").read_bytes(), ADDRESS)["document"]["serves"]


class ClosureTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()

    def test_dropin_class_closure(self):
        serves = _fixture_serves(self.s)
        got = self.s.closure(serves, DROPIN_CLASS)
        self.assertIn(DROPIN_CLASS, got)
        self.assertEqual(len(got), len(set(got)))
        self.assertEqual(got, sorted(got))
        self.assertTrue(set(got) <= set(serves))
        # Every rr of the selection, and what they refer to, is reached.
        for said in serves[DROPIN_CLASS]:
            self.assertIn(said, got)
            self.assertTrue(set(serves[said]) <= set(got))

    def test_dropin_class_closure_exact(self):
        # Worked out by hand from python/fixtures/mad/source.json (19 of the 26 served definitions).
        self.assertEqual(self.s.closure(_fixture_serves(self.s), DROPIN_CLASS), [
            "EA58Nm6t6HoMUEBJIrPNPoYc8qAVvyzRg5XYrzBimYP9",
            "ECru8k3CerlcJF0Khe0EOubRaA4U4APPGqOdvkzNR0L1",
            "EEfawOeTW43nlaZXFYBxjw5XQuLkbm6Jdw-L1EbAXviK",
            "EEnCEVn-d6pEqEbX2tdKpaZnuldiha_x4l0FZdeQXhU-",
            "EF56Ri4rLV0XIxs3S7kUqo0pnUl6PQ8Y65PHydnIDvvQ",
            "EFAvkd09h6QHoWcybDcqnPhRjREEQ8KpHA-fCZnSHLAx",
            "EFGi5d0P_tlNrY6_qw8QqVZEjo3AgqBMHHeBJG7FL1i3",
            "EFQ6ngR2kib-dwmW8mizJ4k5IdO0YysG0xSty9ESHDE4",
            "EGVaGa3kbb3-tg3Z7lj2Q9urXxS71HNZkD3sIT99f-NB",
            "EHC_ESsWWD0Y2UKuPcdAfd8LzZ1hPSA2Z9afFtFsOpjC",
            "EIjETqo6CwKdjLKiUbve977olj92nuhBoy4NPywpqD1U",
            "EJd1iQ-VgrOUblb-gE6m4jYNC3Iua5t8nJS9KK6yph69",
            "EJoufOp-lX-0igGuBPemVZZTNUf7jTAQhkJsl6HqAmDu",
            "ELddHtVBJWvhi3ejavWnoH0AKT24I1dc2zNr5Z1Fo1Zn",
            "ELjeENxazY8bnEi4Uz3tAIAqwA0DFGFzANqiGiVHNdt7",
            "EMSaOVmC4hKCUfwULHzziJ-eHEzFEb8qPrCp9Lm3kMlL",
            "ENx3CS8UcVw_Cu_0FrI_iWzvazO_kawwVo_OBxIaCs1V",
            "EO6exNffu9clQv9czAMDzj9W_CNW3W47yAJhMqYC05Qz",
            "EPpa3kIljRZ4bp1xvmbgqMxLulVq-LICBv0Z4Daz8U_i",
        ])

    def test_target_not_served_gives_nothing(self):
        self.assertEqual(self.s.closure(_fixture_serves(self.s), SAID_A), [])

    def test_refers_outside_serves_are_left_out(self):
        self.assertEqual(self.s.closure({SAID_A: [SAID_B]}, SAID_A), [SAID_A])

    def test_cycle_terminates(self):
        serves = {SAID_A: [SAID_B], SAID_B: [SAID_C], SAID_C: [SAID_A]}
        self.assertEqual(self.s.closure(serves, SAID_B), [SAID_A, SAID_B, SAID_C])


def _definitions() -> dict:
    return {f.stem: json.loads(f.read_bytes()) for f in (FIXTURES / "compact").glob("*.json")}


class RolesTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()
        self.defs = _definitions()

    def role(self, roles: dict, name: str) -> dict:
        return next(r for r in roles["roles"] if r["role"] == name)

    def test_dropin_class_roles_sorted(self):
        got = self.s.roles_of_selection(self.defs, DROPIN_CLASS)
        self.assertEqual([r["role"] for r in got["roles"]], ["cashier", "front-desk", "instructor", "patron", "recorder"])

    def test_dropin_class_title(self):
        self.assertEqual(self.s.roles_of_selection(self.defs, DROPIN_CLASS)["title"], "Drop-in takes a class")

    def test_title_falls_back_to_alias(self):
        selection = {"kind": "selection", "alias": "class-brick", "name": 5, "rrs": []}
        self.assertEqual(self.s.roles_of_selection({SAID_A: selection}, SAID_A)["title"], "class-brick")
        del selection["alias"]
        self.assertEqual(self.s.roles_of_selection({SAID_A: selection}, SAID_A)["title"], "")

    def test_instructor_class_attended(self):
        instructor = self.role(self.s.roles_of_selection(self.defs, DROPIN_CLASS), "instructor")
        self.assertEqual(instructor["responsibilities"], [{
            "said": "EO6exNffu9clQv9czAMDzj9W_CNW3W47yAJhMqYC05Qz",
            "alias": "class-attended",
            "reliesOn": [
                {"said": "EEnCEVn-d6pEqEbX2tdKpaZnuldiha_x4l0FZdeQXhU-", "alias": "enrollment", "kind": "authorization", "as": "seat"},
                {"said": "EJd1iQ-VgrOUblb-gE6m4jYNC3Iua5t8nJS9KK6yph69", "alias": "attended", "kind": "fact", "as": "visit"},
            ],
            "attests": [{"said": "EHC_ESsWWD0Y2UKuPcdAfd8LzZ1hPSA2Z9afFtFsOpjC", "alias": "class-attended", "kind": "fact"}],
            "outcome": {"said": "EHC_ESsWWD0Y2UKuPcdAfd8LzZ1hPSA2Z9afFtFsOpjC", "alias": "class-attended", "kind": "fact"},
        }])

    def test_cashier_responsibilities_in_rrs_order(self):
        cashier = self.role(self.s.roles_of_selection(self.defs, DROPIN_CLASS), "cashier")
        self.assertEqual([r["alias"] for r in cashier["responsibilities"]], ["payment-received", "paid-admission"])

    def test_target_not_a_selection(self):
        empty = {"title": "", "roles": []}
        self.assertEqual(self.s.roles_of_selection(self.defs, "EO6exNffu9clQv9czAMDzj9W_CNW3W47yAJhMqYC05Qz"), empty)
        self.assertEqual(self.s.roles_of_selection(self.defs, SAID_A), empty)
        self.assertEqual(self.s.roles_of_selection({SAID_A: {"kind": "selection", "name": "x", "rrs": "no"}}, SAID_A), empty)
        self.assertEqual(self.s.roles_of_selection({SAID_A: ["selection"]}, SAID_A), empty)

    def test_missing_definition_gets_empty_alias_and_kind(self):
        defs = {
            SAID_A: {"kind": "selection", "name": "S", "rrs": [SAID_B]},
            SAID_B: {"kind": "rr", "alias": "r", "role": "clerk", "outcome": {"kind": "fact", "said": SAID_C},
                     "rely_on": [{"said": SAID_C, "as": "x"}], "attest": [SAID_C]},
        }
        got = self.s.roles_of_selection(defs, SAID_A)
        ref = {"said": SAID_C, "alias": "", "kind": ""}
        self.assertEqual(got["roles"], [{"role": "clerk", "responsibilities": [{
            "said": SAID_B, "alias": "r", "reliesOn": [dict(ref, **{"as": "x"})], "attests": [ref], "outcome": ref}]}])

    def test_rr_with_non_string_role_skipped(self):
        defs = {
            SAID_A: {"kind": "selection", "name": "S", "rrs": [SAID_B, SAID_C, "junk", 7]},
            SAID_B: {"kind": "rr", "alias": "r", "role": ["clerk"]},
            SAID_C: {"kind": "rr", "alias": "q", "role": "clerk"},
        }
        got = self.s.roles_of_selection(defs, SAID_A)
        self.assertEqual(got["roles"], [{"role": "clerk", "responsibilities": [
            {"said": SAID_C, "alias": "q", "reliesOn": [], "attests": [], "outcome": None}]}])

    def test_repeated_rr_listed_once_in_first_order(self):
        defs = {
            SAID_A: {"kind": "selection", "name": "S", "rrs": [SAID_B, SAID_C, SAID_B, SAID_C]},
            SAID_B: {"kind": "rr", "alias": "b", "role": "clerk"},
            SAID_C: {"kind": "rr", "alias": "c", "role": "clerk"},
        }
        got = self.s.roles_of_selection(defs, SAID_A)
        self.assertEqual([r["said"] for r in got["roles"][0]["responsibilities"]], [SAID_B, SAID_C])

    def test_malformed_members_ignored(self):
        defs = {
            SAID_A: {"kind": "selection", "name": "S", "rrs": [SAID_B]},
            SAID_B: {"kind": "rr", "alias": 9, "role": "clerk", "outcome": {"said": "nope"},
                     "rely_on": ["x", {"said": "nope", "as": "y"}, {"as": "z"}, {"said": SAID_C, "as": 3}, {"said": SAID_C, "as": "ok"}],
                     "attest": [SAID_C, "nope", {"said": SAID_C}]},
            SAID_C: {"kind": "fact", "alias": "c"},
        }
        got = self.s.roles_of_selection(defs, SAID_A)
        self.assertEqual(got["roles"][0]["responsibilities"], [{
            "said": SAID_B, "alias": "",
            "reliesOn": [{"said": SAID_C, "alias": "c", "kind": "fact", "as": "ok"}],
            "attests": [{"said": SAID_C, "alias": "c", "kind": "fact"}],
            "outcome": None}])

    # Names shown, offered as an identifier's name and stored: at most 200 UTF-16 units, no C0/C1 control character.
    # Otherwise a name is treated as a member of the wrong type.

    def bounded_defs(self, text: str) -> dict:
        """A selection and an rr in which every displayed string is `text`, plus one rr and one fact named "ok"."""
        return {
            SAID_A: {"kind": "selection", "name": text, "alias": text, "rrs": [SAID_B, SAID_D]},
            SAID_B: {"kind": "rr", "alias": text, "role": text, "outcome": {"said": SAID_C},
                     "rely_on": [{"said": SAID_C, "as": text}], "attest": [SAID_C]},
            SAID_C: {"kind": text, "alias": text},
            SAID_D: {"kind": "rr", "alias": "ok", "role": "ok", "rely_on": [{"said": SAID_C, "as": "ok"}]},
        }

    def test_names_of_200_units_are_used(self):
        for text in ("x" * 200, "\U0001F600" * 100, "é" * 200):  # an emoji is two UTF-16 units
            with self.subTest(units=self.s._units(text)):
                got = self.s.roles_of_selection(self.bounded_defs(text), SAID_A)
                ref = {"said": SAID_C, "alias": text, "kind": text}
                self.assertEqual(got["title"], text)
                self.assertEqual(self.role(got, text)["responsibilities"], [{
                    "said": SAID_B, "alias": text, "reliesOn": [dict(ref, **{"as": text})], "attests": [ref],
                    "outcome": ref}])

    def test_names_over_200_units_or_with_a_control_character_are_not_used(self):
        for text in ("x" * 201, "\U0001F600" * 100 + "x", "cash\u0000ier", "cash\nier", "cash\u007fier",
                     "cash\u0085ier", "cash\u009fier"):
            with self.subTest(text=text[:12], units=self.s._units(text)):
                got = self.s.roles_of_selection(self.bounded_defs(text), SAID_A)
                ref = {"said": SAID_C, "alias": "", "kind": ""}
                self.assertEqual(got["title"], "")
                # The rr whose role is unusable is ignored; in the other, the unusable "as" drops nothing else.
                self.assertEqual(got["roles"], [{"role": "ok", "responsibilities": [{
                    "said": SAID_D, "alias": "ok", "reliesOn": [dict(ref, **{"as": "ok"})], "attests": [],
                    "outcome": None}]}])

    def test_names_with_a_forbidden_character_are_not_used(self):
        for mark in FORBIDDEN_EXAMPLES:
            with self.subTest(mark=hex(ord(mark))):
                got = self.s.roles_of_selection(self.bounded_defs(f"cashier{mark}"), SAID_A)
                ref = {"said": SAID_C, "alias": "", "kind": ""}
                self.assertEqual(got["title"], "")
                self.assertEqual(got["roles"], [{"role": "ok", "responsibilities": [{
                    "said": SAID_D, "alias": "ok", "reliesOn": [dict(ref, **{"as": "ok"})], "attests": [],
                    "outcome": None}]}])

    def test_names_in_any_script_or_with_joined_emoji_are_used(self):
        for text in KEPT_EXAMPLES:
            with self.subTest(text=text):
                got = self.s.roles_of_selection(self.bounded_defs(text), SAID_A)
                self.assertEqual(got["title"], text)
                self.assertEqual([role["role"] for role in got["roles"]], sorted(["ok", text]))

    def test_a_role_that_does_not_show_is_ignored(self):
        for text in ("", " ", "   ", "\u00a0", "\t "):
            with self.subTest(text=repr(text)):
                defs = self.bounded_defs("ok")
                defs[SAID_B]["role"] = text
                got = self.s.roles_of_selection(defs, SAID_A)
                self.assertEqual([role["role"] for role in got["roles"]], ["ok"])
                self.assertEqual([r["said"] for r in got["roles"][0]["responsibilities"]], [SAID_D])

    def test_a_role_named_only_with_joiners_and_white_space_is_ignored(self):
        for text in ("\u200d", "\u200c \u200d", "\u200c", " \u200d\u00a0"):
            for form in ("parsed", "derivable"):
                with self.subTest(text=ascii(text), form=form):
                    defs = self.bounded_defs("ok")
                    defs[SAID_B]["role"] = text
                    if form == "derivable":
                        defs = {said: self.s.derivable(found) for said, found in defs.items()}
                    got = self.s.roles_of_selection(defs, SAID_A)
                    self.assertEqual([role["role"] for role in got["roles"]], ["ok"])
                    self.assertEqual([r["said"] for r in got["roles"][0]["responsibilities"]], [SAID_D])

    def test_a_role_with_a_joiner_inside_a_name_that_shows_is_kept(self):
        for text in ("a\u200db", KEPT_EXAMPLES[1], "\U0001F469\u200d\U0001F4BB"):
            for form in ("parsed", "derivable"):
                with self.subTest(text=ascii(text), form=form):
                    defs = self.bounded_defs("ok")
                    defs[SAID_B]["role"] = text
                    if form == "derivable":
                        defs = {said: self.s.derivable(found) for said, found in defs.items()}
                    got = self.s.roles_of_selection(defs, SAID_A)
                    self.assertEqual([role["role"] for role in got["roles"]], sorted(["ok", text]))

    def test_an_unusable_as_drops_only_that_item(self):
        defs = self.bounded_defs("ok")
        defs[SAID_B]["rely_on"] = [{"said": SAID_C, "as": "x" * 201}, {"said": SAID_C, "as": "seat"}]
        got = self.s.roles_of_selection(defs, SAID_A)
        self.assertEqual([item["as"] for item in self.role(got, "ok")["responsibilities"][0]["reliesOn"]], ["seat"])

    def test_title_falls_back_to_a_usable_alias(self):
        selection = {"kind": "selection", "name": "x" * 201, "alias": "class-brick", "rrs": []}
        self.assertEqual(self.s.roles_of_selection({SAID_A: selection}, SAID_A)["title"], "class-brick")

    def test_wrong_typed_lists_ignored(self):
        defs = {
            SAID_A: {"kind": "selection", "name": "S", "rrs": [SAID_B]},
            SAID_B: {"kind": "rr", "alias": "r", "role": "clerk", "outcome": SAID_C, "rely_on": {"said": SAID_C}, "attest": SAID_C},
        }
        got = self.s.roles_of_selection(defs, SAID_A)
        self.assertEqual(got["roles"][0]["responsibilities"],
                         [{"said": SAID_B, "alias": "r", "reliesOn": [], "attests": [], "outcome": None}])


CLASS_BRICK = "EDVEy5suX6WPROMl6vSc5Kc4I86mXxyFsa_xBla4A_dY"
MEMBER_CLASS = "EJfIK_QhDYXcEpciRPUZhVc4ChUEh2EJLAZfn5veZUqL"
PAID_ADMISSION = "EIjETqo6CwKdjLKiUbve977olj92nuhBoy4NPywpqD1U"  # an rr only dropin-class uses
CLASS_ATTENDED = "EO6exNffu9clQv9czAMDzj9W_CNW3W47yAJhMqYC05Qz"  # an rr of class-brick; all three use it
BASE = "https://mad.seriouscoder.one/"


class FakeSource:
    """Serves the fixture source as fetch_bytes would, and records every call.

    `fail` names the definitions whose fetch raises a fault: SAIDs alone when nothing of the body was received, or
    a dict of SAID -> the body bytes received before the fault.
    """

    def __init__(self, s, *, document: bytes | None = None, files: dict | None = None, fail=(), clock=None,
                 ms_per_fetch: float = 0.0):
        self.s = s
        self.clock = clock
        self.ms_per_fetch = ms_per_fetch
        self.document = (FIXTURES / "source.json").read_bytes() if document is None else document
        self.files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")} if files is None else files
        self.fail = dict(fail) if isinstance(fail, dict) else dict.fromkeys(fail, 0)
        self.calls: list[tuple[str, int]] = []

    async def fetch_bytes(self, url: str, timeout_ms: int) -> bytes:
        self.calls.append((url, timeout_ms))
        if self.clock is not None:
            self.clock.now += self.ms_per_fetch
        if url == ADDRESS:
            return self.document
        prefix = BASE + "definitions/"
        said = url[len(prefix):-len(".json")] if url.startswith(prefix) and url.endswith(".json") else None
        if said in self.fail or said not in self.files:
            received = self.fail.get(said, 0)
            if received > self.s.FILE_BYTES:  # the real fetch stops reading a file once it is over the limit
                raise self.s.SourceFault(
                    "TOO_MUCH", "mad.seriouscoder.one publishes more than the wallet reads in one go.",
                    received=received)
            raise self.s.SourceFault("UNREACHABLE", "Could not reach mad.seriouscoder.one.", received=received)
        return self.files[said]

    def definition_calls(self) -> list[str]:
        return [url for url, _ in self.calls if url != ADDRESS]


def _unserved_saids(count: int) -> list[str]:
    """SAIDs, in sorted order, of definitions a test never serves whole: their fetch fails."""
    return [f"EA{i:042d}" for i in range(count)]


class Clock:
    """Milliseconds; time moves only when a FakeSource fetch takes it forward."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class SourceFaultTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()

    def test_fault_carries_the_body_bytes_received_before_it(self):
        fault = self.s.SourceFault("TOO_MUCH", "example.org publishes more than the wallet reads in one go.",
                                   received=1_000_001)
        self.assertEqual(fault.received, 1_000_001)
        self.assertEqual((fault.code, fault.message, str(fault)),
                         ("TOO_MUCH", "example.org publishes more than the wallet reads in one go.",
                          "example.org publishes more than the wallet reads in one go."))

    def test_fault_received_nothing_unless_told(self):
        self.assertEqual(self.s.SourceFault("UNREACHABLE", "Could not reach example.org.").received, 0)


class ReadSourceTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()

    def read(self, fake: FakeSource, text: str = "mad.seriouscoder.one", clock=None, **over) -> dict:
        return asyncio.run(self.s.read_source(text, fetch_bytes=fake.fetch_bytes, digest=digest,
                                              clock=clock or Clock(), **over))

    def by_target(self, got: dict) -> dict:
        return {p["target"]: p for p in got["publications"]}

    def test_full_read_of_the_fixture(self):
        fake = FakeSource(self.s)
        got = self.read(fake)
        self.assertEqual(got["source"], {"address": ADDRESS, "host": "mad.seriouscoder.one", "by": None, "oobi": None,
                                         "introductions": 0, "catalogs": 0})
        self.assertEqual(got["checked"], 26)
        self.assertEqual(got["dropped"], {"entries": 0, "publications": 0})
        self.assertEqual([p["name"] for p in got["publications"]], ["class-brick", "dropin-class", "member-class"])
        for p in got["publications"]:
            self.assertIsNone(p["problem"], p["name"])
            self.assertTrue(p["roles"], p["name"])
            self.assertEqual(set(p), {"target", "name", "description", "tags", "collection", "by", "title", "roles",
                                      "problem"})
        dropin = self.by_target(got)[DROPIN_CLASS]
        self.assertEqual(dropin["title"], "Drop-in takes a class")
        self.assertEqual(dropin["roles"], self.s.roles_of_selection(_definitions(), DROPIN_CLASS)["roles"])
        self.assertEqual(got["definitions"], {said: fake.files[said].decode("utf-8")
                                              for said in _fixture_serves(self.s)})

    def test_each_definition_fetched_once_at_its_address_within_the_request_limit(self):
        fake = FakeSource(self.s)
        self.read(fake)
        self.assertEqual(fake.calls[0][0], ADDRESS)
        urls = fake.definition_calls()
        self.assertEqual(len(urls), 26)
        self.assertEqual(len(set(urls)), 26)
        self.assertEqual(set(urls), {self.s.definition_address(BASE, said) for said in _fixture_serves(self.s)})
        for _, timeout_ms in fake.calls:
            self.assertTrue(0 < timeout_ms <= 10_000, timeout_ms)

    def test_altered_definition_marks_only_the_publications_using_it(self):
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        files[PAID_ADMISSION] = files[PAID_ADMISSION].replace(b"paid-admission", b"paid-admissiom", 1)
        got = self.by_target(self.read(FakeSource(self.s, files=files)))
        self.assertEqual((got[DROPIN_CLASS]["problem"], got[DROPIN_CLASS]["roles"]), ("mismatch", []))
        for target in (CLASS_BRICK, MEMBER_CLASS):
            self.assertIsNone(got[target]["problem"])
            self.assertTrue(got[target]["roles"])

    def test_each_publication_keeps_what_its_roles_were_derived_from(self):
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        files[PAID_ADMISSION] = files[PAID_ADMISSION].replace(b"paid-admission", b"paid-admissiom", 1)
        got = self.read(FakeSource(self.s, files=files))
        serves = self.s.read_source_document((FIXTURES / "source.json").read_bytes(), ADDRESS)["document"]["serves"]
        # Its own closure, not the whole read; nothing for a publication with a problem.
        self.assertEqual(got["derivedFrom"], {target: self.s.closure(serves, target)
                                              for target in (CLASS_BRICK, MEMBER_CLASS)})

    def test_altered_definition_is_not_counted_or_returned(self):
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        files[CLASS_ATTENDED] = files[CLASS_ATTENDED] + b" "
        got = self.read(FakeSource(self.s, files=files))
        self.assertEqual(got["checked"], 25)
        self.assertNotIn(CLASS_ATTENDED, got["definitions"])
        self.assertEqual([(p["problem"], p["roles"]) for p in got["publications"]], [("mismatch", [])] * 3)

    def test_unavailable_definition_is_unreadable_not_a_failed_read(self):
        fake = FakeSource(self.s, fail={PAID_ADMISSION})
        got = self.by_target(self.read(fake))
        self.assertEqual((got[DROPIN_CLASS]["problem"], got[DROPIN_CLASS]["roles"]), ("unreadable", []))
        self.assertIsNone(got[CLASS_BRICK]["problem"])
        self.assertIsNone(got[MEMBER_CLASS]["problem"])
        self.assertEqual(fake.definition_calls().count(self.s.definition_address(BASE, PAID_ADMISSION)), 1)

    def test_definition_that_is_not_json_is_unreadable(self):
        # Bytes that pass the SAID check but are not JSON: the target SAID inside non-JSON text.
        raw = b"not json " + b"#" * 44
        said = self.s.encode_said(digest(raw))
        raw = raw.replace(b"#" * 44, said.encode())
        document = _doc(serves=[{"said": said}], publications=[_publication(target=said)])
        got = self.read(FakeSource(self.s, document=document, files={said: raw}))
        self.assertEqual(got["publications"][0]["problem"], "unreadable")
        self.assertEqual(got["checked"], 0)

    def test_definition_that_is_not_utf8_is_unreadable(self):
        raw = b'{"d":"' + b"#" * 44 + b'","x":"\xff"}'
        said = self.s.encode_said(digest(raw))
        raw = raw.replace(b"#" * 44, said.encode())
        document = _doc(serves=[{"said": said}], publications=[_publication(target=said)])
        got = self.read(FakeSource(self.s, document=document, files={said: raw}))
        self.assertEqual((got["publications"][0]["problem"], got["checked"]), ("unreadable", 0))

    def test_definition_with_a_lone_surrogate_is_unreadable(self):
        for text in (b'"x":"a\\ud83d"', b'"x\\udc00":1'):
            with self.subTest(text=text):
                raw = b'{"d":"' + b"#" * 44 + b'",' + text + b"}"
                said = self.s.encode_said(digest(raw))
                raw = raw.replace(b"#" * 44, said.encode())
                document = _doc(serves=[{"said": said}], publications=[_publication(target=said)])
                got = self.read(FakeSource(self.s, document=document, files={said: raw}))
                self.assertEqual((got["publications"][0]["problem"], got["checked"]), ("unreadable", 0))

    def selection_of(self, raw: bytes) -> tuple[str, bytes]:
        """A definition whose bytes are `raw` with its SAID in place of the 44 '#', and the document of one
        publication of it."""
        said = self.s.encode_said(digest(raw))
        return said, raw.replace(b"#" * 44, said.encode())

    def test_each_cause_of_a_definition_that_cannot_be_read_is_unreadable(self):
        deep = b'{"d":"' + b"#" * 44 + b'","x":' + b"[" * 64 + b"]" * 64 + b"}"
        causes = {
            "its fetch fails": (b'{"d":"' + b"#" * 44 + b'"}', True),
            "not JSON": (b"not json " + b"#" * 44, False),
            "nested too deep": (deep, False),
            "a lone surrogate": (b'{"d":"' + b"#" * 44 + b'","x":"\\ud83d"}', False),
        }
        for cause, (raw, fails) in causes.items():
            with self.subTest(cause=cause):
                said, served = self.selection_of(raw)
                document = _doc(serves=[{"said": said}], publications=[_publication(target=said)])
                fake = FakeSource(self.s, document=document, files={said: served}, fail={said} if fails else ())
                got = self.read(fake)
                self.assertEqual((got["publications"][0]["problem"], got["publications"][0]["roles"], got["checked"]),
                                 ("unreadable", [], 0))

    def test_a_definition_whose_bytes_fail_their_said_is_a_mismatch(self):
        said, served = self.selection_of(b'{"d":"' + b"#" * 44 + b'","kind":"selection"}')
        document = _doc(serves=[{"said": said}], publications=[_publication(target=said)])
        got = self.read(FakeSource(self.s, document=document, files={said: served.replace(b"selection", b"Selection")}))
        self.assertEqual((got["publications"][0]["problem"], got["checked"]), ("mismatch", 0))

    def test_mismatch_wins_over_unreadable(self):
        # dropin-class needs one tampered definition (only it uses PAID_ADMISSION) and one that cannot be fetched
        # (all three use CLASS_ATTENDED).
        files = {f.stem: f.read_bytes() for f in (FIXTURES / "compact").glob("*.json")}
        files[PAID_ADMISSION] = files[PAID_ADMISSION].replace(b"paid-admission", b"paid-admissiom", 1)
        got = self.by_target(self.read(FakeSource(self.s, files=files, fail={CLASS_ATTENDED})))
        self.assertEqual(got[DROPIN_CLASS]["problem"], "mismatch")
        self.assertEqual([got[t]["problem"] for t in (CLASS_BRICK, MEMBER_CLASS)], ["unreadable"] * 2)

    def test_limit_counts_failed_fetch_attempts(self):
        payment = "ECru8k3CerlcJF0Khe0EOubRaA4U4APPGqOdvkzNR0L1"  # in all three closures
        fake = FakeSource(self.s, fail={payment})
        self.read(fake, definitions_per_read=12)
        self.assertEqual(len(fake.definition_calls()), 12)

    def test_unreadable_wins_over_limit(self):
        payment = "ECru8k3CerlcJF0Khe0EOubRaA4U4APPGqOdvkzNR0L1"
        # Every closure fits in 23 definitions (12, 19, 23), the read's 26 do not: member-class is cut by the limit
        # and also holds the failed definition.
        got = self.by_target(self.read(FakeSource(self.s, fail={payment}), definitions_per_read=23))
        self.assertEqual([got[t]["problem"] for t in (CLASS_BRICK, DROPIN_CLASS, MEMBER_CLASS)], ["unreadable"] * 3)

    def test_publication_whose_target_is_not_served_is_unreadable(self):
        document = _doc(serves=[], publications=[_publication(target=SAID_A)])
        fake = FakeSource(self.s, document=document, files={})
        got = self.read(fake)
        self.assertEqual((got["publications"][0]["problem"], got["publications"][0]["roles"]), ("unreadable", []))
        self.assertEqual(fake.definition_calls(), [])

    def test_limit_cuts_the_read_and_fetches_nothing_beyond_it(self):
        fake = FakeSource(self.s)
        got = self.by_target(self.read(fake, definitions_per_read=12))
        self.assertEqual(len(fake.definition_calls()), 12)
        self.assertIsNone(got[CLASS_BRICK]["problem"])  # its closure is exactly 12 definitions
        self.assertTrue(got[CLASS_BRICK]["roles"])
        for target in (DROPIN_CLASS, MEMBER_CLASS):
            self.assertEqual((got[target]["problem"], got[target]["roles"]), ("limit", []))

    def test_default_limit_is_fifty_definitions(self):
        files = {}
        for i in range(60):
            raw = b'{"d":"' + b"#" * 44 + b'","n":' + str(i).encode() + b"}"
            real = self.s.encode_said(digest(raw))
            files[real] = raw.replace(b"#" * 44, real.encode())
        real_saids = list(files)
        # Two publications of 30 definitions each: the first is read whole, the second is cut after 20 more.
        first, second = real_saids[:30], real_saids[30:]
        serves = [{"said": first[0], "refers": first[1:]}, {"said": second[0], "refers": second[1:]}] + [
            {"said": said} for said in first[1:] + second[1:]]
        document = _doc(serves=serves, publications=[_publication(target=first[0]), _publication(target=second[0])])
        fake = FakeSource(self.s, document=document, files=files)
        got = self.read(fake)
        self.assertEqual(len(fake.definition_calls()), 50)
        self.assertEqual(got["checked"], 50)
        self.assertEqual([p["problem"] for p in got["publications"]], [None, "limit"])

    def big_definitions(self, count: int, size: int) -> dict:
        """`count` definitions of `size` bytes each that pass their SAID check, in insertion order."""
        files = {}
        for i in range(count):
            head = b'{"d":"' + b"#" * 44 + b'","n":' + str(i).encode() + b',"pad":"'
            raw = head + b"x" * (size - len(head) - 2) + b'"}'
            said = self.s.encode_said(digest(raw))
            files[said] = raw.replace(b"#" * 44, said.encode())
            self.assertEqual(len(files[said]), size)
        return files

    def test_read_byte_budget_is_four_million(self):
        self.assertEqual(self.s.READ_BYTES, 4_000_000)

    def test_byte_budget_stops_fetching_and_marks_cut_publications_limit(self):
        files = self.big_definitions(6, 900_000)
        saids = list(files)
        document = _doc(serves=[{"said": said} for said in saids],
                        publications=[_publication(target=said) for said in saids])
        fake = FakeSource(self.s, document=document, files=files)
        got = self.read(fake)
        # 4 x 900,000 bytes plus the document is under 4,000,000: the fifth is fetched; after it the budget is spent.
        self.assertEqual(fake.definition_calls(), [self.s.definition_address(BASE, said) for said in saids[:5]])
        self.assertEqual([p["problem"] for p in got["publications"]], [None] * 5 + ["limit"])
        self.assertEqual(got["checked"], 5)

    def test_byte_budget_counts_the_source_document(self):
        files = self.big_definitions(5, 800_000)
        saids = list(files)
        document = _doc(serves=[{"said": said} for said in saids],
                        publications=[_publication(target=said) for said in saids], pad="x" * 950_000)
        fake = FakeSource(self.s, document=document, files=files)
        got = self.read(fake)
        # 950,000 + 4 x 800,000 spends the budget, so the fifth definition is never requested.
        self.assertEqual(fake.definition_calls(), [self.s.definition_address(BASE, said) for said in saids[:4]])
        self.assertEqual(got["publications"][4]["problem"], "limit")

    def test_unreadable_wins_over_the_byte_budget(self):
        files = self.big_definitions(7, 900_000)
        saids = list(files)
        # This fake fails `failing` before any body byte arrives, so only saids[1:6] spend the budget.
        failing, cut = saids[0], saids[6]
        document = _doc(
            serves=[{"said": said} for said in saids[:6]] + [{"said": cut, "refers": [failing]}],
            publications=[_publication(target=said) for said in saids[:6]] + [_publication(target=cut)])
        fake = FakeSource(self.s, document=document, files=files, fail={failing})
        got = self.read(fake)
        self.assertNotIn(self.s.definition_address(BASE, cut), fake.definition_calls())
        # The last publication needs the failed definition and one cut by the budget: unreadable wins.
        self.assertEqual(got["publications"][6]["problem"], "unreadable")
        self.assertEqual(got["publications"][0]["problem"], "unreadable")

    def test_byte_budget_counts_the_bytes_received_by_failed_definitions(self):
        # A hostile source: every definition is just over the file limit, so each fetch fails after 1,000,001 bytes.
        saids = _unserved_saids(50)
        document = _doc(
            serves=[{"said": said} for said in saids[:49]] + [{"said": saids[49], "refers": [saids[0]]}],
            publications=[_publication(target=said) for said in saids])
        fake = FakeSource(self.s, document=document, files={}, fail=dict.fromkeys(saids, 1_000_001))
        got = self.read(fake)
        # Four such fetches spend the budget (4,000,004 bytes): the other 46 definitions are never requested.
        self.assertEqual(fake.definition_calls(), [self.s.definition_address(BASE, said) for said in saids[:4]])
        # Requested and failed: unreadable. Only cut: limit. The last one is cut and also needs saids[0], which failed.
        self.assertEqual([p["problem"] for p in got["publications"]],
                         ["unreadable"] * 4 + ["limit"] * 45 + ["unreadable"])
        self.assertEqual((got["checked"], got["definitions"]), (0, {}))

    def test_byte_budget_adds_up_definitions_that_arrived_and_ones_that_failed(self):
        files = self.big_definitions(5, 900_000)
        good = list(files)
        too_big, cut_off, missing = _unserved_saids(3)
        order = [good[0], too_big, good[1], cut_off, missing, good[2], good[3], good[4]]
        document = _doc(serves=[{"said": said} for said in order],
                        publications=[_publication(target=said) for said in order])
        fake = FakeSource(self.s, document=document, files=files,
                          fail={too_big: 1_000_001, cut_off: 600_000, missing: 0})
        got = self.read(fake)
        # 900,000 + 1,000,001 + 900,000 + 600,000 + 0 is under the budget, so good[2] is fetched; its 900,000 bytes
        # spend the budget and the last two are never requested. Counting only what arrived whole (2,700,000) would
        # fetch them both.
        self.assertEqual(fake.definition_calls(), [self.s.definition_address(BASE, said) for said in order[:6]])
        self.assertEqual([p["problem"] for p in got["publications"]],
                         [None, "unreadable", None, "unreadable", "unreadable", None, "limit", "limit"])
        self.assertEqual(got["checked"], 3)

    def test_faults_that_received_nothing_spend_none_of_the_byte_budget(self):
        saids = _unserved_saids(10)
        document = _doc(serves=[{"said": said} for said in saids],
                        publications=[_publication(target=said) for said in saids])
        fake = FakeSource(self.s, document=document, files={}, fail=saids)  # as if every definition answered 404
        got = self.read(fake, read_bytes=len(document) + 1)
        # The document leaves one byte of the budget, and ten faults that received nothing do not spend it.
        self.assertEqual(fake.definition_calls(), [self.s.definition_address(BASE, said) for said in saids])
        self.assertEqual([p["problem"] for p in got["publications"]], ["unreadable"] * 10)

    def test_roles_of_every_fixture_publication_match_role_derivation(self):
        got = self.read(FakeSource(self.s))
        definitions = _definitions()
        for p in got["publications"]:
            expected = self.s.roles_of_selection(definitions, p["target"])
            self.assertEqual((p["title"], p["roles"]), (expected["title"], expected["roles"]), p["name"])

    def test_read_stops_when_the_clock_passes_sixty_seconds(self):
        clock = Clock()
        fake = FakeSource(self.s, clock=clock, ms_per_fetch=5_000)
        with self.assertRaises(self.s.SourceFault) as raised:
            self.read(fake, clock=clock)
        self.assertEqual(raised.exception.code, "UNREACHABLE")
        self.assertEqual(raised.exception.message, "Could not reach mad.seriouscoder.one.")
        self.assertEqual(len(fake.calls), 12)  # the 12th fetch ends at 60 s; the read stops before a 13th

    def test_request_timeout_never_outlasts_the_read(self):
        clock = Clock()
        fake = FakeSource(self.s, clock=clock, ms_per_fetch=2_000)
        self.read(fake, clock=clock)
        # 26 fetches of 2 s each have used 52 s when the 27th starts: 8 s of the read are left.
        self.assertEqual([t for _, t in fake.calls], [10_000] * 26 + [8_000])

    def test_invalid_source_document_is_bad_source(self):
        fake = FakeSource(self.s, document=b"[]")
        with self.assertRaises(self.s.SourceFault) as raised:
            self.read(fake)
        self.assertEqual(raised.exception.code, "BAD_SOURCE")
        self.assertEqual(raised.exception.message, "mad.seriouscoder.one did not return a valid source.")
        self.assertEqual(fake.definition_calls(), [])

    def test_fault_fetching_the_document_stops_the_read(self):
        calls = []

        async def refuse(url, timeout_ms):
            calls.append(url)
            raise self.s.SourceFault("NOT_READABLE", "Could not read mad.seriouscoder.one: it cannot be reached, or it "
                                                     "does not allow wallets to read it yet.")
        with self.assertRaises(self.s.SourceFault) as raised:
            asyncio.run(self.s.read_source("mad.seriouscoder.one", fetch_bytes=refuse, digest=digest, clock=Clock()))
        self.assertEqual(raised.exception.code, "NOT_READABLE")
        self.assertEqual(calls, [ADDRESS])

    def test_bad_address_fetches_nothing(self):
        fake = FakeSource(self.s)
        with self.assertRaises(self.s.SourceFault) as raised:
            self.read(fake, text="http://x")
        self.assertEqual(raised.exception.code, "BAD_ADDRESS")
        self.assertEqual(fake.calls, [])

    def test_publication_by_carried_through(self):
        document = json.loads((FIXTURES / "source.json").read_bytes())
        document["publications"][0]["by"] = "Eabc_-1"
        got = self.read(FakeSource(self.s, document=json.dumps(document).encode()))
        self.assertEqual([p["by"] for p in got["publications"]], ["Eabc_-1", None, None])



# What a read costs is bounded (the whole-feature review's C2): publications, closures, parsing, the answer, time.

def _made(body: dict) -> tuple[str, bytes]:
    """A definition with its SAID: `body` after "d", compact; (SAID, served bytes)."""
    text = json.dumps({"d": "#" * 44, **body}, separators=(",", ":"))
    said = _load().encode_said(digest(text.encode()))
    return said, text.replace("#" * 44, said).encode()


def _items(publications: list) -> int:
    """The answer's items: a role, a responsibility, and each reference (reliesOn, attests, outcome) count one."""
    return sum(1 + sum(1 + len(r["reliesOn"]) + len(r["attests"]) + (r["outcome"] is not None)
                       for r in role["responsibilities"])
               for publication in publications for role in publication["roles"])


class Selection:
    """A selection with one rr, `role`, that relies `relied` times on one fact: its roles count `relied` + 2 items."""

    def __init__(self, relied: int, role: str = "member"):
        self.fact, fact = _made({"kind": "fact", "alias": "seat"})
        self.rr, rr = _made({"kind": "rr", "role": role, "alias": "join",
                             "rely_on": [{"said": self.fact, "as": "seat"}] * relied})
        self.target, selection = _made({"kind": "selection", "name": "Join", "rrs": [self.rr]})
        self.files = {self.fact: fact, self.rr: rr, self.target: selection}
        self.serves = [{"said": self.target, "refers": [self.rr, self.fact]}, {"said": self.rr, "refers": [self.fact]},
                       {"said": self.fact}]


class ReadCostTest(unittest.TestCase):
    def setUp(self):
        self.s = _load()

    def read(self, fake: FakeSource, clock=None) -> dict:
        return asyncio.run(self.s.read_source("mad.seriouscoder.one", fetch_bytes=fake.fetch_bytes, digest=digest,
                                              clock=clock or Clock()))

    def source(self, selections: list, publications: list) -> FakeSource:
        serves = {item["said"]: item for selection in selections for item in selection.serves}
        files = {said: raw for selection in selections for said, raw in selection.files.items()}
        return FakeSource(self.s, document=_doc(serves=list(serves.values()), publications=publications),
                          files=files)

    def test_the_limits(self):
        self.assertEqual((self.s.PUBLICATIONS_PER_READ, self.s.ANSWER_ITEMS), (50, 5_000))

    def test_the_first_fifty_valid_publications_are_read_and_the_rest_counted(self):
        one = Selection(1)
        for valid, dropped, read, unread in ((51, 0, 50, 1), (50, 0, 50, 0), (50, 3, 50, 0), (60, 2, 50, 10)):
            with self.subTest(valid=valid, dropped=dropped):
                # Dropped publications first: they do not use up a place.
                publications = [_publication(target="nope")] * dropped + [
                    _publication(target=one.target, name=f"p{n}") for n in range(valid)]
                got = self.read(self.source([one], publications))
                self.assertEqual([p["name"] for p in got["publications"]], [f"p{n}" for n in range(read)])
                self.assertEqual((got["unread"], got["dropped"]["publications"]), (unread, dropped))
                self.assertEqual({p["problem"] for p in got["publications"]}, {None})

    def test_an_amplifying_source_is_bounded(self):
        # The review's source: 400 publications of one selection whose one rr relies 15,000 times on one definition.
        heavy = Selection(15_000)
        fake = self.source([heavy], [_publication(target=heavy.target, name=f"p{n}") for n in range(400)])
        parsed = []
        parse = self.s._parse_bounded

        def counting(raw):
            parsed.append(bytes(raw))
            return parse(raw)
        self.s._parse_bounded = counting
        clock = Clock()
        reads = []

        def counted_clock():
            reads.append(1)
            return clock()
        got = asyncio.run(self.s.read_source("mad.seriouscoder.one", fetch_bytes=fake.fetch_bytes, digest=digest,
                                             clock=counted_clock))
        definitions = [raw for raw in parsed if raw != fake.document]
        self.assertEqual(sorted(definitions), sorted(heavy.files.values()))  # each parsed once
        self.assertEqual(len(got["publications"]), 50)
        self.assertEqual(got["unread"], 350)
        self.assertLessEqual(_items(got["publications"]), 5_000)
        self.assertEqual({(p["problem"], len(p["roles"])) for p in got["publications"]}, {("limit", 0)})
        self.assertGreaterEqual(len(reads), 50)

    def test_a_publication_that_takes_exactly_what_is_left_is_shown(self):
        small = Selection(1)  # 3 items
        exact = Selection(5_000 - 3 - 2, role="exact")  # 4,997 items: with the small one, 5,000
        over = Selection(5_000 - 3 - 2 + 1, role="over")  # one more
        got = self.read(self.source([small, exact], [_publication(target=small.target, name="small"),
                                                     _publication(target=exact.target, name="exact")]))
        self.assertEqual([p["problem"] for p in got["publications"]], [None, None])
        self.assertEqual(_items(got["publications"]), 5_000)
        got = self.read(self.source([small, over], [_publication(target=small.target, name="small"),
                                                    _publication(target=over.target, name="over"),
                                                    _publication(target=small.target, name="small again")]))
        self.assertEqual([(p["problem"], len(p["roles"])) for p in got["publications"]],
                         [(None, 1), ("limit", 0), (None, 1)])
        self.assertEqual(_items(got["publications"]), 6)
        self.assertNotIn(over.target, got["derivedFrom"])

    def chain(self, length: int) -> list[str]:
        return [self.s.encode_said(digest(str(n).encode())) for n in range(length)]

    def test_a_closure_of_more_than_fifty_definitions_is_a_limit_and_fetches_nothing(self):
        for length, problem, fetched in ((51, "limit", 0), (50, "unreadable", 50)):
            with self.subTest(length=length):
                saids = self.chain(length)
                document = _doc(serves=[{"said": said, "refers": saids[i + 1:i + 2]} for i, said in enumerate(saids)],
                                publications=[_publication(target=saids[0])])
                fake = FakeSource(self.s, document=document, files={})
                got = self.read(fake)
                self.assertEqual(got["publications"][0]["problem"], problem)
                self.assertEqual(len(fake.definition_calls()), fetched)

    def test_a_closure_is_not_followed_past_the_limit(self):
        # The review's closure source: a chain of 4,000 served definitions, named by 4,500 publications.
        saids = self.chain(4_000)
        document = _doc(serves=[{"said": said, "refers": saids[i + 1:i + 2]} for i, said in enumerate(saids)],
                        publications=[_publication(target=saids[0])] * 4_500)
        lookups = []

        class Counting(dict):
            def __getitem__(self, key):
                lookups.append(key)
                return super().__getitem__(key)

            def get(self, key, default=None):
                lookups.append(key)
                return super().get(key, default)

            def __contains__(self, key):
                lookups.append(key)
                return super().__contains__(key)
        read_document = self.s.read_source_document

        def counting_document(raw, address):
            read = read_document(raw, address)
            read["document"]["serves"] = Counting(read["document"]["serves"])
            return read
        self.s.read_source_document = counting_document
        fake = FakeSource(self.s, document=document, files={})
        got = self.read(fake)
        self.assertEqual({p["problem"] for p in got["publications"]}, {"limit"})
        self.assertEqual(fake.definition_calls(), [])
        self.assertLess(len(lookups), 3 * 52, len(lookups))

    def test_the_clock_is_read_for_every_publication(self):
        one = Selection(1)
        reads = []
        clock = Clock()

        def counted():
            reads.append(1)
            return clock()
        fake = self.source([one], [_publication(target=one.target, name=f"p{n}") for n in range(50)])
        asyncio.run(self.s.read_source("mad.seriouscoder.one", fetch_bytes=fake.fetch_bytes, digest=digest,
                                       clock=counted))
        # Once at the start, and around the document's and the three definitions' fetches: the rest are per publication.
        self.assertGreaterEqual(len(reads), 50)

    def test_deriving_stops_when_the_read_runs_out_of_time(self):
        one = Selection(1)
        clock = Clock()

        def slow():
            clock.now += 2_000  # each reading of the clock costs two seconds of deriving
            return clock.now
        fake = self.source([one], [_publication(target=one.target, name=f"p{n}") for n in range(50)])
        with self.assertRaises(self.s.SourceFault) as raised:
            asyncio.run(self.s.read_source("mad.seriouscoder.one", fetch_bytes=fake.fetch_bytes, digest=digest,
                                           clock=slow))
        self.assertEqual(raised.exception.code, "UNREACHABLE")

    def test_another_task_makes_progress_while_a_read_derives(self):
        one = Selection(1)
        fake = self.source([one], [_publication(target=one.target, name=f"p{n}") for n in range(50)])

        async def both():
            ticks = []
            done = False

            async def other():
                while not done:
                    ticks.append(1)
                    await asyncio.sleep(0)
            task = asyncio.ensure_future(other())
            await asyncio.sleep(0)
            ticks.clear()
            await self.s.read_source("mad.seriouscoder.one", fetch_bytes=fake.fetch_bytes, digest=digest,
                                     clock=Clock())
            done = True
            await task
            return len(ticks)
        # The fake's fetches never wait, so the other task runs only when the read lets it, between publications.
        self.assertGreaterEqual(asyncio.run(both()), 49)


if __name__ == "__main__":
    unittest.main()
