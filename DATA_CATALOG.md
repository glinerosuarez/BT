# Data Catalog

Current database engine: SQLite

SQLite does not use named schemas like Postgres. The logical schema for all tables below is `main`.

Notes:
- JSON-like fields are stored as `TEXT` containing serialized JSON arrays or objects.
- Timestamps are stored as `TEXT` in ISO-8601 or SQLite `CURRENT_TIMESTAMP` format.
- The primary analytical table is `main.jobs`.

## Schema `main`

### Table `jobs`

Purpose: One row per persisted job posting after pipeline filtering and enrichment.

| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER` | Surrogate primary key. |
| `dedupe_key` | `TEXT` | Unique pipeline deduplication key for the posting. |
| `source` | `TEXT` | Source connector name, for example `handshake`, `linkedin`, `greenhouse`. |
| `external_id` | `TEXT` | Source-native job identifier when available. |
| `url` | `TEXT` | Canonical job URL used for navigation and dedupe support. |
| `title` | `TEXT` | Job title. |
| `company` | `TEXT` | Employer name. |
| `location` | `TEXT` | Parsed location string. |
| `is_internship` | `INTEGER` | Internship flag, stored as `0/1`. |
| `posted_at` | `TEXT` | Source-reported posting timestamp or derived timestamp. |
| `description` | `TEXT` | Raw or normalized job description/body text retained for downstream review and scoring. |
| `compensation_type` | `TEXT` | Compensation classification, typically `paid`, `unpaid`, or `unknown`. |
| `work_auth_signals` | `TEXT` | JSON array of work authorization signals extracted from the job text. |
| `sponsorship_signals` | `TEXT` | JSON array of sponsorship or visa friendliness signals. |
| `skills` | `TEXT` | JSON array of extracted skills. |
| `ingested_at` | `TEXT` | Timestamp when the pipeline ingested the row. |
| `relevance_score` | `REAL` | Stage-1 relevance score from deterministic filtering. |
| `eligibility_confidence` | `REAL` | Confidence score for eligibility or work authorization assessment. |
| `eligibility_status` | `TEXT` | Eligibility gate outcome, such as `pass`, `review`, or `reject`. |
| `relevance_hits` | `TEXT` | JSON array of lexical hits that contributed to relevance. |
| `role_relevance_label` | `TEXT` | Deterministic role-fit label from the role relevance gate. |
| `role_relevance_reason_codes` | `TEXT` | JSON array of reason codes explaining `role_relevance_label`. |
| `policy_gate_status` | `TEXT` | Policy-style hard-gate outcome before deeper scoring. |
| `policy_gate_reason_codes` | `TEXT` | JSON array of reason codes explaining the policy gate result. |
| `profile_match_score` | `REAL` | Deterministic Stage-2 profile scorer numeric score. |
| `profile_match_label` | `TEXT` | Deterministic Stage-2 label, typically `pass`, `review`, or `reject`. |
| `profile_match_reason_codes` | `TEXT` | JSON array of deterministic Stage-2 reason codes. |
| `profile_version` | `TEXT` | Version of the deterministic profile definition used. |
| `scorer_version` | `TEXT` | Version of the deterministic scorer implementation used. |
| `job_text_version` | `TEXT` | Version of the normalized job text representation, for example `job_text_v1`. |
| `job_text_snapshot` | `TEXT` | Persisted normalized job text used for deterministic and semantic scoring. |
| `semantic_match_score` | `REAL` | Final semantic score after adjustments and penalties. |
| `semantic_match_label` | `TEXT` | Semantic scorer label, typically `pass`, `review`, or `reject`. |
| `semantic_match_reason_codes` | `TEXT` | JSON array of semantic reason codes for the final label or score. |
| `semantic_base_score` | `REAL` | Raw semantic similarity score before penalties. |
| `semantic_research_heaviness_score` | `REAL` | Penalty component capturing how research-heavy the role appears. |
| `semantic_adjustment_reason_codes` | `TEXT` | JSON array of semantic penalty or adjustment reason codes. |
| `semantic_profile_id` | `TEXT` | Best-matching semantic target profile, for example `data_engineering`. |
| `semantic_model_name` | `TEXT` | Embedding model or backend name used by the semantic scorer. |
| `semantic_scorer_version` | `TEXT` | Version of the semantic scorer implementation. |
| `semantic_text_hash` | `TEXT` | Stable hash of the normalized job text used for semantic scoring or caching consistency. |
| `age_days` | `REAL` | Derived posting age in days. |
| `age_unknown` | `INTEGER` | `0/1` flag indicating whether posting age could not be determined. |
| `source_detail` | `TEXT` | Source-specific provenance detail, often the search URL or feed URL that produced the row. |
| `source_metadata` | `TEXT` | JSON object with source-specific metadata, such as scrape quality diagnostics. |
| `source_quality_status` | `TEXT` | Source-quality assessment, for example `detail_complete`, `card_only`, or `detail_polluted`. |
| `source_quality_reason_codes` | `TEXT` | JSON array explaining the source quality status. |
| `source_quality_prev_status` | `TEXT` | Prior source-quality status if a degraded record later recovered. |
| `source_quality_recovered_at` | `TEXT` | Timestamp when the source-quality status improved or recovered. |
| `manual_fit_label` | `TEXT` | Human label for fit assessment. |
| `manual_fit_reason_codes` | `TEXT` | JSON array of manual labeling reason codes. |
| `manual_labeled_at` | `TEXT` | Timestamp when manual labeling was applied. |
| `notified` | `INTEGER` | `0/1` flag indicating whether a notification was sent. |
| `notified_at` | `TEXT` | Timestamp when a notification was sent. |

### Table `seen_events`

Purpose: Deduplication and notification memory across pipeline runs.

| Column | Type | Description |
|---|---|---|
| `dedupe_key` | `TEXT` | Primary key matching the pipeline dedupe key. |
| `first_seen_at` | `TEXT` | First time this dedupe key was observed. |
| `last_seen_at` | `TEXT` | Most recent time this dedupe key was observed. |
| `seen_count` | `INTEGER` | Number of runs in which this posting was seen. |
| `notified` | `INTEGER` | `0/1` flag indicating whether any notification was sent for this key. |
| `notified_at` | `TEXT` | Timestamp of the notification mark. |

### Table `run_logs`

Purpose: One row per pipeline execution with top-level funnel metrics.

| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER` | Surrogate primary key for the run. |
| `run_at` | `TEXT` | Timestamp when the run log row was created. |
| `source_count` | `INTEGER` | Number of raw items fetched across sources. |
| `normalized_count` | `INTEGER` | Number of records successfully normalized into core job records. |
| `rejected_missing_core_fields_count` | `INTEGER` | Count rejected because required fields were missing after normalization. |
| `after_stage_1a_count` | `INTEGER` | Count remaining after Stage 1A. |
| `after_stage_1b_count` | `INTEGER` | Count remaining after Stage 1B. |
| `after_stage_1c_count` | `INTEGER` | Count remaining after Stage 1C. |
| `passed_filter_count` | `INTEGER` | Count that passed the full filtering pipeline. |
| `persisted_count` | `INTEGER` | Count inserted or updated into storage as accepted jobs. |
| `notified_count` | `INTEGER` | Count that triggered notifications. |
| `duplicate_count` | `INTEGER` | Count recognized as duplicates. |
| `error_count` | `INTEGER` | Count of pipeline errors recorded for the run. |

