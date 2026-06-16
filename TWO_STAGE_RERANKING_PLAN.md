# Two-Stage Retrieval and User-Fit Reranking Plan

## Decision

Evolve the current internship sourcing pipeline into four explicit decision layers:

1. `Stage 1A`: ingestion, normalization, dedupe, freshness
2. `Stage 1B`: broad role relevance classification
3. `Stage 1C`: deterministic user policy gates
4. `Stage 2`: semantic user-fit reranking

The objective is to improve notification precision without materially harming recall.

## Problem

The current pipeline is effective at finding ML/data internships with broad lexical matching, but it still passes roles that are technically relevant and still poor fits for the user. The failure mode is not only "bad ML detection." It is "user-fit mismatch":

- `PhD-only` internships
- economics-heavy roles
- research-heavy roles
- adjacent domains that look ML-like in keyword space but are not worth the user's attention

The cost of false positives is user attention. The cost of false negatives is missed opportunities. The new design must reduce the first without silently exploding the second.

## Architecture

### Stage 1A: Ingestion and Freshness

Responsibilities:

- fetch from sources
- normalize raw postings
- compute stable dedupe identity
- apply posting age window

Output:

- normalized jobs with source metadata

This stage should not attempt final fit decisions.

### Stage 1B: Broad Role Relevance

Responsibilities:

- answer only: `is this a real data/ML internship?`
- keep existing internship, US-scope, explicit work-auth rejection, and broad lexical data/ML relevance logic

Output:

- `role_relevance_label`
- `role_relevance_reasons`

This stage is recall-oriented. It should be broad, cheap, and explainable.

### Stage 1C: Deterministic User Policy Gates

Responsibilities:

- catch explicit user no-go cases before semantic scoring
- stay narrow and policy-driven

Initial hard gates:

- `phd_required`
- explicit `economics` in title or team
- explicit `operations research` in title or team
- explicit negative degree/domain phrases

Non-goals for the first version:

- fuzzy domain inference
- broad regex attempts to approximate semantics

Output:

- `policy_gate_status` in `{pass, reject}`
- `policy_gate_reason_codes`

This stage exists so Stage 2 is not asked to solve deterministic decisions.

### Stage 2: Semantic User-Fit Reranking

Responsibilities:

- answer only: `is this role a good fit for this user?`
- operate only on jobs that survive Stage 1C

First implementation choice:

- embeddings-based similarity
- no per-item LLM calls

Output:

- `profile_match_score`
- `profile_match_label` in `{pass, review, reject}`
- `profile_match_reason_codes`
- `profile_version`
- `scorer_version`
- `job_text_version`

This stage should not replace retrieval.

## Representation Contract

`job_text_v1` must be deterministic. If it is noisy, the scorer will be noisy.

Required fields:

- title
- normalized team or org if available
- normalized location
- first 2 to 3 summary sentences after boilerplate stripping
- top 5 qualification bullets
- top 5 responsibility bullets
- extracted structured flags appended as tokens

Initial structured flags:

- `mentions_phd`
- `mentions_masters`
- `mentions_economics`
- `mentions_operations_research`
- `mentions_research`
- `mentions_causal_inference`
- `mentions_llm`
- `mentions_production_ml`

Rules:

- strip employer boilerplate
- keep extraction deterministic
- version the representation from day one

## Data Model Changes

Add Stage 1B, Stage 1C, and Stage 2 diagnostics to SQLite.

Recommended job-level fields:

- `role_relevance_label`
- `role_relevance_reason_codes`
- `policy_gate_status`
- `policy_gate_reason_codes`
- `profile_match_score`
- `profile_match_label`
- `profile_match_reason_codes`
- `profile_version`
- `scorer_version`
- `job_text_version`
- `job_text_snapshot`

Recommended evaluation metadata:

- `manual_fit_label`
- `manual_fit_reason_codes`
- `manual_labeled_at`

