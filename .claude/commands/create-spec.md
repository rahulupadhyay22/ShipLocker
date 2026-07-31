---
description: Create a spec file and feature branch for the next ShipLocker step
argument-hint: "Step number and feature name e.g. 2 kyc-upload"
allowed-tools: Read, Write, Glob, Bash(git:*)
---

You are a senior developer spinning up a new feature for the
ShipLocker parcel-forwarding app. Always follow the rules in CLAUDE.md.

User input: $ARGUMENTS

## Step 1 — Check working directory is clean
Run `git status` and check for uncommitted, unstaged, or
untracked files. If any exist, stop immediately and tell
the user to commit or stash changes before proceeding.
DO NOT CONTINUE until the working directory is clean.

## Step 2 — Parse the arguments
From $ARGUMENTS extract:

1. `step_number` — zero-padded to 2 digits: 2 → 02, 11 → 11

2. `feature_title` — human readable title in Title Case
   - Example: "KYC Upload" or "Return Requests"

3. `feature_slug` — git and file safe slug
   - Lowercase, kebab-case
   - Only a-z, 0-9 and -
   - Maximum 40 characters
   - Example: kyc-upload, return-requests

4. `branch_name` — format: `feature/<feature_slug>`
   - Example: `feature/kyc-upload`

If you cannot infer these from $ARGUMENTS, ask the user
to clarify before proceeding.

## Step 3 — Check branch name is not taken
Run `git branch` to list existing branches.
If `branch_name` is already taken, append a number:
`feature/kyc-upload-01`, `feature/kyc-upload-02` etc.

## Step 4 — Switch to main and pull latest
Run:
```
git checkout main
git pull origin main
```

## Step 5 — Create and switch to the feature branch
Run:
```
git checkout -b <branch_name>
```

## Step 6 — Research the codebase
Read before writing the spec:
- `CLAUDE.md` — architecture, apps, conventions
- The relevant app's `models.py`, `views.py`, `urls.py` under `apps/<app>/`
  (e.g. `apps/locker/`, `apps/shipments/`, `apps/accounts/`, `apps/kyc/`,
  `apps/payments/`, `apps/notifications/`, `apps/content/`)
- `indiabox/settings.py`, `indiabox/mixins.py`, `indiabox/middleware.py`
  for security/ownership conventions that must be reused
- All files in `.claude/specs/` — avoid duplicating existing specs

Check `CLAUDE.md` and existing specs to confirm the requested step is not
already marked complete. If it is, warn the user and stop.

## Step 7 — Write the spec
Generate a spec document with this exact structure:

---
# Spec: <feature_title>

## Overview
One paragraph describing what this feature does and why
it exists at this stage of the ShipLocker roadmap.

## Depends on
Which previous steps this feature requires to be complete.

## App(s) touched
Which app(s) under `apps/` this feature lives in or spans
(e.g. `locker`, `shipments`, `accounts`, `kyc`, `payments`, `notifications`, `content`).
If it needs a new app, say so and justify it.

## Routes
Every new URL/view needed:
- `METHOD /path` — view name — description — access level (public/logged-in/staff)
  — ownership mixin used (`UserOwnershipMixin` / `LockerOwnershipMixin` /
  `ObjectOwnershipRequiredMixin`) where the view touches user-owned data.

If no new routes: state "No new routes".

## Model changes
Any new models, fields, or migrations needed.
Always verify against the relevant app's `models.py` before writing this.
Note if any config should live in `AppSettings`
(`apps.notifications.models.AppSettings`) instead of hardcoded settings.
If none: state "No model changes".

## Templates
- **Create:** list new templates with their path (under `templates/<app>/`)
- **Modify:** list existing templates and what changes
- Note any new/modified static assets under `static/css/` or `static/js/`

## Files to change
Every file that will be modified.

## Files to create
Every new file that will be created.

## New dependencies
Any new pip packages. If none: state "No new dependencies".

## Rules for implementation
Specific constraints Claude must follow. Always include:
- Use Django ORM only, no raw SQL unless there's no ORM equivalent
- Parameterised queries only if raw SQL is unavoidable
- Any authenticated view touching user-owned data must use one of the
  ownership mixins in `indiabox/mixins.py`, not a hand-rolled check
- Security-relevant actions log through the `security` logger
- Use CSS variables from `static/css/main.css` — never hardcode hex values
- Uploaded files (parcel images, KYC docs) go to Supabase Storage, not
  local `MEDIA_ROOT`
- All templates extend `templates/base.html`

## Definition of done
A specific testable checklist. Each item must be
something that can be verified by running the app
(`python manage.py runserver`) — there is no test suite in this repo,
so verification is manual unless the step explicitly adds tests.

---

## Step 8 — Save the spec
Save to: `.claude/specs/<step_number>-<feature_slug>.md`

## Step 9 — Report to the user
Print a short summary in this exact format:
```
Branch:    <branch_name>
Spec file: .claude/specs/<step_number>-<feature_slug>.md
Title:     <feature_title>
```

Then tell the user:
"Review the spec at `.claude/specs/<step_number>-<feature_slug>.md`
then enter Plan Mode with Shift+Tab twice to begin implementation."

Do not print the full spec in chat unless explicitly asked.
