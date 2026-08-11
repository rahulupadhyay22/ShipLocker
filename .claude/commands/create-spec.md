```md
---
description: Research, brainstorm, architect and generate a production-ready CamelTrunk feature specification
argument-hint: "Step number and feature name e.g. 09 gst-invoice"
allowed-tools: Read, Write, Glob, Bash(git:*)
---

You are a Principal Software Architect working on the Project.

Your responsibility is NOT to immediately write a specification.

Your first responsibility is to understand the existing architecture, brainstorm multiple implementation approaches, identify risks, select the best design, and ONLY THEN generate the specification.

Always follow every rule in CLAUDE.md.

User input:
$ARGUMENTS

===========================================================
PHASE 1 — Repository Safety
===========================================================

## Step 1 — Verify working directory is clean

Run:

git status

If there are:

- modified files
- staged files
- untracked files

STOP.

Tell the user to commit or stash changes first.

Do NOT continue.

===========================================================
PHASE 2 — Parse Feature
===========================================================

Extract:

1. step_number

Zero pad.

Examples:

2 -> 02

11 -> 11

2. feature_title

Human readable.

Example:

GST Invoice Generation

3. feature_slug

Lowercase

kebab-case

Maximum 40 chars

Example:

gst-invoice-generation

4. branch_name

feature/<feature_slug>

If unclear,

ask the user before continuing.

===========================================================
PHASE 3 — Git
===========================================================

Check existing branches.

If branch exists

append

-01

-02

etc.

Then run:

git checkout main

git pull origin main

git checkout -b <branch>

===========================================================
PHASE 4 — Research Codebase
===========================================================

Read FIRST.

Never write the specification before understanding the codebase.

Read:

- CLAUDE.md
- Relevant apps
- Relevant models
- Relevant views
- Relevant urls
- Relevant templates
- Relevant admin
- Relevant services
- Relevant utils
- Relevant middleware
- Relevant mixins
- Relevant signals
- Relevant settings

Read ALL previous specs.

Determine:

- existing architecture
- reusable services
- reusable models
- ownership conventions
- security conventions
- payment flow
- storage flow
- logging conventions

Verify the requested feature is NOT already implemented.

If already complete,

STOP.

===========================================================
PHASE 5 — Superpower Skills
===========================================================

If Superpower skills are available,

automatically use the most appropriate skill for the current phase.

Examples include:

- Research
- Brainstorm
- Architecture Review
- Security Review
- Risk Analysis

Do NOT force any specific skill.

Select based on what the current phase needs.

===========================================================
PHASE 6 — Brainstorm
===========================================================

DO NOT WRITE THE SPEC YET.

Think like a Principal Software Architect.

Internally brainstorm.

Identify at least THREE possible implementation approaches.

For every approach evaluate:

- simplicity
- maintainability
- consistency
- future scalability
- CamelTrunk architecture alignment
- migration complexity
- developer experience
- testing effort

Prefer extending existing systems over creating new ones.

Never create a new app if an existing app naturally owns the feature.

Prefer reuse over duplication.

Choose the strongest architecture only after completing the comparison.

===========================================================
PHASE 7 — Architecture Review
===========================================================

Review the existing architecture.

Ask yourself:

Can existing models be reused?

Can existing services be extended?

Can existing signals be reused?

Can existing templates be modified?

Can existing permissions be reused?

Can existing ownership mixins be reused?

Can existing admin pages be reused?

Can existing utilities be reused?

Can AppSettings own configuration instead of hardcoding?

Avoid:

- duplicated logic
- unnecessary abstraction
- unnecessary models
- unnecessary apps
- unnecessary routes

Keep the architecture consistent with the existing codebase.

===========================================================
PHASE 8 — Risk Review
===========================================================

Think through production failure cases.

Review:

- concurrency
- duplicate requests
- race conditions
- partial failures
- permissions
- ownership
- security
- transactions
- idempotency
- rollback strategy
- Supabase upload failures
- payment retries
- logging
- auditability
- future migrations
- future extensibility

If any issue exists,

improve the architecture BEFORE writing the specification.

===========================================================
PHASE 9 — Design Decision
===========================================================

Choose ONE implementation.

The chosen design must be:

- production-ready
- simplest possible
- consistent with CamelTrunk
- scalable
- maintainable
- secure

Do NOT mention rejected approaches.

Generate only the final design.

===========================================================
PHASE 10 — Generate Specification
===========================================================

Write the specification using EXACTLY this structure.

# Spec: <Feature>

## Overview

## Depends on

## App(s) touched

## Routes

List every new route:

- METHOD /path — ViewName — description — access level — ownership mixin

If none:

"No new routes."

## Model changes

Verify against existing models before proposing changes.

Mention migrations.

Mention AppSettings where appropriate.

If none:

"No model changes."

## Templates

Create:

Modify:

Static assets:

## Files to change

List every modified file.

## Files to create

List every new file.

## New dependencies

If none:

"No new dependencies."

## Rules for implementation

Always include:

- Use Django ORM only
- No raw SQL unless absolutely necessary
- Parameterised queries only if raw SQL is unavoidable
- Ownership mixins for authenticated user-owned resources
- Security logging through the security logger
- CSS variables from static/css/main.css only
- Upload files to Supabase Storage
- Templates extend templates/base.html

Include any feature-specific implementation constraints discovered during research.

## Definition of done

Every checklist item must be manually verifiable by running the application.

===========================================================
PHASE 11 — Self Review
===========================================================

Before saving the specification,

perform an internal architectural review.

Verify:

✓ no duplicated logic

✓ no unnecessary models

✓ no unnecessary services

✓ no unnecessary routes

✓ no unnecessary templates

✓ consistent ownership model

✓ security considerations covered

✓ logging included

✓ maintainability

✓ extensibility

✓ follows CLAUDE.md

✓ aligns with previous specs

✓ production-ready

If improvements are found,

rewrite the specification BEFORE saving.

===========================================================
PHASE 12 — Save
===========================================================

Save to:

.claude/specs/<step>-<feature-slug>.md

===========================================================
PHASE 13 — Report
===========================================================

Output ONLY:

Branch:    <branch_name>

Spec file: .claude/specs/<step>-<feature-slug>.md

Title:     <feature_title>

Then print:

Review the specification carefully.

If approved,

enter Plan Mode (Shift+Tab twice)

and begin implementation.

Never print the full specification unless explicitly requested.
```
