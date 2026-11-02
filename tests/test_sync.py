#!/usr/bin/env python3
"""Sync job: index -> match -> review, and the confirmed apply step."""

import json
import os
import sys
import threading
import unittest
from typing import Any

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS)
sys.path.insert(0, os.path.join(os.path.dirname(TESTS), "bin"))

import server as srv  # noqa: E402
from test_server import ServerHarnessTestCase, _await, _FakeProc  # noqa: E402


def stage_proc(argv, calls, release=None):
    calls.append(argv[3] if len(argv) > 3 else "?")
    return _FakeProc(["ok"], rc=0, release=release)


class SyncTestCase(ServerHarnessTestCase):
    """service is typed Any: tests swap seams (_runner, _child) with fakes."""

    service: Any

    def setUp(self):
        super().setUp()
        self.service = self.h.service
        self.calls = []
        self.service._runner = lambda argv: stage_proc(argv, self.calls)
        self.pending = 0
        self.service._pending_count = lambda: self.pending

    def no_review(self):
        service = self.service
        service._plan_cleanup = lambda scanned: {
            "families": [], "removals": [], "candidate_bytes": 0,
            "candidate_files": 0, "candidate_families": 0}
        service._plan_organize = lambda root, data, scanned=None, progress=None: {
            "moves": [], "groups": [], "skips": {}, "warnings": [],
            "totals": {"assets_total": 1, "moves": 0, "move_bytes": 0, "skips": {}}}

    def start(self, trigger=None) -> Any:
        status, _, body = self.post("/api/sync/start", {"trigger": trigger} if trigger else None)
        self.assertEqual(status, 202, body)
        return body

    def sync(self) -> Any:
        return self.service._sync or {}

    def wait_status(self, *statuses) -> Any:
        self.assertTrue(_await(lambda: self.sync().get("status") in statuses),
                        self.sync().get("status"))
        return self.sync()

    def finish_review(self) -> Any:
        self.start()
        return self.wait_status("review")


class TestSyncRun(SyncTestCase):
    def test_completes_without_review_and_updates_index(self):
        self.no_review()
        self.h._make_archive("c.unitypackage", b"cc")
        rows = self.h._scan_rows() + [{"rel_path": "c.unitypackage", "size": 2}]
        self.service._scan = lambda root, strict=True, progress=None: rows
        self.start()
        job = self.wait_status("completed", "failed")
        self.assertEqual(job["status"], "completed", job["error"])
        self.assertEqual(self.h.update_calls, [1])
        self.assertIsNone(job["result"]["review"])

    def test_review_holds_file_changes(self):
        job = self.finish_review()
        review = job["result"]["review"]
        self.assertEqual(review["count"], 2)
        self.assertIsNotNone(review["cleanup"])
        self.assertIsNotNone(review["organize"])
        self.assertEqual(self.h.apply_cleanup_calls, [])
        self.assertEqual(self.h.apply_organize_calls, [])

    def test_state_exposes_sync(self):
        self.no_review()
        self.start()
        self.wait_status("completed")
        _, _, raw = self.h.request("GET", "/api/state")
        state = json.loads(raw)
        self.assertEqual(state["sync"]["kind"], "sync")
        self.assertNotIn("log", state["sync"])

    def test_unknown_trigger_rejected(self):
        status, _, _ = self.post("/api/sync/start", {"trigger": "nightly"})
        self.assertEqual(status, 400)


class TestSyncMatchStage(SyncTestCase):
    def test_manual_sync_matches_pending(self):
        self.no_review()
        self.pending = 2
        self.start()
        self.wait_status("completed")
        self.assertEqual(self.calls, ["resolve", "enrich", "emit"])
        self.assertEqual(self.sync()["status"], "completed")

    def test_auto_sync_skips_unchanged_pending_recently_tried(self):
        self.no_review()
        self.pending = 2
        self.start("auto")
        self.wait_status("completed")
        self.calls.clear()
        self.start("auto")
        self.wait_status("completed")
        self.assertEqual(self.calls, [])

    def test_cancel_during_match_stops_child(self):
        self.no_review()
        self.pending = 1
        release = threading.Event()
        procs = []

        def runner(argv):
            proc = _FakeProc(["ok"], rc=0, release=release)
            procs.append(proc)
            self.service._child = proc  # the real runner registers the child
            return proc

        self.service._runner = runner
        self.start()
        self.assertTrue(_await(lambda: procs))
        status, _, _ = self.post("/api/sync/cancel")
        self.assertEqual(status, 200)
        release.set()
        job = self.wait_status("cancelled")
        self.assertEqual(job["status"], "cancelled")
        self.assertTrue(procs[0].terminated)


