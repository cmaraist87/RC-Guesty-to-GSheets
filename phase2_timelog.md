# Phase 2 — Connecteam Integration · Running Time Log

Services provided to **Ramos Cleaning, Inc.** · payable to **Christopher Maraist**

Phase 1 (Guesty → Google Sheet) is invoiced separately and **closed**. Nothing
before 2026-09-12 is billed here, including the Phase 1 defect work finished that
morning. This log starts at the point Phase 2 work resumed.

Rate and discount carry over from the Phase 1 invoice (family rate, 15%).

From 2026-09-22 this log also carries **daily-run reliability** work on the
Guesty → Sheet system, matching invoice RCI-2026-03. That is new scope Chris
asked for, not defect work — defect work stays unbilled, as agreed. Rows are
marked so the two can be separated at invoice time.

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

| 2026-09-21 | 0.75 | **First write to a real crew board.** Established first that a Connecteam Job belongs to the board that owns it -- a jobId Connecteam itself had put on the Austin board was refused by the test board -- so the test board, which owns none, could never validate a card carrying a property, and Chris accepted a live Austin test. Read the Austin board first: 0 cards in October, so nothing of the team's could be duplicated. Opened a deliberate market path in the workflow (spelled out, not a boolean; the comment claiming none existed was corrected). First attempt died on a shell quoting error before Python ran, so nothing was written. Second: **38 Austin cards created, all Unassigned, every one carrying its property as a Job.** Re-ran it -- all 38 recognised, 0 created -- which proves the rewritten duplicate key on live data. | ✅ |

| 2026-09-21 | 0.75 | Austin cards removed at Chris' instruction and the board verified back to the team's own 1190 -- wide-window read, zero of ours left. Market boards then locked in three places: connecteam_push refuses --live without --test before it reads anything, the workflow's market option was removed, and a test asserts both for all five markets and reads the workflow file so the option cannot quietly return. | ✅ |
| 2026-09-21 | 1.0 | Why the test board could not take the new card. A Job carries `instanceIds` (the boards it belongs to) and `isDeleted` -- **506 of the account's 1429 Jobs are soft-deleted** and still come back from the API. Neither was being read, and the Job first probed with was a deleted one, which sent the earlier diagnosis down the wrong road. The matcher now filters on both, so a card points at a live Job on the right board by construction rather than by luck; four tests pin it. | ✅ |
| 2026-09-21 | 0.5 | Built the delete path -- the one thing the integration could not do. Board and window are both required, a card assigned to somebody is refused whatever the filters say, and it lists before it acts. Cleared 41 stale cards from the test board; 3 assigned ones correctly refused. | ✅ |
| 2026-09-21 | 1.25 | Job administration established against the API: creation takes an ARRAY with `title` and `instanceIds` (two field names and the body shape all guessed wrong first), and titles are unique account-wide, so eight of the nine Austin properties could not be created a second time and had to be SHARED onto the test board instead via PUT. Since PUT replaces the record, each Job is read whole, only its board list changed, and every other field compared back field by field -- proved on one before the rest. **44 cards now on Chris Test, every one carrying its property as a Job.** | ✅ |

| 2026-09-22 | 0.5 | **[reliability]** Read-only lookup tools: everything Guesty and the sheet hold for a given reservation, and every sheet row for a property without spending a Guesty token. Built to answer the HMKMRDCK9Y / HMPJTAMCDS question. *Investigation itself absorbed; the tools billed.* | ✅ |
| 2026-09-23 | 1.0 | Connecteam rollout readiness: every market sized by cards per month and by properties with no Job, reported per BOARD because two markets share each of the larger ones. Go-live order set from the figures — Austin 38 cards, Savannah/Thunderbolt 134, New Orleans/Bay St. Louis 431 — and 48 properties across the two larger boards found to have no Job, which would produce no card at all, silently. | ✅ |
| 2026-09-24 | 1.5 | **[reliability]** Measured when the overnight run actually completed across 25 days: met the 6:45 deadline on 13, missed on 12, the split falling exactly where the schedule was last re-cut. Found two of the four triggers had NEVER counted — 23:47 and 00:19 Chicago, outside the once-a-day window, standing down on arrival every morning. Re-cut to 22 across the morning, then lowered the floor again after the first night landed later than any of the previous thirteen. | ✅ |
| 2026-09-24 | 1.0 | **[reliability]** Built and proved the `as_scheduled` dispatch so an external, punctual trigger can drive the daily run — checking out the pinned tag, obeying the safety toggle, and claiming the day so Cloud Scheduler and GitHub's crons can never both sync. | ✅ |
| 2026-09-24 | 0.5 | **[reliability]** Six-step setup document for the timed trigger, written to be followed without technical background and formatted to print. | ✅ |
| 2026-09-26 | 0.5 | Scoped the change-timestamp column before building: confirmed a new column is carried and cannot make a row look changed again, that formatting is keyed by column NAME not position, and where it must sit to avoid the column-shift fault this sheet has had. Three decisions surfaced for the team. No code written. | ✅ |
| 2026-09-28 | 1.25 | **[reliability]** **Watchdog built, tested and live.** A sync that runs and fails is already visible; a sync that never runs is silent, and that gap was open. It reads the completion record the sync already writes — not the sheet's 'last modified', which any crew member ticking a box would update — and fails on purpose when the deadline passes without a completed run, so the existing email carries the news. Ten tests, most of them about staying SILENT, because a monitor that cries wolf gets muted. Proved live in both directions, and a drill confirmed the email actually reaches Chris. Also confirmed the Google Cloud project the system runs in, and corrected a permissions check that reported a healthy setup as broken. | ✅ |

