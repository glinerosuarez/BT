from __future__ import annotations

from datetime import datetime
import re

from .types import AnswerResolution, ApplicationAnswers, ApplicationProfile, Blocker, FieldCapability


class ResolutionError(RuntimeError):
    def __init__(self, blocker: Blocker) -> None:
        super().__init__(blocker.reason)
        self.blocker = blocker


_QUESTION_FIELD_MAP: list[tuple[tuple[str, ...], str]] = [
    (("first name",), "identity.first_name"),
    (("last name", "surname", "family name"), "identity.last_name"),
    (("full name", "legal name", "name"), "identity.full_name"),
    (("email", "email address"), "identity.email"),
    (("phone", "mobile"), "identity.phone"),
    (("sponsorship", "require visa"), "work_authorization.requires_future_sponsorship"),
    (("authorized to work", "work authorization"), "work_authorization.us_work_authorized"),
    (("cpt",), "work_authorization.cpt"),
    (("opt",), "work_authorization.opt"),
    (("city",), "identity.city"),
    (("state", "region", "province"), "identity.region"),
    (("country",), "identity.country"),
    (("linkedin",), "identity.linkedin_url"),
    (("github",), "identity.github_url"),
    (("portfolio", "website"), "identity.portfolio_url"),
    (("university", "college", "institution"), "education.school"),
    (("degree",), "education.degree"),
    (("major", "field of study"), "education.major"),
    (("graduation", "graduate date"), "education.graduation_date"),
    (("gpa",), "education.gpa"),
    (("current company", "current employer", "employer"), "employment.current_company"),
    (("current title", "job title", "title"), "employment.current_title"),
    (("years of experience",), "employment.years_experience"),
    (("salary", "compensation"), "preferences.salary_min_usd"),
    (("remote",), "preferences.remote_ok"),
    (("relocation",), "preferences.relocation_ok"),
]

_INTENT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("consent_required", ("provide your consent", "has my consent")),
    ("current_location", ("current location", "where are you based", "city/state of residence")),
    ("work_auth_us", ("legally authorized to work in the united states", "authorized to work in the united states")),
    (
        "future_sponsorship_us",
        (
            "require employer sponsorship to work in the united states",
            "require sponsorship to work in the united states",
            "require medpace inc. to commence",
        ),
    ),
    ("on_site_acknowledgement", ("requires me to work on-site", "requires me to work on site")),
    ("education_end_month", ("end date month",)),
    ("education_end_year", ("end date year", "what year will you graduate")),
    ("identity_linkedin_url", ("linkedin profile",)),
    ("identity_additional_link", ("additional link",)),
]

_FIELD_CAPABILITIES: tuple[FieldCapability, ...] = (
    FieldCapability(
        portal="linkedin",
        widget_types=("checkbox-group",),
        intents=("consent_required",),
        resolver_mode="computed_yes",
        submit_policy="safe_autofill_if_single_option",
    ),
    FieldCapability(
        portal="linkedin",
        widget_types=("text",),
        intents=("current_location", "identity_linkedin_url"),
        resolver_mode="structured_or_computed",
        submit_policy="safe_autofill",
    ),
    FieldCapability(
        portal="greenhouse",
        widget_types=("radio-group", "select-one"),
        intents=("work_auth_us", "future_sponsorship_us", "on_site_acknowledgement"),
        resolver_mode="structured_boolean_yes_no",
        submit_policy="safe_autofill",
    ),
    FieldCapability(
        portal="greenhouse",
        widget_types=("text",),
        intents=("identity_linkedin_url", "identity_additional_link", "current_location"),
        resolver_mode="structured_or_computed",
        submit_policy="safe_autofill",
    ),
    FieldCapability(
        portal="greenhouse",
        widget_types=("select-one", "text"),
        intents=("education_end_month", "education_end_year"),
        resolver_mode="structured_or_computed",
        submit_policy="safe_autofill",
    ),
)


