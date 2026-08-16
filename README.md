# Job Hunter

Automated sourcing pipeline for US-based ML/Data internships with eligibility-aware filtering and Telegram alerts.

It also includes a tailoring module for job-specific artifacts and an adapter-based application module for resumable automated submits on supported targets.

The LangGraph orchestrator connects those tools into a durable autonomous workflow with four roles: orchestration, sourcing, application writing, and applying. It accepts `profile_match_label` as a decision input, enforces a configurable daily unique-job attempt limit, and pauses unsafe or ambiguous applications for a Telegram intervention.

## What it does

- Pulls postings from multi-source connectors (`Arbeitnow`, `Remotive`, `The Muse`, `Greenhouse`, `Lever`, `RSS`, `Ashby`, optional public GitHub internship repos).
- Supports an optional logged-in `Handshake` browser automation source for personal account searches.
- Supports an optional logged-in `Interstride` browser automation source for student-account searches.
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

## Optional local embeddings backend

Install this only when you want to run the future Stage 2 semantic scorer locally:

```bash
pip install -e '.[local-embeddings]'
```

Current implementation status:

- Local batching utilities are in `job_hunter/stage2_local_embeddings.py`.
- Default model target is `BAAI/bge-small-en-v1.5`.
- For this repo, diagnostics default to `cpu`; use `--device mps` only if you have verified that your local PyTorch runtime exposes MPS successfully.
- This is packaged separately so the default install stays lightweight.

## Run continuously

```bash
set -a; source .env; set +a
python -m job_hunter.run_loop --interval-minutes 15
```

## Autonomous orchestrator

Install the project, configure the orchestrator/model variables in `.env`, and initialize it before the first autonomous cycle:

```bash
pip install -e .
python -m job_hunter.orchestrator init --format json
python -m job_hunter.orchestrator once --attempt-limit 3 --format json
python -m job_hunter.orchestrator run --attempt-limit 5
```

Initialization records the current maximum job ID. With `JOB_HUNTER_ORCHESTRATOR_NEW_JOBS_ONLY=true`, only jobs sourced after that baseline are eligible. Candidates must have a `profile_match_label` of `pass` or `review` and must also pass the existing eligibility, policy, source-quality, and duplicate-submission gates. The orchestration agent then decides whether to apply and which ready profile to use; the label is an input, not an automatic apply instruction.

An orchestrator profile is ready only when its directory contains all of:

- `resume.md`
- `cover_letter.md`
- `application_profile.json`
- `application_answers.json`

`init` reports missing files and only ready profiles are offered to the agent. In the current checkout, `ml_eng_intern` is ready; complete the reported files for `backend` and `data_intern` before they can be selected.

The application-writer agent uses the same shared system and user prompts as the existing tailoring CLI. It writes the same Markdown, PDF, metadata, requirement, and evidence artifacts through `TailoringService`.

