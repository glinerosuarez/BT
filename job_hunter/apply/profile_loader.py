from __future__ import annotations

import json
from pathlib import Path

from .types import (
    AnswerRule,
    ApplicationAnswers,
    ApplicationIdentity,
    ApplicationProfile,
    EducationProfile,
    EmploymentProfile,
    PreferenceProfile,
    WorkAuthorization,
)


class ProfileValidationError(RuntimeError):
    pass


def load_application_inputs(profile_root: str, profile_name: str) -> tuple[ApplicationProfile, ApplicationAnswers]:
    profile_dir = Path(profile_root).expanduser() / profile_name
    profile_path = profile_dir / "application_profile.json"
    answers_path = profile_dir / "application_answers.json"
    if not profile_path.exists():
        raise ProfileValidationError(f"Missing required profile file: {profile_path}")
    if not answers_path.exists():
        raise ProfileValidationError(f"Missing required profile file: {answers_path}")
    return _parse_profile(_read_json(profile_path)), _parse_answers(_read_json(answers_path))


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileValidationError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileValidationError(f"Expected object in {path}")
    return payload


def _require_section(payload: dict[str, object], name: str) -> dict[str, object]:
    raw = payload.get(name)
    if not isinstance(raw, dict):
        raise ProfileValidationError(f"Missing required object section: {name}")
    return raw


def _require_str(section: dict[str, object], key: str, *, section_name: str) -> str:
    value = str(section.get(key) or "").strip()
    if not value:
        raise ProfileValidationError(f"Missing required field: {section_name}.{key}")
    return value


def _require_bool(section: dict[str, object], key: str, *, section_name: str) -> bool:
    value = section.get(key)
    if not isinstance(value, bool):
        raise ProfileValidationError(f"Missing required boolean field: {section_name}.{key}")
    return value


def _parse_profile(payload: dict[str, object]) -> ApplicationProfile:
    identity = _require_section(payload, "identity")
    work_auth = _require_section(payload, "work_authorization")
    education = _require_section(payload, "education")
    employment = _require_section(payload, "employment")
    preferences = _require_section(payload, "preferences")
    uploads = payload.get("uploads") if isinstance(payload.get("uploads"), dict) else {}
    return ApplicationProfile(
        identity=ApplicationIdentity(
            full_name=_require_str(identity, "full_name", section_name="identity"),
            email=_require_str(identity, "email", section_name="identity"),
            phone=_require_str(identity, "phone", section_name="identity"),
            city=_require_str(identity, "city", section_name="identity"),
            region=_require_str(identity, "region", section_name="identity"),
            country=_require_str(identity, "country", section_name="identity"),
            linkedin_url=_require_str(identity, "linkedin_url", section_name="identity"),
            github_url=_require_str(identity, "github_url", section_name="identity"),
            portfolio_url=_require_str(identity, "portfolio_url", section_name="identity"),
        ),
        work_authorization=WorkAuthorization(
            us_work_authorized=_require_bool(work_auth, "us_work_authorized", section_name="work_authorization"),
            requires_future_sponsorship=_require_bool(
                work_auth,
                "requires_future_sponsorship",
                section_name="work_authorization",
            ),
            cpt=_require_bool(work_auth, "cpt", section_name="work_authorization"),
            opt=_require_bool(work_auth, "opt", section_name="work_authorization"),
        ),
        education=EducationProfile(
            school=_require_str(education, "school", section_name="education"),
            degree=_require_str(education, "degree", section_name="education"),
            major=_require_str(education, "major", section_name="education"),
            graduation_date=_require_str(education, "graduation_date", section_name="education"),
            gpa=_require_str(education, "gpa", section_name="education"),
        ),
        employment=EmploymentProfile(
            current_company=_require_str(employment, "current_company", section_name="employment"),
            current_title=_require_str(employment, "current_title", section_name="employment"),
            years_experience=_require_str(employment, "years_experience", section_name="employment"),
        ),
        preferences=PreferenceProfile(
            salary_min_usd=_require_str(preferences, "salary_min_usd", section_name="preferences"),
            remote_ok=_require_bool(preferences, "remote_ok", section_name="preferences"),
            relocation_ok=_require_bool(preferences, "relocation_ok", section_name="preferences"),
        ),
        uploads={str(key): str(value).strip() for key, value in uploads.items() if str(key).strip()},
    )


def _parse_answers(payload: dict[str, object]) -> ApplicationAnswers:
    raw_overrides = payload.get("question_overrides", [])
    raw_defaults = payload.get("field_defaults", {})
    if not isinstance(raw_overrides, list):
        raise ProfileValidationError("question_overrides must be an array")
    if not isinstance(raw_defaults, dict):
        raise ProfileValidationError("field_defaults must be an object")
    overrides: list[AnswerRule] = []
    for idx, item in enumerate(raw_overrides):
        if not isinstance(item, dict):
            raise ProfileValidationError(f"question_overrides[{idx}] must be an object")
        match_type = str(item.get("match_type") or "").strip()
        if match_type not in {"exact", "contains", "regex"}:
            raise ProfileValidationError(f"Unsupported match_type in question_overrides[{idx}]")
        pattern = str(item.get("pattern") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not pattern or not answer:
            raise ProfileValidationError(f"question_overrides[{idx}] must include pattern and answer")
        overrides.append(AnswerRule(match_type=match_type, pattern=pattern, answer=answer))
    defaults = {str(key).strip(): str(value).strip() for key, value in raw_defaults.items() if str(key).strip()}
    return ApplicationAnswers(question_overrides=overrides, field_defaults=defaults)
