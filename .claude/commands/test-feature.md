---
description: Writes and runs tests for a specific CamelTrunk feature. Pass the spec name as argument e.g. /test-feature 05-carrier-sync
allowed-tools: Bash(python manage.py test)
---

Run the full testing pipeline for the feature specified
in $ARGUMENTS.

If no argument is provided, stop immediately and say:
"Please provide a spec name. Usage: /test-feature
<spec-name> e.g. /test-feature 05-carrier-sync"

If `.claude/specs/$ARGUMENTS.md` does not exist, stop
immediately and say:
"Spec file not found at .claude/specs/$ARGUMENTS.md.
Please check the spec name and try again."

---

## Step 1: Write Tests

Invoke the **locker-test-writer** subagent with the
following context:

- Spec file to base tests on:
  `.claude/specs/$ARGUMENTS.md`
- Source files to read for structure: the relevant
  app(s) under `apps/` named in the spec's "App(s)
  touched" section (e.g. `apps/locker/models.py`,
  `views.py`, `urls.py`), plus `indiabox/mixins.py` for
  ownership-mixin conventions
- Output test file to create:
  `apps/<app>/tests/test_$ARGUMENTS.py` — resolve
  `<app>` from the spec's "App(s) touched" section; if
  the feature spans multiple apps, create one test file
  per app, each scoped to that app's slice of the feature
- Instruction: Write tests based on what the spec says
  the feature SHOULD do. Do NOT derive test logic from
  reading the implementation. Cover happy paths, edge
  cases, auth guards, ownership guards (404-not-403 via
  `ObjectOwnershipRequiredMixin`/`UserOwnershipMixin`/
  `LockerOwnershipMixin` where applicable), validation
  errors, and DB side effects.

Wait for locker-test-writer to fully complete and
confirm the test file(s) have been written before
proceeding to Step 2.

---

## Step 2: Run Tests

Once locker-test-writer has finished, invoke the
**locker-test-runner** subagent with the following
context:

- Test file(s) to execute:
  `apps/<app>/tests/test_$ARGUMENTS.py` (as created in
  Step 1)
- Spec file for context:
  `.claude/specs/$ARGUMENTS.md`
- Source files to analyze against when diagnosing
  failures: the same app(s) under `apps/` used in Step 1
- Run command:
  `python manage.py test apps.<app>.tests.test_$ARGUMENTS -v 2`
- Instruction: Run ONLY the specified test file(s). Do
  NOT run the full test suite. Analyze any failures by
  cross-referencing the test code, the spec, and the
  source files. Classify each failure as a bug or a
  missing feature.

---

## Handoff Rules

- Do NOT start Step 2 until Step 1 is fully complete
- Do NOT attempt to fix any code regardless of what
  the test results show
- Do NOT run any tests beyond the file(s) written in
  Step 1
- If locker-test-writer reports it could not write
  the test file, stop and report the reason — do NOT
  proceed to Step 2

---

## Final Output

After both subagents complete, produce a combined
summary:

### Testing Pipeline Report — $ARGUMENTS

**Step 1 — Tests Written**
- List each test written with a one-line description
  of which spec requirement it validates

**Step 2 — Test Results**
- Mirror the locker-test-runner's structured report

**Verdict**
One of:
- ✅ Ready for code review — all tests pass
- ❌ Needs fixes — list the failing tests and their root causes
