"""Match a property to the Connecteam Job the card should point at.

The property used to be the shift TITLE. It now goes in the Job field, which
means a shift carries a `jobId`, and that id has to name a Job that already
exists on the board. Verified against the API on 2026-09-21: a title is
REQUIRED and may not be empty, and `endTime` is required and must be after
`startTime` -- so the card keeps a short nominal block rather than none.

Matching cannot be a plain lookup. The account holds ~1429 Jobs typed in by hand
over years:

    1018 Ferdinan / 1018 Ferdinand (x2) / 1018 Ferdinand V2 (x3)
    1026 N Robert (x2) / 1026 N Robertson / 1026 N Robertson V2
    1100 Ursulines  / Heirloom
    1163 Webberville A / 1163 webberville A V2

So names are compared on the part that identifies the place -- case folded,
punctuation dropped, anything after a slash discarded, a trailing V-number set
aside -- and where that still leaves several, the HIGHEST version wins. That is
the team's rule, given on 2026-09-21: a V2 is the current revision of a listing.

Nothing is ever guessed. A property with no Job resolves to None and the caller
reports it rather than inventing one.
"""
from __future__ import annotations

import re
from collections import defaultdict

_VERSION = re.compile(r"\bv([0-9]+)\b", re.I)


def norm(name: str) -> str:
    """The comparable part of a Job or property name.

    Everything after a slash is somebody's note -- a building, a lockbox, a
    contact -- and a trailing V-number is a listing revision, not another place.
    """
    s = str(name or "").strip().lower()
    s = s.split("/")[0]
    s = _VERSION.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def version_of(name: str) -> int:
    """3 for '2901 E 3rd A V3', 0 for a name with no version marker."""
    found = _VERSION.findall(str(name or ""))
    return max((int(v) for v in found), default=0)


def usable(jobs, board: str | None = None) -> list[dict]:
    """The Jobs a card on `board` may actually point at.

    /jobs/v1/jobs answers for the whole ACCOUNT, and two thirds of what it
    returns cannot be used on any given board:

      * `isDeleted` -- 506 of the 1429 on this account are soft-deleted. They
        still come back from the list. Pointing a card at one is refused with
        "job_id ... does not exist", which is what happened on 2026-09-21 and
        sent the whole investigation down the wrong road.
      * `instanceIds` -- the boards a Job belongs to. Austin owns 35, the test
        board owns 3, New Orleans 722. A Job from another board is refused the
        same way.

    Both were being ignored. The Austin push of 2026-09-21 landed only because
    the eight Jobs it happened to pick were live and Austin's.
    """
    out = []
    for j in jobs or ():
        if j.get("isDeleted"):
            continue
        if board is not None:
            ids = {str(i) for i in (j.get("instanceIds") or ())}
            if str(board) not in ids:
                continue
        out.append(j)
    return out


def build_index(jobs, board: str | None = None) -> dict[str, list[tuple[str, str]]]:
    """{comparable name -> [(jobId, raw name)]} for the Jobs `board` can use.

    Pass `board` -- without it the index spans the account and can hand back a
    Job the target board will refuse.
    """
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for j in usable(jobs, board):
        name = j.get("name") or j.get("title") or j.get("jobName") or ""
        jid = j.get("jobId") or j.get("id")
        if name and jid:
            index[norm(name)].append((str(jid), str(name)))
    return dict(index)


def resolve(prop: str, index) -> tuple[str | None, str | None, str]:
    """(jobId, job name, why) for one property. jobId is None when unmatched.

    `why` is for the log: every card that goes out should be able to say which
    Job it chose and on what grounds, because the choice is a rule applied to
    messy data and not an identity.
    """
    key = norm(prop)
    if not key:
        return None, None, "property name is blank"
    hits = (index or {}).get(key) or []
    if not hits:
        return None, None, "no Job on this board matches"
    if len(hits) == 1:
        return hits[0][0], hits[0][1], "one Job matches"
    # Highest version wins. The name and then the id break the tie, because the
    # account really does hold two Jobs with ONE name -- 1018 Ferdinand twice --
    # and a tie settled by whatever order the API answered in would move a
    # property between Jobs from one morning to the next. The id is arbitrary but
    # stable, which is the property that matters.
    best = max(hits, key=lambda h: (version_of(h[1]), h[1], h[0]))
    others = ", ".join(n for _i, n in hits if n != best[1])
    return best[0], best[1], f"{len(hits)} matched, took the highest version (over {others})"


def resolve_all(props, index) -> tuple[dict[str, tuple[str, str]], list[tuple[str, str]]]:
    """(matched {property -> (jobId, why)}, unmatched [(property, why)])."""
    matched, missing = {}, []
    for p in sorted(set(props)):
        jid, _name, why = resolve(p, index)
        if jid:
            matched[p] = (jid, why)
        else:
            missing.append((p, why))
    return matched, missing