class TestSyncExclusion(SyncTestCase):
    def test_second_start_coalesces_into_one_rerun(self):
        self.no_review()
        self.pending = 1
        release = threading.Event()
        self.service._runner = lambda argv: stage_proc(argv, self.calls, release=release)
        first = self.start()
        self.assertTrue(_await(lambda: self.calls))
        self.assertTrue(self.start()["coalesced"])
        self.assertTrue(self.start()["coalesced"])
        release.set()
        self.assertTrue(_await(lambda: self.sync().get("id") != first["job_id"]
                               and self.sync().get("status") == "completed"))
        self.assertFalse(self.service._sync_rerun)

    def test_actions_and_enrich_busy_while_sync_runs(self):
        self.pending = 1
        release = threading.Event()
        self.service._runner = lambda argv: stage_proc(argv, self.calls, release=release)
        self.start()
        self.assertTrue(_await(lambda: self.calls))
        with self.assertRaises(srv.Busy):
            self.service.start_action("resync", "plan")
        with self.assertRaises(srv.Busy):
            self.service.op_enrich_start()
        release.set()
        self.wait_status("review", "completed")


class TestSyncApply(SyncTestCase):
    def test_wrong_hash_rejected(self):
        self.finish_review()
        status, _, body = self.post("/api/sync/apply", {"review_hash": "0" * 64})
        self.assertEqual(status, 409)
        self.assertEqual((body or {}).get("error"), "plan_changed")
        self.assertEqual(self.h.apply_cleanup_calls, [])

    def test_confirm_applies_cleanup_then_organize(self):
        order = []
        service = self.service
        service._apply_cleanup = lambda *a, **k: order.append("cleanup") or 0
        service._apply_organize = lambda *a, **k: order.append("organize") or {}
        job = self.finish_review()
        # Cleanup removes a.unitypackage, so the organize move of that file drops out.
        service._plan_organize = lambda root, data, scanned=None, progress=None: {
            "moves": [], "groups": [], "skips": {}, "warnings": [],
            "totals": {"assets_total": 1, "moves": 0, "move_bytes": 0, "skips": {}}}
        original = service._compute_review
        service._compute_review = lambda: job["result"]["review"] if order == [] else original()
        status, _, _ = self.post("/api/sync/apply",
                                 {"review_hash": job["result"]["review"]["review_hash"]})
        self.assertEqual(status, 202)
        done = self.wait_status("completed", "failed", "review")
        self.assertEqual(done["status"], "completed", done["error"])
        self.assertEqual(order, ["cleanup"])

    def test_library_changed_before_apply_touches_nothing(self):
        job = self.finish_review()
        review_hash = job["result"]["review"]["review_hash"]
        self.service._plan_cleanup = lambda scanned: {
            "families": [], "removals": [{"rel_path": "b.unitypackage", "size": 5}],
            "candidate_bytes": 5, "candidate_files": 1, "candidate_families": 1}
        self.post("/api/sync/apply", {"review_hash": review_hash})
        done = self.wait_status("review", "completed", "failed")
        self.assertEqual(done["error_code"], "plan_changed")
        self.assertEqual(self.h.apply_cleanup_calls, [])
        self.assertEqual(self.h.apply_organize_calls, [])

    def test_organize_failure_after_cleanup_reports_partial(self):
        service = self.service
        job = self.finish_review()
        review = job["result"]["review"]
        service._compute_review = lambda: review
        service.op_organize_plan = lambda progress=None: {"preview": {"moves": []}, "plan_hash": "x"}
        review["organize"]["preview"]["moves"] = [
            {"src": "b.unitypackage", "dst": "3D/b.unitypackage"}]
        self.post("/api/sync/apply", {"review_hash": review["review_hash"]})
        done = self.wait_status("review", "completed", "failed")
        self.assertEqual(done["error_code"], "partial_applied")
        self.assertEqual(len(self.h.apply_cleanup_calls), 1)
        self.assertEqual(self.h.apply_organize_calls, [])

    def test_cancel_dismisses_review(self):
        self.finish_review()
        status, _, _ = self.post("/api/sync/cancel")
        self.assertEqual(status, 200)
        self.assertEqual(self.sync()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
