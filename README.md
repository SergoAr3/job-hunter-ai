# Veylinq

> **AI-assisted job search and application tracking in one workspace.**
>
> Veylinq helps users discover vacancies, save and manage applications, turn a CV into a structured profile, and estimate how well a job matches that profile.

**Status:** actively in development. The core user flows already work; additional job sources, market coverage, and matching improvements are still being built.

## What already works

- **Job discovery** from an external source through the web app and Telegram bot.
- **Application tracking** with saved jobs, statuses, notes, and status history.
- **Profile management** with manual editing and an AI-generated draft from a CV.
- **Job matching** that compares a user's profile with vacancy requirements and explains the result.
- **Web + Telegram** clients using the same backend API.

## Where AI is used

AI is part of the product workflow rather than a separate demo feature:

1. **CV parsing** — a PDF/DOCX resume is converted into a structured profile draft that the user can review and confirm.
2. **Job data enrichment** — vacancy data can be normalized and enriched before it is used by the matching layer.
3. **Candidate-to-job matching** — Veylinq evaluates how well a vacancy fits the user's profile, including required and desired skills and other criteria.

## Product flow

```text
Find a vacancy
      ↓
Review details + match
      ↓
Save to Applications
      ↓
Track status / notes / next action
```

A CV can also be uploaded to create an AI-assisted profile draft used by the matching flow.

## Product screenshots

> Screenshots are being added. The product already has working Web screens for Discover, Applications, job details, profile, and matching, plus Telegram flows.

Recommended screenshots for the repository:

1. **Discover** — vacancy search results with match information.
2. **Application details** — saved job, status, and match explanation.
3. **Profile / CV import** — AI-generated profile draft from an uploaded CV.
4. **Telegram bot** — discovery or application tracking flow.

## Architecture

```text
Telegram → Bot ─┐
                ├→ API ─┬→ PostgreSQL
Browser  → Web ─┘       ├→ OpenAI API
                        └→ Trudvsem
```

The API is the central layer for business logic, matching, integrations, and data access. The web app and Telegram bot are API clients and do not access the database directly.

## Tech stack

**Backend:** Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL  
**Bot:** aiogram  
**Web:** Next.js, React, TypeScript  
**AI:** OpenAI API  
**Infra:** Docker Compose  
**Tests:** pytest, Vitest, React Testing Library

## Current implementation

### Discover

- Search vacancies from the currently connected source (Trudvsem / Работа России).
- View vacancy details and a preliminary profile match.
- Save a vacancy to Applications.
- Normalize external vacancy data into the internal job model.

### Applications

- Search, filter, sort, and paginate saved applications.
- Open an application details page.
- Change application status and view status history.
- Store notes and next actions.
- Show match results with required skills, desired skills, other criteria, and unknown data.

### Profile

- Create and edit a user profile manually.
- Upload a PDF/DOCX CV.
- Generate an AI-assisted profile draft.
- Review and confirm extracted information before it becomes part of the profile.

### Telegram

- Discover vacancies.
- Save jobs.
- View applications.
- Update application statuses.

## Matching

Veylinq currently has a structured matching layer for user and job skills.

The current production matcher remains the default. A newer hybrid version can be enabled explicitly and falls back to the stable matcher when input data is incomplete or unsafe.

The goal is not only to return a score, but to make the result understandable to the user.

## Local development

The primary local workflow is:

```bash
make dev
```

This starts PostgreSQL, applies Alembic migrations, and runs the API, bot, and web app.

Main local endpoints:

- Web: `http://127.0.0.1:3100`
- API: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/docs`

Environment variables include `TELEGRAM_BOT_TOKEN` for Telegram functionality and `OPENAI_API_KEY` for AI-powered features.

Tests:

```bash
# API / Bot
python -m pytest tests

# Web
npm test
npm run lint
npm run typecheck
npm run build
```

## What is still in development

- More job sources and markets.
- Armenia-specific vacancy integrations.
- International remote and ATS sources.
- Matching quality improvements.
- Better Discover ranking and filtering.
- More complete application follow-up workflows.
- Public demo and presentation improvements.

---

Veylinq is an actively developed product focused on making job discovery and application tracking more structured, with AI used where it can remove repetitive manual work and improve matching quality.