class AnswerResolver:
    def __init__(self, *, profile: ApplicationProfile, answers: ApplicationAnswers) -> None:
        self.profile = profile
        self.answers = answers
        self._structured = profile.structured_answers()

    def resolve(self, *, question_text: str, field_name: str = "", field_type: str = "") -> AnswerResolution:
        normalized_question = " ".join(question_text.lower().split())
        normalized_field_name = field_name.strip().lower()

        computed = self._computed_answer(
            question=normalized_question,
            field_name=normalized_field_name,
            field_type=field_type.strip().lower(),
        )
        if computed is not None:
            return computed

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

    def classify_intent(self, *, question_text: str, field_name: str = "") -> str | None:
        normalized_question = " ".join(question_text.lower().split())
        normalized_field_name = field_name.strip().lower()
        for intent, patterns in _INTENT_PATTERNS:
            if any(pattern in normalized_question or pattern == normalized_field_name for pattern in patterns):
                return intent
        return None

    def resolve_for_portal(
        self,
        *,
        portal: str,
        question_text: str,
        field_name: str = "",
        field_type: str = "",
    ) -> AnswerResolution:
        intent = self.classify_intent(question_text=question_text, field_name=field_name)
        capability = self._capability_for(portal=portal, field_type=field_type, intent=intent)
        if capability is not None and intent is not None:
            resolution = self._resolve_intent_value(intent=intent, question_text=question_text, field_name=field_name, field_type=field_type)
            if resolution is not None:
                return AnswerResolution(
                    answer=resolution.answer,
                    source=f"capability:{portal}:{intent}:{capability.submit_policy}",
                    matched_rule=capability.resolver_mode,
                )
        return self.resolve(question_text=question_text, field_name=field_name, field_type=field_type)

    def capability_for_field(self, *, portal: str, question_text: str, field_name: str = "", field_type: str = "") -> FieldCapability | None:
        intent = self.classify_intent(question_text=question_text, field_name=field_name)
        return self._capability_for(portal=portal, field_type=field_type, intent=intent)

    def _structured_key_for(self, *, question: str, field_name: str) -> str:
        if field_name and field_name in self._structured:
            return field_name
        if field_name.startswith("school"):
            return "education.school"
        if question.rstrip("*").strip() == "school":
            return "education.school"
        for patterns, key in _QUESTION_FIELD_MAP:
            if any(pattern in question or pattern == field_name for pattern in patterns):
                return key
        return ""

    def _capability_for(self, *, portal: str, field_type: str, intent: str | None) -> FieldCapability | None:
        if intent is None:
            return None
        normalized_widget = field_type.strip().lower()
        for capability in _FIELD_CAPABILITIES:
            if capability.portal != portal:
                continue
            if normalized_widget not in capability.widget_types:
                continue
            if intent not in capability.intents:
                continue
            return capability
        return None

    def _resolve_intent_value(
        self,
        *,
        intent: str,
        question_text: str,
        field_name: str,
        field_type: str,
    ) -> AnswerResolution | None:
        question = " ".join(question_text.lower().split())
        return self._computed_answer(question=question, field_name=field_name.strip().lower(), field_type=field_type.strip().lower(), forced_intent=intent)

    def _computed_answer(self, *, question: str, field_name: str, field_type: str, forced_intent: str | None = None) -> AnswerResolution | None:
        normalized_field_name = field_name.lower()

        if "first name" in question or field_name.endswith("first_name") or field_name == "first_name":
            first_name = _first_name(self._structured.get("identity.full_name", ""))
            if first_name:
                return AnswerResolution(answer=first_name, source="computed:identity.first_name")

        if "last name" in question or field_name.endswith("last_name") or field_name == "last_name":
            last_name = _last_name(self._structured.get("identity.full_name", ""))
            if last_name:
                return AnswerResolution(answer=last_name, source="computed:identity.last_name")

        if "degree" in question or field_name.startswith("degree"):
            degree = _canonical_degree(self._structured.get("education.degree", ""))
            if degree:
                return AnswerResolution(answer=degree, source="computed:education.degree")

        if forced_intent == "consent_required" or "provide your consent" in question or "has my consent" in question:
            return AnswerResolution(answer="Yes", source="computed:consent_acknowledgement")

        if forced_intent == "current_location" or "current location" in question:
            city = self._structured.get("identity.city", "").strip()
            region = self._structured.get("identity.region", "").strip()
            if city and region:
                return AnswerResolution(answer=f"{city}, {region}", source="computed:identity.location")
            if city:
                return AnswerResolution(answer=city, source="computed:identity.location")

        if forced_intent == "work_auth_us" or "legally authorized to work in the united states" in question:
            authorized = self._structured.get("work_authorization.us_work_authorized", "").strip().lower()
            if authorized in {"true", "yes", "1"}:
                return AnswerResolution(answer="Yes", source="computed:work_authorization.us_work_authorized")
            if authorized in {"false", "no", "0"}:
                return AnswerResolution(answer="No", source="computed:work_authorization.us_work_authorized")

        if "authorized to work in the united states" in question:
            authorized = self._structured.get("work_authorization.us_work_authorized", "").strip().lower()
            if authorized in {"true", "yes", "1"}:
                return AnswerResolution(answer="Yes", source="computed:work_authorization.us_work_authorized")
            if authorized in {"false", "no", "0"}:
                return AnswerResolution(answer="No", source="computed:work_authorization.us_work_authorized")

        if forced_intent == "future_sponsorship_us" or "require employer sponsorship to work in the united states" in question:
            sponsorship = self._structured.get("work_authorization.requires_future_sponsorship", "").strip().lower()
            if sponsorship in {"true", "yes", "1"}:
                return AnswerResolution(answer="Yes", source="computed:work_authorization.requires_future_sponsorship")
            if sponsorship in {"false", "no", "0"}:
                return AnswerResolution(answer="No", source="computed:work_authorization.requires_future_sponsorship")

        if forced_intent == "on_site_acknowledgement" or "requires me to work on-site" in question or "requires me to work on site" in question:
            return AnswerResolution(answer="Yes", source="computed:preferences.on_site_acknowledgement")

        if "are you over 18" in question:
            return AnswerResolution(answer="Yes", source="computed:identity.over_18")

        if "previously been employed by medpace" in question:
            return AnswerResolution(answer="No", source="computed:employment.previously_employed_by_company")

        if "ever interviewed with medpace" in question:
            return AnswerResolution(answer="No", source="computed:employment.previously_interviewed_with_company")

        if "relatives employed by medpace" in question:
            return AnswerResolution(answer="No", source="computed:employment.relatives_employed_by_company")

        if "require medpace inc. to commence" in question or ("sponsor" in question and "immigration" in question):
            sponsorship = self._structured.get("work_authorization.requires_future_sponsorship", "").strip().lower()
            if sponsorship in {"true", "yes", "1"}:
                return AnswerResolution(answer="Yes", source="computed:work_authorization.requires_future_sponsorship")
            if self._structured.get("work_authorization.opt", "").strip().lower() in {"true", "yes", "1"}:
                return AnswerResolution(answer="No, I hold a current US Work Visa", source="computed:work_authorization.current_visa")
            if self._structured.get("work_authorization.cpt", "").strip().lower() in {"true", "yes", "1"}:
                return AnswerResolution(answer="No, I hold a current US Work Visa", source="computed:work_authorization.current_visa")
            return AnswerResolution(answer="No", source="computed:work_authorization.requires_future_sponsorship")

        if "current type of us work visa" in question:
            if self._structured.get("work_authorization.opt", "").strip().lower() in {"true", "yes", "1"}:
                return AnswerResolution(answer="F-1 OPT", source="computed:work_authorization.opt")
            if self._structured.get("work_authorization.cpt", "").strip().lower() in {"true", "yes", "1"}:
                return AnswerResolution(answer="F-1 CPT", source="computed:work_authorization.cpt")
            return AnswerResolution(answer="N/A", source="computed:work_authorization.none")

        if "expiration date of your current us work visa" in question:
            visa_expiration = _visa_expiration_date(self._structured.get("education.graduation_date", ""))
            if visa_expiration:
                return AnswerResolution(answer=visa_expiration, source="computed:work_authorization.visa_expiration")
            return AnswerResolution(answer="N/A", source="computed:work_authorization.visa_expiration")

        if "undergraduate gpa" in question or ("gpa" in question and "4.0 scale" in question):
            gpa = self._structured.get("education.gpa", "").strip()
            if gpa:
                return AnswerResolution(answer=gpa, source="computed:education.gpa")

        if "professional experience employer" in question or question == "employer":
            company = self._structured.get("employment.current_company", "").strip()
            if company:
                return AnswerResolution(answer=company, source="computed:employment.current_company")

        if "professional experience title" in question or question == "title":
            title = self._structured.get("employment.current_title", "").strip()
            if title:
                return AnswerResolution(answer=title, source="computed:employment.current_title")

        if "professional experience country" in question:
            country = _canonical_country(self._structured.get("identity.country", "").strip())
            if country:
                return AnswerResolution(answer=country, source="computed:identity.country")

        if "professional experience state/province" in question:
            region = _canonical_region(self._structured.get("identity.region", "").strip())
            if region:
                return AnswerResolution(answer=region, source="computed:identity.region")

        if "reason for leaving" in question:
            return AnswerResolution(answer="Current role", source="computed:employment.reason_for_leaving")

        if "may we contact" in question:
            return AnswerResolution(answer="No", source="computed:employment.may_contact")

        if "professional experience start date" in question:
            experience_start = _employment_start_date(self._structured.get("employment.years_experience", ""))
            if (field_type == "select-year" or normalized_field_name.endswith("_year")) and experience_start["year"]:
                return AnswerResolution(answer=experience_start["year"], source="computed:employment.start_year")
            if (field_type == "select-month" or normalized_field_name.endswith("_month")) and experience_start["month"]:
                return AnswerResolution(answer=experience_start["month"], source="computed:employment.start_month")
            if (field_type == "select-day" or normalized_field_name.endswith("_day")) and experience_start["day"]:
                return AnswerResolution(answer=experience_start["day"], source="computed:employment.start_day")

        if "professional experience end date" in question:
            experience_end = _employment_end_date()
            if field_type == "select-year" or normalized_field_name.endswith("_year"):
                return AnswerResolution(answer=experience_end["year"], source="computed:employment.end_year")
            if field_type == "select-month" or normalized_field_name.endswith("_month"):
                return AnswerResolution(answer=experience_end["month"], source="computed:employment.end_month")
            if field_type == "select-day" or normalized_field_name.endswith("_day"):
                return AnswerResolution(answer=experience_end["day"], source="computed:employment.end_day")

        if "discipline" in question or "discipline" in field_name:
            major = self._structured.get("education.major", "").strip()
            if major:
                return AnswerResolution(answer=major, source="computed:education.major")

        if forced_intent == "education_end_month" or "end date month" in question or field_name.startswith("end-month"):
            month = _graduation_month_name(self._structured.get("education.graduation_date", ""))
            if month:
                return AnswerResolution(answer=month, source="computed:education.end_month")

        if "start date year" in question or field_name.startswith("start-year"):
            year = _education_start_year(
                graduation_date=self._structured.get("education.graduation_date", ""),
                degree=self._structured.get("education.degree", ""),
            )
            if year:
                return AnswerResolution(answer=year, source="computed:education.start_year")

        if forced_intent == "education_end_year" or "end date year" in question or field_name.startswith("end-year"):
            year = _graduation_year(self._structured.get("education.graduation_date", ""))
            if year:
                return AnswerResolution(answer=year, source="computed:education.end_year")

        if "what year will you graduate" in question:
            year = _graduation_year(self._structured.get("education.graduation_date", ""))
            if year:
                return AnswerResolution(answer=year, source="computed:education.end_year")

        if forced_intent == "identity_linkedin_url" or "linkedin profile" in question:
            value = self._structured.get("identity.linkedin_url", "").strip()
            if value:
                return AnswerResolution(answer=value, source="computed:identity.linkedin_url")

        if forced_intent == "identity_additional_link" or "additional link" in question:
            for key in ("identity.github_url", "identity.portfolio_url"):
                value = self._structured.get(key, "").strip()
                if value:
                    return AnswerResolution(answer=value, source=f"computed:{key}")

        return None


