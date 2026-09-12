# Phase 2 — Connecteam Integration · Running Time Log

Services provided to **Ramos Cleaning, Inc.** · payable to **Christopher Maraist**

Phase 1 (Guesty → Google Sheet) is invoiced separately and **closed**. Nothing
before 2026-09-12 is billed here, including the Phase 1 defect work finished that
morning. This log starts at the point Phase 2 work resumed.

Rate and discount carry over from the Phase 1 invoice (family rate, 15%).

| Date | Hrs | Work | Status |
|------|-----|------|--------|
| 2026-09-12 | 0.75 | Phase 2 restart: reviewed `connecteam_client` / `connecteam_map` / `connecteam_push` against the post-Phase-1 codebase, confirmed the safety rails (create-only, `live=False` default, unassigned assertion, read-before-write duplicate guard), identified the four open design gaps, drafted the sequenced plan. | ✅ |
| 2026-09-12 | 0.5 | Moved the Connecteam preview into GitHub Actions (preview-only; `--live` deliberately unreachable) so it no longer depends on the office machine's CA bundle. Ran the first post-Phase-1 Austin preview: 37 jobs, all Unassigned, property names clean. Found that the preview spans the whole month, so 10 of the 37 are for dates already past. | ✅ |

**Total to date: 1.25 h**

---

## How this log is kept

One row per working session, added as the work happens rather than
reconstructed afterwards. Hours are what was actually spent on Phase 2 — design,
build, test, verification runs, and the reading needed to do them.

**Not billed here:** Phase 1 maintenance, defect work on the sheet sync, and
anything already covered by the Phase 1 invoice.

Chris: the hours are my estimate of the session length. Correct any row — you were
there for them and I was not watching a clock.

## Open scope (not yet started)

These are the pieces of Phase 2 still to do. Listed so the log shows what remains,
not only what is spent.

- First live push — one city, one narrow date window, verified on a phone
- Job lifecycle: update and delete a pushed job when the booking moves or cancels
- Link each sheet row to its Connecteam job so a later run can find it again
- Decide the rolling push window **(now blocking the first live push)**
- Automate the push after the daily sync
- Extend from the first city to all five markets
