from __future__ import annotations

import re

from .types import AnswerResolution, ApplicationAnswers, ApplicationProfile, Blocker


class ResolutionError(RuntimeError):
    def __init__(self, blocker: Blocker) -> None:
        super().__init__(blocker.reason)
        self.blocker = blocker


_QUESTION_FIELD_MAP: list[tuple[tuple[str, ...], str]] = [
    (("full name", "legal name", "name"), "identity.full_name"),
    (("email", "email address"), "identity.email"),
    (("phone", "mobile"), "identity.phone"),
    (("city",), "identity.city"),
    (("state", "region", "province"), "identity.region"),
    (("country",), "identity.country"),
    (("linkedin",), "identity.linkedin_url"),
    (("github",), "identity.github_url"),
    (("portfolio", "website"), "identity.portfolio_url"),
    (("authorized to work", "work authorization"), "work_authorization.us_work_authorized"),
    (("sponsorship", "require visa"), "work_authorization.requires_future_sponsorship"),
    (("cpt",), "work_authorization.cpt"),
    (("opt",), "work_authorization.opt"),
    (("school", "university"), "education.school"),
    (("degree",), "education.degree"),
    (("major", "field of study"), "education.major"),
    (("graduation", "graduate date"), "education.graduation_date"),
    (("gpa",), "education.gpa"),
    (("current company", "employer"), "employment.current_company"),
    (("current title", "job title"), "employment.current_title"),
    (("years of experience", "experience"), "employment.years_experience"),
    (("salary", "compensation"), "preferences.salary_min_usd"),
    (("remote",), "preferences.remote_ok"),
    (("relocation",), "preferences.relocation_ok"),
]


class AnswerResolver:
    def __init__(self, *, profile: ApplicationProfile, answers: ApplicationAnswers) -> None:
        self.profile = profile
        self.answers = answers
        self._structured = profile.structured_answers()

    def resolve(self, *, question_text: str, field_name: str = "", field_type: str = "") -> AnswerResolution:
        normalized_question = " ".join(question_text.lower().split())
        normalized_field_name = field_name.strip().lower()

        structured_key = self._structured_key_for(question=normalized_question, field_name=normalized_field_name)
        if structured_key:
            return AnswerResolution(answer=self._structured[structured_key], source=f"structured:{structured_key}")

        default_key = normalized_field_name or normalized_question
        if default_key in self.answers.field_defaults:
            return AnswerResolution(answer=self.answers.field_defaults[default_key], source=f"default:{default_key}")

        for rule in self.answers.question_overrides:
            if _rule_matches(rule.match_type, rule.pattern, normalized_question):
                return AnswerResolution(answer=rule.answer, source=f"override:{rule.match_type}", matched_rule=rule.pattern)

        raise ResolutionError(
            Blocker(
                reason="missing_required_answer",
                question_text=question_text,
                field_name=field_name,
                field_type=field_type,
                details={"normalized_question": normalized_question},
            )
        )

    def _structured_key_for(self, *, question: str, field_name: str) -> str:
        if field_name and field_name in self._structured:
            return field_name
        for patterns, key in _QUESTION_FIELD_MAP:
            if any(pattern in question or pattern == field_name for pattern in patterns):
                return key
        return ""


def _rule_matches(match_type: str, pattern: str, question: str) -> bool:
    lowered = pattern.lower()
    if match_type == "exact":
        return question == lowered
    if match_type == "contains":
        return lowered in question
    return re.search(pattern, question, re.IGNORECASE) is not None
