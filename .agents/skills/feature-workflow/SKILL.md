---
name: feature-workflow
description: Safely implement one scoped Job Hunter AI task in a dedicated Git branch. Use for feature, fix, refactor, chore, or documentation work that changes the repository.
---

# Feature workflow

Use this workflow for a single scoped repository task. Do not use it for read-only
questions, planning-only requests, or work the user explicitly asks to perform in
an existing branch.

## Before work

1. Read `AGENTS.md` and any task-relevant documentation.
2. Run `git status` and identify the current branch.
3. Before creating a new branch, confirm that the current branch is `main`.
4. Do not implement directly in `main`. Require a clean working tree before
   switching branches or making changes.

If changes are present, stop. Report the changed files and status; do not run
`stash`, `reset`, `checkout`, `restore`, `commit`, or any discard operation.

## Branch

Choose a concise, lowercase kebab-case name that reflects the task:

- `feat/<short-name>` — new functionality
- `fix/<short-name>` — bug fix
- `refactor/<short-name>` — refactoring
- `chore/<short-name>` — tooling, infrastructure, or maintenance
- `docs/<short-name>` — documentation

Examples: `feat/telegram-start`, `feat/job-ingestion`, `fix/api-timeout`,
`refactor/user-service`.

From a clean local `main`, create the branch. If an upstream `main` exists,
check whether the local branch is current first. If it is behind or diverged,
stop and report that state instead of silently merging or pulling.

## Implement

Before editing, inspect the code relevant to the task. Implement only the
requested scope; avoid unrelated refactors. Follow `AGENTS.md`, including its
rules for dependencies, secrets, tests, and placement of business logic.

## Verify and report

Before finishing, run relevant tests and any configured lint or type checks.
Then run `git diff` and `git status`.

Report the branch name, implemented scope, changed files, checks and results,
known limitations, current Git status, and one proposed commit message. Stop
after the report.

Never merge into `main`, push (including force-push), delete branches, reset,
discard or restore user changes, or create a commit unless the user explicitly
authorizes that specific action.
