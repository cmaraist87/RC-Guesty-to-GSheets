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

| 2026-09-12 | 1.0 | Built `connecteam_board.py` (read-only; no write path) to read the live boards and diff them against the sheet. Found three things that change the design: `existing_shifts` returns only 10 rows per board (a page cap), so the duplicate guard would not have stopped a second live run doubling the board; the team carries the property in `jobId` (10/10 filled) not `title` (5-6/10), which is the field our push sets; and their shifts run 0.5-8.5h from 06:00/08:00 rather than our fixed 4h from checkout. | ✅ |

| 2026-09-12 | 0.5 | Time model settled by Chris: a job card starts at the checkout time and runs a fixed 4h, regardless of the next arrival. Implemented, rewrote the test that pinned the old variable-length behaviour, re-previewed Austin -- 37 jobs, 36 of them 11:00-15:00 and one 13:00-17:00 where the checkout is later. | ✅ |

| 2026-09-12 | 1.25 | Blocker 1 **fixed**: `existing_shifts` now pages until a board is exhausted. Verified against the live boards -- Austin 10 -> 31, New Orleans/Bay St Louis 10 -> 882, Savannah/Thunderbolt 10 -> 194 for September. Blocker 2 **diagnosed**: found the Jobs endpoint (`/jobs/v1/jobs`, four other candidates 404), confirmed Job names are property addresses, and counted ~266 distinct Jobs actually in use. The Jobs list ignores the offset parameter the shifts accept, so reading it in full needs one more discovery step. | ◐ |

| 2026-09-13 | 0.5 | Wired Chris' Test Scheduler (16642349) in as a named target: `--test` on the push redirects a city's jobs to it, `--board` lets the reader open any board by id. The id lives in code rather than an argument so sending somewhere by mistake needs a code change; a test asserts it is not also a market's board. | ✅ |

| 2026-09-13 | 0.25 | Test board rebuilt by Chris, so its scheduler id moved again (19710485 -> 19713722). Confirmed against the account listing rather than a URL, and made `--test` verify the id and print the board's name before writing -- the id has now moved twice and a stale one should stop, not 404 mid-write. | ✅ |

| 2026-09-13 | 1.0 | **First write of the project.** Added a live path reachable only when bolted to `--test`, so no workflow input can reach a market board. First attempt refused (HTTP 400): Connecteam validates colour against a fixed palette and neither of ours was on it — nothing written, which is what the test board is for. Recorded the API's own palette, repinned both colours, retried: **37 Austin jobs created on 'Chris Test', all Unassigned.** Ran it a second time to exercise the duplicate guard — all 37 recognised and skipped, 0 created, which also proves the pagination fix under real conditions. | ✅ |

| 2026-09-21 | 1.5 | Card redesign, as asked: the property moves out of the shift **title** into the **Job** field, and the end time goes. Established against the API what it would actually allow -- a shift with no `endTime` is refused ("Field required"), so is one whose end equals its start, and so is an empty or absent `title`; a 15-minute block is accepted. So the card is now a 15-minute marker that states a start time without claiming how long a property takes, and the title carries the job KIND (Clean / Turnover) since it cannot be blank. Built `connecteam_jobs.py` to match a property to a Job: ~1429 Jobs typed in by hand, so names are compared on the identifying part and the highest version wins (Chris' rule). Austin October previews 38 cards across 8 properties. | ✅ |
| 2026-09-21 | 0.5 | Caught two defects the redesign introduced before anything was pushed. The duplicate guard keyed on (title, start), which was sound while the title held the property -- with every title now "Clean", seven Austin cleans at 11:00 on 18 October collapsed to one key, so six would be skipped forever and a deleted card could never come back; the key is now (jobId, title, start). The preview had the same blind spot and printed 38 identical lines, so it now names the property. Both covered by tests. | ✅ |

**Total to date: 7.75 h**

> Phase 1 work continues alongside and is **not** billed here.


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

- ~~Map property → Connecteam `jobId`~~ — **done 2026-09-21** (`connecteam_jobs.py`; highest-version rule)
- ~~Decide whether a card with no Job attached is acceptable~~ — **settled**: no Job, no card; the property is reported for the team to create
- **Team to create 3 Austin Jobs**: 1802 Martin Luther King, 4807 Prock B, 4807 Prock C (only the first has an October clean)
- Clean up ~6 `PROBE` shifts left on the test board, dated 2027-06-01 — needs a delete path, which does not exist yet
- Decide where the first market write happens: the test board cannot validate `jobId` (it rejects the account-wide Jobs as "does not exist")
- Job lifecycle: update and delete when a booking moves or cancels
- First write to a **market** board
- Job lifecycle: update and delete a pushed job when the booking moves or cancels
- Link each sheet row to its Connecteam job so a later run can find it again
- Decide the rolling push window **(now blocking the first live push)**
- Automate the push after the daily sync
- Extend from the first city to all five markets
