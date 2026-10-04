// Run: npx electron tests/test-focus-outline.cjs
// Isolated renderer fixture: never opens the real library or writes its data.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { buildSync } = require('esbuild');
let win;
const evaluate = code => win.webContents.executeJavaScript(code);
async function frame() {
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
}
async function waitFor(expression) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error(`Timed out: ${expression}`);
}
async function key(keyCode, modifiers = []) {
  win.webContents.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
  win.webContents.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
  await frame();
}
async function click(selector) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({ block: 'nearest' })`);
  const point = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return {x: Math.round(r.x + Math.min(50, r.width / 2)), y: Math.round(r.y + r.height / 2)}; })()`);
  win.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point });
  win.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...point });
  await waitFor('document.documentElement.dataset.focusVisible === "false"');
}
const outline = selector => evaluate(`getComputedStyle(document.querySelector(${JSON.stringify(selector)})).outlineStyle`);
app.whenReady().then(async () => {
  let exitCode = 0;
  try {
    const errors = [];
    win = new BrowserWindow({ show: true, width: 900, height: 500 });
    win.webContents.on('console-message', (_event, details) => {
      if (details.level === 'error') errors.push(details.message);
    });
    const css = ['styles.css', 'components/FavoritePackages.css', 'components/asset-context-menu.css', 'components/react-bits/SpringCheck.css', 'components/react-bits/SquishSwitch.css', 'components/react-bits/BranchedMenu.css', 'components/react-bits/RubberSegment.css', 'components/react-bits/GlideSelect.css'].map(file => fs.readFileSync(path.join(__dirname, '../app/src', file), 'utf8')).join('\n');
    const script = buildSync({
      stdin: { contents: 'import React from "react"; import { createRoot } from "react-dom/client"; import { FocusVisibility } from "./app/src/components/focus-visibility"; function Fixture() { React.useEffect(() => { window.fixtureReady = true; }, []); return React.createElement(FocusVisibility); } createRoot(document.getElementById("root")).render(React.createElement(React.StrictMode, null, React.createElement(Fixture)));', resolveDir: path.join(__dirname, '..'), loader: 'tsx' },
      bundle: true, write: false, format: 'iife', platform: 'browser', define: { 'process.env.NODE_ENV': '"production"' },
    }).outputFiles[0].text;
    await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<style>${css}</style><div id="root"></div><details class="favorite-package-disclosure"><summary>Saved packages</summary></details><button>Next</button><input aria-label="Search"><label class="spring-check"><input type="checkbox" class="spring-check__input"><span class="spring-check__press">Select</span></label><button class="asset-card" style="width:220px">Fixture asset</button><button class="list-row">Fixture row</button><a href="#fixture">Fixture link</a><textarea aria-label="Notes"></textarea><div contenteditable="true">Editable text</div><div class="search-wrap"><input aria-label="Wrapped search"></div><button class="glide-select__trigger">Select filter</button><div class="branched-menu"><button class="branched-menu__toggle">Category</button></div><button class="rubber-segment__item">Grid</button><button class="asset-context-menu__item">Menu action</button><label class="squish-switch"><input type="checkbox" class="squish-switch__input"><span class="squish-switch__track">Switch</span></label><dialog tabindex="-1">Fixture dialog</dialog><script>${script}</script>`));
    await waitFor('window.fixtureReady === true');
    assert.equal(await evaluate('document.documentElement.dataset.focusVisible'), 'true', 'Initial virtual/keyboard focus remains visible');
    await click('summary');
    assert.equal(await evaluate('document.activeElement.tagName'), 'SUMMARY');
    assert.equal(await outline('summary'), 'none');
    await key('Meta');
    assert.equal(await evaluate('document.activeElement.matches(":focus-visible")'), true, 'Reproduce Chromium modifier-only focus-visible promotion');
    await key('Tab', ['meta']);
    assert.equal(await outline('summary'), 'none', 'Cmd+Tab must not outline the pointer-focused disclosure');
    await evaluate('window.dispatchEvent(new Event("blur")); window.dispatchEvent(new Event("focus"));');
    await frame();
    assert.equal(await outline('summary'), 'none', 'App focus restoration preserves pointer modality');
    fs.writeFileSync(path.join(os.tmpdir(), 'ual-focus-pointer.png'), (await win.webContents.capturePage()).toPNG());
    await key('Tab');
    assert.equal(await evaluate('document.activeElement.tagName'), 'BUTTON');
    await waitFor('document.documentElement.dataset.focusVisible === "true"');
    assert.equal(await outline('button'), 'solid', 'Tab retains a visible keyboard focus ring');
    await key('Tab', ['shift']);
    assert.equal(await evaluate('document.activeElement.tagName'), 'SUMMARY');
    assert.equal(await outline('summary'), 'solid', 'Shift+Tab outlines the disclosure');
    await key('Meta');
    assert.equal(await outline('summary'), 'solid', 'App switching does not clear genuine keyboard focus');
    const open = await evaluate('document.querySelector("details").open');
    await key('Space');
    assert.equal(await evaluate('document.querySelector("details").open'), !open, 'Keyboard activation is unchanged');
    fs.writeFileSync(path.join(os.tmpdir(), 'ual-focus-keyboard.png'), (await win.webContents.capturePage()).toPNG());
    await click('button');
    await key('Meta');
    assert.equal(await outline('button'), 'none', 'Buttons also ignore modifier-only focus rings');
    await key('Down');
    await waitFor('document.documentElement.dataset.focusVisible === "true"');
    assert.equal(await outline('button'), 'solid', 'Arrow navigation enables keyboard focus');
    await click('input[aria-label="Search"]');
    assert.equal(await outline('input[aria-label="Search"]'), 'solid', 'Pointer-focused text inputs keep their outline');
    await key('Meta');
    assert.equal(await outline('input[aria-label="Search"]'), 'solid');
    await key('Tab');
    assert.equal(await outline('.spring-check__press'), 'solid', 'Keyboard checkbox focus remains visible');
    await click('.spring-check__press');
    await key('Meta');
    assert.equal(await outline('.spring-check__press'), 'none', 'Wrapped checkbox focus also ignores app-switch modifiers');
    for (const theme of ['light', 'dark']) {
      await evaluate(`document.documentElement.dataset.theme = ${JSON.stringify(theme)}`);
      await key('Tab');
      for (const selector of ['.asset-card', '.list-row', 'a', 'textarea', '[contenteditable]', '.glide-select__trigger', '.branched-menu__toggle', '.rubber-segment__item']) {
        await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);
        await frame();
        const style = await evaluate(`(() => { const s = getComputedStyle(document.querySelector(${JSON.stringify(selector)})); const probe = document.createElement('span'); probe.style.color = 'var(--accent)'; document.body.append(probe); const accent = getComputedStyle(probe).color; probe.remove(); return { width: s.outlineWidth, style: s.outlineStyle, color: s.outlineColor, accent }; })()`);
        assert.equal(style.style, 'solid', `${theme}: ${selector} has designed keyboard focus`);
        assert.equal(style.width, '2px', `${theme}: ${selector} has no thick/default ring`);
        assert.equal(style.color, style.accent, `${theme}: ${selector} uses the theme accent`);
        await key('Escape');
        assert.equal(await outline(selector), 'solid', 'Escape preserves genuine keyboard focus');
      }
      await evaluate('document.querySelector("dialog").show(); document.querySelector("dialog").focus()');
      assert.equal(await outline('dialog'), 'none', 'Programmatic dialog focus has no browser-default ring');
      await evaluate('document.querySelector("dialog").close()');
      for (const selector of ['.asset-card', '.list-row', 'a', '.glide-select__trigger', '.branched-menu__toggle', '.rubber-segment__item', '.asset-context-menu__item', '.squish-switch__track']) {
        await click(selector);
        await key('Meta');
        assert.equal(await outline(selector), 'none', `${theme}: ${selector} stays ring-free after pointer/app switching`);
        const focused = await evaluate('document.activeElement.outerHTML');
        await key('Escape');
        await key('Escape');
        assert.equal(await outline(selector), 'none', `${theme}: Escape does not promote pointer focus on ${selector}; ${await evaluate('JSON.stringify({ visible: document.documentElement.dataset.focusVisible, active: document.activeElement.tagName, target: document.activeElement.className })')}`);
        assert.equal(await evaluate('document.activeElement.outerHTML'), focused, 'Escape does not blur the focused control');
        await key('Tab');
        assert.equal(await evaluate('document.documentElement.dataset.focusVisible'), 'true', 'Tab resumes keyboard focus after Escape');
      }
      await click('.search-wrap input');
      assert.equal(await outline('.search-wrap'), 'solid', 'Search keeps one designed wrapper ring');
      assert.equal(await outline('.search-wrap input'), 'none', 'Search does not draw a second input ring');
      await click('textarea');
      assert.equal(await outline('textarea'), 'solid', 'Text areas keep designed text-entry focus');
      await click('[contenteditable]');
      assert.equal(await outline('[contenteditable]'), 'solid', 'Editable text keeps designed text-entry focus');
      await click('.asset-card');
      fs.writeFileSync(path.join(os.tmpdir(), `ual-focus-${theme}-pointer.png`), (await win.webContents.capturePage()).toPNG());
      await key('Tab', ['shift']);
      await evaluate('document.querySelector(".asset-card").focus()');
      await frame();
      fs.writeFileSync(path.join(os.tmpdir(), `ual-focus-${theme}-keyboard.png`), (await win.webContents.capturePage()).toPNG());
    }
    assert.deepEqual(errors, [], 'No renderer console errors');
    console.log('PASS: pointer/app switching, keyboard navigation, cards/rows, links, text entry, dialog, and themed controls in light/dark');
  } catch (error) { console.error(error); exitCode = 1; }
  finally { app.exit(exitCode); }
});
