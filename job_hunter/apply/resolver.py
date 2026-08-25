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
    (("last name", "surname", "family name", "father's family name", "fathers family name"), "identity.last_name"),
    (("full name", "legal name", "name"), "identity.full_name"),
    (("email", "email address"), "identity.email"),
    (("sponsorship", "require visa"), "work_authorization.requires_future_sponsorship"),
    (("authorized to work", "work authorization"), "work_authorization.us_work_authorized"),
    (("cpt",), "work_authorization.cpt"),
    (("opt",), "work_authorization.opt"),
    (("city",), "identity.city"),
    (("country",), "identity.country"),
    (("linkedin",), "identity.linkedin_url"),
    (("github",), "identity.github_url"),
    (("portfolio", "website"), "identity.portfolio_url"),
    (("degree",), "education.degree"),
    (("major", "field of study"), "education.major"),
    (("graduation", "graduate date"), "education.graduation_date"),
    (("gpa",), "education.gpa"),
    (("current company", "current employer"), "employment.current_company"),
    (("current title", "job title", "title"), "employment.current_title"),
    (("years of experience",), "employment.years_experience"),
    (("salary", "compensation expectation", "compensation expectations"), "preferences.salary_min_usd"),
    (("remote",), "preferences.remote_ok"),
    (("relocation", "relocate"), "preferences.relocation_ok"),
]

_INTENT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "consent_required",
        (
            "provide your consent",
            "has my consent",
            "consent to the terms and conditions",
            "accepttermsandagreements",
        ),
    ),
    ("current_location", ("current location", "where are you based", "city/state of residence")),
    (
        "work_auth_us",
        (
            "legally authorized to work in the united states",
            "authorized to work in the united states",
            "eligible to legally work in the united states",
            "legally eligible to work in the u.s.",
            "legally permitted to work in the country where this job is located",
        ),
    ),
    (
        "future_sponsorship_us",
        (
            "require employer sponsorship to work in the united states",
            "require sponsorship to work in the united states",
            "require visa sponsorship for employment",
            "require datarobot sponsorship for a visa or work permit",
            "require medpace inc. to commence",
        ),
    ),
    (
        "employment_eligibility_us",
        (
            "appropriate option describing your employment eligibility",
            "employment eligibility",
        ),
    ),
    (
        "on_site_acknowledgement",
        (
            "requires me to work on-site",
            "requires me to work on site",
            "able and willing to work",
            "relocate if needed",
            "able to work onsite in one of our offices",
            "able to work on-site in one of our offices",
            "comfortable being onsite",
            "comfortable being on-site",
            "anchor days",
            "willing to relocate",
            "willingness to relocate",
        ),
    ),
    ("education_end_month", ("end date month",)),
    ("education_end_year", ("end date year", "what year will you graduate")),
    ("identity_linkedin_url", ("linkedin profile",)),
    ("identity_additional_link", ("additional link",)),
]

