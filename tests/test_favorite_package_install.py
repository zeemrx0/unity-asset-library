import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
spec = importlib.util.spec_from_file_location("favorite_install", ROOT / "bin" / "install_favorite_packages.py")
assert spec is not None and spec.loader is not None
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class FavoriteInstallTests(unittest.TestCase):
    def test_conflict_preserves_manifest_and_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            (root / "ProjectSettings").mkdir()
            (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
            (root / "Packages").mkdir()
            (root / "Temp").mkdir()
            (root / "Temp/UnityLockfile").touch()
            original = {"dependencies": {"com.unity.mathematics": "1.2.0", "com.example.other": "2"}, "scopedRegistries": [{"name": "private"}]}
            path = root / "Packages/manifest.json"
            path.write_text(json.dumps(original))
            request = {"id": "j", "project": str(root), "packages": [{"id": "x", "label": "Math", "kind": "registry", "source": "com.unity.mathematics", "version": "1.3.2"}]}
            with mock.patch.object(installer, "_editor", return_value="/Unity"), mock.patch.object(installer, "_run_helper") as helper:
                result = installer.run(request)
            self.assertEqual(result["status"], "awaiting_confirmation")
            self.assertEqual(result["results"][0]["status"], "conflict")
            self.assertEqual(result["results"][0]["existing_spec"], "1.2.0")
            self.assertEqual(result["results"][0]["requested_spec"], "1.3.2")
            helper.assert_not_called()
            self.assertEqual(json.loads(path.read_text()), original)

    def test_approved_overwrite_dispatches_existing_dependency_to_upm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ("Assets", "Packages", "ProjectSettings", "Temp"):
                (root / folder).mkdir()
            (root / "Temp/UnityLockfile").touch()
            (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
            (root / "Packages/manifest.json").write_text('{"dependencies":{"com.unity.mathematics":"1.2.0"}}')
            request = {"id": "j", "project": str(root), "overwrite": True, "packages": [
                {"id": "x", "label": "Math", "kind": "registry", "source": "com.unity.mathematics", "version": "1.3.2"}]}
            with mock.patch.object(installer, "_editor", return_value="/Unity"), \
                    mock.patch.object(installer, "_run_live_helper", return_value={"rows": [{"status": "installed"}]}) as helper:
                result = installer.run(request)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(helper.call_args.kwargs["overwrite"])
            self.assertEqual(helper.call_args.args[2][0]["existing_spec"], "1.2.0")

    def test_overwrite_flag_must_be_boolean(self):
        for value in ("yes", 1, None):
            with self.subTest(value=value), self.assertRaises(installer.PreflightError):
                installer._validate({"id": "j", "project": "/unused", "packages": [], "overwrite": value})

    def test_matching_dependency_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            (root / "ProjectSettings").mkdir()
            (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
            (root / "Packages").mkdir()
            (root / "Temp").mkdir()
            (root / "Temp/UnityLockfile").touch()
            (root / "Packages/manifest.json").write_text(json.dumps({"dependencies": {"com.unity.mathematics": "1.3.2"}}))
            request = {"id": "j", "project": str(root), "packages": [{"id": "x", "label": "Math", "kind": "registry", "source": "com.unity.mathematics", "version": "1.3.2"}]}
            with mock.patch.object(installer, "_editor", return_value="/Unity"), mock.patch.object(installer, "_run_helper") as helper:
                result = installer.run(request)
            self.assertEqual(result["results"][0]["status"], "skipped")
            helper.assert_not_called()

    def test_open_editor_installs_registry_and_git_through_upm(self):
        for kind, source, version in (("registry", "com.unity.mathematics", "1.3.2"),
                                      ("git", "https://example.invalid/package.git", "main")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for folder in ("Assets", "Packages", "ProjectSettings", "Temp"):
                    (root / folder).mkdir()
                (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
                (root / "Temp/UnityLockfile").touch()
                manifest = root / "Packages/manifest.json"
                original = '{"dependencies":{},"scopedRegistries":[{"name":"private"}]}'
                manifest.write_text(original)
                request = {"id": "j", "project": str(root), "packages": [
                    {"id": "x", "label": "Package", "kind": kind, "source": source, "version": version}]}
                with mock.patch.object(installer, "_editor", return_value="/Unity") as editor, \
                        mock.patch.object(installer, "_git_package_name", return_value="com.example.package"), \
                        mock.patch.object(installer, "_run_live_helper", return_value={"rows": [
                            {"status": "installed"}]}) as helper:
                    result = installer.run(request)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["results"][0]["status"], "installed")
                self.assertEqual(result["completed"], 1)
                editor.assert_called_once()
                helper.assert_called_once()
                self.assertEqual(manifest.read_text(), original)

    def test_live_install_uses_scratch_upm_despite_target_compile_errors(self):
        for kind, conflict, overwrite in (("registry", False, False), ("git", False, False), ("registry", True, False), ("registry", True, True)):
            with self.subTest(kind=kind, conflict=conflict, overwrite=overwrite), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for folder in ("Assets", "Packages", "ProjectSettings", "Temp"):
                    (root / folder).mkdir()
                (root / "Temp/UnityLockfile").touch()
                (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
                (root / "Assets/Broken.cs").write_text("deliberately invalid C#")
                manifest_path = root / "Packages/manifest.json"
                registries = [{"name": "private", "url": "https://example.invalid", "scopes": ["com.example"]}]
                manifest_path.write_text(json.dumps({"dependencies": {}, "scopedRegistries": registries}))
                value = "1.0.0" if kind == "registry" else "https://example.invalid/package.git#main"
                package = {"id": "x", "package_name": "com.example.package", "kind": kind, "version": "", "existing_spec": "2.0.0" if overwrite else None}
                snapshots = []

                def resolve(scratch, editor, packages, specs, progress, value=value, root=root, registries=registries, kind=kind, conflict=conflict):
                    self.assertNotEqual(Path(scratch), root)
                    scratch_manifest = Path(scratch) / "Packages/manifest.json"
                    self.assertEqual(json.loads(scratch_manifest.read_text())["scopedRegistries"], registries)
                    scratch_manifest.write_text(json.dumps({"dependencies": {"com.example.package": value}}))
                    # Simulate an unrelated Unity/user edit while the scratch Editor works.
                    current = json.loads((root / "Packages/manifest.json").read_text())
                    current["dependencies"]["com.example.other"] = "2.0.0"
                    if conflict:
                        current["dependencies"]["com.example.package"] = "2.0.0"
                    (root / "Packages/manifest.json").write_text(json.dumps(current))
                    (root / "Packages/packages-lock.json").write_text(json.dumps({"dependencies": {
                        "com.example.package": {"version": value, "source": kind, "depth": 0}}}))
                    items = [{"name": "com.example.package", "kind": kind, "status": "installed"}]
                    progress({"items": items})
                    return {"rows": items}

                with mock.patch.object(installer, "_run_helper", side_effect=resolve) as helper, \
                        mock.patch.object(installer, "_activate_editor", side_effect=RuntimeError("Activation unavailable")) as activate:
                    result = installer._run_live_helper(str(root), "/Unity", [package], [value], snapshots.append, overwrite=overwrite)
                self.assertEqual(result["rows"][0]["status"], "failed" if conflict and not overwrite else "installed")
                helper.assert_called_once()
                self.assertEqual(activate.call_count, 0 if conflict and not overwrite else 1)
                if not conflict or overwrite:
                    self.assertIn("awaiting_refresh", [state.get("phase") for state in snapshots])
                self.assertEqual(snapshots[0]["items"][0]["status"], "installing")
                manifest = json.loads(manifest_path.read_text())
                self.assertEqual(manifest["dependencies"], {"com.example.other": "2.0.0", "com.example.package": "2.0.0" if conflict and not overwrite else value})
                self.assertEqual(manifest["scopedRegistries"], registries)
                self.assertEqual(list((root / "Assets").iterdir()), [root / "Assets/Broken.cs"])
                self.assertTrue((root / "Temp/UnityLockfile").exists())

    def test_live_install_does_not_claim_success_before_target_upm_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ("Assets", "Packages", "ProjectSettings"):
                (root / folder).mkdir()
            (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
            manifest = root / "Packages/manifest.json"
            manifest.write_text('{"dependencies":{}}')

            def resolve(scratch, *args):
                (Path(scratch) / "Packages/manifest.json").write_text('{"dependencies":{"com.example.package":"1.0.0"}}')
                return {"rows": [{"name": "com.example.package", "kind": "registry", "status": "installed"}]}

            with mock.patch.object(installer, "_run_helper", side_effect=resolve), \
                    mock.patch.object(installer.time, "monotonic", side_effect=[0, 121]), \
                    mock.patch.object(installer, "_activate_editor"):
                result = installer._run_live_helper(str(root), "/Unity", [], [])
            self.assertEqual(result["rows"][0]["status"], "failed")
            self.assertIn("has not resolved", result["rows"][0]["error"])
            self.assertEqual(json.loads(manifest.read_text())["dependencies"]["com.example.package"], "1.0.0")
            self.assertEqual(list((root / "Assets").iterdir()), [])

    def test_live_install_rejects_manifest_change_before_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ("Assets", "Packages", "ProjectSettings"):
                (root / folder).mkdir()
            (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
            manifest = root / "Packages/manifest.json"
            manifest.write_text('{"dependencies":{}}')
            changed = '{"dependencies":{"com.example.other":"2.0.0"}}'

            def resolve(scratch, *args):
                (Path(scratch) / "Packages/manifest.json").write_text('{"dependencies":{"com.example.package":"1.0.0"}}')
                return {"rows": [{"name": "com.example.package", "kind": "registry", "status": "installed"}]}

            with mock.patch.object(installer, "_run_helper", side_effect=resolve), \
                    mock.patch.object(installer.os, "fsync", side_effect=lambda _: manifest.write_text(changed)), \
                    mock.patch.object(installer.os, "replace") as replace, \
                    self.assertRaisesRegex(RuntimeError, "manifest changed"):
                installer._run_live_helper(str(root), "/Unity", [], [])
            replace.assert_not_called()
            self.assertEqual(manifest.read_text(), changed)
            self.assertEqual(list((root / "Packages").iterdir()), [manifest])

    def test_editor_activation_targets_exact_project_not_other_windows(self):
        project = "/fixture/target project"
        processes = (
            "111 /Applications/Unity.app/Contents/MacOS/Unity -projectpath /fixture/other -useHub\n"
            "222 /Applications/Unity.app/Contents/MacOS/Unity -projectPath /fixture/target project -useHub\n"
            "333 /Applications/Unity.app/Contents/MacOS/Unity -adb2 -projectPath /fixture/target project\n")
        with mock.patch.object(installer.sys, "platform", "darwin"), \
                mock.patch.object(installer.subprocess, "run", side_effect=[
                    mock.Mock(stdout=processes), mock.Mock(stdout="true\n")]) as command:
            installer._activate_editor(project)
        self.assertEqual(command.call_count, 2)
        self.assertIn("runningApplicationWithProcessIdentifier(222)", command.call_args.args[0][-1])
        self.assertEqual(command.call_args.args[0][:3], ["/usr/bin/osascript", "-l", "JavaScript"])

    def test_batch_helper_never_injects_scripts_into_a_project_that_opened_during_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            (root / "Temp").mkdir()
            (root / "Temp/UnityLockfile").touch()
            with mock.patch.object(installer.subprocess, "Popen") as launch, \
                    self.assertRaisesRegex(RuntimeError, "opened during setup"):
                installer._run_helper(str(root), "/Unity", [], [])
            launch.assert_not_called()
            self.assertEqual(list((root / "Assets").iterdir()), [])

    def test_closed_helper_still_launches_batch_unity_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            package = {"id": "x", "package_name": "com.example.package", "kind": "registry", "version": "1.0.0"}
            process = mock.Mock(returncode=0)
            process.poll.return_value = 0

            def launch(args, **kwargs):
                self.assertEqual(args[:3], ["/Unity", "-batchmode", "-nographics"])
                folder, = (root / "Assets").glob("UALFavoriteInstall_*")
                source = (folder / "Installer.cs").read_text()
                self.assertIn("static readonly bool batch = true;", source)
                match = re.search(r"static readonly string statePath = (.*);", source)
                assert match is not None
                state_path = Path(json.loads(match[1]))
                state = json.loads(state_path.read_text())
                state["items"][0]["status"] = "installed"
                state.update(phase="done", index=1)
                state_path.write_text(json.dumps(state))
                return process

            with mock.patch.object(installer.subprocess, "Popen", side_effect=launch) as launch_mock:
                result = installer._run_helper(str(root), "/Unity", [package], ["com.example.package@1.0.0"])
            launch_mock.assert_called_once()
            self.assertEqual(result["rows"][0]["status"], "installed")
            self.assertEqual(list((root / "Assets").iterdir()), [])

    def test_symlink_safety_checks_still_apply_with_editor_open(self):
        for target in ("Assets", "Packages", "Packages/manifest.json"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "Temp").mkdir()
                (root / "Temp/UnityLockfile").touch()
                (root / "external").mkdir()
                if target.startswith("Packages/"):
                    (root / "Packages").mkdir()
                (root / target).symlink_to(root / "external")
                with self.assertRaisesRegex(installer.PreflightError, "symlinked"):
                    installer._check_project(str(root))

    def test_git_subfolder_metadata_uses_the_selected_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "Packages" / "Utilities"
            package.mkdir(parents=True)
            manifest = package / "package.json"
            manifest.write_text('{"name":"com.example.original","version":"1.0.0"}')
            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
            git("init", "-b", "main")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "original")
            git("tag", "v1.0.0")
            manifest.write_text('{"name":"com.example.renamed","version":"2.0.0"}')
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "rename")
            row = {"id": "git", "label": "Utilities", "kind": "git",
                   "source": root.as_uri() + "?path=/Packages/Utilities#v1.0.0", "version": ""}
            self.assertEqual(installer._git_package_name(row), "com.example.original")

    def test_git_subfolder_traversal_is_rejected_before_reading_metadata(self):
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            installer._git_locator("https://example.invalid/package.git?path=/%2e%2e/other", "")

if __name__ == "__main__":
    unittest.main()
