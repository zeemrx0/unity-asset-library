#!/usr/bin/env python3
"""Install saved registry/Git favorites in an open or closed project through real UPM.

run --request PATH accepts {id, project, packages: [{id,label,kind,source,version}]}.
UL_PROGRESS lines report per-package outcomes. Exit 0 completed, 1 row failures,
2 preflight failure. Requires the project's installed Editor and Git for Git URLs.
UPM resolves package versions and owns lock files. Live installs merge only new
dependencies into a freshly read manifest; target scripts need not compile.
"""
import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlsplit, urlunsplit

import import_packages as imports
from favorite_packages import normalize_package
from progress import json_progress

TIMEOUT = 1200


class PreflightError(Exception):
    pass


def _validate(request):
    if (not isinstance(request, dict) or not {"id", "project", "packages"}.issubset(request)
            or set(request) - {"id", "project", "packages", "overwrite"}
            or ("overwrite" in request and not isinstance(request["overwrite"], bool))
            or not isinstance(request["id"], str) or not request["id"]):
        raise PreflightError("Request requires id, project and packages")
    project = imports.project_info(request["project"])["path"]
    rows = request["packages"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise PreflightError("Choose between 1 and 1000 saved packages")
    rows = [normalize_package(row) for row in rows]
    if len({row["id"] for row in rows}) != len(rows):
        raise PreflightError("Duplicate saved-package ids")
    return project, rows


def _check_project(project):
    for folder in ("Assets", "Packages"):
        if os.path.islink(os.path.join(project, folder)):
            raise PreflightError(f"Cannot safely install through a symlinked {folder} folder")
    if os.path.islink(os.path.join(project, "Packages", "manifest.json")):
        raise PreflightError("Cannot safely update a symlinked package manifest")


def _editor(version):
    try:
        paths = (f"/Applications/Unity/Hub/Editor/{version}/Unity.app/Contents/MacOS/Unity",
                 os.path.expanduser(f"~/.unity/Editors/{version}/Editor/Unity"))
        for path in paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        # Reuse the same installed CLI needed for Hub project discovery, including
        # Editors installed outside the default Hub directory. Never auto-install.
        entries = imports.run_unity_json(["editors", "--json"])
        if not isinstance(entries, list):
            raise PreflightError("Unity Editor discovery returned an invalid list")
        for entry in entries:
            if entry.get("version") == version and entry.get("location"):
                location = entry["location"]
                candidate = os.path.join(location, "Contents", "MacOS", "Unity") if location.endswith(".app") else os.path.join(location, "Unity.app", "Contents", "MacOS", "Unity")
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
        raise PreflightError(f"Matching Unity Editor {version} is not installed")
    except OSError as error:
        raise PreflightError("Cannot inspect installed Unity Editors: " + str(error)) from error


def _git_locator(source, version):
    if re.match(r"[^/@\s]+@[^/:\s]+:", source):
        repo, _, ref = source.partition("#")
        repo, _, query = repo.partition("?")
    else:
        url = urlsplit(source)
        ref, query = url.fragment, url.query
        scheme = url.scheme.removeprefix("git+")
        repo = urlunsplit((scheme, url.netloc, url.path, "", ""))
    path = parse_qs(query).get("path", [""])[0].lstrip("/")
    if any(part in ("..", ".") for part in path.split("/")) or ":" in path or "\\" in path:
        raise ValueError("Git package subfolder must stay inside the repository")
    return repo, ref or version, path


def _git_package_name(package):
    try:
        repo, ref, path = _git_locator(package["source"], package["version"])
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        with tempfile.TemporaryDirectory(prefix="ual-package-metadata-") as directory:
            clone = subprocess.run(["git", "clone", "--quiet", "--no-checkout", "--", repo, directory],
                                   capture_output=True, text=True, timeout=180, env=env, check=False)
            if clone.returncode:
                raise ValueError("Cannot read Git package metadata: " + clone.stderr.strip()[-1000:])
            revision = "HEAD"
            if ref:
                for candidate in (ref, "refs/remotes/origin/" + ref):
                    result = subprocess.run(["git", "-C", directory, "rev-parse", "--verify", "--end-of-options", candidate + "^{commit}"],
                                            capture_output=True, text=True, timeout=30, env=env, check=False)
                    if result.returncode == 0:
                        revision = result.stdout.strip()
                        break
                else:
                    raise ValueError("Git ref was not found in the repository")
            filename = str(PurePosixPath(path) / "package.json") if path else "package.json"
            result = subprocess.run(["git", "-C", directory, "show", revision + ":" + filename],
                                    capture_output=True, text=True, timeout=30, env=env, check=False)
            if result.returncode:
                raise ValueError("Git source has no readable package.json at the selected path/ref")
            name = json.loads(result.stdout).get("name")
            normalize_package({"id": "metadata", "label": "metadata", "kind": "registry", "source": name, "version": ""})
            return name
    except (OSError, ValueError) as error:
        raise ValueError("Cannot read Git package metadata: " + str(error)) from error


HELPER = r'''using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEditor.PackageManager.Requests;
using UnityEngine;

namespace UALFavoriteTOKEN {
[Serializable] public class Item { public string id; public string name; public string spec; public string kind; public string version; public string status; public string error; }
[Serializable] public class State { public Item[] items; public int index; public string phase; }
[InitializeOnLoad] public static class Installer {
    static readonly string statePath = STATE_PATH;
    static readonly bool batch = BATCH_MODE;
    static State state;
    static AddRequest add;
    static ListRequest list;
    static double deadline;
    static Installer() {
        if (string.IsNullOrEmpty(statePath) || !File.Exists(statePath)) return;
        try {
            state = JsonUtility.FromJson<State>(File.ReadAllText(statePath));
            if (state == null || state.items == null) return;
            deadline = EditorApplication.timeSinceStartup + 900;
            EditorApplication.update += Tick;
            // A package can reload assemblies before its AddRequest is observed.
            // Restore from disk and verify the resolved list rather than replay Add.
            if (state.phase == "adding" || state.phase == "verifying") Verify();
        } catch (Exception error) { Debug.LogException(error); Stop(2); }
    }
    static void Stop(int code) {
        EditorApplication.update -= Tick;
        if (batch) EditorApplication.Exit(code);
    }
    static void Save() {
        string temporary = statePath + ".tmp";
        File.WriteAllText(temporary, JsonUtility.ToJson(state));
        File.Copy(temporary, statePath, true);
        File.Delete(temporary);
    }
    static void Verify() { state.phase = "verifying"; Save(); list = Client.List(false, true); }
    static void FinishItem(string status, string error = null) {
        var item = state.items[state.index];
        item.status = status; item.error = error;
        state.index++; state.phase = "ready"; add = null; list = null; Save();
    }
    static void Tick() {
        if (!File.Exists(statePath)) { Stop(2); return; }
        try {
            if (EditorApplication.timeSinceStartup > deadline) {
                if (state.index < state.items.Length) FinishItem("failed", "Unity Package Manager timed out");
                state.phase = "done"; Save(); Stop(1); return;
            }
            if (EditorApplication.isCompiling || EditorApplication.isUpdating) return;
            if (state.index >= state.items.Length) {
                state.phase = "done"; Save(); EditorApplication.update -= Tick;
                Stop(state.items.Any(item => item.status == "failed") ? 1 : 0); return;
            }
            if (list != null) {
                if (!list.IsCompleted) return;
                var item = state.items[state.index];
                if (list.Status != StatusCode.Success) { FinishItem("failed", list.Error.message); return; }
                var resolved = list.Result.FirstOrDefault(info => info.name == item.name);
                bool matches = resolved != null && resolved.isDirectDependency &&
                    (item.kind == "git" ? resolved.source == PackageSource.Git :
                     resolved.source == PackageSource.Registry && (string.IsNullOrEmpty(item.version) || resolved.version == item.version));
                if (matches) FinishItem("installed");
                else FinishItem("failed", "UPM did not resolve the requested direct dependency");
                return;
            }
            if (add != null) {
                if (!add.IsCompleted) return;
                if (add.Status != StatusCode.Success) { FinishItem("failed", add.Error.message); return; }
                Verify(); return;
            }
            state.items[state.index].status = "installing"; state.phase = "adding"; Save();
            add = Client.Add(state.items[state.index].spec);
        } catch (Exception error) {
            Debug.LogException(error);
            if (state.index < state.items.Length) FinishItem("failed", error.Message);
            else Stop(2);
        }
    }
}
}
'''


def _run_helper(project, editor, packages, specs, progress=None):
    batch = not os.path.exists(os.path.join(project, "Temp", "UnityLockfile"))
    if not batch:
        raise RuntimeError("The target Unity Editor opened during setup. Retry installation while keeping Unity open.")
    if editor is None:
        editor = _editor(imports.project_info(project)["version"])
    token = uuid.uuid4().hex
    folder = os.path.join(project, "Assets", "UALFavoriteInstall_" + token)
    with tempfile.TemporaryDirectory(prefix="ual-upm-") as temporary:
        state_path = os.path.join(temporary, "state.json")
        log_path = os.path.join(temporary, "Editor.log")
        state = {"items": [{"id": package["id"], "name": package["package_name"], "kind": package["kind"],
                            "version": package["version"], "spec": spec, "status": "pending", "error": None}
                           for package, spec in zip(packages, specs, strict=True)], "index": 0, "phase": "ready"}
        try:
            with open(state_path, "w", encoding="utf-8") as stream:
                json.dump(state, stream)
            os.mkdir(folder)
        except OSError as error:
            raise RuntimeError("Cannot create the temporary UPM installer: " + str(error)) from error
        try:
            with open(os.path.join(folder, "Installer.cs"), "w", encoding="utf-8") as stream:
                stream.write(HELPER.replace("TOKEN", token).replace("STATE_PATH", json.dumps(state_path))
                             .replace("BATCH_MODE", "true" if batch else "false"))
            with open(os.path.join(folder, "Installer.asmdef"), "w", encoding="utf-8") as stream:
                json.dump({"name": "UALFavorite" + token, "includePlatforms": ["Editor"], "autoReferenced": False}, stream)
            process = None
            if batch:
                process = subprocess.Popen([editor, "-batchmode", "-nographics", "-projectPath", project, "-logFile", log_path],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + TIMEOUT
            previous = None
            try:
                while process is None or process.poll() is None:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Unity package installation timed out; inspect the project before retrying")
                    try:
                        with open(state_path, encoding="utf-8") as stream:
                            current = json.load(stream)
                        if current != previous and progress:
                            progress(current)
                        previous = current
                        if process is None and current.get("phase") == "done":
                            break
                    except (OSError, ValueError):
                        pass  # The helper replaces its state between Editor updates.
                    time.sleep(0.2)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            with open(state_path, encoding="utf-8") as stream:
                result = json.load(stream)
            if result.get("phase") != "done":
                try:
                    with open(log_path, encoding="utf-8", errors="replace") as stream:
                        log = stream.read()[-5000:]
                except OSError:
                    log = "No Editor log available"
                raise RuntimeError(f"Unity did not finish package installation (exit {process.returncode if process else 'live Editor'}): {log}")
            if progress:
                progress(result)
            return {"rows": result["items"]}
        finally:
            shutil.rmtree(folder)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(folder + ".meta")


def _activate_editor(project):
    # Unity notices external manifest changes on focus. Target the exact PID, not
    # an application name: multiple Unity versions/projects can be open at once.
    if sys.platform != "darwin":
        return
    result = subprocess.run(["/bin/ps", "-axo", "pid=,args="], capture_output=True,
                            text=True, timeout=10, check=True)
    for line in result.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        if not pid.isdigit() or "/Unity.app/Contents/MacOS/Unity " not in command or "-adb2" in command:
            continue
        match = re.search(r"\s-projectpath\s+(.+?)(?=\s+-[A-Za-z]|$)", command, re.IGNORECASE)
        if match is None or os.path.realpath(match[1].strip('"')) != project:
            continue
        script = ('ObjC.import("AppKit"); '
                  f'$.NSRunningApplication.runningApplicationWithProcessIdentifier({pid}).activateWithOptions(3);')
        focused = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
                                 capture_output=True, text=True, timeout=10, check=True)
        if focused.stdout.strip() != "true":
            raise RuntimeError("Packages were added; focus the target Unity Editor to resolve them")
        return
    raise RuntimeError("Target Unity Editor closed; reopen the project to resolve the added packages")


def _run_live_helper(project, editor, packages, specs, progress=None, overwrite=False):
    try:
        # An injected script cannot load when the missing packages cause compile errors.
        # Resolve with the matching Editor in a disposable, script-free project instead.
        with tempfile.TemporaryDirectory(prefix="ual-upm-resolve-") as scratch:
            for folder in ("Assets", "Packages", "ProjectSettings"):
                os.mkdir(os.path.join(scratch, folder))
            with open(os.path.join(scratch, "ProjectSettings", "ProjectVersion.txt"), "w", encoding="utf-8") as stream:
                stream.write("m_EditorVersion: " + imports.project_info(project)["version"] + "\n")
            manifest_path = os.path.join(project, "Packages", "manifest.json")
            with open(manifest_path, encoding="utf-8") as stream:
                registries = json.load(stream).get("scopedRegistries", [])
            scratch_manifest = os.path.join(scratch, "Packages", "manifest.json")
            with open(scratch_manifest, "w", encoding="utf-8") as stream:
                json.dump({"dependencies": {}, "scopedRegistries": registries}, stream)

            def resolving(state):
                if progress:
                    progress({"items": [{**item, "status": "installing" if item["status"] == "installed" else item["status"]}
                                        for item in state["items"]]})

            answer = _run_helper(scratch, editor, packages, specs, resolving)
            with open(scratch_manifest, encoding="utf-8") as stream:
                resolved = json.load(stream)["dependencies"]

        _check_project(project)
        with open(manifest_path, "rb") as stream:
            original = stream.read()
        manifest = json.loads(original)
        if not isinstance(manifest.get("dependencies"), dict):
            raise PreflightError("Project package manifest changed to invalid dependencies")
        pending = {}
        for item in answer["rows"]:
            if item["status"] != "installed":
                continue
            name = item["name"]
            value = resolved.get(name)
            if not isinstance(value, str) or not value:
                raise RuntimeError("UPM did not provide a resolved dependency for " + name)
            existing = manifest["dependencies"].get(name)
            expected = next((package.get("existing_spec") for package in packages if package["package_name"] == name), None)
            if existing is not None and existing != value and (not overwrite or existing != expected):
                item.update(status="failed", error="Project dependency changed during resolution; existing dependency was kept")
                continue
            manifest["dependencies"][name] = value
            item["status"] = "installing"
            pending[name] = (item, value)
        if not pending:
            return answer
        descriptor, temporary = tempfile.mkstemp(prefix=".ual-manifest-", dir=os.path.dirname(manifest_path))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            with open(manifest_path, "rb") as stream:
                if stream.read() != original:
                    raise ValueError("Project manifest changed during installation; retry after Unity finishes")
            os.replace(temporary, manifest_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)
        if progress:
            progress({"phase": "awaiting_refresh", "items": answer["rows"]})
        # Manual Assets/Refresh is still available if activation fails.
        with contextlib.suppress(OSError, RuntimeError, subprocess.SubprocessError):
            _activate_editor(project)
        deadline = time.monotonic() + 120
        previous = None
        while pending and time.monotonic() < deadline:
            try:
                with open(os.path.join(project, "Packages", "packages-lock.json"), encoding="utf-8") as stream:
                    locked = json.load(stream)["dependencies"]
                for name, (item, value) in list(pending.items()):
                    entry = locked.get(name, {})
                    if (entry.get("depth") == 0 and entry.get("source") == item["kind"]
                            and entry.get("version") == value):
                        item["status"] = "installed"
                        del pending[name]
            except (OSError, ValueError, KeyError):
                pass  # UPM may replace its lock file while resolving the manifest.
            statuses = [item["status"] for item in answer["rows"]]
            if progress and statuses != previous:
                progress({"phase": "awaiting_refresh" if pending else "done", "items": answer["rows"]})
            previous = statuses
            if pending:
                time.sleep(0.5)
        for item, _ in pending.values():
            item.update(status="failed", error="Dependencies were added, but Unity has not resolved them. Focus the target Editor, refresh assets, and check Package Manager errors.")
        return answer
    except (OSError, ValueError) as error:
        raise RuntimeError("Live package installation could not read or update its manifests: " + str(error)) from error


def run(request):
    project, packages = _validate(request)
    results = [{"id": row["id"], "label": row["label"], "source": row["source"], "status": "pending"} for row in packages]
    job = {"id": request["id"], "kind": "packages", "status": "running", "project": project,
           "stage": "preflight", "completed": 0, "total": len(packages), "current_item": None, "results": results, "error": None}

    def emit(stage):
        job["stage"] = stage
        job["completed"] = sum(row["status"] in ("installed", "skipped", "failed", "conflict") for row in results)
        job["current_item"] = next((row["label"] for row in results if row["status"] == "installing"), None)
        json_progress(job)

    with imports._per_project_lock(project):
        _check_project(project)
        try:
            with open(os.path.join(project, "Packages", "manifest.json"), encoding="utf-8") as stream:
                manifest = json.load(stream)
        except (OSError, ValueError) as error:
            raise PreflightError("Cannot read the target package manifest: " + str(error)) from error
        if not isinstance(manifest, dict) or not isinstance(manifest.get("dependencies"), dict):
            raise PreflightError("Project package manifest has invalid dependencies")
        dependencies = manifest["dependencies"]
        eligible, specs, positions, requested_names = [], [], [], set()
        emit("preflight")
        for index, package in enumerate(packages):
            try:
                name = package["source"] if package["kind"] == "registry" else _git_package_name(package)
                spec = package["source"] + (("@" if package["kind"] == "registry" else "#") + package["version"] if package["version"] else "")
                if name in requested_names:
                    raise ValueError("Another selected favorite targets the same package; install one version at a time")
                requested_names.add(name)
                if name in dependencies:
                    old = dependencies[name]
                    same = old == spec if package["kind"] == "git" else (
                        old == package["version"] if package["version"] else bool(re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][\w.+-]+)?", str(old))))
                    if same:
                        results[index]["status"] = "skipped"
                    elif not request.get("overwrite", False):
                        results[index].update(status="conflict", existing_spec=str(old), requested_spec=package["version"] or spec,
                                              error=f"{name}: installed {old}; requested {package["version"] or spec}")
                if results[index]["status"] == "pending":
                    eligible.append({**package, "package_name": name, "existing_spec": dependencies.get(name)})
                    specs.append(spec)
                    positions.append(index)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                results[index].update(status="failed", error=str(error))
            emit("preflight")
        if eligible:
            editor = _editor(imports.project_info(project)["version"])

            def update(state):
                for position, item in zip(positions, state.get("items", []), strict=True):
                    results[position]["status"] = item["status"]
                    if item.get("error"):
                        results[position]["error"] = item["error"]
                emit("awaiting_refresh" if state.get("phase") == "awaiting_refresh" else "installing")

            try:
                if os.path.exists(os.path.join(project, "Temp", "UnityLockfile")):
                    answer = _run_live_helper(project, editor, eligible, specs, update, overwrite=request.get("overwrite", False))
                else:
                    answer = _run_helper(project, editor, eligible, specs, update)
                update({"items": answer["rows"]})
            except (OSError, ValueError, RuntimeError, PreflightError, subprocess.SubprocessError) as error:
                for position in positions:
                    if results[position]["status"] not in ("installed", "failed"):
                        results[position].update(status="failed", error=str(error))
        job["status"] = ("awaiting_confirmation" if any(row["status"] == "conflict" for row in results) else
                         "failed" if any(row["status"] == "failed" for row in results) else "completed")
        emit("finished")
    return job


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.request, encoding="utf-8") as stream:
            outcome = run(json.load(stream))
    except (OSError, ValueError, PreflightError, imports.RequestError) as error:
        print(json.dumps({"kind": "packages", "status": "failed", "stage": "preflight", "error": str(error)}))
        return 2
    print(json.dumps(outcome))
    return 1 if outcome["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