_FIELD_CAPABILITIES: tuple[FieldCapability, ...] = (
    FieldCapability(
        portal="workday",
        widget_types=("checkbox", "checkbox-group"),
        intents=("consent_required",),
        resolver_mode="computed_yes",
        submit_policy="safe_autofill_if_single_option",
    ),
    FieldCapability(
        portal="workday",
        widget_types=("radio-group", "select-one", "listbox-button"),
        intents=("work_auth_us", "future_sponsorship_us", "employment_eligibility_us"),
        resolver_mode="structured_boolean_yes_no",
        submit_policy="safe_autofill",
    ),
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
        portal="ashby",
        widget_types=("yes-no",),
        intents=("on_site_acknowledgement", "work_auth_us", "future_sponsorship_us"),
        resolver_mode="computed_yes",
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

        # Exact question rules are deliberate, portal-specific corrections
        # and must be able to supersede a generic computed fallback.
        for rule in self.answers.question_overrides:
            if rule.match_type == "exact" and _rule_matches(rule.match_type, rule.pattern, normalized_question):
                return AnswerResolution(answer=rule.answer, source="override:exact", matched_rule=rule.pattern)

        computed = self._computed_answer(
            question=normalized_question,
            field_name=normalized_field_name,
            field_type=field_type.strip().lower(),
        )
        if computed is not None:
            return computed

        structured_key = self._structured_key_for(question=normalized_question, field_name=normalized_field_name)
        if structured_key:
            structured_value = self._structured.get(structured_key, "")
            if structured_value:
                if structured_value.lower() in {"true", "false"}:
                    return AnswerResolution(
                        answer="Yes" if structured_value.lower() == "true" else "No",
                        source=f"structured:{structured_key}",
                    )
                return AnswerResolution(answer=structured_value, source=f"structured:{structured_key}")

        default_key = normalized_field_name or normalized_question
        if default_key in self.answers.field_defaults:
            return AnswerResolution(answer=self.answers.field_defaults[default_key], source=f"default:{default_key}")

        for rule in self.answers.question_overrides:
            if rule.match_type == "exact":
                continue
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

    def explicit_override(self, *, question_text: str) -> AnswerResolution | None:
        normalized_question = " ".join(question_text.lower().split())
        for rule in self.answers.question_overrides:
            if _rule_matches(rule.match_type, rule.pattern, normalized_question):
                return AnswerResolution(answer=rule.answer, source=f"override:{rule.match_type}", matched_rule=rule.pattern)
        return None

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
        if question.rstrip("*").strip() in {"school", "school or university"}:
            return "education.school"
        if (
            field_name in {"phone", "phone_number", "mobile"}
            or question.rstrip("*").strip() in {"phone", "phone number", "mobile"}
        ):
            return "identity.phone"
        if (
            field_name in {"state", "state/province", "region", "province", "countryregion"}
            or question.rstrip("*").strip() in {"state", "state/province", "region", "province", "department"}
        ):
            return "identity.region"

        if question.rstrip("*").strip() in {"location", "current location"}:
            return "identity.city"

        for patterns, key in _QUESTION_FIELD_MAP:
            if any(_question_contains_pattern(question, pattern) or pattern == field_name for pattern in patterns):
                if key == "identity.full_name" and any(term in question for term in ("related to", "referral", "who currently works", "referrer", "emergency contact", "relatives", "family")):
                    continue
                if key == "identity.city" and any(term in question for term in ("council", "government", "agency", "elected", "utility", "commission")):
                    continue
                if key == "identity.portfolio_url" and any(term in question for term in ("former employee", "previously employed", "previous worker", "worked for", "affiliates", "subsidiaries")):
                    continue
                if key == "identity.email" and any(term in question for term in ("contact you by email", "consent", "retain", "retaining", "news", "opportunities", "updates")):
                    continue
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

        # User-declared global policy: U.S. work authorization is affirmed and
        # current or future sponsorship is declined, regardless of portal wording.
        if _is_us_work_authorization_question(question):
            return AnswerResolution(answer="Yes", source="policy:work_authorization.us_authorized")

        if _is_us_sponsorship_question(question):
            return AnswerResolution(answer="No", source="policy:work_authorization.no_sponsorship")

        if "first name" in question or field_name.endswith("first_name") or field_name == "first_name":
            first_name = _first_name(self._structured.get("identity.full_name", ""))
            if first_name:
                return AnswerResolution(answer=first_name, source="computed:identity.first_name")

        if (
            "last name" in question
            or "family name" in question
            or field_name.endswith("last_name")
            or field_name == "last_name"
        ):
            last_name = _last_name(self._structured.get("identity.full_name", ""))
            if last_name:
                return AnswerResolution(answer=last_name, source="computed:identity.last_name")

        if "degree" in question or "highest level of education" in question or field_name.startswith("degree"):
            degree = _canonical_degree(self._structured.get("education.degree", ""))
            if degree:
                return AnswerResolution(answer=degree, source="computed:education.degree")

        if "currently pursuing a major" in question and "following disciplines" in question:
            major = self._structured.get("education.major", "").lower()
            recognized_disciplines = ("computer science", "computer engineering")
            if major:
                is_listed = any(
                    discipline in question and discipline in major for discipline in recognized_disciplines
                )
                return AnswerResolution(
                    answer="Yes" if is_listed else "No",
                    source="computed:education.major_eligibility",
                )

        if (
            forced_intent == "consent_required"
            or "provide your consent" in question
            or "has my consent" in question
            or "consent to the terms and conditions" in question
            or normalized_field_name == "accepttermsandagreements"
        ):
            return AnswerResolution(answer="Yes", source="computed:consent_acknowledgement")

        if forced_intent == "current_location" or "current location" in question:
            city = self._structured.get("identity.city", "").strip()
            region = self._structured.get("identity.region", "").strip()
            if city and region:
                return AnswerResolution(answer=f"{city}, {region}", source="computed:identity.location")
            if city:
                return AnswerResolution(answer=city, source="computed:identity.location")

        if "18 or older" in question or "at least 18 years old" in question or "18 years of age" in question:
            return AnswerResolution(answer="Yes", source="computed:eligibility.age_of_majority")

        if "minimum qualification" in question or "basic qualification" in question or "meet the requirements" in question:
            return AnswerResolution(answer="Yes", source="policy:eligibility.meets_minimum_qualifications")


        if "ernst & young" in question or "ernst and young" in question:
            return AnswerResolution(answer="No", source="policy:prior_employer.no")

        if "non-competition" in question or "non-disclosure" in question or "non-solicitation" in question:
            return AnswerResolution(answer="No", source="policy:employment.no_conflicting_agreement")

        if "conflict of interest" in question or ("immediate family" in question and "relationships" in question):
            return AnswerResolution(answer="No", source="policy:employment.no_conflict_of_interest")


        if "intellectual property rights" in question or "patents, trademarks" in question:
            return AnswerResolution(answer="No", source="policy:employment.no_ip_rights")

        if (
            "secondary non-intel" in question
            or ("board of directors" in question and "non-intel" in question)
            or "if hired, do you intend to" in question
            or "do you intend to (select all that apply)" in question
        ):
            return AnswerResolution(answer="Neither", source="policy:employment.outside_activities_neither")


        if "department of defense" in question or "dod" in question.split():
            return AnswerResolution(answer="No", source="policy:employment.no_dod")

        if "federal, state or local government" in question:
            return AnswerResolution(answer="No", source="policy:employment.no_government")

        if "export control" in question:
            return AnswerResolution(answer="Yes", source="policy:work_authorization.export_control_authorized")


        if "valid driver's license" in question or "valid drivers license" in question:
            drivers_license = str(self.answers.field_defaults.get("valid_drivers_license", "")).strip().lower()
            if drivers_license in {"true", "yes", "1"}:
                return AnswerResolution(answer="Yes", source="policy:identity.valid_drivers_license")

        if "ai-assisted resume-screening" in question or "automated screening" in question:
            screening_opt_in = str(self.answers.field_defaults.get("ai_resume_screening_opt_in", "")).strip().lower()
            if screening_opt_in in {"true", "yes", "1", "opt in"}:
                return AnswerResolution(answer="Opt In", source="policy:application.ai_resume_screening")

        if "pursue a cpa" in question or "pursuing a cpa" in question:
            cpa_intent = str(self.answers.field_defaults.get("pursue_cpa", "")).strip().lower()
            if cpa_intent in {"false", "no", "0"}:
                return AnswerResolution(answer="No", source="policy:education.pursue_cpa")

        if (
            "active student" in question
            or "currently enrolled" in question
            or "current student" in question
            or "graduated within the past" in question
            or "enrolled in a degree" in question
        ):
            return AnswerResolution(answer="Yes", source="policy:education.active_student")

        if "conferences/career fairs" in question or "conferences or career fairs" in question or ("conferences" in question and "career fairs" in question):
            return AnswerResolution(answer="None of the above", source="policy:recruiting_events.none")

        if "degree are you working towards" in question or "what degree are you working towards" in question or "highest degree obtained" in question:
            degree = _canonical_degree(self._structured.get("education.degree", "").strip()) or "Master's Degree"
            return AnswerResolution(answer=degree, source="computed:education.degree")

        if "geographic mobility" in question or "willing to relocate" in question:
            relocation_ok = self._structured.get("preferences.relocation_ok", "").strip().lower()
            if relocation_ok in {"true", "yes", "1"}:
                return AnswerResolution(answer="Yes", source="computed:preferences.relocation_ok")
            return AnswerResolution(answer="No", source="computed:preferences.relocation_ok")



        if _is_close_relationship_question(question):
            close_relationship = str(
                self.answers.field_defaults.get("close_personal_relationship", "")
            ).strip().lower()
            if close_relationship in {"false", "no", "0"}:
                return AnswerResolution(answer="No", source="policy:employment.close_personal_relationship")

        if "currently work at" in question and "client" in question:
            client_relationship = str(
                self.answers.field_defaults.get("company_client_relationship", "")
            ).strip().lower()
            if client_relationship in {"false", "no", "0"}:
                return AnswerResolution(answer="No", source="policy:employment.company_client_relationship")

        if (
            "running for public office" in question
            or ("government entity" in question and "past two" in question)
        ):
            return AnswerResolution(answer="No", source="policy:public_office.no")



        if (
            "discharged" in question
            and "resign" in question
            and "termination" in question
        ):
            return AnswerResolution(answer="No", source="policy:employment_termination.no")

        if "application acknowledgement" in question and "i acknowledge" in question:
            return AnswerResolution(answer="I acknowledge", source="policy:application_acknowledgement.accept")

        if (
            "will not disclose or use" in question
            or ("confidential" in question and "proprietary information" in question)
            or ("former employer" in question and "confidential" in question)
        ):
            return AnswerResolution(answer="I agree", source="policy:confidentiality_acknowledgement.agree")

        if (
            "retaining your candidate profile" in question
            or "consent to ge vernova" in question
            or ("consent" in question and "future roles" in question)
            or ("consent" in question and "job opportunities" in question)
            or ("consent" in question and "retaining" in question)
        ):
            return AnswerResolution(answer="I consent", source="policy:candidate_profile_retention.consent")

        if "communication method" in question or "contacting you throughout the recruiting process" in question:
            return AnswerResolution(answer="Email", source="policy:recruiting_communication.email")

        if "ge vernova segment" in question or "strong interest in a ge vernova segment" in question:
            return AnswerResolution(answer="Electrification", source="policy:ge_vernova_segment.electrification")

        if "working onsite" in question or "onsite at our" in question or "onsite office" in question:

            return AnswerResolution(answer="Yes", source="policy:onsite_work.yes")

        if "related to anyone" in question or "who currently works at" in question:
            return AnswerResolution(answer="No", source="policy:related_to_employee.no")

        if "previous weave employee" in question or "previous employee" in question:
            return AnswerResolution(answer="No", source="policy:previous_employee.no")

        if "which categories describe you" in question:
            return AnswerResolution(answer="Hispanic, Latinx or Spanish origin", source="policy:self_identify.ethnicity")

        if "lesbian, gay or bisexual" in question or "lgb" in question:
            return AnswerResolution(answer="No", source="policy:self_identify.lgb.no")

        if "military veteran or service member" in question or "served as part of the military" in question or "currently serving" in question:
            return AnswerResolution(answer="No", source="policy:self_identify.veteran.no")









        if (
            "may waive my right to receive a copy" in question
            and "california civil code" in question
            and "public record" in question
        ):
            return AnswerResolution(answer="Waive", source="policy:california_public_record.waive")

        if "california resident" in question:
            region = self._structured.get("identity.region", "").strip().lower()
            if region in {"ca", "california"}:
                return AnswerResolution(answer="Yes", source="computed:identity.california_resident")

        if _is_prior_employment_question(question):
            prior_employers = _split_answer_values(
                self.answers.field_defaults.get("previous_employers", "")
            )
            if prior_employers:
                answer = "Yes" if any(employer in question for employer in prior_employers) else "No"
                return AnswerResolution(answer=answer, source="policy:employment_history")

        if (
            ("currently working at" in question or "ever worked at" in question)
            and any(token in question for token in ("employee", "intern", "contractor"))
        ):
            current_company = self._structured.get("employment.current_company", "").strip().lower()
            if current_company:
                answer = "Yes" if current_company in question else "No"
                return AnswerResolution(answer=answer, source="computed:employment.company_relationship")

        if "country of the position you are applying" in question:
            country = _canonical_country(self._structured.get("identity.country", "").strip())
            if country:
                return AnswerResolution(answer=country, source="computed:position.country")

        if question == "country" or question == "country*" or normalized_field_name == "country":
            country = _canonical_country(self._structured.get("identity.country", "").strip())
            if country:
                return AnswerResolution(answer=country, source="computed:identity.country")

        # Address fields — pulled from field_defaults so they can be overridden
        # per-profile without touching the resolver logic.
        if (
            "address line 1" in question
            or normalized_field_name in {"address_line_1", "addressline1", "address1", "streetaddress"}
        ):
            addr = str(self.answers.field_defaults.get("address_line_1") or
                       self.answers.field_defaults.get("street_address") or "").strip()
            if addr:
                return AnswerResolution(answer=addr, source="policy:identity.address_line_1")

        if (
            "address line 2" in question
            or normalized_field_name in {"address_line_2", "addressline2", "address2", "apartment", "apt", "unit"}
        ):
            addr2 = str(self.answers.field_defaults.get("address_line_2") or
                        self.answers.field_defaults.get("apartment") or
                        self.answers.field_defaults.get("unit") or "").strip()
            if addr2:
                return AnswerResolution(answer=addr2, source="policy:identity.address_line_2")


        if (
            "zip code" in question
            or "postal code" in question
            or normalized_field_name in {"zip_code", "zipcode", "postalcode", "postal_code", "zip"}
        ):
            postal = str(self.answers.field_defaults.get("zip_code") or
                         self.answers.field_defaults.get("postal_code") or "").strip()
            if postal:
                return AnswerResolution(answer=postal, source="policy:identity.zip_code")

        if (
            question.rstrip("* ").strip() == "county"
            or normalized_field_name in {"county"}
        ):
            county = str(self.answers.field_defaults.get("county") or "").strip()
            if county:
                return AnswerResolution(answer=county, source="policy:identity.county")

        if "specific source" in question or normalized_field_name in {"specific_source", "specificsource"}:
            return AnswerResolution(answer="Company Website", source="policy:how_did_you_hear")

        if "source" in question and "influenced your decision" in question:
            return AnswerResolution(answer="LinkedIn", source="policy:how_did_you_hear.linkedin")

        if (
            "available to start" in question
            or "earliest start date" in question
            or "availability to start" in question
            or "availability start date" in question
            or "when would you be available" in question
        ):
            start_date = str(self.answers.field_defaults.get("available_start_date", "05/18/2026")).strip()
            return AnswerResolution(answer=start_date, source="policy:availability.start_date")

        if "earliest month" in question or "month you'd be able to join" in question or "month you would be able to join" in question:
            return AnswerResolution(answer="May 2026", source="policy:availability.start_month")

        if ("based in san francisco" in question and "relocating" in question) or ("open to relocating" in question and "based in" in question):
            return AnswerResolution(answer="Open to relocating", source="computed:preferences.relocation_choice")

        if "require visa sponsorship to work in your selected location" in question or ("require visa sponsorship" in question and "expire" in question):
            return AnswerResolution(
                answer="No, I am authorized to work in the US for any employer without sponsorship.",
                source="computed:work_authorization.visa_sponsorship_explanation",
            )

        if "why are you interested in working at exa" in question or "why are you interested in working at" in question:
            return AnswerResolution(
                answer="I'm excited about Exa's mission to build neural search for AI models and developers. Having built embedding retrieval systems and agentic extraction pipelines, I want to contribute to Exa's web-scale indexing, reranking, and search infrastructure.",
                source="essay:interest_in_company",
            )

        if "something you worked on that you were proud of" in question or "proud of" in question:
            return AnswerResolution(
                answer="I built an end-to-end agentic document extraction and evaluation pipeline that parsed multi-format ESG and financial filings with 94% accuracy, optimizing LLM token throughput with hybrid OCR and schema-validated JSON outputs.",
                source="essay:proud_project",
            )

        if "what motivates you" in question:
            return AnswerResolution(
                answer="I am motivated by solving core retrieval, performance, and distributed systems challenges—transforming unstructured web data into fast, clean, and semantically rich APIs that power autonomous AI agents.",
                source="essay:motivation",
            )

        if "how did you hear about exa" in question:
            return AnswerResolution(answer="GitHub", source="policy:how_did_you_hear.github")






        if normalized_field_name in {"region2", "countryregion"}:
            state = self._structured.get("identity.region", "").strip()
            if state:
                return AnswerResolution(answer="California" if state.upper() == "CA" else state, source="structured:identity.region")



        if "compensation expectations" in question or "salary expectations" in question:

            compensation = self._structured.get("preferences.salary_min_usd", "").strip()
            if compensation:
                return AnswerResolution(answer=compensation, source="computed:preferences.salary_min_usd")


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

        if (
            forced_intent == "employment_eligibility_us"
            or "please select the statement that best applies to you" in question
            or "statement that best describes your work authorization" in question
            or "statement that best applies to your authorization" in question
        ):
            authorized = self._structured.get("work_authorization.us_work_authorized", "").strip().lower()
            sponsorship = self._structured.get("work_authorization.requires_future_sponsorship", "").strip().lower()
            if authorized in {"true", "yes", "1"} and sponsorship in {"false", "no", "0"}:
                return AnswerResolution(
                    answer="__work_auth_us_no_sponsorship__",
                    source="computed:work_authorization.employment_eligibility",
                )
            if authorized in {"true", "yes", "1"} and sponsorship in {"true", "yes", "1"}:
                return AnswerResolution(
                    answer="__work_auth_us_sponsorship_required__",
                    source="computed:work_authorization.employment_eligibility",
                )
            if authorized in {"false", "no", "0"}:
                return AnswerResolution(
                    answer="__work_auth_us_not_authorized__",
                    source="computed:work_authorization.employment_eligibility",
                )


        if forced_intent == "on_site_acknowledgement":
            return AnswerResolution(answer="Yes", source="computed:preferences.on_site_acknowledgement")

        if "phone device type" in question:
            return AnswerResolution(answer="Mobile", source="computed:identity.phone_device_type")


        if "other opportunities" in question or "considered for other" in question or "future opportunities" in question:
            return AnswerResolution(answer="Yes", source="policy:future_opportunities")

        if "convicted of" in question or "pled guilty" in question or "criminal or drug related" in question:
            return AnswerResolution(answer="No", source="policy:criminal_history")

        if "related to a current or previous employee" in question or "relatives currently employed" in question:
            return AnswerResolution(answer="No", source="policy:relative_employee")


        if question.rstrip("*").strip() == "company" or normalized_field_name == "companyname":
            company = self._structured.get("employment.current_company", "").strip()
            if company:
                return AnswerResolution(answer=company, source="structured:employment.current_company")

        if "startdate-datesection" in normalized_field_name:
            experience_start = _employment_start_date(
                current_start_date=self._structured.get("employment.current_start_date", ""),
                years_experience=self._structured.get("employment.years_experience", ""),
            )
            if "month-input" in normalized_field_name:
                return AnswerResolution(
                    answer=str(datetime.strptime(experience_start["month"], "%B").month),
                    source="computed:employment.start_month",
                )
            if "year-input" in normalized_field_name:
                return AnswerResolution(answer=experience_start["year"], source="computed:employment.start_year")

        if "enddate-datesection" in normalized_field_name:
            experience_end = _employment_end_date()
            if "month-input" in normalized_field_name:
                return AnswerResolution(
                    answer=str(datetime.strptime(experience_end["month"], "%B").month),
                    source="computed:employment.end_month",
                )
            if "year-input" in normalized_field_name:
                return AnswerResolution(answer=experience_end["year"], source="computed:employment.end_year")

        if "country phone code" in question or "countryphonecode" in normalized_field_name:
            country_code = _phone_country_code(self._structured.get("identity.phone", ""))
            if country_code:
                return AnswerResolution(answer=country_code, source="computed:identity.phone_country_code")

        if (
            (question in {"phone number*", "phone number", "phone", "primary phone", "cell phone", "mobile phone"}
             or ("phone" in question and not any(k in question for k in ("consent", "sms", "text", "opt in", "agree", "code", "type", "device"))))
            or normalized_field_name in {"phonenumber", "cellphone", "primaryphone", "phone"}
        ):
            local_phone = _phone_local_number(self._structured.get("identity.phone", ""))
            if local_phone:
                return AnswerResolution(answer=local_phone, source="computed:identity.phone_local_number")

        if "are you over 18" in question:
            return AnswerResolution(answer="Yes", source="computed:identity.over_18")

        if "eligible to work in the country" in question or "authorized to work in the country" in question:
            return AnswerResolution(answer="Yes", source="policy:work_authorization.authorized")

        if "gender" in question or normalized_field_name in {"gender", "sex"}:
            gender = str(self.answers.field_defaults.get("gender", "Male")).strip()
            if gender:
                return AnswerResolution(answer=gender, source="policy:self_identify.gender")

        if "hispanic or latino" in question:
            race_ethnicity = str(self.answers.field_defaults.get("race_ethnicity", "")).strip().lower()
            if "hispanic" in race_ethnicity or "latino" in race_ethnicity:
                return AnswerResolution(answer="Yes", source="computed:self_identify.hispanic_or_latino")
            if race_ethnicity:
                return AnswerResolution(answer="No", source="computed:self_identify.hispanic_or_latino")

        if "ethnicity" in question or normalized_field_name in {"ethnicity", "raceethnicity"}:
            race_ethnicity = str(self.answers.field_defaults.get("race_ethnicity", "")).strip()
            if race_ethnicity:
                return AnswerResolution(answer=race_ethnicity, source="computed:self_identify.ethnicity")

        if "veteran status" in question or "veteran's status" in question or "categories of protected veterans" in question or "veteran" in normalized_field_name:
            veteran_status = str(self.answers.field_defaults.get("veteran_status", "")).strip().lower()
            if veteran_status in {"false", "no", "0"}:
                return AnswerResolution(
                    answer="I am not a protected veteran.",
                    source="computed:self_identify.veteran_status",
                )


        if "disability" in question or "disabilitystatus" in normalized_field_name:
            disability_status = str(self.answers.field_defaults.get("disability_status", "")).strip().lower()
            if disability_status in {"false", "no", "0"}:
                return AnswerResolution(
                    answer="No",
                    source="computed:self_identify.disability_status",
                )
            if disability_status in {"true", "yes", "1"}:
                return AnswerResolution(
                    answer="Yes",
                    source="computed:self_identify.disability_status",
                )


        if (
            "certify that i have read" in question
            or "employment understanding" in question
            or "agreebutton" in normalized_field_name
        ):
            return AnswerResolution(answer="true", source="policy:agreement.consent")

        if "middle name" in question or normalized_field_name == "middlename":
            return AnswerResolution(answer="", source="policy:identity.middle_name_empty")

        if "preferred name" in question or normalized_field_name == "preferredname":
            first_name = self._structured.get("identity.first_name", "")
            return AnswerResolution(answer=first_name, source="computed:identity.first_name")

        if "shift preference" in question or normalized_field_name == "shiftpreference":
            return AnswerResolution(answer="No Preference", source="policy:job.no_shift_preference")

        if "preferred contact method" in question:
            return AnswerResolution(answer="Email", source="policy:contact.preferred_email")


        if "previously" in question and "employ" in question:
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
            experience_start = _employment_start_date(
                current_start_date=self._structured.get("employment.current_start_date", ""),
                years_experience=self._structured.get("employment.years_experience", ""),
            )
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

        if "start date month" in question or field_name.startswith("start-month"):
            start_date = self._structured.get("education.start_date", "").strip()
            if start_date:
                match = re.search(r"-(\d{1,2})$", start_date)
                if match:
                    month_num = int(match.group(1))
                    if 1 <= month_num <= 12:
                        return AnswerResolution(
                            answer=datetime(2000, month_num, 1).strftime("%B"),
                            source="structured:education.start_date",
                        )
            return AnswerResolution(answer="August", source="computed:education.start_month")

        if forced_intent == "education_end_month" or "end date month" in question or field_name.startswith("end-month"):

            month = _graduation_month_name(self._structured.get("education.graduation_date", ""))
            if month:
                return AnswerResolution(answer=month, source="computed:education.end_month")

        if (
            "start date year" in question
            or field_name.startswith("start-year")
            or "firstyearattended" in normalized_field_name
            or "from (actual or expected)" in question
        ):
            start_date = self._structured.get("education.start_date", "").strip()
            if start_date:
                match = re.match(r"^(\d{4})", start_date)
                if match:
                    return AnswerResolution(answer=match.group(1), source="structured:education.start_date")
            year = _education_start_year(
                graduation_date=self._structured.get("education.graduation_date", ""),
                degree=self._structured.get("education.degree", ""),
            )
            if year:
                return AnswerResolution(answer=year, source="computed:education.start_year")

        if (
            forced_intent == "education_end_year"
            or "end date year" in question
            or field_name.startswith("end-year")
            or "lastyearattended" in normalized_field_name
            or "to (actual or expected)" in question
            or "what year will you graduate" in question
        ):
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


def _question_contains_pattern(question: str, pattern: str) -> bool:
    """Match field terms as words so `city` does not match `ethnicity`."""
    return re.search(rf"(?<!\w){re.escape(pattern)}(?!\w)", question) is not None


def _graduation_year(graduation_date: str) -> str:
    match = re.match(r"^\s*(\d{4})", graduation_date or "")
    return match.group(1) if match else ""


def _first_name(full_name: str) -> str:
    parts = [part for part in (full_name or "").strip().split() if part]
    return parts[0] if parts else ""


def _last_name(full_name: str) -> str:
    parts = [part for part in (full_name or "").strip().split() if part]
    return parts[-1] if len(parts) >= 2 else ""


def _phone_country_code(phone: str) -> str:
    raw = (phone or "").strip()
    if not raw:
        return ""
    match = re.match(r"^\+?(\d{1,3})", raw)
    if not match:
        return ""
    return f"+{match.group(1)}"


def _phone_local_number(phone: str) -> str:
    raw = (phone or "").strip()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if raw.startswith("+"):
        country_code = _phone_country_code(raw).lstrip("+")
        if country_code and digits.startswith(country_code):
            digits = digits[len(country_code):]
    return digits


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


def _employment_start_date(*, current_start_date: str, years_experience: str) -> dict[str, str]:
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", current_start_date.strip())
    if match:
        year, month = match.groups()
        month_number = int(month)
        if 1 <= month_number <= 12:
            return {
                "year": year,
                "month": datetime(2000, month_number, 1).strftime("%B"),
                "day": "1",
            }
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


def _is_us_work_authorization_question(question: str) -> bool:
    mentions_us = any(token in question for token in ("united states", "u.s.", "us "))
    asks_authorization = any(token in question for token in ("authorized to work", "authorised to work", "eligible to work", "lawfully work", "legally work"))
    return mentions_us and asks_authorization


def _is_us_sponsorship_question(question: str) -> bool:
    if "medpace" in question:
        return False
    mentions_sponsorship = any(token in question for token in ("sponsor", "sponsorship", "visa", "immigration"))
    asks_future_need = any(
        token in question
        for token in (
            "require",
            "need",
            "now or in the future",
            "current or future",
        )
    )
    return mentions_sponsorship and asks_future_need



def _is_prior_employment_question(question: str) -> bool:
    return bool(
        re.search(
            r"\b(?:ever|previously)\s+(?:been\s+)?(?:worked|employed)\s+(?:at|for|by)\b",
            question,
        )
    )


def _is_close_relationship_question(question: str) -> bool:
    relationship_terms = (
        "relatives currently working",
        "family members currently working",
        "familial",
        "romantic",
        "close personal relationship",
    )
    return any(term in question for term in relationship_terms) and any(
        term in question for term in ("employee", "applicant", "employed", "working")
    )


def _split_answer_values(value: object) -> tuple[str, ...]:
    return tuple(
        item.strip().lower()
        for item in str(value or "").split("||")
        if item.strip()
    )


def _rule_matches(match_type: str, pattern: str, question: str) -> bool:
    lowered = pattern.lower()
    if match_type == "exact":
        return question == lowered
    if match_type == "contains":
        return lowered in question
    return re.search(pattern, question, re.IGNORECASE) is not None