| 2026-09-29 | 0.5 | Asked the API which card colours it will accept rather than guessing, by sending one deliberately invalid colour and reading the refusal -- the rejection enumerates the palette, so the request being REFUSED is the point and nothing is ever created. Sorted the answer by hue and lightness, because a client request arrives as "dark blue" and "light green" and a bare list of hex codes does not answer that. Card colours set from it: turnovers dark blue, every other clean palest green. Red deliberately avoided -- the team colour their own day-off cards red, so a red job card reads as a day off at a glance. | ✅ |

| 2026-09-30 | 0.75 | **1401 Carondelet merged.** Two Guesty listings ("1401 Caron U V1" and "1401 Caron A U V2") were producing two properties that appeared never to overlap and to hand off to each other -- one turnover shown as two unrelated jobs, twice in the window. Keyed the alias on the Guesty LISTING ID rather than the nickname, so it survives the renames this account does mid-booking, and so it cannot over-reach: 1409, 1413, 1417 and 1421 Carondelet A/B are genuinely separate flats in the same building, and a rule that stripped a trailing letter would have sent a cleaner to the wrong door. Seven tests, most of them guarding the over-reach. | ✅ |

| 2026-09-30 | 0.75 | **1802 Martin Luther King brought onto the Austin board.** Creating it was refused -- Connecteam Job titles are unique account-wide and the record already existed -- so the board was added to the Job's `instanceIds` instead, which grants Austin the property and takes it from nobody. Since PUT replaces the record, the Job is read whole, only its board list changed, written back and re-read field by field. Austin now reports **0 properties missing a Job**: without this, that property's cleans produced no card at all, silently. Job creation and sharing both now require an explicit `--market-board` flag rather than riding on `--confirm`. | ✅ |

| 2026-09-30 | 0.25 | Turnover colour changed to light royal blue at the client's request, replacing the dark blue set the day before. Recorded in the code what the change costs -- the dark blue separated turnovers from ordinary cleans by 142 in RGB terms and the lighter one by 90, against a green of nearly the same lightness -- so the trade-off is written down once and not re-argued. The colour test now pins the PAIRING (blue on turnovers, green on everything else) rather than the exact hex, since the hex is the client's to move and the pairing is the part that must never flip. *Reworking my own first proposal absorbed; only the client's change billed.* | ✅ |

| 2026-09-30 | 0.5 | **Found that the Connecteam palette is 33 colours, not 19.** The client truncated API error bodies at 400 characters, and the rejection that enumerates the palette is longer than that -- so fourteen colours, including all three greys, were unreachable. Worse, the probe written to verify the colour constant read the same truncated string and reported "code says 19, API says 19", which looked like confirmation and was the same error twice. Chris saying the picker shows black, dark gray and light gray is what exposed it. Limit raised and the rejection now prints verbatim before anything parses it. | ✅ |

