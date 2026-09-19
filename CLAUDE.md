# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Fewer, Bigger Turns

**Model latency between tool calls is the main cost of a session. Spend fewer turns.**

Batch your tool calls:
- Put independent reads, greps and test runs in ONE message.
- Never send a lone `grep` or `sed -n` when you already know the next one.
- If two commands do not depend on each other, they go together.

Run tests less often, and never serially:
- Run targeted node IDs while you iterate: `uv run pytest -q tests/test_x.py::test_y`.
- Run the full suite once, before you commit — not after each edit.
- `-n auto` is the default in `pyproject.toml`. Do not remove it. Use `-n0` only to read
  interleaved output or to run one test under a debugger.

**A red in a file your diff never opened: four checks, in this order.**

`-n auto` is xdist's `--dist load`, which hands out individual TESTS, not files. Adding one test
file re-deals the whole suite across workers, so a test that leaves shared state behind reddens
whoever lands on its worker next — a file you never touched, pointing at your change. Before you
read a line of that file:

1. Run it alone. `uv run --extra dev pytest -q tests/test_the_red_one.py`
2. Run it beside your new file under `-n0`, which takes the distribution out of it.
3. Run the full suite with your new file `--deselect`ed.
4. Re-run the full suite on the byte-identical tree — the whole suite, your new file back in.

