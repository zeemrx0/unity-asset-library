const assert = require("node:assert/strict");
const path = require("node:path");

// Run extraction in Electron, not ELECTRON_RUN_AS_NODE: only the main process
// installs the ASAR filesystem wrapper that caused update staging to fail.
if (process.versions.electron) {
  const { app } = require("electron");
  const fs = require("original-fs").promises;
  const { extractBundle } = require("../electron/updates.cjs");
  (async () => {
    const [zip, workspace, source] = process.argv.slice(2);
    const bundle = await extractBundle(zip, workspace);
    const archive = path.join(bundle, "Contents", "Resources", "app.asar");
    assert.deepEqual(await fs.readFile(archive), await fs.readFile(source));
    assert.equal(JSON.parse(await require("node:fs/promises").readFile(path.join(archive, "package.json"), "utf8")).name, "update-probe");
    assert.equal(await fs.readFile(path.join(bundle, "Contents", "Resources", "app.asar.unpacked", "probe"), "utf8"), "unpacked bytes");
    console.log("PASS: Electron extracts app.asar as an ordinary file; archive bytes and unpacked resources preserved");

    // Load the updater from inside an actual ASAR, just like the installed app.
    // Capture spawn so this probe cannot replace or relaunch an application.
    const childProcess = require("node:child_process");
    const originalSpawn = childProcess.spawn;
    let invocation;
    childProcess.spawn = (...args) => {
      invocation = args;
      const child = new (require("node:events").EventEmitter)();
      child.unref = () => {};
      process.nextTick(() => child.emit("spawn"));
      return child;
    };
    try {
      const { launchInstaller } = require(path.join(source, "electron", "updates.cjs"));
      const userData = path.join(workspace, "user-data");
      await launchInstaller({ target: bundle, workspace, userData });
      const helper = path.join(userData, "updates", "install-update.sh");
      assert.deepEqual(await fs.readFile(helper), await require("node:fs/promises").readFile(path.join(source, "electron", "install-update.sh")));
      assert.deepEqual(invocation, ["/bin/sh", [helper, String(process.pid), bundle, workspace, path.join(userData, "updates", "install.log")], { detached: true, stdio: "ignore" }]);
      console.log("PASS: packaged updater stages the exact installer script from ASAR and launches the detached helper");
    } finally {
      childProcess.spawn = originalSpawn;
    }
  })().then(() => app.exit(0), (error) => { console.error(error); app.exit(1); });
} else {
  const fs = require("node:fs/promises");
  const os = require("node:os");
  const { promisify } = require("node:util");
  const run = promisify(require("node:child_process").execFile);
  (async () => {
    if (process.platform !== "darwin") return;
    const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "ual-electron-update-")));
    try {
      const input = path.join(root, "input", "Unity Asset Library.app");
      const resources = path.join(input, "Contents", "Resources");
      const contents = path.join(root, "asar-source");
      await fs.mkdir(contents);
      await fs.writeFile(path.join(contents, "package.json"), JSON.stringify({ name: "update-probe", releaseRepository: require("../package.json").releaseRepository }));
      await fs.mkdir(path.join(contents, "electron"));
      for (const name of ["updates.cjs", "install-update.sh"]) {
        await fs.copyFile(path.join(__dirname, "..", "electron", name), path.join(contents, "electron", name));
      }
      await fs.symlink(path.join(__dirname, "..", "node_modules"), path.join(root, "node_modules"));
      await fs.mkdir(path.join(resources, "app.asar.unpacked"), { recursive: true });
      await fs.writeFile(path.join(resources, "app.asar.unpacked", "probe"), "unpacked bytes");
      const archive = path.join(resources, "app.asar");
      await require("@electron/asar").createPackage(contents, archive);
      const zip = path.join(root, "update.zip");
      await run("/usr/bin/ditto", ["-c", "-k", "--keepParent", input, zip]);
      const env = { ...process.env };
      delete env.ELECTRON_RUN_AS_NODE;
      const { stdout } = await run(require("electron"), [__filename, zip, path.join(root, "staged"), archive], { env, timeout: 30000 });
      console.log(stdout.trim());
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  })().catch((error) => { console.error(error); process.exitCode = 1; });
}
