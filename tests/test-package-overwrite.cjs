// Run after npm run build: node_modules/.bin/electron tests/test-package-overwrite.cjs
const { app, BrowserWindow, ipcMain } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ual-package-overwrite-'));
app.setPath('userData', path.join(temp, 'profile'));
app.on('window-all-closed', () => {});
const calls = [];
const errors = [];
let sequence = 0;
let refreshing = false;
const job = () => refreshing ? { id: "refresh", kind: "packages", status: "running", stage: "awaiting_refresh", project: "/fixture/Unity project", completed: 0, total: 1, error: null, results: [{ id: "math", label: "Mathematics", status: "installing" }] } : ({ id: `conflict-${sequence}`, kind: 'packages', status: 'awaiting_confirmation', stage: 'finished', project: '/fixture/Unity project', completed: 2, total: 2, error: null, results: [
  { id: 'math', label: 'Mathematics', source: 'com.unity.mathematics', status: 'conflict', existing_spec: '1.2.6', requested_spec: '1.3.2' },
  { id: 'existing', label: 'Already installed', source: 'com.example.other', status: 'skipped' },
] });
ipcMain.handle('fixture:status', () => job());
ipcMain.handle('fixture:install', (_event, request) => { calls.push(request); return { ...job(), id: 'replacement', status: 'queued', completed: 0, results: [] }; });
const preload = path.join(temp, 'preload.cjs');
fs.writeFileSync(preload, `const { contextBridge, ipcRenderer } = require('electron'); contextBridge.exposeInMainWorld('ual', {
  getStorage: async () => ({ path: '/fixture', ready: true, canChange: true, busy: false }),
  getState: async () => ({ ready: true, vault_root: '/fixture', action: null, job: null }), getAction: async () => ({ action: null }),
  getAssets: async () => ({ assets: [] }), favorites: async () => ({ favorites: [] }), getTags: async () => ({ tags: [], assignments: {} }),
  getPreferences: async () => ({ theme: 'dark' }), setPreferences: async prefs => prefs, getImport: async () => null,
  getPackageFavorites: async () => ({ packages: [] }), getPackageInstall: () => ipcRenderer.invoke('fixture:status'),
  startPackageInstall: request => ipcRenderer.invoke('fixture:install', request)
});`);
let win;
const evaluate = code => win.webContents.executeJavaScript(code);
async function waitFor(code) {
  const deadline = Date.now() + 7000;
  while (Date.now() < deadline) {
    if (await evaluate(code)) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error('Timed out: ' + code);
}
async function click(selector) {
  const point = await evaluate(`(() => { const node = document.querySelector(${JSON.stringify(selector)}); node.scrollIntoView({ block: 'nearest' }); const r = node.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()`);
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  win.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point });
  win.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...point });
}
app.whenReady().then(async () => {
  let exitCode = 0;
  try {
    for (const choice of ['Keep existing', 'Replace version']) {
      sequence++;
      win = new BrowserWindow({ show: false, width: 1200, height: 800, webPreferences: { preload, contextIsolation: true, sandbox: true } });
      win.webContents.on('console-message', (_event, level, message) => { if (level === 3) errors.push(message); });
      await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'));
      await waitFor(`!!document.querySelector('.task-indicator')`);
      await click('.task-indicator');
      await waitFor(`!!document.querySelector('#package-replace-title')`);
      const content = await evaluate(`document.querySelector('[aria-labelledby="package-replace-title"]').textContent`);
      for (const text of ['Replace installed package versions?', 'Mathematics', '1.2.6', '1.3.2', '/fixture/Unity project']) assert(content.includes(text), text);
      assert(!content.includes('Already installed'), 'Only conflicts appear in the confirmation');
      assert.equal(calls.length, 0, 'Showing confirmation must not start replacement');
      await evaluate(`(() => { const buttons = [...document.querySelectorAll('[aria-labelledby="package-replace-title"] button')]; buttons.find(button => button.textContent === ${JSON.stringify(choice)}).id = 'fixture-choice'; })()`);
      if (choice === 'Replace version') {
        await evaluate('new Promise(resolve => setTimeout(resolve, 350))');
        await fs.promises.writeFile(path.join(os.tmpdir(), 'ual-package-overwrite.png'), (await win.webContents.capturePage()).toPNG());
      }
      await click('#fixture-choice');
      await waitFor(`!document.querySelector('#package-replace-title')`);
      if (choice === 'Keep existing') assert.equal(calls.length, 0, 'Keeping existing versions sends no install request');
      else {
        assert.equal(calls.length, 1);
        assert.deepEqual(calls[0], { project: '/fixture/Unity project', ids: ['math'], overwrite: true });
      }
      win.destroy();
    }
    refreshing = true;
    win = new BrowserWindow({ show: false, width: 1200, height: 800, webPreferences: { preload, contextIsolation: true, sandbox: true } });
    win.webContents.on('console-message', (_event, level, message) => { if (level === 3) errors.push(message); });
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'));
    await waitFor(`!!document.querySelector('.task-indicator')`);
    await click('.task-indicator');
    await waitFor(`document.querySelector('.favorite-package-progress')?.textContent.includes('Waiting for Unity refresh')`);
    const refreshText = await evaluate(`document.querySelector('.favorite-package-progress').textContent`);
    assert(refreshText.includes('Assets → Refresh (⌘R on macOS)'), refreshText);
    assert(refreshText.includes('Awaiting Unity'), refreshText);
    assert.equal(await evaluate(`!!document.querySelector('#package-replace-title')`), false, 'Refresh guidance is not destructive consent');
    assert.equal(calls.length, 1, 'Refresh guidance does not submit another installation');
    await evaluate('new Promise(resolve => setTimeout(resolve, 350))');
    await fs.promises.writeFile(path.join(os.tmpdir(), 'ual-package-refresh.png'), (await win.webContents.capturePage()).toPNG());
    win.destroy();
    assert.deepEqual(errors, [], 'Renderer console errors');
    console.log('PASS: installed/requested versions displayed; Keep existing makes no request; Replace version sends explicit consent for conflicts only; refresh waiting exposes Assets/Refresh instructions; no console errors.');
    console.log('Screenshot: ' + path.join(os.tmpdir(), 'ual-package-overwrite.png'));
  } catch (error) { console.error(error); exitCode = 1; }
  finally { if (win && !win.isDestroyed()) win.destroy(); await fs.promises.rm(temp, { recursive: true, force: true }); app.exit(exitCode); }
});