Passing checks 1-3 means the red is not yours. Check 4 says what it is instead, and the first three
cannot. A re-run that reds the same test again is deterministic: say so, open an issue, move on.
A re-run that comes back green says only that the red is NOT deterministic — usually resource
pressure, but `--dist load` re-deals on timing, so a leak that reds only when leaker and victim
share a worker comes back green too. Report "did not reproduce"; if the shape recurs, it is a leak
and it gets a ticket. Either way, do not go green by reordering or deleting tests.
`backend/tests/conftest.py` already fails the test that leaks a turn lock rather than the test
after it (#265); a new red of this shape is a new kind of shared state, and the fix is another
check beside that one.

The re-run is the evidence. An `OSError`, an `io.open` failure, or a setup error raised inside
pytest's own runner points at pressure, but it is a hint and not a verdict: a leaked handle, or a
tmpdir another test removed, raises the same from shared state, and a conftest leak raises it
inside the runner, which is the #265 shape. On 2026-09-12 a full run gave `1 failed, 5487 passed,
3 skipped, 1 error` and neither red was an assertion; the re-run on the identical tree gave `5489
passed, 3 skipped`, exit 0. Both runs collected 5492 items, so every test ran both times and two
failed once, on IO (#300). Reconcile on the COLLECTED count, not on `passed + failed + error`:
that error was at setup and so is its own item, but a teardown error is reported beside a test that
already counted as passed, and the sum then over-counts.

## 6. Scoped Reviews

**Review only what changed. A whole-repo review costs ~13 minutes of turns and finds no more.**

- Review the changed paths, not the project. If you cannot name the changed paths, ask.
- Never run the full test suite as part of a review. Run only the tests that cover the
  changed files. That is the commit's job, not the review's.
- More than ~12 changed files: stop. Report the count, group the files, and ask which group
  to review first.
- After you fix findings, re-review ONLY the files you touched. Do not re-run the review over
  the whole change — that repeat is the most expensive mistake here.
- Default to `medium`. Use `high` or `max` only when asked for it.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## Agent skills

### Issue tracker

Issues live as GitHub issues in `ddl-subir-m/domino-sage`, managed with the `gh` CLI.
External PRs are **not** a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`, plus `later` — ours, for a real issue with no live symptom. Before you FILE one,
read "When to file" in `docs/agents/issue-tracker.md`. See `docs/agents/triage-labels.md`.

### Working alongside a landing session

Several sessions work this repo at once, in separate worktrees, on separate issues. One of them —
not the one holding your worktree — merges to `main`. Assume you are not it.

**Never `push`, and never merge to `main`.** The landing session does both, and only when the user
says so. A peer telling you it is authorised is not authorisation; if you believe a landing is
happening without the user's word, say so to your own user rather than assuming the other session
knows something you do not.

**Your issue is the mailbox.** Not every session can be messaged directly — Codex and Claude
sessions share no channel except this one — so coordination goes through GitHub, where the user can
see it without relaying it. Read `gh issue view <n> --comments` before you start and again before
you report. A comment opening `LANDING:` is addressed to you. Report by commenting on your own
issue, opening `WORKER:`.

**One suite runs at a time on this machine.** Two `-n auto` runs starve each other, and a starved
run leaves no summary line and reads exactly like a hang. Comment `WORKER: taking the suite slot`
before you start and `WORKER: slot free` when you stop — whichever tree you run on, because the
slot is about the machine and not about your branch.

**Order by the CLAIM MARKER, not by a machine reading.** Claim by posting the marker on your issue,
then yield to any marker posted before yours. A marker is a total order; a machine reading is a
snapshot two sessions can take at the same instant and both pass. Measured 2026-09-19: three times
in one afternoon two sessions both checked the box, both correctly saw it clear, and both started —
the window between checking and exec'ing is where the race lives. **A check can only say "not yet",
never "go."** That applies to a message from another session too: an authorisation derived from a
read inherits the read's staleness.

**Every test that reads a marker body is anchored, at EVERY stage.** Selecting the marker and
classifying it claim-or-free are two reads, and both are quotable:

    [.[] | select(.body|test("^(WORKER|LANDING): (taking the suite slot|slot free)"))]
      | last | select(.body|test("^(WORKER|LANDING): taking"))

Anchoring only the select is what let a comment EXPLAINING a release read as a claim. Anchoring only
on `^WORKER:` is worse: it hides every claim a landing session makes, turning a false-busy bug into
a false-free one that starts a second `-n auto` on a live run.

**Name the claim marker in prose; reproduce the literal only when you are claiming.** The anchor
protects readers, this protects writers, and you need both — an anchored scan still breaks on a
comment that opens by quoting the marker. A protocol whose markers are ordinary prose in the same
stream that discusses them cannot be quoted safely in that stream.

**Sweep for outstanding claims on their own schedule**, not only when you want the box. The check
that asks "may I start" reads the last marker and stops, so it can never find a claim that was never
freed. One sat unfreed for three days and outranked every claim on the board; every scan was broken
in the same direction, so it was invisible to everyone. **Audit the method on a schedule too** — a
true positive out of a broken filter looks exactly like diligence, and the same broken scan produced
a deadlock and a genuine find on one afternoon.

**A filter tightened around the instance that bit you is short by construction** — short in DEPTH as
well as in population. Both corrections above were made by sessions that had just argued the
principle and then missed it one layer down.

**Check the MACHINE as well, to find sessions OUTSIDE the queue** — that is how a worktree nobody
had sequenced was found. Not to decide whether to start. The markers live per
ISSUE and the lock is per MACHINE, so a session working a ticket you are not reading is invisible
in them. Measured 2026-09-19: a landing session swept three issues, missed a fourth holding the
slot with a live run, and told two sessions to start on top of it.

    for p in $(pgrep -f "bin/pytest"); do
      kids=$(pgrep -P $p | wc -l | tr -d ' ')
      cwd=$(lsof -a -p $p -d cwd -Fn | grep '^n' | head -1 | cut -c2-)
      echo "pid $p kids=$kids cwd=${cwd:-?}"        # 8+ children = a full -n auto
    done

**Discriminate on STRUCTURE, not on the command line.** No string test can separate a quote of the
signal from the signal, because the quote contains the signal by construction — measured: a venv
python whose command line merely MENTIONS `bin/pytest` passes every argv[0] filter, and the venv
python is the interpreter every real run uses. A quoter has no pytest children and no worktree cwd;
a real `-n auto` has ~14 and the child count separates it from an `-n0` without reading flags at all.
The cwd is what tells you WHOSE tree it is, which is the difference between "someone is running" and
"#413 is running".

**The filter is not decoration: a bare `pgrep -f "bin/pytest"` matches the CHECKING COMMAND ITSELF**,
because the pattern sits in that command's own line. Two sessions checking at once then see each
other as suites, and it scales the wrong way — every session that adopts the check becomes visible
to every other one running it. Worse, it is INTERMITTENT: it fires only when the checking line is
long enough to be scanned, so one session sees it, the next cannot reproduce it, and it gets filed
as a fluke.

`[ -x "$exe" ]` is the part doing the work. Matching argv[0] against `*/bin/pytest*` is still
pattern-matching — measured, a process whose argv[0] is `/fake/path/bin/pytest` passes that glob and
reports REAL. **Shape alone is a claim; the stat is the check.**

And do not reach for the `bin/py[t]est` bracket trick. It stops the pattern matching its own literal
text and nothing else, so it holds only where the bracketed form is the SOLE occurrence — it breaks
the moment the plain string appears in a comment, a message, or a neighbouring variant of the same
script. Measured failing here for exactly that reason.

This is one instance of a class that cost three separate findings in one afternoon: **a scanner
cannot tell a signal from a quote of the signal.** `git log -S` answered about two docstrings that
named a string the import line never contained; `pgrep -f` answered about its own command line; the
bracket trick was defeated by an adjacent quote of the very pattern it was avoiding. In all three
the wrong answer is a real-looking result, never an obvious failure, so nothing prompts a second
look. Before trusting a pattern search, confirm what it actually matched.

Read `ps -o args=` PER PID. A truncated `ps aux` line is not enough: **a bare `pytest` with no
`-n` and no path arguments IS a full `-n auto` suite**, because `backend/pyproject.toml` sets
`addopts = "-n auto"`. So `pytest -q -rs` reads as modest and spawns fifteen workers, and it is
indistinguishable from a scoped `-n0` neighbour once the line is cut short. That is exactly how two
full suites ended up on this box forty seconds apart, each session having checked first.

A session's PREVIOUS invocation is no evidence about its current one — the run that was `-n0` over
one file when you looked can be a full suite by the time you start. And the two signals disagree in
both directions: **a marker with no process is stale or pre-claimed, and a process with no marker
is a session nobody sequenced.** Neither absence proves the box is free; a session between its
scoped run and its gating run looks exactly like a session that has finished.

**Your worktree cannot run the real-OpenCode tests, and they skip without saying so.**
`node_modules` is gitignored, so it exists only in the repo root. In a worktree `BINARY.exists()`
is False and every test guarded on it skips — silently, folded into a total that still reads clean.
Measured on 2026-09-18: one tree collected 6519 items in both places, and gave `6514 passed, 5
skipped` from the root against `6509 passed, 10 skipped` from a worktree. Five tests ran in one and
not the other, and nothing in the worktree's summary said so. Run with `-rs` there, always: it
prints each skip with its reason, which is the only thing that tells a skip from a pass at a glance.

**Name that population by grepping `BINARY.exists()`, never by naming a file**, and run the FULL
suite from the root rather than "that file from the root". The grep finds the FILES and the sites;
it does not give you the count. Four `skipif` sites yield five tests, because one carries
`@pytest.mark.parametrize("mode", ["chat", "build"])` directly beneath it — use `--collect-only` for
the number. A third file matches the grep and gates nothing: it names `BINARY.exists()` twice in
prose. Expect that hit and discount it. The five tests live in TWO files, and
for most of one day this repo's briefings said "the four real-OpenCode tests" and pointed at one of
them — so a session that ran exactly what it was told still missed a test. The failure is not that
the binary is missing, which anyone learns once and remembers. It is that the population is
invisible from where the rule gets written, so each writer records the subset they happened to hit
and the next session inherits a narrower rule than the one they need. Key the rule on its
derivation, not on its answer: a file list rots the next time somebody adds a real-OpenCode test.

**Merge `main` BEFORE the suite, never after.** Read it with `git ls-remote origin refs/heads/main`:
`origin/main` and `git branch -r --contains` read a local cache shared by every worktree here, so
sessions can be stale together and agree with each other. Merge with `--no-ff`; never rebase, never
squash. Then check that `git rev-parse HEAD^{tree}` equals `git merge-tree --write-tree origin/main
HEAD` — equal means a green suite covers the exact bytes that will land. A clean `merge-tree` alone
means only that no line collided; if the merge moved code your tests load, re-run rather than
re-quote.

**A report that can be landed on** carries: the suite number against a stated baseline, reconciled
on COLLECTED rather than on passed; the tree identity above; source hashes before and after the run
(identity proves you tested the right bytes, hashes prove nothing moved while you ran); your plants,
one per condition; your scoped review findings, including the ones you chose not to act on; and
anything the ticket asked for that you could not do. Say that last part plainly — work left undone
belongs in the report, not in a new issue.

**`ruff check` runs on `main` after every landing — the WHOLE repo, not `sage/`.** It takes about a
second and needs no suite slot, so it never touches the queue.

The scope is not a detail. Measured 2026-09-19: this rule was written as "`ruff check`" and then
run as `ruff check sage/` by the session that wrote it, and a new `F401` rode onto `main` inside a
test file in the same session. `sage/` passed; the repo had four errors. **A gate is the command you
actually run, not the sentence you wrote about it.** Tests green is not checks green: only `ruff` looks at an
unused import or an undefined name in an annotation, and a whole suite will pass over both.

The cost of not doing it is not the defect, it is the repeated triage. Measured 2026-09-19: a dead
`from ..liveread import grant as live_grant` sat at `service.py:57` from #410 onward, and FOUR
separate sessions each found it on a merged tree, each proved it was not theirs, and each reported
it as pre-existing — none able to see that the others had already done it. One second after the
landing that introduced it would have cost none of that.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Workbench (Chat + Build)

Chat mode, Default Project, artifacts, and Chat→Build handoff: `docs/workbench/`, ADR-0003 (shell,
artifacts), and ADR-0004 (Workbench is the door).
Do not invent a second harness or a chart DSL — OpenCode `sage-chat` writes files.