def _graduation_year(graduation_date: str) -> str:
    match = re.match(r"^\s*(\d{4})", graduation_date or "")
    return match.group(1) if match else ""


def _first_name(full_name: str) -> str:
    parts = [part for part in (full_name or "").strip().split() if part]
    return parts[0] if parts else ""


def _last_name(full_name: str) -> str:
    parts = [part for part in (full_name or "").strip().split() if part]
    return parts[-1] if len(parts) >= 2 else ""


def _education_start_year(*, graduation_date: str, degree: str) -> str:
    end_year = _graduation_year(graduation_date)
    if not end_year:
        return ""
    duration_years = 4
    lowered_degree = (degree or "").lower()
    if any(token in lowered_degree for token in ("m.s", "ms", "master", "mba", "m.eng", "meng")):
        duration_years = 2
    elif any(token in lowered_degree for token in ("ph.d", "phd", "doctor")):
        duration_years = 5
    elif any(token in lowered_degree for token in ("associate", "a.s", "a.a")):
        duration_years = 2
    elif any(token in lowered_degree for token in ("certificate", "bootcamp")):
        duration_years = 1
    return str(int(end_year) - duration_years)


def _visa_expiration_date(graduation_date: str) -> str:
    value = (graduation_date or "").strip()
    if not value:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        year, month, day = value.split("-")
        return f"{day}/{month}/{year}"
    if re.match(r"^\d{4}/\d{2}/\d{2}$", value):
        year, month, day = value.split("/")
        return f"{day}/{month}/{year}"
    if re.match(r"^\d{4}-\d{2}$", value):
        year, month = value.split("-")
        return f"01/{month}/{year}"
    if re.match(r"^\d{4}/\d{2}$", value):
        year, month = value.split("/")
        return f"01/{month}/{year}"
    return ""


