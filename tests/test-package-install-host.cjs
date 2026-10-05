const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ual-package-host-'));
const entry = path.resolve(__dirname, '../electron/main.cjs');
const localRequire = createRequire(entry);
(async () => {
  try {
    const project = path.join(root, 'Unity project');
    fs.mkdirSync(project);
    const handlers = {};
    const requests = [];
    const state = { service: 'unity-asset-library', api_version: 7, ready: true, csrf: 'test', vault_root: root, repo: root, instance_id: 'fixture', job: null, action: null };
    const app = { isPackaged: false, getPath: () => root, setPath() {}, whenReady: () => ({ then() {} }), on() {}, quit() {} };
    const context = vm.createContext({
      require: name => name === 'electron' ? { app, ipcMain: { handle: (name, fn) => { handlers[name] = fn; } } } : localRequire(name),
      __dirname: path.dirname(entry), process: { ...process, resourcesPath: root }, console, Buffer, URL, AbortController, setTimeout, clearTimeout,
      fetch: async url => ({ ok: true, json: async () => url.endsWith('/api/state') ? state : { packages: [{ id: 'math', label: 'Math', kind: 'registry', source: 'com.unity.mathematics', version: '1.3.2' }] } }),
      packageBackend: async endpoint => endpoint === '/api/state' ? state : { packages: [{ id: 'math', label: 'Math', kind: 'registry', source: 'com.unity.mathematics', version: '1.3.2' }] },
      packageCli: async args => {
        const request = JSON.parse(fs.readFileSync(args[args.indexOf('--request') + 1], 'utf8'));
        requests.push(request);
        return { status: request.overwrite ? 'completed' : 'awaiting_confirmation', completed: 1, results: [{ id: 'math', status: request.overwrite ? 'installed' : 'conflict' }] };
      },
    });
    vm.runInContext(fs.readFileSync(entry, 'utf8'), context);
    vm.runInContext(`registerIpc(); backendRequest = packageBackend; runPythonCli = packageCli; backendSession = { root: ${JSON.stringify(root)}, vaultRoot: ${JSON.stringify(root)}, csrf: 'test', port: 9876 }; Object.assign(storageState, { ready: true, busy: false });`, context);
    const request = { project, ids: ['math'] };
    for (const invalid of [{ ...request, overwrite: 'yes' }, { ...request, overwrite: 1 }, { ...request, unexpected: true }]) {
      assert.throws(() => handlers['packages:start']({}, invalid), /Choose saved packages/);
    }
    handlers['packages:start']({}, request);
    await vm.runInContext('packageInstallPromise', context);
    assert.equal(requests[0].overwrite, false);
    assert.equal(handlers['packages:status']({}).status, 'awaiting_confirmation');
    handlers['packages:start']({}, { ...request, overwrite: true });
    await vm.runInContext('packageInstallPromise', context);
    assert.equal(requests[1].overwrite, true);
    assert.equal(handlers['packages:status']({}).status, 'completed');
    assert.throws(() => handlers['shell:open-external']({}, 'not a URL'), /valid web link/);
    console.log('PASS: package overwrite defaults off, strict boolean validation, awaiting confirmation preserved, explicit consent reaches worker, malformed links handled.');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
