// Run after npm run build: npx electron tests/test-app-update-progress.cjs
// Uses an isolated profile and updater snapshots; never replaces an installed app.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ual-update-progress-'));
app.setPath('userData', path.join(temp, 'profile'));
const preload = path.join(temp, 'preload.cjs');
fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('ual', {
  getStorage: async () => ({ path: null, ready: false, needsSetup: true, canChange: true, busy: false }),
  getUpdateStatus: async () => JSON.parse(localStorage.getItem('fixture:update') || 'null'),
  checkForUpdates: async () => { localStorage.setItem('fixture:checks', String(Number(localStorage.getItem('fixture:checks') || 0) + 1)); },
  getPreferences: async () => ({}), getLegacyLibraryRoot: async () => null,
  getImport: async () => null, getPackageInstall: async () => null, getPackageFavorites: async () => ({ packages: [] })
});`);
let win;
const evaluate = code => win.webContents.executeJavaScript(code);
async function waitFor(code) {
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    if (await evaluate(code)) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error('Timed out: ' + code);
}
async function status(phase, percent = null, error = null) {
  await evaluate(`localStorage.setItem('fixture:update', JSON.stringify(${JSON.stringify({ phase, percent, error, version: '0.3.0' })}))`);
}
app.whenReady().then(async () => {
  let exitCode = 0;
  try {
    win = new BrowserWindow({ show: false, width: 1200, height: 800, webPreferences: { preload, contextIsolation: true, sandbox: true } });
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'));
    assert.equal(await evaluate(`!!document.querySelector('.app-update-progress')`), false);
    await status('downloading', 42);
    await waitFor(`document.querySelector('.app-update-progress progress')?.value === 42`);
    assert((await evaluate(`document.querySelector('.app-update-progress').textContent`)).includes('42%'));
    assert((await evaluate(`document.querySelector('.app-update-progress').textContent`)).includes('0.3.0'));
    for (const phase of ['extracting', 'verifying', 'installing']) {
      await status(phase);
      await waitFor(`document.querySelector('.app-update-progress .progress-stage')?.textContent.toLowerCase().includes('${phase === 'installing' ? 'restarting' : phase}')`);
      assert.equal(await evaluate(`document.querySelector('.app-update-progress progress').hasAttribute('value')`), false, 'Preparation has no invented percentage');
    }
    win.webContents.debugger.attach('1.3');
    await win.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
    assert.equal(await evaluate(`getComputedStyle(document.querySelector('.app-update-progress .progress-indeterminate')).animationName`), 'none');
    win.webContents.debugger.detach();
    await status('ready', 100);
    await waitFor(`document.querySelector('.app-update-progress button')?.textContent.includes('Restart and Install')`);
    assert.equal(await evaluate(`document.querySelector('.app-update-progress progress').value`), 100);
    await evaluate(`document.querySelector('.app-update-progress button').click()`);
    await waitFor(`localStorage.getItem('fixture:checks') === '1'`);
    await status('failed', null, 'Checksum mismatch');
    await waitFor(`document.querySelector('.app-update-progress [role=alert]')?.textContent.includes('Checksum mismatch')`);
    assert.equal(await evaluate(`!!document.querySelector('.app-update-progress progress')`), false);
    await evaluate(`document.querySelector('.app-update-progress button').click()`);
    await waitFor(`localStorage.getItem('fixture:checks') === '2'`);
    for (const [width, height] of [[800, 600], [1200, 800]]) {
      win.setSize(width, height);
      await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      assert.equal(await evaluate(`(() => { const r = document.querySelector('.app-update-progress').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; })()`), true);
    }
    await status('idle');
    await waitFor(`!document.querySelector('.app-update-progress')`);
    console.log('PASS: visible update percentage during setup, preparation without fake percentages, restart/retry bridge, failure, reduced motion, and viewport fit');
  } catch (error) { console.error(error); exitCode = 1; }
  finally { win?.destroy(); fs.rmSync(temp, { recursive: true, force: true }); app.exit(exitCode); }
});
