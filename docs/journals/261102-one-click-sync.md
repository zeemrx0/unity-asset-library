# One-click Sync (2026-11-02)

Branch: `feat/one-click-sync`. Plan: `plans/261102-1520-one-click-sync/`.

## What changed

- One **Sync** button replaces the Library actions menu (Resync index, Enrich, Organize, Clean up old versions) and the empty-state Scan library button.
- Sync runs one backend job: scan and update the index, match pending assets to the Asset Store, then plan old-version deletions and category-folder moves. Index and matching apply on their own. File deletions and moves wait in one "Review N changes" dialog and apply only after one confirmation.
- Sync starts by itself 30 seconds after archive changes in the library folder, 5 seconds after the backend session starts, and on window focus at most every 10 minutes. Overlapping starts coalesce into one follow-up run.
- Store matching order: saved match, then the Store id embedded in local `.unitypackage` headers (never read from cloud-only files), then web search.
- The name gate is now symmetric: identifying words on each side must appear on the other. Edition words (Pro, HDRP, Mobile and similar) must match. Two different products that both pass make the result ambiguous, so nothing is written.

## Numbers

- Calibration set from 724 verified matches: 719 still match, 0 of 2160 confusable pairs match (the old gate matched 19, all different products), and 7 previously unverified pairs now match. Details: `plans/reports/matcher-261102-1520-calibration.md`.
- `make test`: 400 Python tests pass, typecheck passes, renderer suites pass.

## Follow-ups

- Pyright reports Optional-typing findings on unchanged older lines in `bin/resolve_store.py`, `bin/server.py` and `tests/test_resolve_store.py`. They became visible when `pyrightconfig.json` started resolving `bin/` imports.
