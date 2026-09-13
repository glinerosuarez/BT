---
name: architectural-decision-gate
description: Enforces mandatory user consultation via ask_question or interactive interview for all architectural, model, provider, or policy decisions.
trigger: always_on
---

# Architectural & Decision Governance Gate

## Mandatory Rule

You MUST NEVER autonomously resolve, select, assume, or switch:
1. **LLM Providers, Model Tiers, or Endpoints**: (e.g. switching between OpenAI, Anthropic, NVIDIA NIM, local models, or changing model versions).
2. **Availability, Eligibility, or Disqualification Rules**: (e.g. visa sponsorship requirements, student degree restrictions, workload/term availability, geographic constraints).
3. **Database Schema, Storage, or Gating Policy Changes**: (e.g. changing filter precedence, modifying stage2 combined labels).
4. **Fallback Execution Strategies**: Whenever an API error, rate limit (429), timeout, or missing key occurs, you MUST NOT silently fallback to an unapproved model or alternate provider.

## Enforcement Procedure

Whenever any of the above situations occurs or a choice needs to be made:
1. **STOP execution immediately**. Do NOT write ad-hoc workaround scripts or proceed silently.
2. **Call the `ask_question` tool** (or recommend `/grill-me`) to present the options, trade-offs, and your recommendation to the user.
3. Wait for the user's explicit selection.
4. Record the approved decision in `specs/<feature>/decisions.md` before executing further actions.
