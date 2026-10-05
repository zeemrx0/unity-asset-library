// Run: npx electron tests/test-view-controls.cjs
// Isolated renderer fixture; does not open or modify library data.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { buildSync } = require('esbuild');

app.whenReady().then(async () => {
  let win;
  let exitCode = 0;
  try {
    const css = ['styles.css', 'components/react-bits/RubberSegment.css', 'components/react-bits/GlideSelect.css']
      .map(file => fs.readFileSync(path.join(__dirname, '../app/src', file), 'utf8')).join('\n');
    const script = buildSync({
      stdin: {
        contents: `import React from 'react'; import { createRoot } from 'react-dom/client';
          import RubberSegment from './app/src/components/react-bits/RubberSegment';
          import GlideSelect from './app/src/components/react-bits/GlideSelect';
          import { LayoutGrid, List } from 'lucide-react';
          function Fixture() {
            const [view, setView] = React.useState('grid');
            const items = [{value:'grid',label:<LayoutGrid className="icon" />,ariaLabel:'Grid view'}, {value:'list',label:<List className="icon" />,ariaLabel:'List view'}];
            return <><div className="view-controls"><RubberSegment aria-label="Asset view" size="sm" draggable={false} items={items} value={view} onChange={setView} />
              <GlideSelect className="sort-control" ariaLabel="Filter by author" value="all" options={[{value:'all',label:'All authors'}]} onChange={()=>{}} />
              <GlideSelect className="sort-control" ariaLabel="Sort assets" value="name" options={[{value:'name',label:'Name'}]} onChange={()=>{}} /></div>
              <div id="unrelated"><RubberSegment size="sm" items={items} /></div></>;
          }
          createRoot(document.getElementById('root')).render(<Fixture />);`,
        resolveDir: path.join(__dirname, '..'), loader: 'tsx',
      },
      loader: { ".css": "empty" }, bundle: true, write: false, format: 'iife', platform: 'browser',
      define: { 'process.env.NODE_ENV': '"production"' },
    }).outputFiles.find(file => !file.path.endsWith('.css')).text;
    win = new BrowserWindow({ show: false, width: 900, height: 500 });
    await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<style>${css}</style><div id="root"></div><script>${script}</script>`));
    const evaluate = async code => {
      try { return await win.webContents.executeJavaScript(code); }
      catch (error) { throw new Error(`Renderer probe failed: ${code}`, { cause: error }); }
    };
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    for (const width of [900, 480]) {
      win.setSize(width, 500);
      for (const theme of ['light', 'dark']) {
        await evaluate(`document.documentElement.dataset.theme = '${theme}'`);
        const dimensions = await evaluate(`(() => {
          const controls = [...document.querySelectorAll('.view-controls .rubber-segment, .view-controls .glide-select__trigger')];
          return controls.map(el => { const r = el.getBoundingClientRect(); return {height:r.height,top:r.top,bottom:r.bottom}; });
        })()`);
        assert.equal(dimensions.length, 3);
        for (const control of dimensions) {
          assert.equal(control.height, 36, `${width}px ${theme}: every toolbar control is 36px tall`);
          assert.equal(control.top, dimensions[0].top, 'Control tops align');
          assert.equal(control.bottom, dimensions[0].bottom, 'Control bottoms align');
        }
      }
    }
    assert.equal(await evaluate('document.querySelector("#unrelated .rubber-segment").getBoundingClientRect().height'), 28, 'Other small segmented controls stay unchanged');
    await evaluate('document.querySelector(".view-controls .rubber-segment button:nth-of-type(2)").click()');
    await evaluate('new Promise(resolve => requestAnimationFrame(resolve))');
    assert.equal(await evaluate('document.querySelector(".view-controls .rubber-segment button:nth-of-type(2)").getAttribute("aria-pressed")'), 'true', 'List switching still works');
    await evaluate('document.querySelector(".view-controls .rubber-segment button:nth-of-type(1)").click()');
    await evaluate('new Promise(resolve => requestAnimationFrame(resolve))');
    assert.equal(await evaluate('document.querySelector(".view-controls .rubber-segment button:nth-of-type(1)").getAttribute("aria-pressed")'), 'true', 'Grid switching still works');
    console.log('PASS: toolbar height/alignment, both themes and widths, unrelated control sizing, grid/list switching');
  } catch (error) {
    console.error(error);
    exitCode = 1;
  } finally {
    if (win) win.destroy();
    app.exit(exitCode);
  }
});