Each role can use OpenAI, Anthropic, or an NVIDIA-hosted NIM model independently. To use a free NVIDIA API Catalog endpoint, generate a key at [build.nvidia.com](https://build.nvidia.com), choose a currently available chat model ID, and configure the role with `nvidia_nim`:

```bash
NVIDIA_API_KEY=nvapi-...
JOB_HUNTER_NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
JOB_HUNTER_NVIDIA_NIM_STRUCTURED_OUTPUT_METHOD=json_schema

JOB_HUNTER_ORCHESTRATOR_PROVIDER=nvidia_nim
JOB_HUNTER_ORCHESTRATOR_MODEL=meta/llama-3.1-70b-instruct
JOB_HUNTER_SOURCING_PROVIDER=nvidia_nim
JOB_HUNTER_SOURCING_MODEL=meta/llama-3.1-70b-instruct
```

The same provider/model pairing works for `JOB_HUNTER_WRITER_*` and `JOB_HUNTER_APPLIER_*`. Model availability in NVIDIA's free catalog can change, and the selected model must support structured JSON output for these agents. `json_schema` is the default; `function_calling` is available for models whose catalog page advertises tool calling instead. The base URL can also target a self-hosted OpenAI-compatible NIM deployment.

The sourcing agent can discover Greenhouse, Lever, Ashby, RSS, and public GitHub sources through Tavily. Discovered sources enter a registry as candidates, are SSRF-checked and probed, and become active after two successful probes. Two consecutive failures quarantine an active source. Inspect or roll back registry changes with:

```bash
python -m job_hunter.orchestrator sources list --format json
python -m job_hunter.orchestrator sources history --source-id 12 --format json
python -m job_hunter.orchestrator sources rollback --source-id 12 --format json
```

Blocked applications are persisted as LangGraph interrupts. With Telegram configured, use `/status`, `/open <intervention-id>`, `/retry <intervention-id>`, `/continue <intervention-id>`, or `/skip <intervention-id>`. The same actions are available locally:

```bash
python -m job_hunter.orchestrator interventions list --format json
python -m job_hunter.orchestrator interventions resolve --intervention-id 7 --action retry --format json
```

Phoenix tracing is enabled by default and exported over OTLP/HTTP to `http://127.0.0.1:6006/v1/traces`. Instrumentation failures do not stop the workflow, and prompt/output/message capture is disabled by privacy flags. Set `JOB_HUNTER_PHOENIX_ENABLED=false` to disable it.

## Handshake usage

- Bootstrap the saved browser session once:
  - `python -m job_hunter.handshake_login`
- Prefer multiple narrow saved-search URLs over one broad semantic query.
- A practical starting set is:
  - `machine learning`
  - `data science`
  - `applied scientist`
  - `software and ai`
- `JOB_HUNTER_HANDSHAKE_SEARCH_URLS` accepts a comma-separated list of quoted URLs in `.env`.
- Handshake source quality depends heavily on the search URLs you give it. Broad searches like `data` tend to surface many adjacent non-target internships.

## Interstride usage

- Bootstrap the saved browser session once:
  - `python -m job_hunter.interstride_login`
- Set `JOB_HUNTER_SOURCE_INTERSTRIDE=true` in `.env` after logging in.
- `JOB_HUNTER_INTERSTRIDE_SEARCH_URLS` accepts comma-separated Interstride search URLs. The default is `https://student.interstride.com/jobs/search`.

## CLI entrypoints

- `python -m job_hunter.run_once`
- `python -m job_hunter.run_loop --interval-minutes N`
- `python -m job_hunter.maintain_sources --probe-active`
- `python -m job_hunter.funnel_report`
- `python -m job_hunter.funnel_report --format json`
- `python -m job_hunter.handshake_login`
- `python -m job_hunter.interstride_login`
- `python -m job_hunter.tailor_jobs generate --job-id N --profile default`
- `python -m job_hunter.tailor_jobs batch --profile default --limit 10`
- `python -m job_hunter.tailor_jobs list --limit 20`
- `python -m job_hunter.tailor_jobs show --artifact-id N --format json`
- `python -m job_hunter.apply_jobs submit --job-id N --profile default`
- `python -m job_hunter.apply_jobs batch --profile default --limit 5`
- `python -m job_hunter.apply_jobs list --status blocked --limit 20`
- `python -m job_hunter.apply_jobs show --application-id N --format json`
- `python -m job_hunter.apply_jobs resume --application-id N`
- `python -m job_hunter.stage2_report list --limit 20`
- `python -m job_hunter.stage2_report show --job-id N`
- `python -m job_hunter.stage2_report export-labeled --output /tmp/stage2-labeled.json --limit 200`
- `python -m job_hunter.stage2_report embedding-diagnostics --limit 200`
- `python -m job_hunter.label_jobs stats`
- `python -m job_hunter.label_jobs list --limit 20`
- `python -m job_hunter.label_jobs show --job-id N`
- `python -m job_hunter.label_jobs export --output /tmp/label-batch.json --limit 50`
- `python -m job_hunter.label_jobs export --output /tmp/label-batch.md --limit 50 --format markdown`
- `python -m job_hunter.label_jobs label --job-id N --fit-label bad_fit --reason-codes bad_fit_phd_only`

## Design notes

- [Two-stage reranking plan](</Users/gabriel.linero/repos/job-hunter/TWO_STAGE_RERANKING_PLAN.md>)

## Tailoring setup

- Create a shared preferences file at `profiles/default/preferences.md`.
- Create a profile at `profiles/<profile-name>/` with:
  - `resume.md`
  - `cover_letter.md`
  - optional profile-specific `preferences.md`
- Set:
  - `JOB_HUNTER_TAILORING_ANTHROPIC_MODEL`
  - `ANTHROPIC_API_KEY`
- Tailored artifacts are written under `artifacts/tailoring/<profile>/<job-id>-<company>-<title>/`.
  - `resume.md`
  - `cover_letter.md`
  - `resume.pdf`
  - `cover_letter.pdf`
  - `metadata.json`

## Application setup

- Create `profiles/<profile>/application_profile.json`.
- Create `profiles/<profile>/application_answers.json`.
- Automated submit currently supports `LinkedIn Easy Apply` and `Greenhouse`.
- Unsupported portals, captchas, login/account walls, unknown required questions, and ambiguous submit states are stored as `blocked` runs for manual takeover.
- Application artifacts are written under `artifacts/applications/<profile>/<application-id>/`.
  - `run.json`
  - `blocker.json` when blocked
  - `confirmation.json` when submitted

## SQLite tables

- `jobs`: normalized postings with score/eligibility fields and notification state.
  - also stores manual fit labels and Stage 2 shadow-mode reranking fields
- `seen_events`: dedupe and notification tracking.
- `run_logs`: per-run metrics.
- `source_run_logs`: per-source funnel diagnostics (fetched, dead tokens/feed errors, rejected by rule, persisted, notified).
- `source_item_health`: per-token/feed health state used for quarantine/restore automation.
- `tailoring_artifacts`: tailored output records keyed by job, profile, and context hashes.
- `application_runs`: resumable application attempts and final outcomes.
- `application_steps`: per-step state snapshots for blocker/debug visibility.

## Core environment variables

- `JOB_HUNTER_DB_PATH`
- `JOB_HUNTER_POLL_INTERVAL_MINUTES`
- `JOB_HUNTER_SOURCE_ARBEITNOW`
- `JOB_HUNTER_SOURCE_REMOTIVE`
- `JOB_HUNTER_SOURCE_THEMUSE`
- `JOB_HUNTER_SOURCE_GREENHOUSE`
- `JOB_HUNTER_SOURCE_LEVER`
- `JOB_HUNTER_SOURCE_RSS`
- `JOB_HUNTER_SOURCE_GITHUB_REPOS`
- `JOB_HUNTER_SOURCE_ASHBY`
- `JOB_HUNTER_SOURCE_HANDSHAKE`
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
- `JOB_HUNTER_GITHUB_REPO_READMES`
- `JOB_HUNTER_ASHBY_BOARDS`
- `JOB_HUNTER_HANDSHAKE_SEARCH_URLS`
- `JOB_HUNTER_HANDSHAKE_PROFILE_DIR`
- `JOB_HUNTER_HANDSHAKE_HEADLESS`
- `JOB_HUNTER_HANDSHAKE_MAX_RESULTS`
- `JOB_HUNTER_HANDSHAKE_PAGE_TIMEOUT_SECONDS`
- `JOB_HUNTER_USAJOBS_USER_AGENT`
- `JOB_HUNTER_USAJOBS_AUTH_KEY`
- `JOB_HUNTER_ADZUNA_APP_ID`
- `JOB_HUNTER_ADZUNA_APP_KEY`
- `JOB_HUNTER_TAILORING_PROVIDER`
- `JOB_HUNTER_TAILORING_ANTHROPIC_MODEL`
- `JOB_HUNTER_TAILORING_BATCH_DEFAULT_LIMIT`
- `JOB_HUNTER_APPLY_PROVIDER`
- `JOB_HUNTER_APPLY_ANTHROPIC_MODEL`
- `JOB_HUNTER_APPLY_BROWSER_PROFILE_DIR`
- `JOB_HUNTER_APPLY_HEADLESS`
- `JOB_HUNTER_APPLY_PAGE_TIMEOUT_SECONDS`
- `JOB_HUNTER_APPLY_BATCH_DEFAULT_LIMIT`
- `JOB_HUNTER_APPLY_OUTPUT_ROOT`
- `JOB_HUNTER_APPLY_GMAIL_VERIFICATION_ENABLED`
- `JOB_HUNTER_APPLY_GMAIL_ACCESS_TOKEN`
- `JOB_HUNTER_APPLY_GMAIL_REFRESH_TOKEN`
- `JOB_HUNTER_APPLY_GMAIL_CLIENT_ID`
- `JOB_HUNTER_APPLY_GMAIL_CLIENT_SECRET`
- `JOB_HUNTER_APPLY_GMAIL_POLL_TIMEOUT_SECONDS`
- `JOB_HUNTER_APPLY_GMAIL_POLL_INTERVAL_SECONDS`
- `JOB_HUNTER_APPLY_GMAIL_SENDER_FILTER`
- `JOB_HUNTER_TELEGRAM_BOT_TOKEN`
- `JOB_HUNTER_TELEGRAM_CHAT_ID`

## Testing

```bash
python -m unittest discover -s tests -v
```

## Gmail Bootstrap

To enable automatic Greenhouse email verification via Gmail, first create a Google OAuth desktop client and then run:

```bash
python -m job_hunter.gmail_auth init --client-secret-file ~/Downloads/client_secret_*.json
```

This writes the Gmail OAuth values into your local `.env`:

- `JOB_HUNTER_APPLY_GMAIL_VERIFICATION_ENABLED=true`
- `JOB_HUNTER_APPLY_GMAIL_CLIENT_ID`
- `JOB_HUNTER_APPLY_GMAIL_CLIENT_SECRET`
- `JOB_HUNTER_APPLY_GMAIL_ACCESS_TOKEN`
- `JOB_HUNTER_APPLY_GMAIL_REFRESH_TOKEN`
