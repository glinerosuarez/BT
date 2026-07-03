from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class ApplicationIdentity:
    full_name: str
    email: str
    phone: str
    city: str
    region: str
    country: str
    linkedin_url: str
    github_url: str
    portfolio_url: str


@dataclass(slots=True)
class WorkAuthorization:
    us_work_authorized: bool
    requires_future_sponsorship: bool
    cpt: bool
    opt: bool


@dataclass(slots=True)
class EducationProfile:
    school: str
    degree: str
    major: str
    graduation_date: str
    gpa: str


@dataclass(slots=True)
class EmploymentProfile:
    current_company: str
    current_title: str
    years_experience: str


@dataclass(slots=True)
class PreferenceProfile:
    salary_min_usd: str
    remote_ok: bool
    relocation_ok: bool


@dataclass(slots=True)
class ApplicationProfile:
    identity: ApplicationIdentity
    work_authorization: WorkAuthorization
    education: EducationProfile
    employment: EmploymentProfile
    preferences: PreferenceProfile
    uploads: dict[str, str] = field(default_factory=dict)

    def structured_answers(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        for section_name, section in asdict(self).items():
            if isinstance(section, dict):
                for key, value in section.items():
                    payload[f"{section_name}.{key}"] = str(value)
        return payload


@dataclass(slots=True)
class AnswerRule:
    match_type: str
    pattern: str
    answer: str


@dataclass(slots=True)
class ApplicationAnswers:
    question_overrides: list[AnswerRule] = field(default_factory=list)
    field_defaults: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AnswerResolution:
    answer: str
    source: str
    matched_rule: str | None = None


@dataclass(slots=True)
class Blocker:
    reason: str
    question_text: str = ""
    field_name: str = ""
    field_type: str = ""
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "question_text": self.question_text,
            "field_name": self.field_name,
            "field_type": self.field_type,
            "details": self.details,
        }


@dataclass(slots=True)
class StepSnapshot:
    step_key: str
    step_label: str
    status: str
    field_name: str = ""
    field_type: str = ""
    question_text: str = ""
    answer_source: str = ""
    answer_value: str = ""
    screenshot_path: str = ""
    payload: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "step_key": self.step_key,
            "step_label": self.step_label,
            "status": self.status,
            "field_name": self.field_name,
            "field_type": self.field_type,
            "question_text": self.question_text,
            "answer_source": self.answer_source,
            "answer_value": self.answer_value,
            "screenshot_path": self.screenshot_path,
            "payload": self.payload,
        }


@dataclass(slots=True)
class SubmitResult:
    status: str
    current_url: str
    confirmation_payload: dict[str, object] = field(default_factory=dict)
    blocker: Blocker | None = None
    steps: list[StepSnapshot] = field(default_factory=list)
    target_url: str = ""
    adapter_name: str = ""


@dataclass(slots=True)
class ApplicationRunRecord:
    application_run_id: int
    job_id: int
    profile_name: str
    adapter_name: str
    status: str
    target_url: str
    current_url: str
    output_dir: str
