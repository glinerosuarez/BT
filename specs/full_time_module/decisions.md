# Full-Time Jobs Module — Architectural & Design Decisions

## 1. Context & Motivation
The user is eligible to graduate in May 2027, Summer 2027, or December 2027, and seeks to search concurrently for:
- Full-time roles (starting May 2027 or earlier/later)
- Summer 2027 internships

## 2. Target Roles & Experience Levels
- **Scope**: Any full-time role matching the core tech stack / keywords (Software Engineering, Backend, Distributed Systems, Data Engineering, Machine Learning, AI Engineering) regardless of stated experience level.
- **Seniority & Title Policy**: Exclude management and high-level leadership titles (`Director`, `VP`, `Manager`, `Head of`, `Principal`, `Staff`), but retain Junior, Associate, Mid-Level, and Senior Individual Contributor (IC) roles.
- **Start Dates**: Accept all full-time postings regardless of stated start date or graduation requirements; candidate will manually screen specific start dates.

## 3. Architecture & Execution Model
- **Integration**: Shared database (`job_hunter.db`) with a unified pipeline engine.
- **CLI & Execution Mode**:
  - Flag: `--job-type [all|internship|full_time]`.
  - Default: `all` (scrapes and processes both internships and full-time together).
  - Targeted runs supported via `--job-type internship` or `--job-type full_time`.

## 4. Database Schema & Storage Decisions
- **`jobs` table migration**:
  - Add explicit `job_type` column (`'internship'` | `'full_time'`) with backward-compatible SQLite migration (`ALTER TABLE jobs ADD COLUMN job_type TEXT DEFAULT 'internship'`).
  - Maintain `is_internship` column synced (`is_internship = 1` when `job_type == 'internship'`, `0` when `full_time`) to ensure full backward compatibility with any existing queries or views.

## 5. Sources & Search Strategy
- **LinkedIn**: Full-time search queries (`backend engineer`, `software engineer`, `data engineer`, `machine learning engineer`, `ai engineer`) with job type filters (`f_JT=F`).
- **Handshake**: Full-time recall queries (`jobType=1`).
- **GitHub Repositories**: Default to `SimplifyJobs/New-Grad-Positions` (`dev/README.md`), overridable via `GITHUB_NEW_GRAD_REPO_README` env var.
- **Direct ATS (Greenhouse, Lever, Ashby)**: Scrape without `_is_internship` rejection gate when running in `full_time` or `all` mode.
- **Career Portals (Apple, HiringCafe)**: Support full-time search queries and endpoints.

## 6. Gating & Stage 2 Evaluation
- **Gating**:
  - For `internship`: Enforce `_is_internship(job)` and student policy gates.
  - For `full_time`: Reject jobs matching internship patterns, reject high-level leadership/management titles, and pass builder/technical signals.
- **Stage 2 Scoring**: Reuse deterministic rule scorer and local semantic embedding scorer tailored to builder signals.
- **Stage 2 Combiner**: Adhere to established truth table:
  - `PASS + PASS -> PASS`
  - `REJECT + REJECT -> REJECT`
  - `All other combinations -> REVIEW`

## 7. Notifications
- **Destination**: Shared Telegram chat.
- **Formatting**: Distinguish alerts with clear tagging: `[Full-Time Alert]` vs `[Internship Alert]`.
