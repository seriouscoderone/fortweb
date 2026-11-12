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

    def test_wrong_typed_lists_ignored(self):
        defs = {
            SAID_A: {"kind": "selection", "name": "S", "rrs": [SAID_B]},
            SAID_B: {"kind": "rr", "alias": "r", "role": "clerk", "outcome": SAID_C, "rely_on": {"said": SAID_C}, "attest": SAID_C},
        }
        got = self.s.roles_of_selection(defs, SAID_A)
        self.assertEqual(got["roles"][0]["responsibilities"],
                         [{"said": SAID_B, "alias": "r", "reliesOn": [], "attests": [], "outcome": None}])


if __name__ == "__main__":
    unittest.main()
