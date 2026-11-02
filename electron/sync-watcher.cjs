"use strict";

// Watches the library folder and asks for a sync after archive changes settle.
// Events are hints only: the backend sync compares the whole folder with the
// index, so a missed or duplicate event cannot corrupt anything.
const fs = require("node:fs");

const ARCHIVE = /\.(unitypackage|zip)$/i;
// The app's own staging areas: cleanup quarantine, library data, temp stages.
const OWN_PATHS = /(^|[\\/])(_Quarantine|\.data)([\\/]|$)|\.cleanup-/;

function isRelevant(filename) {
  if (!filename) return true; // the platform could not name the file; check anyway
  const name = String(filename);
  return ARCHIVE.test(name) && !OWN_PATHS.test(name);
}

function createSyncWatcher({
  root,
  onTrigger,
  debounceMs = 30000,
  retryMs = 60000,
  watch = fs.watch,
  timers = { setTimeout, clearTimeout },
}) {
  let watcher = null;
  let debounce = null;
  let retry = null;
  let closed = false;

  function schedule() {
    if (closed) return;
    if (debounce) timers.clearTimeout(debounce);
    debounce = timers.setTimeout(() => {
      debounce = null;
      if (!closed) onTrigger();
    }, debounceMs);
  }

  function start() {
    retry = null;
    if (closed) return;
    try {
      watcher = watch(root, { recursive: true }, (_event, filename) => {
        if (isRelevant(filename)) schedule();
      });
      watcher.on("error", restart);
    } catch {
      restart();
    }
  }

  function restart() {
    if (watcher) {
      try { watcher.close(); } catch { /* already closed */ }
      watcher = null;
    }
    if (!closed && !retry) retry = timers.setTimeout(start, retryMs);
  }

  start();
  return {
    close() {
      closed = true;
      if (debounce) timers.clearTimeout(debounce);
      if (retry) timers.clearTimeout(retry);
      debounce = null;
      retry = null;
      if (watcher) {
        try { watcher.close(); } catch { /* already closed */ }
        watcher = null;
      }
    },
  };
}

module.exports = { createSyncWatcher, isRelevant };
