---
description: Start a milestone from docs/PLAN.md on its own branch
---

Start milestone **$1** from `docs/PLAN.md`.

1. Read the milestone's section in `docs/PLAN.md` — its tasks and its
   "Done when" criterion. Read any ADR in `docs/decisions/` it touches.
2. Confirm the previous milestone is marked `[x]`. If it is not, say so and stop.
3. Create the branch (`m<N>-<short-name>`), and flip the milestone marker to
   `[~]` in `docs/PLAN.md` as the branch's first commit.
4. Build it. Keep to the milestone's scope — later milestones depend on this one
   being finished, not on it being expanded.
5. Verify against the "Done when" criterion and the matching entry in the
   Verification section. Report the actual result, including failures.
6. Only once verification passes: flip the marker to `[x]` and commit.

Do not push or add a remote.
