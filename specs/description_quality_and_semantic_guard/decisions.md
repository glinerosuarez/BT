# Description Quality & Stage 2 Semantic Guard Decisions

## 1. Context & Motivation
Job 1451 (`Eggs Unlimited - Lead Platform Engineer`, ID: 1451) was scraped with only the LinkedIn top card/header chrome (192 characters) because:
1. `detail_page.wait_for_selector` included `.jobs-unified-top-card, main`, which resolved prematurely before `#job-details` was hydrated by React.
2. `_DETAIL_MIN_LENGTH` was set to `200`, and `_build_row` marked descriptions >= 200 characters as `detail_complete`.
3. In Stage 2 semantic scoring, `_builder_evidence_adjustment` assumed the description was complete and applied `BUILDER_SPARSE_PENALTY_ZERO_BUCKETS` (-0.12), causing a false `reject` (0.484) on what was otherwise a 0.604 base match.

## 2. Approved Decisions (2026-09-17)
1. **Scraper Hydration & Selector Timing**:
   - Wait specifically for `#job-details, .jobs-description, .jobs-description__content, [data-view-name="job-details"]`.
   - Scroll dynamically to trigger React lazy-loading of description blocks.
   - Run `EXPAND_MORE_SCRIPT` after the description element is confirmed in the DOM.
2. **Quality Threshold & Classification**:
   - Raise `_DETAIL_MIN_LENGTH` from 200 to 400 characters.
   - In `_build_row`, classify as `detail_partial` (never `detail_complete`) if `len(description) < 400` or if the description matches the raw `card_text`.
3. **Stage 2 Semantic Scoring Guard**:
   - In `_builder_evidence_adjustment`, do not apply the zero-bucket sparsity penalty (-0.12) when the job description is verified as thin (< 400 characters).
   - Instead, attach `semantic_thin_description_review` and ensure the job is kept for manual review rather than falsely rejected due to ingestion truncation.
