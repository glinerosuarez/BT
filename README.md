# Job Hunter

Automated sourcing pipeline for US-based ML/Data internships with eligibility-aware filtering and Telegram alerts.

## What it does

- Pulls postings from multi-source connectors (`Arbeitnow`, `Remotive`, `The Muse`, `Greenhouse`, `Lever`, `RSS`).
- Supports optional keyed connectors (`USAJobs`, `Adzuna`) when credentials are provided.
- Loads large default ATS/RSS source lists from `job_hunter/data/*.txt` (with env overrides).
- Normalizes jobs into a unified schema.
- Keeps US-scoped internships only.
- Excludes postings that explicitly require existing US work authorization.
- Prioritizes sponsorship-friendly roles (`visa sponsorship`, `CPT`, `OPT`, etc.).
- Applies a configurable posting-age filter (`JOB_HUNTER_MAX_POSTING_AGE_DAYS`, default `7`).
- Scores relevance for ML/Data keywords + recency.
- Deduplicates and stores results in SQLite.
- Sends realtime Telegram alerts for new qualifying opportunities.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
cp .env.example .env
set -a; source .env; set +a
python -m job_hunter.run_once
```

## Run continuously

```bash
set -a; source .env; set +a
python -m job_hunter.run_loop --interval-minutes 15
```

## CLI entrypoints

- `python -m job_hunter.run_once`
- `python -m job_hunter.run_loop --interval-minutes N`
- `python -m job_hunter.maintain_sources --probe-active`
- `python -m job_hunter.label_jobs stats`
- `python -m job_hunter.label_jobs list --limit 20`
- `python -m job_hunter.label_jobs show --job-id N`
- `python -m job_hunter.label_jobs export --output /tmp/label-batch.json --limit 50`
- `python -m job_hunter.label_jobs export --output /tmp/label-batch.md --limit 50 --format markdown`
- `python -m job_hunter.label_jobs label --job-id N --fit-label bad_fit --reason-codes bad_fit_phd_only`

## Design notes

- [Two-stage reranking plan](</Users/gabriel.linero/repos/job-hunter/TWO_STAGE_RERANKING_PLAN.md>)

## SQLite tables

- `jobs`: normalized postings with score/eligibility fields and notification state.
  - also stores manual fit labels for evaluation work
- `seen_events`: dedupe and notification tracking.
- `run_logs`: per-run metrics.
- `source_run_logs`: per-source funnel diagnostics (fetched, dead tokens/feed errors, rejected by rule, persisted, notified).
- `source_item_health`: per-token/feed health state used for quarantine/restore automation.

## Core environment variables

- `JOB_HUNTER_DB_PATH`
- `JOB_HUNTER_POLL_INTERVAL_MINUTES`
- `JOB_HUNTER_SOURCE_ARBEITNOW`
- `JOB_HUNTER_SOURCE_REMOTIVE`
- `JOB_HUNTER_SOURCE_THEMUSE`
- `JOB_HUNTER_SOURCE_GREENHOUSE`
- `JOB_HUNTER_SOURCE_LEVER`
- `JOB_HUNTER_SOURCE_RSS`
- `JOB_HUNTER_SOURCE_USAJOBS`
- `JOB_HUNTER_SOURCE_ADZUNA`
- `JOB_HUNTER_GREENHOUSE_TOKEN_FILE`
- `JOB_HUNTER_LEVER_TOKEN_FILE`
- `JOB_HUNTER_RSS_FEED_FILE`
- `JOB_HUNTER_GREENHOUSE_QUARANTINE_FILE`
- `JOB_HUNTER_LEVER_QUARANTINE_FILE`
- `JOB_HUNTER_RSS_QUARANTINE_FILE`
- `JOB_HUNTER_SOURCE_FAILURE_QUARANTINE_THRESHOLD`
- `JOB_HUNTER_SOURCE_RESTORE_SUCCESS_THRESHOLD`
- `JOB_HUNTER_MIN_RELEVANCE_SCORE`
- `JOB_HUNTER_MIN_ELIGIBILITY_CONFIDENCE`
- `JOB_HUNTER_NOTIFY_AMBIGUOUS`
- `JOB_HUNTER_MAX_POSTING_AGE_DAYS`
- `JOB_HUNTER_TITLE_BLACKLIST_PATTERNS`
- `JOB_HUNTER_DATA_ROLE_TITLE_PATTERNS`
- `JOB_HUNTER_NON_DATA_TITLE_PATTERNS`
- `JOB_HUNTER_POLICY_REJECT_PATTERNS`
- `JOB_HUNTER_MIN_DATA_SIGNAL_COUNT`
- `JOB_HUNTER_GREENHOUSE_BOARDS`
- `JOB_HUNTER_LEVER_COMPANIES`
- `JOB_HUNTER_RSS_FEEDS`
- `JOB_HUNTER_USAJOBS_USER_AGENT`
- `JOB_HUNTER_USAJOBS_AUTH_KEY`
- `JOB_HUNTER_ADZUNA_APP_ID`
- `JOB_HUNTER_ADZUNA_APP_KEY`
- `JOB_HUNTER_TELEGRAM_BOT_TOKEN`
- `JOB_HUNTER_TELEGRAM_CHAT_ID`

## Testing

```bash
python -m unittest discover -s tests -v
```