| 2026-09-30 | 1.75 | **Cancellations painted light gray (#969696), Chris Test only.** Two halves. New cards: a cancellation is not in any cell's VALUE -- it is strikethrough on the row -- so the push now reads the tab's format as well as its contents, and cancelled beats turnover, because a cancelled turnover is not urgent work, it is not work. Existing cards were the harder half: the push only ever created and skipped what was already there, so a card created green stayed green after its booking was cancelled. Took four rounds of probing against a throwaway card to establish the update call, none of it documented or guessable -- v2 not v1 (v1 refuses to edit an open shift, and every card we make is one), `shiftId` not `id`, only the half of the compound id before the colon, and `openSpots` omitted. The card is written back whole with one value changed, the board re-read afterwards, and any card accepted-but-unchanged reported, since one probe returned HTTP 200 and did nothing at all. Gated to the test board by a switch separate from the card lock, so a market board cleared for cards one day does not silently inherit this too. Fourteen tests. | ✅ |

| 2026-10-06 | 0.25 | Picked the integration back up: verified the nightly is green and beating the 06:45 deadline, read both boards back, and re-checked Job coverage per board. Found and fixed a test that had started failing on 1 October -- it fixed its data at September 2026 and expected a tab to be auto-created, which the sync correctly refuses for a month that has ended. A test rotting rather than a bug appearing, pinned so the suite does not fail with the passage of time and hide the failure that matters. Also found the 1401 Carondelet merge is forward-only: the alias applies when a booking is re-derived, and Property is not one of the four fields that mark a row as changed, so rows already in the sheet keep the old name and the unit still appears twice. Backfill needed. | ✅ |

| 2026-10-07 | 2.5 | **Stage 1: the board now reconciles against the sheet instead of only being added to.** Rolling 45-day window (a calendar window is one day deep on the 30th for the month the crews are about to work), spanning two month tabs and sometimes three, read one at a time because strikethrough positions are per worksheet. Identity moved from the SLOT to the Confirmation Code after Chris corrected the rule: a booking whose time changes stays green and keeps its own card; only cancelled or removed bookings go grey. That correction exposed a trap no amount of care with strikethrough could have avoided -- the sheet strikes a row for `moved` as well as for `cancelled`, so in the format those two are identical and only the booking's code tells them apart. Card identity is held in the state bucket and treated as a hint, always checked against the board. 48 tests across two new suites. | ✅ |

| 2026-10-07 | 1.0 | **Four faults found by running it on the test board, none of which a test would have caught.** Every update against a real card was being refused -- a real card carries a location derived from its Job and the API rejects it, while the probe reported success because its card had no Job and so no location. Established by canary that the update MERGES rather than replaces, so it now sends only the fields that changed: nothing the server refuses can be sent, and no note or task the team added can be dropped. Then: the change set was the whole desired payload rather than the diff (caught by its own new guard, first time it ran); the title was never compared, so a booking that became a turnover went blue and stayed titled "Clean"; and the read-back looked for a moved card where it used to be, reporting a false failure for a move that had worked. Board left internally consistent -- 49 green cards, 49 "Clean", 18 blue, 18 "Turnover". | ✅ |

| 2026-10-07 | 0.75 | **Answering "can you guarantee existing cards are not touched?" with code rather than with intent.** A card must now be ours on BOTH its title and its colour before anything can adopt, move, recolour or grey it; Austin's own cards are titled in the crews' Spanish and carry no colour at all, so they fail twice. The consequence is a test rather than a claim: on a first live run against a market board nothing passes both tests and the map is empty, so there is no card the system is ABLE to modify -- creates are the only outcome available to it. Verified read-only against the live Austin board, which holds 6 cards inside the window and would take 67 creates and 0 changes. | ✅ |

| 2026-10-07 | 0.75 | **An undo.** A live run now stores the board exactly as it was before touching anything -- cards whole, because a summary cannot be restored from -- as a dated record that is never overwritten plus a `latest` pointer. `card_restore` reads it back and fixes the difference: cards the push created are deleted, cards it changed are put back, and a card missing from the board is reported rather than recreated. It touches only cards that could be ours, because between the snapshot and the restore the team may have edited their own board, and reverting THEIR work is a worse accident than the one being undone. Plus a selectable window, so a cautious first push can be three days and five cards instead of 45 days and 67. | ✅ |

| 2026-10-07 | 0.5 | **[reliability]** Automated the push onto the nightly, Chris Test only, and found that the workflow file and the pinned code come from different places: a step added to main today would have run against code tagged weeks earlier, pushing cards in the old colour with no window and no reconcile. The step now asks the checked-out code whether it is the code the step was written for. Separately, an edit deleted `connecteam_push`'s entry point, so the script imported, defined main(), called nothing, printed nothing and exited 0 -- reported as a SUCCESS by GitHub while 35 test suites passed, because none of them starts a tool the way the workflow starts it. That blind spot is now covered by reading the tool list out of the workflow files. | ✅ |

| 2026-10-07 | 0.5 | **1401 Carondelet merged across the whole workbook** -- 54 rows in seven tabs now carry the one name, so the unit is one property on the schedule and resolves to one Job instead of two. Matched on an exact name rather than a pattern, because 1405, 1409, 1413, 1417 and 1421 Carondelet A/B are real separate flats and a looser rule would send a cleaner to the wrong door; a guard refuses any entry that maps between two different street numbers. *The off-by-one below, and its repair, are absorbed.* | ✅ |

| 2026-10-07 | 0.0 | **ABSORBED -- my error.** The first run of that cleanup wrote all 54 cells one column to the LEFT of Property, into `assigned`, which is a checkbox the team ticks: a 0-based column index was handed to a 1-based column-letter helper. It reported "Merged 54 of 54" and changed nothing it meant to, caught only because the verify re-run still found all 54 rows. Recovered the prior values from the morning's own snapshot artifact -- 25 provably FALSE, none TRUE, 29 with no record -- restored all 54 to a real boolean FALSE, then applied the merge to the right column and confirmed both. `bad_units.py` carried the identical bug and was fixed too. Added `sheet_header`, which prints every column with the letter it actually lives in, because the two conventions were nowhere written down and that is what made the fault invisible. | ✅ |

**Total to date: 29.0 h**

*Invoiced: RCI-2026-02 (12.0 h) and RCI-2026-03 (4.5 h) = 16.5 h.*

**Held for final billing: 12.5 h.** Chris' instruction on 2026-10-06 — account for
these now, invoice them once the Connecteam integration is complete, as one
closing invoice rather than a third interim one. The 15% family rate applies as
before.

| Held | Hrs |
|------|-----|
| 2026-09-26 timestamp-column scoping | 0.5 |
| 2026-09-28 watchdog **[reliability]** | 1.25 |
| 2026-09-29 colour palette established | 0.5 |
| 2026-09-30 1401 Carondelet merge | 0.75 |
| 2026-09-30 1802 Martin Luther King Job | 0.75 |
| 2026-09-30 turnover colour change | 0.25 |
| 2026-09-30 palette found to be 33, not 19 | 0.5 |
| 2026-09-30 cancellations light gray | 1.75 |
| 2026-10-06 state review, rotted test, Carondelet finding | 0.25 |
| 2026-10-07 Stage 1: reconcile by booking, rolling window | 2.5 |
| 2026-10-07 four faults found on the test board | 1.0 |
| 2026-10-07 the untouched-cards guarantee | 0.75 |
| 2026-10-07 snapshot, undo, selectable window | 0.75 |
| 2026-10-07 nightly automation **[reliability]** | 0.5 |
| 2026-10-07 1401 Carondelet merged workbook-wide | 0.5 |

Still to come before that invoice closes: the rolling push window, reconciling the
board against the sheet (a vanished booking currently leaves its card for ever),
the moved-booking case, automating the push after the nightly sync, the Carondelet
backfill, and the two larger markets' Jobs.

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
- ~~Austin Jobs missing~~ — **done 2026-09-30**: 1802 Martin Luther King shared onto the Austin board; Austin now reports **0 properties missing a Job**
- ~~Delete path~~ — **done 2026-09-21**; used since to clear the `PROBE` shifts, the 37 old-design cards, and 46 cards in the old turnover colour
- ~~Decide where the first market write happens~~ — **done**: Austin, on Chris' call
- ~~Card colours~~ — **done 2026-09-30**: turnover light royal blue, clean palest green, cancelled light gray
- ~~Update a card already on the board~~ — **done 2026-09-30**: `PUT /scheduler/v2/.../shifts`, established by probing; used for the cancelled colour
- Verify how the card renders on a crew phone now the title is Clean/Turnover and the place is the Job
- **Rolling push window** — nothing yet decides which dates get pushed; every push names a month by hand. *Blocks automation.*
- **Reconcile the board against the sheet** — the push only creates and recolours. A booking that VANISHES from the sheet leaves its card on the board for ever. *The one gap that makes a crew board unsafe.*
- **A moved booking** — a changed checkout time creates a second card and orphans the first, because cards are matched on (Job, title, start time)
- Link each sheet row to its card, so a later run can find it without re-deriving the match
- Automate the push after the daily sync
- 1401 Carondelet backfill — the merge is forward-only; rows already in the sheet keep the old name
- Should a cancelled card's TITLE say so, not only its colour (it still reads "Clean")
- Same-slot collisions — a cancelled booking and its replacement at the same property, date and time produce two cards, one gray and one green
- 3 legacy cards on Chris Test from 13 September — assigned to somebody, so the delete guard refuses them; they need unassigning in the UI first
- Jobs still missing: 22 properties on the Savannah/Thunderbolt board, 27 on the New Orleans/Bay St. Louis board
- Extend from Austin to the other four markets — Savannah/Thunderbolt, then New Orleans/Bay St. Louis