`job_text_snapshot` is needed for rescoring and auditability. Version columns without reconstructable input are weak operationally.

## Labeling Protocol

Do this before threshold tuning.

Primary labels:

- `good_fit`
- `borderline`
- `bad_fit`

Reason labels:

- `bad_fit_phd_only`
- `bad_fit_domain_mismatch`
- `bad_fit_research_heavy`
- `bad_fit_seniority_mismatch`
- `bad_fit_non_target_function`

Rubric:

- `good_fit`: the user would likely apply
- `borderline`: the user would want to inspect it, but it is not obviously worth applying to
- `bad_fit`: not worth user attention

Dataset guidance:

- use historical stored postings
- cover multiple sources
- include known true positives, false positives, and borderline cases
- do not tune thresholds on a tiny anecdotal sample

## Rollback and Safety Criteria

Shadow mode is required before Stage 2 gates notifications.

Define explicit kill-switch metrics before rollout:

- minimum acceptable recall on labeled `good_fit` jobs
- maximum acceptable suppression regret
- minimum acceptable review-bucket yield

Operational rule:

- if recall falls below target, Stage 2 returns to shadow-only
- if suppression regret exceeds threshold, Stage 2 gating is disabled immediately

Semantic blocking must remain reversible by config.

## Evaluation Plan

### Offline

Measure:

- precision
- recall
- false-negative rate on `good_fit`
- confusion by reason label
- performance slices by source
- performance slices by role family

Required slices:

- ML engineering
- data science
- analytics
- research-heavy
- economics or quant-adjacent

### Online Shadow

Track:

- current notifier outcome
- hypothetical Stage 2 outcome
- disagreement counts
- score distributions by source
- review-bucket yield

### Online Gated

Start with:

- only reject very low-confidence `reject` cases

Track:

- notification precision
- suppression regret
- drift by source and company

## Counterproposals Rejected

### Regex-only expansion

Rejected because it will continue to accumulate brittle exceptions and will not model user-fit cleanly.

### Replace retrieval with semantic search

Rejected because it risks recall loss and makes the pipeline harder to debug.

### Per-item LLM classification first

Rejected for the first iteration because it is more expensive, less deterministic, and harder to audit.

## Implementation Sequence

1. Define the labeling rubric and evaluation slices.
2. Add the narrow deterministic hard gates.
3. Formalize `job_text_v1`.
4. Add schema fields for Stage 1B, Stage 1C, Stage 2, and manual labels.
5. Build a labeled dataset from historical postings.
6. Validate hard gates alone on labeled data.
7. Generate and inspect `job_text_v1` manually on a sample.
8. Implement Stage 2 scorer in shadow mode.
9. Evaluate `pass/review/reject` against labeled truth.
10. Enable gating only for low-confidence rejects.
11. Expand gating only if regret stays low and recall remains above target.

## First Implementation Checklist

- add config for user hard exclusions
- add `job_text_v1` extraction utility
- add schema migration for new diagnostics fields
- add manual-label workflow for historical jobs
- add scorer interface with versioned output contract
- add shadow-mode logging path
- add rollback config switches
- add metrics queries or reports for offline evaluation

## Open Questions

- What exact user profile should be encoded first: ML engineering, data science, applied scientist, analytics, or a mix?
- How much recall loss is acceptable?
- How many labeled postings are realistic for the first calibration round?
- Which embedding model and storage pattern best fit the local, SQLite-based architecture?
- Should `review` be surfaced via Telegram, a DB view, or both?

## Review Status

This plan was reviewed against the `architect-skeptic` brief from `~/.agents/agents/architect-skeptic.md`.

Review outcome:

- verdict: `approve with changes`
- required changes incorporated:
  - separate role relevance from user fit
  - define label protocol before model work
  - make `job_text_v1` deterministic
  - add explicit rollback criteria
  - keep hard gates narrow and policy-driven