def _graduation_month_name(graduation_date: str) -> str:
    value = (graduation_date or "").strip()
    match = re.match(r"^\d{4}[-/](\d{2})(?:[-/]\d{2})?$", value)
    if not match:
        return ""
    month_number = int(match.group(1))
    if month_number < 1 or month_number > 12:
        return ""
    return datetime(2000, month_number, 1).strftime("%B")


def _employment_start_date(years_experience: str) -> dict[str, str]:
    now = datetime.now()
    try:
        years = max(int(float(years_experience)), 0)
    except ValueError:
        years = 0
    start_year = now.year - years if years > 0 else now.year
    return {
        "year": str(start_year),
        "month": "January",
        "day": "1",
    }


def _employment_end_date() -> dict[str, str]:
    now = datetime.now()
    return {
        "year": str(now.year),
        "month": now.strftime("%B"),
        "day": str(now.day),
    }


def _canonical_degree(degree: str) -> str:
    lowered = (degree or "").strip().lower()
    if not lowered:
        return ""
    if any(token in lowered for token in ("m.s", "ms", "master", "mba", "m.eng", "meng")):
        return "Master's Degree"
    if any(token in lowered for token in ("b.s", "bs", "b.a", "ba", "bachelor")):
        return "Bachelor's Degree"
    if any(token in lowered for token in ("ph.d", "phd", "doctor of philosophy")):
        return "Doctor of Philosophy (Ph.D.)"
    if any(token in lowered for token in ("associate", "a.s", "a.a")):
        return "Associate's Degree"
    return degree.strip()


def _canonical_country(country: str) -> str:
    lowered = (country or "").strip().lower()
    if lowered in {"usa", "us", "u.s.", "u.s.a.", "united states of america"}:
        return "United States"
    return country.strip()


def _canonical_region(region: str) -> str:
    lowered = (region or "").strip().lower()
    if lowered == "ca":
        return "California"
    if lowered == "ny":
        return "New York"
    return region.strip()


def _rule_matches(match_type: str, pattern: str, question: str) -> bool:
    lowered = pattern.lower()
    if match_type == "exact":
        return question == lowered
    if match_type == "contains":
        return lowered in question
    return re.search(pattern, question, re.IGNORECASE) is not None