### Table `source_run_logs`

Purpose: Per-source funnel metrics for each pipeline run.

| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER` | Surrogate primary key. |
| `run_log_id` | `INTEGER` | Foreign key to `run_logs.id`. |
| `source_name` | `TEXT` | Source connector name. |
| `fetched_count` | `INTEGER` | Raw items fetched from the source. |
| `normalized_count` | `INTEGER` | Items normalized into internal job records. |
| `rejected_missing_core_fields_count` | `INTEGER` | Items rejected for missing required fields. |
| `rejected_age_count` | `INTEGER` | Items rejected because they were outside the lookback window. |
| `after_stage_1a_count` | `INTEGER` | Count remaining after Stage 1A for this source. |
| `rejected_internship_count` | `INTEGER` | Count rejected by the internship gate. |
| `rejected_us_scope_count` | `INTEGER` | Count rejected by the US-scope or work-authorization gate. |
| `rejected_title_blacklist_count` | `INTEGER` | Count rejected by title blacklist rules. |
| `rejected_data_role_count` | `INTEGER` | Count rejected for lacking target data, AI, or backend role alignment. |
| `after_stage_1b_count` | `INTEGER` | Count remaining after Stage 1B for this source. |
| `rejected_policy_gate_count` | `INTEGER` | Count rejected by policy-style hard gates. |
| `after_stage_1c_count` | `INTEGER` | Count remaining after Stage 1C. |
| `rejected_eligibility_count` | `INTEGER` | Count rejected by eligibility assessment. |
| `rejected_relevance_count` | `INTEGER` | Count rejected by relevance scoring. |
| `rejected_source_quality_count` | `INTEGER` | Count rejected due to poor source scrape quality. |
| `recovered_source_quality_count` | `INTEGER` | Count whose source quality improved enough to recover. |
| `persisted_count` | `INTEGER` | Count persisted to `jobs`. |
| `notified_count` | `INTEGER` | Count notified from this source in that run. |
| `duplicate_count` | `INTEGER` | Count recognized as duplicates for this source. |
| `error_count` | `INTEGER` | Count of errors for this source or run. |
| `dead_token_count` | `INTEGER` | Count of source tokens, boards, or feed items marked dead. |
| `feed_error_count` | `INTEGER` | Count of feed or fetch failures. |
| `security_verification_blocked_count` | `INTEGER` | Count blocked by anti-bot or security verification. |

### Table `source_item_health`

Purpose: Health tracking for per-source entities such as feed URLs, company tokens, or boards.

Primary key: `source_name`, `item_value`

| Column | Type | Description |
|---|---|---|
| `source_name` | `TEXT` | Source connector name. |
| `item_value` | `TEXT` | Source-specific item identifier, such as a board token or feed URL. |
| `status` | `TEXT` | Latest health status, typically `success` or `failure`. |
| `consecutive_failures` | `INTEGER` | Current failure streak for the item. |
| `consecutive_successes` | `INTEGER` | Current success streak for the item. |
| `total_failures` | `INTEGER` | Lifetime failure count for the item. |
| `total_successes` | `INTEGER` | Lifetime success count for the item. |
| `last_error` | `TEXT` | Most recent error message for failures. |
| `last_checked_at` | `TEXT` | Timestamp of the last health update. |

### Table `tailoring_artifacts`

Purpose: Stores generated application-tailoring outputs tied to a specific job.

| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER` | Surrogate primary key. |
| `job_id` | `INTEGER` | Foreign key to `jobs.id`. |
| `profile_name` | `TEXT` | User or application profile used to tailor artifacts. |
| `provider_name` | `TEXT` | LLM or generation provider name. |
| `model_name` | `TEXT` | Model identifier used to generate outputs. |
| `prompt_version` | `TEXT` | Prompt or template version. |
| `resume_source_hash` | `TEXT` | Hash of the resume source input. |
| `cover_letter_source_hash` | `TEXT` | Hash of the cover letter source input. |
| `preferences_source_hash` | `TEXT` | Hash of the user preferences input. |
| `job_context_hash` | `TEXT` | Hash of the job context used for tailoring. |
| `resume_markdown` | `TEXT` | Generated tailored resume content in Markdown. |
| `cover_letter_markdown` | `TEXT` | Generated tailored cover letter content in Markdown. |
| `highlight_requirements` | `TEXT` | Serialized extracted or highlighted requirements used in tailoring. |
| `evidence_map` | `TEXT` | Serialized mapping from requirements to supporting candidate evidence. |
| `output_dir` | `TEXT` | Filesystem output directory for exported artifacts. |
| `created_at` | `TEXT` | Creation timestamp. |

Unique key:
- `job_id`
- `profile_name`
- `prompt_version`
- `resume_source_hash`
- `cover_letter_source_hash`
- `preferences_source_hash`
- `job_context_hash`

## Relationship Summary

- `jobs` is the core fact table.
- `seen_events` is dedupe and notification memory keyed by `dedupe_key`.
- `run_logs` is one row per pipeline run.
- `source_run_logs` is a child of `run_logs`.
- `source_item_health` tracks source-entity reliability over time.
- `tailoring_artifacts` is a child of `jobs`.
