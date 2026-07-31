---
description: Runs parallel security and quality code
  review for a specific ShipLocker feature. Pass the spec
  name as argument e.g. /code-review-feature 03-return-request
allowed-tools: Bash(git diff), Bash(git diff --staged)
---

Run the full code review pipeline for the feature
specified in $ARGUMENTS.

If no argument is provided, stop immediately and say:
"Please provide a spec name. Usage: /code-review-feature
<spec-name> e.g. /code-review-feature 03-return-request"

## Pre-flight Check

Before invoking any subagents, collect the diff:
- Run `git diff` for unstaged changes
- Run `git diff --staged` for staged changes
- Combine both into a single diff

If both are empty, stop immediately and say:
"No changes detected. Implement the feature before
running code review."

---

## Step 1: Parallel Review

Invoke both subagents simultaneously with the same
context:

**locker-security-reviewer** receives:
- The combined diff from the pre-flight check
- Spec file for context: `.claude/specs/$ARGUMENTS.md`
- Source files to reference: the relevant app(s) under
  `apps/` (per the spec's "App(s) touched" section —
  typically `models.py`, `views.py`, `urls.py`), plus
  `indiabox/mixins.py` and `indiabox/middleware.py`
- Instruction: Review only the changed code for
  security vulnerabilities. Do not comment on quality
  or style.

**locker-quality-reviewer** receives:
- The combined diff from the pre-flight check
- Spec file for context: `.claude/specs/$ARGUMENTS.md`
- Source files to reference: the relevant app(s) under
  `apps/`, plus `templates/` and `static/css/`
- Instruction: Review only the changed code for quality,
  Django best practices, and maintainability. Do not
  comment on security concerns.

Both subagents must run in parallel. Do not wait for
one to finish before starting the other.

---

## Step 2: Unified Report

Once both subagents have completed, combine their
findings into a single unified report. De-duplicate
any overlapping findings — if both agents flagged the
same line for different reasons, merge them into one
finding with both perspectives noted.

Structure the combined report as:
Code Review Report — $ARGUMENTS
Security Findings
[locker-security-reviewer output]
Quality Findings
[locker-quality-reviewer output]
Combined Action Plan
Ordered checklist of everything that needs to be fixed,
prioritized by severity:

[Critical/High security findings first]
[Quality CHANGES REQUESTED items second]
[Medium/Low security findings third]
[Quality APPROVED WITH SUGGESTIONS items last]

Overall Verdict
APPROVED — ready to commit
APPROVED WITH SUGGESTIONS — can commit, address
suggestions in future steps
CHANGES REQUESTED — must fix before committing,
see action plan above
---

## Step 3: Ask for Approval

After presenting the unified report, ask:

"Do you want me to implement the action plan now?"

Wait for explicit user confirmation before making
any changes. Do not touch any files until the user
approves.

---

## Rules
- Do NOT edit any files before user approval
- Do NOT start one reviewer before the other —
  both must run in parallel
- Do NOT skip the pre-flight diff check
- Do NOT proceed if the spec file at
  `.claude/specs/$ARGUMENTS.md` does not exist —
  report it and stop
- If either subagent fails or returns no output,
  report it and do not present a partial review
  as complete
