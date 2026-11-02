#!/usr/bin/env python3
"""Package header store-id parsing. Hermetic; no real vault access."""

import builtins
import json
import os
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
os.environ.setdefault("UAL_APP_DATA", tempfile.mkdtemp(prefix="ual-test-app-data-"))

import package_header as ph  # noqa: E402


def subfield(si, data):
    return si + struct.pack("<H", len(data)) + data


def header(extra, flags=0x04):
    return b"\x1f\x8b\x08" + bytes([flags]) + b"\x00" * 6 + struct.pack("<H", len(extra)) + extra + b"payload"


def meta(**over):
    obj = {"link": {"id": "177524", "type": "content"}, "id": "177524",
           "title": "Stylized Clouds Pack", "version": "2.0",
           "publisher": {"id": "42859", "label": "Ember Glitch"},
           "category": {"id": "21", "label": "3D Models/Environments"}}
    obj.update(over)
    return json.dumps(obj).encode()


def store_header(**over):
    return header(subfield(b"A$", meta(**over)))


def parsed_id(head):
    result = ph.parse_header(head)
    assert result is not None
    return result["id"]


class ParseHeaderTests(unittest.TestCase):
    def test_valid_header(self):
        self.assertEqual(ph.parse_header(store_header()), {
            "id": "177524", "title": "Stylized Clouds Pack", "publisher": "Ember Glitch",
            "category": "3D Models/Environments", "version": "2.0"})

    def test_misspelled_link_type_still_parses(self):
        head = store_header(link={"id": "177524", "type": "contemt"})
        self.assertEqual(parsed_id(head), "177524")

    def test_unrelated_subfield_before_store_data(self):
        head = header(subfield(b"XY", b"abc") + subfield(b"A$", meta()))
        self.assertEqual(parsed_id(head), "177524")

    def test_rejections(self):
        cases = {
            "no fextra": header(subfield(b"A$", meta()), flags=0x08),
            "xlen past buffer": store_header()[:20],
            "bad utf8": header(subfield(b"A$", b'{"id":"1\xec"}')),
            "ids differ": store_header(link={"id": "1"}),
            "non digit": store_header(id="12a", link={"id": "12a"}),
            "too long": store_header(id="1" * 13, link={"id": "1" * 13}),
            "not json object": header(subfield(b"A$", b"[1]")),
            "no id": header(subfield(b"A$", b'{"title":"x"}')),
            "truncated subfield": header(b"A$" + struct.pack("<H", 50) + b"{}"),
            "not gzip": b"PK\x03\x04" + b"\x00" * 20,
            "short": b"\x1f\x8b",
        }
        for name, head in cases.items():
            with self.subTest(name):
                self.assertIsNone(ph.parse_header(head))


def fake_stat(size, blocks, flags=0):
    return types.SimpleNamespace(st_size=size, st_blocks=blocks, st_flags=flags, st_mtime=1.0)


class MaterializedTests(unittest.TestCase):
    def test_dataless_file_is_never_opened(self):
        stat = fake_stat(1000, 8, ph.SF_DATALESS)
        opener = mock.Mock(side_effect=AssertionError("opened a cloud-only file"))
        with mock.patch.object(ph.os, "stat", return_value=stat), \
                mock.patch.object(builtins, "open", opener):
            self.assertIsNone(ph.read_store_header("/vault/cloud.unitypackage"))
        opener.assert_not_called()

    def test_sparse_file_is_not_materialized(self):
        self.assertFalse(ph.is_materialized(fake_stat(10_000_000, 0)))
        self.assertTrue(ph.is_materialized(fake_stat(10_000_000, 128)))
        self.assertTrue(ph.is_materialized(fake_stat(100, 8)))

    def test_real_file(self):
        with tempfile.NamedTemporaryFile(suffix=".unitypackage", delete=False) as fh:
            fh.write(store_header())
        self.addCleanup(os.unlink, fh.name)
        result = ph.read_store_header(fh.name)
        assert result is not None
        self.assertEqual(result["id"], "177524")

    def test_missing_file(self):
        self.assertIsNone(ph.read_store_header("/nonexistent/x.unitypackage"))


class StoreIdsForTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ual-vault-")
        self.cache = os.path.join(self.root, "header-cache.json")

    def write(self, rel, data):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    def asset(self, key, *files, non_store=False):
        return {"asset_key": key, "non_store": non_store,
                "versions": [{"file": f} for f in files]}

    def test_ids_conflicts_and_skips(self):
        self.write("a/One v1.unitypackage", store_header())
        self.write("a/One v2.unitypackage", store_header())
        self.write("b/Two v1.unitypackage", store_header(id="1", link={"id": "1"}))
        self.write("b/Two v2.unitypackage", store_header(id="2", link={"id": "2"}))
        self.write("c/Plain.unitypackage", b"\x1f\x8b\x08\x08" + b"\x00" * 20)
        self.write("d/Archive.zip", store_header())
        assets = [self.asset("one", "a/One v1.unitypackage", "a/One v2.unitypackage"),
                  self.asset("two", "b/Two v1.unitypackage", "b/Two v2.unitypackage"),
                  self.asset("plain", "c/Plain.unitypackage"),
                  self.asset("zip", "d/Archive.zip"),
                  self.asset("nonstore", "a/One v1.unitypackage", non_store=True),
                  self.asset("gone", "x/Missing.unitypackage")]
        found, conflicts = ph.store_ids_for(assets, self.root, self.cache)
        self.assertEqual(set(found), {"one"})
        self.assertEqual(found["one"]["id"], "177524")
        self.assertEqual(conflicts, {"two": ["1", "2"]})
        self.assertTrue(os.path.exists(self.cache))

    def test_cache_reuse_opens_nothing(self):
        self.write("a/One.unitypackage", store_header())
        assets = [self.asset("one", "a/One.unitypackage")]
        ph.store_ids_for(assets, self.root, self.cache)
        with mock.patch.object(ph, "read_store_header",
                               side_effect=AssertionError("reopened unchanged file")):
            found, _ = ph.store_ids_for(assets, self.root, self.cache)
        self.assertEqual(found["one"]["id"], "177524")

    def test_cloud_only_file_not_cached(self):
        self.write("a/One.unitypackage", store_header())
        assets = [self.asset("one", "a/One.unitypackage")]
        with mock.patch.object(ph, "is_materialized", return_value=False):
            found, _ = ph.store_ids_for(assets, self.root, self.cache)
        self.assertEqual(found, {})
        self.assertFalse(os.path.exists(self.cache))


if __name__ == "__main__":
    unittest.main()
