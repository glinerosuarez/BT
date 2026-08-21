---
name: application-essay-writer
description: >-
  Writes compelling, tailored, and metric-grounded essay responses for open-ended job application questions
  such as "Why are you interested in this role/company?", "Tell us about a time something didn't work as expected",
  "Describe a technically challenging project", "How do you use AI tools in software engineering?", and other custom
  prompts on ATS portals (Greenhouse, Workday, Lever, SuccessFactors, Oracle HCM, Ashby).
---

# Application Essay Writer Skill

This skill guides the agent in crafting high-impact, authentic, and metric-backed responses for open-ended job application questions and essay prompts. Every response is strictly grounded in the candidate's verified experience (Gabriel Linero) and tailored to the specific company's domain, mission, and technical requirements.

---

## When to Use This Skill

Activate this skill whenever:
1. An ATS application (Greenhouse, Workday, Lever, SuccessFactors, Oracle, Ashby) presents open-ended text questions (e.g., "Why are you interested in becoming an Enterprise Systems Software Engineering Intern?").
2. The user asks to write, refine, or review responses for specific company application questions.
3. Adding new custom question overrides to `profiles/<profile_name>/application_answers.json`.

---

## References & Candidate Knowledge

Before generating responses, consult the following references:
- **Candidate Experience Vault**: [`references/candidate_experience_vault.md`](./references/candidate_experience_vault.md) — Complete repository of verified work history, projects, metrics, tools, and achievements for Gabriel Linero (Impatico, EPAM, Perficient, USC).
- **Question Rubrics & Frameworks**: [`references/question_rubrics.md`](./references/question_rubrics.md) — Structural formulas (Mission-Bridge-Impact, STAR-L, Depth & Tradeoffs, Leverage vs. Rigor).
- **Sample Essays**: [`examples/sample_essays.md`](./examples/sample_essays.md) — Exemplar responses for real roles (Zipline, AMETEK, Intel, ONE Gas).

---

## Step-by-Step Procedure

### 1. Analyze the Prompt & Constraints
- **Question Intent**: Determine the core category (Why Us / Failure & Debugging / Technical Depth / AI & Modern Tools / Leadership).
- **Word / Character Limits**: Check if the prompt has a hard limit (e.g., "150 words", "1000 characters", or single paragraph). Default to **120–180 words** (1 cohesive paragraph) unless otherwise specified.

### 2. Extract Company & Role Alignment
- Identify the **Company Mission** and the **Specific Team/Subsystem** (e.g., Zipline Enterprise Systems powering parts traceability and 99.9% uptime; AMETEK test automation; Intel firmware/compilers).
- Identify key technical priorities (e.g., distributed data pipelines, high availability, observability, anomaly detection, microservice integrations).

### 3. Select Grounded Experience Pillar
Select the primary candidate experience from the [Candidate Experience Vault](./references/candidate_experience_vault.md):
- **High-throughput data engineering / Spark / Cloud cost optimization** $\rightarrow$ **Perficient (American Chemical Society)**: 24h to <1h runtime reduction, $10k/mo EMR savings, Python framework adoption.
- **IoT data quality / Anomaly detection / Microservice tool-calling** $\rightarrow$ **EPAM (Baker Hughes)**: 95% anomaly catch rate, Model Context Protocol / SaaS conversational data access.
- **AI agents / Document extraction / Observability & Evals** $\rightarrow$ **Impatico**: 1,000+ report processing, Phoenix & OpenTelemetry eval infrastructure, precision increase from 50% to 80%.
- **Academic rigor & Leadership** $\rightarrow$ **USC MS in CS** (3.67 GPA), Director of Society of Hispanic Professional Engineers (SHPE).

### 4. Structure the Response

Apply the designated rubric from [Question Rubrics](./references/question_rubrics.md):

| Question Type | Primary Framework | Key Elements to Include |
| :--- | :--- | :--- |
| **"Why This Role / Company?"** | **Mission — Bridge — Impact** | 1. Company mission & specific team context<br>2. Direct candidate technical bridge<br>3. Concrete value candidate will deliver |
| **"Failure / Debugging / Things Not Working"** | **STAR-L** | 1. Concrete failure mode (schema drift / memory pressure)<br>2. Systematic root cause investigation<br>3. Engineering fix & 4. Quantified outcome |
| **"Technically Challenging Project"** | **Depth, Tradeoffs & Retrospective** | 1. Scale & constraints<br>2. Architectural tradeoffs made<br>3. Hard metrics ($10k savings, 95% speedup)<br>4. What would you do differently |
| **"How You Use AI Tools"** | **Leverage vs. Rigor** | 1. Productivity boost (parsing/boilerplate)<br>2. Critical human engineering judgment (evals, boundaries, correctness) |

### 5. Enforce Core Writing Rules
- 🚫 **No Generic Fluff**: Never start with "I am thrilled to apply..." or "I have always been passionate about...".
- 🚫 **No Hallucinations**: Do NOT invent companies, metrics, or technologies not present in the Candidate Vault.
- ✅ **First-Person & Active Voice**: Use crisp, professional, confident language ("I built...", "I optimized...", "I instrumented...").
- ✅ **Verifiable Metrics**: Include specific numbers where appropriate (e.g., "95% of invalid records", "50% to 80% precision", "24 hours to under 1 hour").

### 6. Persist to Application Answers (If Applicable)
When creating answers for automated job runs:
1. Open `profiles/<profile_name>/application_answers.json`.
2. Add a `question_overrides` entry with:
   ```json
   {
       "match_type": "contains",
       "pattern": "<key words from question>",
       "answer": "<generated response>"
   }
   ```
3. Verify JSON syntax and test with `AnswerResolver`.
