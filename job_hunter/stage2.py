from __future__ import annotations

import re
from dataclasses import dataclass

from job_hunter.models import JobRecord

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_BULLET_LINE_RE = re.compile(r"^(?:[-*•]|\d+[.)])\s+(.*\S)$")
_HEADING_RE = re.compile(r"^(requirements?|qualifications?|responsibilities|what you'll do|what you will do|about the role)$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

_FLAG_PATTERNS: dict[str, re.Pattern[str]] = {
    "mentions_phd": re.compile(r"\b(ph\.?d\.?|doctorate|doctoral)\b", re.IGNORECASE),
    "mentions_masters": re.compile(r"\b(master'?s|masters)\b", re.IGNORECASE),
    "mentions_economics": re.compile(r"\b(economics?|econometric)\b", re.IGNORECASE),
    "mentions_operations_research": re.compile(r"\boperations research\b", re.IGNORECASE),
    "mentions_research": re.compile(r"\bresearch\b", re.IGNORECASE),
    "mentions_causal_inference": re.compile(r"\bcausal inference\b", re.IGNORECASE),
    "mentions_llm": re.compile(r"\b(llm|large language model|large language models)\b", re.IGNORECASE),
    "mentions_production_ml": re.compile(r"\bproduction ml\b|\bml systems\b|\bmodel deployment\b|\bdeployed models?\b", re.IGNORECASE),
}

_BOILERPLATE_MARKERS = (
    "about the employer",
    "what this job offers",
    "what they're looking for",
    "matching is based on your profile",
    "similar jobs",
    "alumni in similar roles",
    "alumni at this employer",
    "save apply",
    "save share apply",
)


@dataclass(slots=True)
class Stage2Result:
    profile_match_score: float
    profile_match_label: str
    profile_match_reason_codes: list[str]
    profile_version: str
    scorer_version: str
    job_text_version: str
    job_text_snapshot: str


class ShadowProfileScorer:
    def __init__(self, profile_version: str = "default_v1", scorer_version: str = "shadow_rules_v1") -> None:
        self.profile_version = profile_version
        self.scorer_version = scorer_version

    def score(self, job: JobRecord) -> Stage2Result:
        job_text = build_job_text_v1(job)
        flags = extract_job_flags(job_text)
        score, label, reasons = _score_shadow_rules(job, flags)
        return Stage2Result(
            profile_match_score=score,
            profile_match_label=label,
            profile_match_reason_codes=reasons,
            profile_version=self.profile_version,
            scorer_version=self.scorer_version,
            job_text_version="job_text_v1",
            job_text_snapshot=job_text,
        )


def build_job_text_v1(job: JobRecord) -> str:
    description_lines = _strip_boilerplate_lines(job.description)
    description_blob = "\n".join(description_lines)
    summary_sentences = _extract_summary_sentences(description_blob, limit=3)
    qualification_bullets = _extract_section_bullets(
        description_lines,
        heading_keywords=("requirements", "qualification", "preferred"),
        limit=5,
    )
    responsibility_bullets = _extract_section_bullets(
        description_lines,
        heading_keywords=("responsibil", "what you'll do", "what you will do"),
        limit=5,
    )
    flags = extract_job_flags(" ".join([job.title, job.company, job.location, description_blob]))

    lines = [
        f"TITLE: {job.title}",
        f"ORG: {job.company}",
        f"LOCATION: {job.location or 'unknown'}",
        "SUMMARY:",
    ]
    lines.extend(summary_sentences or ["- none"])
    lines.append("QUALIFICATIONS:")
    lines.extend(qualification_bullets or ["- none"])
    lines.append("RESPONSIBILITIES:")
    lines.extend(responsibility_bullets or ["- none"])
    lines.append("FLAGS: " + (" ".join(flags) if flags else "none"))
    return "\n".join(lines)


def extract_job_flags(text: str) -> list[str]:
    found = [name for name, pattern in _FLAG_PATTERNS.items() if pattern.search(text)]
    return sorted(found)


def _score_shadow_rules(job: JobRecord, flags: list[str]) -> tuple[float, str, list[str]]:
    score = 0.5
    reasons: list[str] = []
    title = (job.title or "").lower()
    blob = f"{job.title} {job.description}".lower()

    if any(token in title for token in ("data engineer", "machine learning", "ml ", "data science", "ai/ml", "applied scientist")):
        score += 0.2
        reasons.append("target_title_alignment")
    if "unpaid" in blob:
        reasons.append("compensation_unpaid")
    if "mentions_phd" in flags:
        score -= 0.35
        reasons.append("flag_phd")
    if "mentions_research" in flags:
        score -= 0.1
        reasons.append("flag_research")
    if "mentions_economics" in flags or "mentions_operations_research" in flags:
        score -= 0.15
        reasons.append("flag_domain_mismatch")
    if "mentions_llm" in flags or "mentions_production_ml" in flags:
        score += 0.1
        reasons.append("flag_ml_systems")
    if "founding" in title or "lead" in title:
        score -= 0.2
        reasons.append("seniority_signal")

    score = max(0.0, min(score, 1.0))
    if score >= 0.75:
        label = "pass"
    elif score >= 0.45:
        label = "review"
    else:
        label = "reject"
    return score, label, sorted(set(reasons))


def _strip_boilerplate_lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    kept: list[str] = []
    for line in lines:
        lowered = line.strip().lower()
        if lowered in _BOILERPLATE_MARKERS:
            break
        kept.append(line)
    return kept


def _extract_summary_sentences(text: str, limit: int) -> list[str]:
    sentences = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        normalized = _WHITESPACE_RE.sub(" ", sentence).strip()
        if not normalized:
            continue
        if normalized.lower().startswith(("job description", "about ")):
            continue
        sentences.append(normalized)
        if len(sentences) >= limit:
            break
    return sentences


def _extract_section_bullets(lines: list[str], heading_keywords: tuple[str, ...], limit: int) -> list[str]:
    lines = [line.strip() for line in lines if line.strip()]
    bullets: list[str] = []
    in_section = False
    for line in lines:
        lowered = line.lower()
        if any(keyword in lowered for keyword in heading_keywords) and len(lowered) < 80:
            in_section = True
            continue
        if in_section and _HEADING_RE.match(line):
            break
        match = _BULLET_LINE_RE.match(line)
        if in_section and match:
            bullets.append(f"- {_WHITESPACE_RE.sub(' ', match.group(1)).strip()}")
            if len(bullets) >= limit:
                break
        elif in_section and bullets and not match:
            break
    return bullets
