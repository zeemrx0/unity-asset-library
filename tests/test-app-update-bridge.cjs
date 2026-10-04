const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const entry = path.resolve(__dirname, '../electron/main.cjs');
const localRequire = createRequire(entry);
const handlers = {};
const app = { isPackaged: false, getPath: () => '/fixture', setPath() {}, whenReady: () => ({ then() {} }), on() {} };
const context = vm.createContext({ require: name => name === 'electron' ? { app, ipcMain: { handle: (name, handler) => { handlers[name] = handler; } } } : localRequire(name), process, __dirname: path.dirname(entry), console, AbortSignal, setTimeout, clearTimeout });
vm.runInContext(fs.readFileSync(entry, 'utf8'), context);
vm.runInContext('registerIpc()', context);
assert.equal(handlers['updates:status'](), null, 'Before initialization the updater status is null');
let manual = false;
const snapshot = { phase: 'downloading', percent: 42, version: '0.3.0', error: null };
context.fixtureUpdater = { getStatus: () => snapshot, check: value => { manual = value; } };
vm.runInContext('updates = fixtureUpdater', context);
let bridge;
const calls = [];
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../electron/preload.cjs'), 'utf8'), {
  require: () => ({ contextBridge: { exposeInMainWorld: (_name, value) => { bridge = value; } }, ipcRenderer: { invoke: async channel => { calls.push(channel); return handlers[channel](); } } }),
  window: { addEventListener() {} }, process,
});
(async () => {
  assert.deepEqual(await bridge.getUpdateStatus(), snapshot);
  await bridge.checkForUpdates();
  assert.equal(manual, true, 'Renderer checks retain manual confirmation flow');
  assert.deepEqual(calls, ['updates:status', 'updates:check']);
  console.log('PASS: updater IPC registration and preload bridge invoke the real main handlers');
})().catch(error => { console.error(error); process.exitCode = 1; });
