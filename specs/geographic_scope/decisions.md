# Geographic Scope Governance & US-Only Gating Decisions

## 1. Context & Background
Job 1394 (`Werkstudent*in Finance Projects & Systems (m/w/d)` at `OXG Glasfaser GmbH` in `Düsseldorf`) was ingested from `arbeitnow` and passed the `us_scope_pass` gate due to two flaws:
1. `_is_us_scope` used naive substring containment (`hint in location` for `hint in US_LOCATION_HINTS`), where `"us"` matched substrings of non-US locations like `"dusseldorf"` (`d-us-seldorf`), `"belarus"`, `"russia"`, etc.
2. `US_CITY_STATE_RE` matched `,\s*or\b` as Oregon state abbreviation on phrases like `Dubai, United Arab Emirates, or Abu Dhabi`.
3. `JOB_HUNTER_SOURCE_ARBEITNOW=true` was enabled by default, which is primarily a German/European job board.

## 2. Approved Decisions
Approved by user on 2026-09-16 via decision gate:
1. **Disable Arbeitnow**: Set `JOB_HUNTER_SOURCE_ARBEITNOW=false` in `.env` and default configuration since user is exclusively looking for US positions.
2. **Word-Boundary Regex for US Scope**: Replace naive substring matching with word-boundary regex patterns (`\b(?:u\.s\.|usa|united states)\b` and case-sensitive or exact token matching for `\bUS\b`).
3. **Explicit Non-US Geographic Exclusions**: Introduce explicit negative location regexes (`NON_US_LOCATION_PATTERNS`) targeting foreign countries (Germany, Deutschland, UK, United Kingdom, Canada, India, Australia, UAE, United Arab Emirates, France, Netherlands, Switzerland, Spain, etc.) so any location indicating foreign presence is disqualified before fallback or state code checks.
4. **State Abbreviation Regex Hardening**: Ensure state abbreviations match valid city/state formats and are not deceived by conjunctions like "or".
