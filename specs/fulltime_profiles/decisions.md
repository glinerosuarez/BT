# Architectural Decisions: Full-Time Job Profiles

## Context
The system previously supported internship profiles (`ml_eng_intern`, `data_intern`, and an experimental `backend`). Base profiles are needed to support full-time opportunities with proper resume tailoring, application field defaults, and question overrides.

## Approved Decisions (2026-09-14)

### 1. Tracks & Naming Convention
We establish three standard full-time profile tracks under `profiles/`:
- `profiles/swe_fulltime/`: Software Engineer / Backend / Distributed Systems
- `profiles/ml_eng_fulltime/`: Machine Learning Engineer / AI Systems / Agentic Engineering
- `profiles/data_eng_fulltime/`: Data Engineer / Big Data Platforms / ETL & Streaming

### 2. Timeline & Graduation Alignment
- **Graduation Target:** May 2027 – December 2027 (USC Master of Science in Computer Science)
- **Role Seniority:** New Grad / Early Career / Associate Software Engineer
- **Availability:** Post-graduation full-time start dates (Summer/Fall 2027)

### 3. Work Authorization & Sponsorship Policy
- **Authorized to work in the US:** `true` (via F-1 OPT / STEM OPT post-graduation)
- **Requires future sponsorship:** `true` (will require H-1B sponsorship following OPT period)
- **OPT Eligible:** `true`
- **CPT Eligible:** `false` (for post-grad full-time)

### 4. File Structure Per Profile
Each profile directory will contain:
- `application_profile.json`: Identity, work authorization, education, preferences, and file references.
- `resume.md`: Role-tailored markdown resume emphasizing relevant technical pillars and metrics.
- `cover_letter.md`: Role-tailored base cover letter.
- `application_answers.json`: Canonical field matchers and application question overrides.
- `preferences.md`: Job search and match preferences.
