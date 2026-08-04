---
description: Runs the 6-point production-readiness checklist
  (rate limiting, input validation, secrets, dependency
  vulns, error handling, file upload safety) as parallel
  subagents that fix issues directly in the codebase.
---

Run all 6 production-readiness checks below as parallel
subagents against the current codebase. Each subagent
both audits AND fixes issues it finds — this is not a
report-only review.

## Pre-flight

Run `git status` and `git diff --stat` so you know the
repo's current state before subagents start touching
files. If there are large uncommitted changes already,
warn the user once before proceeding (don't block).

---

## Step 1: Parallel Fix Agents

Invoke all 6 subagents simultaneously — do not run them
one after another. Give each the full repo context
(this is a Django app, `apps/` for feature apps,
`indiabox/` for settings/middleware/validators) plus its
specific prompt below verbatim:

**1. Rate limiting**
Add rate limiting appropriate to each endpoint type:
stricter limits on authentication routes (e.g. login,
signup, password reset), moderate limits on public
endpoints, and looser limits on authenticated user
actions. For auth routes, use a combination of per-IP
and per-account limits with exponential backoff rather
than a hard lockout. Make all thresholds configurable,
not hardcoded. (Note: `indiabox/middleware.py` already
has `RateLimitMiddleware` and `LoginAttemptMiddleware` —
extend/tune those rather than building a parallel system.)

**2. Input validation**
Validate every input against a strict schema (type,
length, format) and reject anything that doesn't match —
don't just sanitize/escape. (Note: `indiabox/validators.py`
already holds shared validators — extend that rather than
scattering new ones per view.)

**3. Secrets**
Scan the complete codebase for any hardcoded API keys,
tokens, or passwords. Use environment variables and
verify that nothing sensitive is shipped into the
frontend or pushed to git.

**4. Dependency vulnerabilities**
Run a dependency audit across the project. Identify any
packages with known vulnerabilities, list their severity,
and update or replace them where safe to do so.

**5. Error handling & information leakage**
Review all error handling across the app. Ensure users
never see stack traces, internal file paths, or raw
database errors — return generic messages instead, while
still logging full error details server-side for
debugging (the `security` logger is already wired up in
settings — use it).

**6. File upload safety**
Review any file upload functionality (parcel images, KYC
docs — see Supabase Storage upload handling). Confirm
file type, size, and content are validated (not just the
extension), uploads are stored outside the web root or in
isolated storage, and uploaded files can never be executed
as code.

Each subagent should make the actual code changes needed,
not just describe them.

---

## Step 2: Unified Report

Once all 6 subagents finish, combine their results into
one report:

Production Readiness Report

For each of the 6 checks:
- What was found
- What was changed (files touched)
- Anything flagged but NOT auto-fixed (needs human
  judgment — e.g. rotating a leaked secret, replacing a
  dependency with no safe upgrade path) and why

Overall status: READY / NOT READY, with the not-fixed
items as the blocking list.

---

## Rules
- All 6 subagents run in parallel, not sequentially
- Each subagent fixes issues in its own area only — don't
  let the rate-limiting agent touch upload code, etc.
- If a fix requires a judgment call with real consequences
  (rotating a credential, swapping a dependency with a
  breaking API), the subagent should flag it in the report
  instead of guessing
- If any subagent fails or returns nothing, say so in the
  report — don't present a partial run as a clean pass
- Do not push or deploy anything — this command only edits
  local files
