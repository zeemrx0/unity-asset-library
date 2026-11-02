"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createSyncWatcher, isRelevant } = require("../electron/sync-watcher.cjs");

function fakeTimers() {
  let next = 1;
  const pending = new Map();
  return {
    pending,
    setTimeout(fn, ms) { const id = next++; pending.set(id, { fn, ms }); return id; },
    clearTimeout(id) { pending.delete(id); },
    runAll() {
      const due = [...pending.entries()];
      pending.clear();
      for (const [, timer] of due) timer.fn();
    },
  };
}

function fakeWatch() {
  const made = [];
  const watch = (root, options, handler) => {
    const w = new EventEmitter();
    w.root = root;
    w.options = options;
    w.handler = handler;
    w.closed = false;
    w.close = () => { w.closed = true; };
    made.push(w);
    return w;
  };
  return { watch, made };
}

function setup() {
  const timers = fakeTimers();
  const { watch, made } = fakeWatch();
  let triggers = 0;
  const watcher = createSyncWatcher({ root: "/vault", onTrigger: () => { triggers += 1; }, watch, timers });
  return { timers, made, watcher, count: () => triggers };
}

const tests = {
  "watches the root recursively"() {
    const { made } = setup();
    assert.equal(made.length, 1);
    assert.equal(made[0].root, "/vault");
    assert.equal(made[0].options.recursive, true);
  },
  "two archive events inside the window trigger once"() {
    const { made, timers, count } = setup();
    made[0].handler("rename", "3D/Pack v1.unitypackage");
    made[0].handler("change", "3D/Pack v1.unitypackage");
    assert.equal(timers.pending.size, 1);
    timers.runAll();
    assert.equal(count(), 1);
  },
  "non-archive events are ignored"() {
    const { made, timers } = setup();
    made[0].handler("change", "notes.txt");
    made[0].handler("change", ".DS_Store");
    assert.equal(timers.pending.size, 0);
  },
  "the app's own folders are ignored"() {
    for (const name of ["_Quarantine/x.unitypackage", "_Quarantine/.cleanup-1/a/x.unitypackage",
                        ".data/x.zip", "a/.cleanup-abc/x.unitypackage"]) {
      assert.equal(isRelevant(name), false, name);
    }
    assert.equal(isRelevant("Quarantined Zone Pack.unitypackage"), true);
    assert.equal(isRelevant(null), true);
  },
  "close cancels a pending trigger"() {
    const { made, timers, watcher, count } = setup();
    made[0].handler("change", "a.zip");
    watcher.close();
    timers.runAll();
    assert.equal(count(), 0);
    assert.equal(made[0].closed, true);
  },
  "an error re-creates the watcher after the retry delay"() {
    const { made, timers } = setup();
    made[0].emit("error", new Error("EMFILE"));
    assert.equal(made[0].closed, true);
    assert.equal(made.length, 1);
    timers.runAll();
    assert.equal(made.length, 2);
  },
  "no retry after close"() {
    const { made, timers, watcher } = setup();
    made[0].emit("error", new Error("EMFILE"));
    watcher.close();
    timers.runAll();
    assert.equal(made.length, 1);
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`not ok - ${name}\n  ${error.message}`);
  }
}
if (failed) process.exit(1);
