#!/usr/bin/env python3
import json
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
# Never touch this machine's real application data.
os.environ.setdefault("UAL_APP_DATA", tempfile.mkdtemp(prefix="ual-test-app-data-"))

import index_assets as ia  # noqa: E402
import server  # noqa: E402
import storage  # noqa: E402
import user_tags  # noqa: E402


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.root_a = os.path.join(self.tmp.name, "vault-a")
        self.root_b = os.path.join(self.tmp.name, "vault-b")
        os.mkdir(self.root_a)
        os.mkdir(self.root_b)
        with open(os.path.join(self.root_a, "A v1.0.unitypackage"), "wb") as fh:
            fh.write(b"a")
        with open(os.path.join(self.root_b, "B v1.0.unitypackage"), "wb") as fh:
            fh.write(b"b")

    def tearDown(self):
        self.tmp.cleanup()

    def _state(self, root):
        """This machine's state directory for a library (index, cache, locks)."""
        return ia.app_state_dir(root)

    def _write_legacy_state(self, root, *, with_meta=True, names=("assets.json", "cache.json")):
        """Seed a legacy repo state/ bound to `root`."""
        legacy = os.path.join(self.repo, "state")
        os.makedirs(legacy, exist_ok=True)
        payloads = {
            "assets.json": json.dumps(ia.build(ia.scan(root))),
            "cache.json": json.dumps({
                "resolved": {"a/a": {"status": "resolved", "id": "1"}},
                "misses": {},
            }),
            "index-meta.json": json.dumps({
                "api_version": 6, "vault_root": root, "repo": self.repo,
                "root_identity": storage._identity(root),
            }),
        }
        for name in names:
            with open(os.path.join(legacy, name), "w", encoding="utf-8") as fh:
                fh.write(payloads[name])
        if with_meta and "index-meta.json" not in names:
            with open(os.path.join(legacy, "index-meta.json"), "w", encoding="utf-8") as fh:
                fh.write(payloads["index-meta.json"])
        return legacy

    def test_first_configure_builds_index_in_data_dir_and_writes_pointer(self):
        result = storage.configure(self.repo, self.root_a, "instance-test-1")
        self.assertTrue(result["ready"])
        state = self._state(self.root_a)
        with open(os.path.join(state, "assets.json"), encoding="utf-8") as fh:
            assets = json.load(fh)
        self.assertEqual([v["file"] for a in assets["assets"] for v in a["versions"]],
                         ["A v1.0.unitypackage"])
        for name in ("pending-enrichment.json", "review-queue.md", "index-meta.json",
                     "assets.csv", "config.json"):
            self.assertTrue(os.path.exists(os.path.join(state, name)), name)
        # The repo config.json is only the agreed exception: the last-opened
        # library path, nothing else.
        with open(os.path.join(self.repo, "config.json"), encoding="utf-8") as fh:
            pointer = json.load(fh)
        self.assertEqual(pointer, {"vault_root": os.path.realpath(self.root_a)})
        self.assertEqual(storage.status(self.repo)["path"], os.path.realpath(self.root_a))

    def test_staged_artifact_failure_preserves_previous_state(self):
        storage.configure(self.repo, self.root_a, "instance-test-1")
        with open(os.path.join(self.repo, "config.json"), encoding="utf-8") as fh:
            old_config = fh.read()
        with mock.patch.object(storage.ia, "emit_csv", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                storage.configure(self.repo, self.root_b, "instance-test-2")
        with open(os.path.join(self.repo, "config.json"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), old_config)
        self.assertEqual(storage.status(self.repo)["path"], os.path.realpath(self.root_a))

    def test_recovery_does_not_delete_unstarted_targets(self):
        state = self._state(self.root_a)
        os.makedirs(state)
        assets = os.path.join(state, "assets.json")
        config = os.path.join(state, "config.json")
        with open(assets, "w", encoding="utf-8") as fh:
            fh.write("old-assets")
        txn = "a" * 32
        _, _, backup = storage._entry_paths(state, txn, "assets")
        os.replace(assets, backup)
        _, stage_config, _ = storage._entry_paths(state, txn, "config")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"vault_root": self.root_a}, fh)
        with open(stage_config, "w", encoding="utf-8") as fh:
            fh.write("new-config")
        storage._write_json(storage._journal_path(state), {
            "version": storage.JOURNAL_VERSION, "txn": txn, "status": "applying",
            "entries": ["assets", "config"],
            "backed_up": ["assets"], "existed": ["assets"],
        })
        self.assertTrue(storage.recover(self.repo, state=state))
        with open(assets, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "old-assets")
        with open(config, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["vault_root"], self.root_a)

    def test_running_server_lock_blocks_reconfigure_of_same_library(self):
        service = server.ViewerService(repo=self.repo,
                                       config={"vault_root": self.root_a},
                                       instance_id="instance-test-1")
        service.acquire_instance_lock()
        try:
            with self.assertRaises(storage.StorageError):
                storage.configure(self.repo, self.root_a, "instance-test-2")
        finally:
            service.shutdown()

    def test_root_switch_isolates_libraries(self):
        storage.configure(self.repo, self.root_a, "instance-test-1")
        storage.configure(self.repo, self.root_b, "instance-test-2")
        with open(os.path.join(self._state(self.root_b), "assets.json"), encoding="utf-8") as fh:
            assets = json.load(fh)
        files = [v["file"] for a in assets["assets"] for v in a["versions"]]
        self.assertEqual(files, ["B v1.0.unitypackage"])
        # Library A's data is untouched by the switch.
        with open(os.path.join(self._state(self.root_a), "assets.json"), encoding="utf-8") as fh:
            old_assets = json.load(fh)
        self.assertEqual([v["file"] for a in old_assets["assets"] for v in a["versions"]],
                         ["A v1.0.unitypackage"])
        self.assertEqual(storage.status(self.repo)["path"], os.path.realpath(self.root_b))

    # -- legacy migration ----------------------------------------------------

    def test_legacy_state_migrates_into_matching_library_and_is_preserved(self):
        legacy = self._write_legacy_state(self.root_a)
        history = "## Original history\nAsset added\n"
        with open(os.path.join(legacy, "CHANGES.md"), "w") as fh:
            fh.write(history)
        with open(os.path.join(legacy, "favorites.json"), "w") as fh:
            json.dump({"favorites": ["a"]}, fh)
        with open(os.path.join(self.repo, "config.json"), "w") as fh:
            json.dump({"vault_root": self.root_a, "output_dir": "gallery"}, fh)
        os.mkdir(os.path.join(self.repo, "gallery"))
        with open(os.path.join(self.repo, "gallery", "assets.csv"), "w") as fh:
            fh.write("original,csv\n")
        self.assertTrue(storage.status(self.repo)["ready"])
        with open(os.path.join(self._state(self.root_a), "CHANGES.md")) as fh:
            self.assertEqual(fh.read(), history)
        with open(os.path.join(self._state(self.root_a), "assets.csv")) as fh:
            self.assertEqual(fh.read(), "original,csv\n")
        service = server.ViewerService(repo=self.repo)
        self.assertEqual(service.op_favorites(), {"favorites": ["a"]})
        storage.configure(self.repo, self.root_a, "instance-test-1")
        state = self._state(self.root_a)
        with open(os.path.join(state, "cache.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["resolved"]["a/a"]["id"], "1")
        # The legacy origin stays untouched.
        self.assertTrue(os.path.exists(os.path.join(legacy, "assets.json")))
        self.assertTrue(os.path.exists(os.path.join(legacy, "cache.json")))

    def test_legacy_state_never_bleeds_into_a_different_library(self):
        self._write_legacy_state(self.root_a)
        storage.configure(self.repo, self.root_b, "instance-test-1")
        state_b = self._state(self.root_b)
        self.assertFalse(os.path.exists(os.path.join(state_b, "cache.json")))
        # Reopening the owning library migrates it there.
        storage.configure(self.repo, self.root_a, "instance-test-2")
        self.assertTrue(os.path.exists(os.path.join(self._state(self.root_a), "cache.json")))

    def test_existing_destination_wins_over_legacy(self):
        legacy = self._write_legacy_state(self.root_a)
        state = self._state(self.root_a)
        os.makedirs(state)
        with open(os.path.join(state, "cache.json"), "w", encoding="utf-8") as fh:
            json.dump({"resolved": {"kept": {"status": "resolved", "id": "9"}}, "misses": {}}, fh)
        storage.ensure_migrated(self.repo, {"vault_root": self.root_a})
        with open(os.path.join(state, "cache.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["resolved"]["kept"]["id"], "9")
        self.assertTrue(os.path.exists(os.path.join(legacy, "cache.json")))

    def test_unbound_legacy_state_is_never_migrated(self):
        legacy = self._write_legacy_state(self.root_a, with_meta=False)
        self.assertFalse(storage.ensure_migrated(self.repo, {"vault_root": self.root_a}))
        self.assertFalse(os.path.exists(os.path.join(self._state(self.root_a), "assets.json")))
        self.assertTrue(os.path.exists(os.path.join(legacy, "assets.json")))

    def test_interrupted_migration_resumes(self):
        legacy = self._write_legacy_state(self.root_a)
        state = self._state(self.root_a)
        os.makedirs(state)
        # Simulate a crash after only assets.json was copied.
        with open(os.path.join(legacy, "assets.json"), encoding="utf-8") as fh:
            payload = fh.read()
        ia.write_atomic(os.path.join(state, "assets.json"), payload)
        self.assertTrue(storage.ensure_migrated(self.repo, {"vault_root": self.root_a}))
        self.assertTrue(os.path.exists(os.path.join(state, "cache.json")))
        self.assertTrue(os.path.exists(os.path.join(state, "index-meta.json")))

    def test_migration_marker_prevents_resurrection(self):
        self._write_legacy_state(self.root_a)
        state = self._state(self.root_a)
        self.assertTrue(storage.ensure_migrated(self.repo, {"vault_root": self.root_a}))
        os.unlink(os.path.join(state, "cache.json"))
        self.assertFalse(storage.ensure_migrated(self.repo, {"vault_root": self.root_a}))
        self.assertFalse(os.path.exists(os.path.join(state, "cache.json")))

    def test_symlinked_data_dir_is_rejected(self):
        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.root_a, ".data"))
        self._write_legacy_state(self.root_a)
        with self.assertRaises(storage.StorageError):
            storage.ensure_migrated(self.repo, {"vault_root": self.root_a})

    def test_legacy_journal_recovery_restores_original_repo_paths(self):
        legacy = os.path.join(self.repo, "state")
        os.makedirs(legacy)
        assets = os.path.join(legacy, "assets.json")
        repo_config = os.path.join(self.repo, "config.json")
        with open(assets, "w", encoding="utf-8") as fh:
            fh.write("old-assets")
        with open(repo_config, "w", encoding="utf-8") as fh:
            json.dump({"vault_root": self.root_a, "output_dir": "gallery"}, fh)
        os.makedirs(os.path.join(self.repo, "gallery"))
        txn = "b" * 32
        config_backup = os.path.join(self.repo, f".storage-backup-{txn}-config")
        csv_stage = os.path.join(self.repo, "gallery", f".storage-stage-{txn}-csv")
        os.replace(repo_config, config_backup)
        with open(csv_stage, "w", encoding="utf-8") as fh:
            fh.write("csv,rows\n")
        storage._write_json(storage._journal_path(legacy), {
            "version": storage.JOURNAL_VERSION, "txn": txn, "status": "applying",
            "entries": ["config", "csv"], "output_rel": "gallery",
            "backed_up": ["config"], "existed": ["config"],
        })
        self.assertTrue(storage.recover_startup(self.repo))
        with open(repo_config, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["vault_root"], self.root_a)
        # The csv backup restores to its legacy repo output location.
        txn2 = "c" * 32
        csv_backup = os.path.join(self.repo, "gallery", f".storage-backup-{txn2}-csv")
        with open(csv_backup, "w", encoding="utf-8") as fh:
            fh.write("csv,rows\n")
        storage._write_json(storage._journal_path(legacy), {
            "version": storage.JOURNAL_VERSION, "txn": txn2, "status": "applying",
            "entries": ["csv"], "output_rel": "gallery",
            "backed_up": ["csv"], "existed": ["csv"],
        })
        self.assertTrue(storage.recover_startup(self.repo))
        self.assertTrue(os.path.exists(os.path.join(self.repo, "gallery", "assets.csv")))

    def test_moved_library_reopens_and_rebuilds_without_losing_tags(self):
        storage.configure(self.repo, self.root_a, "instance-test-1")
        state = self._state(self.root_a)
        with open(os.path.join(state, "assets.json"), encoding="utf-8") as fh:
            assets = json.load(fh)
        key = assets["assets"][0]["asset_key"]
        user_tags.mutate(state, {"action": "assign", "tag": "keeper", "asset_key": key})
        moved = os.path.join(self.tmp.name, "vault-a-moved")
        os.rename(self.root_a, moved)
        ia.write_atomic(os.path.join(self.repo, "config.json"),
                        json.dumps({"vault_root": moved}))
        result = storage.status(self.repo)
        self.assertTrue(result["needsIndex"])
        self.assertFalse(result["needsSetup"])
        storage.configure(self.repo, moved, "instance-test-2")
        moved_state = self._state(moved)
        self.assertEqual(user_tags.load(moved_state)["assignments"][key], ["keeper"])
        with open(os.path.join(moved_state, "assets.json"), encoding="utf-8") as fh:
            rebuilt = json.load(fh)
        self.assertEqual([v["file"] for a in rebuilt["assets"] for v in a["versions"]],
                         ["A v1.0.unitypackage"])

    def test_replaced_root_remembers_path_and_rebuilds_preserving_tags(self):
        storage.configure(self.repo, self.root_a, "instance-test-1")
        state = self._state(self.root_a)
        with open(os.path.join(state, "assets.json"), encoding="utf-8") as fh:
            key = json.load(fh)["assets"][0]["asset_key"]
        user_tags.mutate(state, {"action": "assign", "tag": "keeper", "asset_key": key})
        old = self.root_a + "-old"
        os.rename(self.root_a, old)
        os.mkdir(self.root_a)
        os.rename(os.path.join(old, ".data"), os.path.join(self.root_a, ".data"))
        os.rename(os.path.join(old, "A v1.0.unitypackage"),
                  os.path.join(self.root_a, "A v1.0.unitypackage"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = storage.main(["--repo", self.repo, "status"])
        result = json.loads(output.getvalue())
        self.assertEqual(result["path"], os.path.realpath(self.root_a))
        self.assertTrue(result["needsIndex"])
        self.assertFalse(result["needsSetup"])
        self.assertEqual(code, 0)
        storage.configure(self.repo, result["path"], "instance-test-2")
        self.assertTrue(storage.status(self.repo)["ready"])
        self.assertEqual(user_tags.load(state)["assignments"][key], ["keeper"])

    def test_unwritable_library_data_fails_without_fallback(self):
        legacy = self._write_legacy_state(self.root_a)
        original = {}
        for name in os.listdir(legacy):
            with open(os.path.join(legacy, name), "rb") as fh:
                original[name] = fh.read()
        state = self._state(self.root_a)
        os.makedirs(state)
        os.chmod(state, 0o500)
        try:
            with self.assertRaises(storage.StorageError):
                storage.ensure_migrated(self.repo, {"vault_root": self.root_a})
        finally:
            os.chmod(state, 0o700)
        # Failed migration must preserve every original, not fall back to changing it.
        for name, content in original.items():
            with open(os.path.join(legacy, name), "rb") as fh:
                self.assertEqual(fh.read(), content)
        self.assertFalse(os.path.exists(os.path.join(state, storage.MIGRATION_MARKER)))

    def test_cli_root_override_does_not_change_open_library(self):
        storage.configure(self.repo, self.root_a, "instance-test-1")
        indexed_a = os.path.join(self._state(self.root_a), "assets.json")
        with open(indexed_a, "rb") as fh:
            before = fh.read()
        with mock.patch.object(ia, "repo_dir", return_value=self.repo):
            self.assertEqual(ia.main(["scan", "--root", self.root_b]), 0)
        with open(indexed_a, "rb") as fh:
            self.assertEqual(fh.read(), before)
        with open(os.path.join(self._state(self.root_b), "assets.json")) as fh:
            rows = json.load(fh)["assets"]
        self.assertEqual([v["file"] for a in rows for v in a["versions"]], ["B v1.0.unitypackage"])

    def test_asset_operations_cannot_target_data_directory(self):
        state = self._state(self.root_a)
        os.makedirs(state)
        archive = os.path.join(state, "backup.unitypackage")
        with open(archive, "wb") as fh:
            fh.write(b"private backup")
        with self.assertRaises(storage.cv.SafetyError):
            storage.cv.validate_candidate(self.root_a, ".data/backup.unitypackage", {})
        self.assertEqual([r["rel_path"] for r in ia.scan(self.root_a)], ["A v1.0.unitypackage"])
        with open(archive, "rb") as fh:
            self.assertEqual(fh.read(), b"private backup")

    # -- vault .data split ---------------------------------------------------

    def _seed_old_vault_layout(self, root):
        vault = storage.data_dir(root)
        os.makedirs(os.path.join(vault, "ai-enrichment-run"))
        files = {
            "assets.json": json.dumps(ia.build(ia.scan(root))),
            "cache.json": json.dumps({"resolved": {"k1": {"status": "resolved", "id": "42"},
                                                   "k2": {"status": "unverified"}},
                                      "misses": {}}),
            "preferences.json": "{}",
            "CHANGES.md": "# history\n",
            "identity-migration-backup.json": "{}",
            "favorites.json": json.dumps({"favorites": ["k1"]}),
            "user_tags.json": json.dumps({"tags": [], "assignments": {}}),
            "state-write.lock": "",
            os.path.join("ai-enrichment-run", "summary.json"): "{}",
        }
        for name, text in files.items():
            with open(os.path.join(vault, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        return vault

    def test_old_vault_layout_moves_machine_files_and_keeps_user_data(self):
        vault = self._seed_old_vault_layout(self.root_a)
        state = storage.ensure_state_dir(self.root_a)
        self.assertEqual(sorted(os.listdir(vault)), ["favorites.json", "matches.json", "user_tags.json"])
        for name in ("assets.json", "cache.json", "preferences.json", "CHANGES.md",
                     "identity-migration-backup.json"):
            self.assertTrue(os.path.isfile(os.path.join(state, name)), name)
        # Unknown leftovers are archived, never deleted.
        self.assertTrue(os.path.isfile(os.path.join(state, "legacy", "ai-enrichment-run", "summary.json")))
        with open(os.path.join(state, "cache.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["resolved"]["k1"]["id"], "42")
        # Rerun is a no-op.
        self.assertEqual(storage.ensure_state_dir(self.root_a), state)
        self.assertEqual(sorted(os.listdir(vault)), ["favorites.json", "matches.json", "user_tags.json"])

    def test_interrupted_vault_move_finishes_and_state_copy_wins(self):
        vault = self._seed_old_vault_layout(self.root_a)
        state = ia.app_state_dir(self.root_a)
        os.makedirs(state)
        with open(os.path.join(state, "preferences.json"), "w", encoding="utf-8") as fh:
            fh.write('{"kept": true}')
        storage.ensure_state_dir(self.root_a)
        with open(os.path.join(state, "preferences.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"kept": True})
        # The differing vault copy is not deleted: nothing is lost silently.
        self.assertTrue(os.path.exists(os.path.join(vault, "preferences.json")))

    def test_configure_writes_store_id_matches_to_vault(self):
        self._seed_old_vault_layout(self.root_a)
        storage.configure(self.repo, self.root_a, "instance-test-1")
        with open(os.path.join(storage.data_dir(self.root_a), "matches.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"version": 1, "matches": {"k1": "42"}})
        self.assertFalse(os.path.exists(os.path.join(self._state(self.root_a), "matches.json")))

    def test_new_machine_keeps_user_data_and_rebuilds_state(self):
        storage.configure(self.repo, self.root_a, "instance-test-1")
        state = self._state(self.root_a)
        with open(os.path.join(state, "assets.json"), encoding="utf-8") as fh:
            key = json.load(fh)["assets"][0]["asset_key"]
        user_tags.mutate(state, {"action": "assign", "tag": "keeper", "asset_key": key})
        with mock.patch.dict(os.environ, {"UAL_APP_DATA": os.path.join(self.tmp.name, "other-mac")}):
            other = ia.app_state_dir(self.root_a)
            self.assertNotEqual(other, state)
            self.assertTrue(storage.status(self.repo)["needsIndex"])
            storage.configure(self.repo, self.root_a, "instance-test-2")
            self.assertEqual(user_tags.load(other)["assignments"][key], ["keeper"])
            self.assertTrue(os.path.isfile(os.path.join(other, "assets.json")))

    def test_state_dir_bound_to_another_library_is_refused(self):
        state = storage.ensure_state_dir(self.root_a)
        storage._write_json(os.path.join(state, ia.BINDING_NAME), {"vault_root": self.root_b})
        with self.assertRaises(storage.StorageError):
            storage.ensure_state_dir(self.root_a)


if __name__ == "__main__":
    unittest.main(verbosity=2)
