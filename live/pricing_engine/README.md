# pricing_engine: a copy of the model/ fair-value engine

`engine.py`, `fair.py`, `data.py`, `sessions.py` and `empirical.py` are copies
of the files of the same name in `model/`, so that `live/` runs on its own: it
imports nothing from `model/`, and a change to the replays does not change what
live trading does until it is copied across.

The only edit is the repository path in `data.py` (this folder is one level
deeper). `live/tests/test_pricing.py` compares each copy with `model/` as text
and fails when they differ, so drift is a decision, never an accident. Without
`model/` present the check is skipped.

History: first copied at commit `f608b4f` (2026-10-17); re-copied the same
day for the New York-time session filter in `data.in_session`.

**When `model/` changes one of these files**, the test fails. Either copy the
file again (`cp model/<name>.py live/pricing_engine/` and, for `data.py`,
re-apply the path lines), or keep live as it is and mark the test skipped for
that file with the reason.
