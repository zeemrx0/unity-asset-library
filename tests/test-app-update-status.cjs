const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const file = path.resolve(__dirname, '../electron/updates.cjs');
const release = { tag_name: 'v0.3.0', assets: [{ name: 'unity-asset-library-0.3.0-arm64.zip', state: 'uploaded', size: 10, digest: 'sha256:' + 'a'.repeat(64), browser_download_url: 'https://github.com/SCVN-Zee/unity-asset-library/releases/download/v0.3.0/unity-asset-library-0.3.0-arm64.zip' }] };
async function scenario({ choice = 0, failure = false, networkFailure = false, manual = true, current = '0.2.11' } = {}) {
  let updater;
  let restarts = 0;
  let stages = 0;
  const messages = [];
  const snapshots = [];
  const context = vm.createContext({ require: createRequire(file), module: { exports: {} }, __dirname: path.dirname(file), process: { platform: 'darwin', arch: 'arm64', versions: {} }, AbortController, AbortSignal, console: { error() {} }, fetch: async () => {
    snapshots.push(updater.getStatus());
    if (networkFailure) throw new Error('Offline');
    return { ok: true, json: async () => release };
  } });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  context.stage = async (_release, _target, onProgress) => {
    stages++;
    snapshots.push(updater.getStatus());
    for (const [percent, phase] of [[45, 'downloading'], [100, 'downloading'], [null, 'extracting'], [null, 'verifying']]) {
      onProgress(percent, phase);
      snapshots.push(updater.getStatus());
    }
    if (failure) throw new Error('Signature invalid');
    return '/fixture/staged';
  };
  vm.runInContext('stageUpdate = stage', context);
  updater = context.module.exports.createUpdates({
    app: { isPackaged: true, name: 'Fixture', getAppPath: () => '/Applications/Fixture.app/Contents/Resources/app.asar', getVersion: () => current },
    Menu: { buildFromTemplate: x => x, setApplicationMenu() {} },
    dialog: { showMessageBox: async message => { messages.push(message); return { response: message.title === 'Update available' ? choice : 1 }; } },
    requestRestart: () => restarts++,
  });
  assert.equal(updater.getStatus().phase, 'idle');
  const copy = updater.getStatus(); copy.phase = 'corrupted';
  assert.equal(updater.getStatus().phase, 'idle', 'Status is a detached snapshot');
  await updater.check(manual);
  return { updater, snapshots, messages, stages, restarts };
}
(async () => {
  const success = await scenario();
  assert.deepEqual(success.snapshots.map(s => s.phase), ['checking', 'downloading', 'downloading', 'downloading', 'extracting', 'verifying']);
  assert.equal(success.snapshots[2].percent, 45);
  assert.equal(success.snapshots[4].percent, null);
  assert.equal(success.updater.getStatus().phase, 'ready');
  assert.equal(success.updater.getStatus().version, '0.3.0');
  assert.equal(success.restarts, 0, 'Later does not install or restart');
  await success.updater.check(true);
  assert.equal(success.stages, 1, 'Prepared update does not redownload');
  assert.equal(success.messages.at(-1).title, 'Update ready');
  const failure = await scenario({ failure: true, manual: false });
  assert.equal(failure.updater.getStatus().phase, 'failed');
  assert.equal(failure.updater.getStatus().error, 'Signature invalid');
  assert.equal(failure.messages.at(-1).title, 'Update failed', 'Accepted automatic download surfaces verification failures');
  assert.equal((await scenario({ choice: 1 })).updater.getStatus().phase, 'idle');
  assert.equal((await scenario({ current: '0.3.0' })).updater.getStatus().phase, 'idle');
  assert.equal((await scenario({ networkFailure: true, manual: false })).updater.getStatus().phase, 'idle');
  assert.equal((await scenario({ networkFailure: true })).updater.getStatus().phase, 'failed');
  console.log('PASS: updater status lifecycle, snapshot isolation, cancellation, failures, and confirmed restart flow');
})().catch(error => { console.error(error); process.exitCode = 1; });
