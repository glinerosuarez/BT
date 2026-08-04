from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import DEFAULT_POLICY_REJECT_PATTERNS, Settings
from job_hunter.models import JobRecord
from job_hunter.pipeline import (
    _classify_compensation,
    _dedupe_key,
    _evaluate_eligibility,
    _evaluate_source_quality,
    _fails_policy_gate,
    _is_internship,
    _passes_data_role_gate,
    _is_us_scope,
    _score_relevance,
    run_pipeline,
)
from job_hunter.storage import JobStore


def make_settings(db_path: str) -> Settings:
    return Settings(
        db_path=db_path,
        poll_interval_minutes=15,
        request_timeout_seconds=10,
        use_arbeitnow=False,
        use_remotive=False,
        use_themuse=False,
        use_greenhouse=False,
        use_lever=False,
        use_rss=False,
        use_github_repos=False,
        use_ashby=False,
        use_handshake=False,
        use_usajobs=False,
        use_adzuna=False,
        min_relevance_score=3.0,
        min_eligibility_confidence=0.4,
        notify_on_ambiguous_eligibility=True,
        max_posting_age_days=7,
        telegram_bot_token=None,
        telegram_chat_id=None,
        themuse_pages=2,
        greenhouse_boards=[],
        lever_companies=[],
        rss_feeds=[],
        github_repo_readmes=[],
        ashby_boards=[],
        handshake_search_urls=[],
        title_blacklist_patterns=[r"\brecruiter\b"],
        data_role_title_patterns=[
            r"\b(machine learning|ml)\b",
            r"\bdata (science|scientist)\b",
            r"\bdata engineer(ing)?\b",
            r"\banalytics engineer\b",
        ],
        non_data_title_patterns=[
            r"\bdeveloper advocacy\b",
            r"\bgo[- ]to[- ]market\b",
            r"\b(content|video content)\b",
        ],
        policy_reject_patterns=[
            r"\bph\.?d\.?\b",
            r"\bdoctoral\b",
        ],
        min_data_signal_count=2,
        greenhouse_token_file=None,
        lever_token_file=None,
        rss_feed_file=None,
        greenhouse_quarantine_file=None,
        lever_quarantine_file=None,
        rss_quarantine_file=None,
        source_failure_quarantine_threshold=3,
        source_restore_success_threshold=2,
        source_probe_limit_per_run=5,
        handshake_profile_dir=".handshake-profile",
        handshake_headless=True,
        handshake_max_results=25,
        handshake_page_timeout_seconds=30,
        handshake_fetch_details=True,
        usajobs_user_agent=None,
        usajobs_auth_key=None,
        usajobs_results_per_page=250,
        adzuna_app_id=None,
        adzuna_app_key=None,
        adzuna_country="us",
        adzuna_pages=2,
    )


def recent_posted_at(days_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class FakeSource:
    name = "fake"

    def __init__(self, payload: list[dict], fetch_meta: dict[str, object] | None = None) -> None:
        self.payload = payload
        self.fetch_meta = fetch_meta or {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        _ = timeout_seconds
        return self.payload

    def get_fetch_meta(self) -> dict[str, object]:
        return dict(self.fetch_meta)


class FakeNotifier:
    def __init__(self) -> None:
        self.sent = 0

    def send(self, job: JobRecord) -> bool:
        self.sent += 1
        return True


class FakeSemanticResult:
    semantic_base_score = 0.78
    semantic_match_score = 0.72
    semantic_match_label = "pass"
    semantic_match_reason_codes = ["semantic_similarity_pass"]
    semantic_research_heaviness_score = 0.0
    semantic_adjustment_reason_codes = []
    semantic_profile_id = "data_engineering"
    semantic_model_name = "fake-semantic-model"
    semantic_scorer_version = "semantic_shadow_v1"
    semantic_text_hash = "fake-semantic-hash"


class FakeSemanticScorer:
    def score(self, job):
        _ = job
        return FakeSemanticResult()


class PipelineUnitTests(unittest.TestCase):
    def test_default_policy_filters_no_longer_blacklist_operations_research_or_economics_team(self) -> None:
        self.assertNotIn(r"\beconomics team\b", DEFAULT_POLICY_REJECT_PATTERNS)
        self.assertNotIn(r"\boperations research\b", DEFAULT_POLICY_REJECT_PATTERNS)

    def test_compensation_classification(self) -> None:
        self.assertEqual(_classify_compensation("Data Engineering Intern", "Unpaid · Internship Remote"), "unpaid")
        self.assertEqual(_classify_compensation("Data Science Internship", "$18-50/hr · Internship"), "paid")
        self.assertEqual(_classify_compensation("ML Intern", "Build models and pipelines"), "unknown")
        self.assertEqual(
            _classify_compensation(
                "Data Science Internship",
                "Data Science Internship $18-50/hr Internship Pasadena, CA Similar Jobs Unpaid Internship Remote",
            ),
            "paid",
        )

    def test_eligibility_generic_authorized_to_work_requirement_is_ambiguous(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Machine Learning Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at="2026-05-20",
            description="Must be authorized to work in the US.",
            ingested_at="2026-05-25T00:00:00+00:00",
        )
        status, confidence, signals, _ = _evaluate_eligibility(job)
        self.assertEqual(status, "ambiguous")
        self.assertEqual(confidence, 0.6)
        self.assertIn("must_authorized_us", signals)

    def test_eligibility_rejects_current_or_future_sponsorship_block(self) -> None:
        job = JobRecord(
            source="x",
            external_id="2",
            url="https://example.com/2",
            title="Data Science Internship",
            company="Example",
            location="Pasadena, CA",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "Open to candidates with OPT/CPT. "
                "Legally authorized to work in the United States without the need for current or future sponsorship by the company."
            ),
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("no_current_future_sponsorship", negative)
        self.assertEqual(positive, [])

    def test_eligibility_rejects_siemens_style_conflicting_handshake_text(self) -> None:
        job = JobRecord(
            source="handshake",
            external_id="3",
            url="https://example.com/3",
            title="Data Science Internship",
            company="Siemens Digital Industries Software",
            location="Pasadena, CA",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "Open to candidates with OPT/CPT. "
                "Legally authorized to work in the United States without the need for current or future sponsorship by the company. "
                "3.0 GPA Masters Statistics major Data Science major."
            ),
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("no_current_future_sponsorship", negative)
        self.assertEqual(positive, [])

    def test_eligibility_rejects_bosch_style_indefinite_us_auth_and_future_sponsorship_unavailable(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="4",
            url="https://example.com/4",
            title="Business Intelligence Intern - Fall",
            company="Bosch USA",
            location="Watertown, MA",
            is_internship=True,
            posted_at="2026-07-02",
            description=(
                "Indefinite U.S. work authorized individuals only. "
                "Future sponsorship for work authorization unavailable."
            ),
            ingested_at="2026-07-03T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("us_work_authorized_only", negative)
        self.assertIn("future_sponsorship_work_auth_unavailable", negative)
        self.assertEqual(positive, [])

    def test_eligibility_rejects_sponsorship_is_not_available_wording(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="4b",
            url="https://example.com/4b",
            title="Student Intern",
            company="Duravant",
            location="Downers Grove, IL",
            is_internship=True,
            posted_at="2026-07-02",
            description=(
                "Currently pursuing a degree in Statistics, Data Science, Analytics, Economics, or a related field. "
                "Sponsorship is not available for this position."
            ),
            ingested_at="2026-07-07T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("sponsorship_not_available", negative)
        self.assertEqual(positive, [])

    def test_eligibility_rejects_plural_no_sponsorships_available(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="4b-plural",
            url="https://example.com/4b-plural",
            title="AI/ML Data Engineering Intern",
            company="Example",
            location="St. Louis, MO",
            is_internship=True,
            posted_at="2026-07-25",
            description="No sponsorships available.",
            ingested_at="2026-07-25T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("no_sponsorship", negative)
        self.assertEqual(positive, [])

    def test_eligibility_does_not_treat_equal_opportunity_text_as_no_sponsorship(self) -> None:
        job = JobRecord(
            source="handshake",
            external_id="optiver-style-1",
            url="https://example.com/optiver-style-1",
            title="Software Engineer Intern",
            company="Example",
            location="Austin, TX",
            is_internship=True,
            posted_at="2026-07-26",
            description=(
                "We do not discriminate based on protected characteristics. "
                "The company is supportive of U.S. immigration sponsorship for this role."
            ),
            ingested_at="2026-07-26T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "sponsorship_friendly")
        self.assertEqual(confidence, 0.95)
        self.assertEqual(negative, [])
        self.assertIn("visa_sponsorship", positive)

    def test_eligibility_rejects_direct_company_does_not_sponsor_statement(self) -> None:
        job = JobRecord(
            source="x",
            external_id="no-sponsor-1",
            url="https://example.com/no-sponsor-1",
            title="Data Engineer Intern",
            company="Example",
            location="United States",
            is_internship=True,
            posted_at="2026-07-26",
            description="The company does not sponsor visas for this position.",
            ingested_at="2026-07-26T00:00:00+00:00",
        )
        status, confidence, negative, _ = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("do_not_sponsor", negative)

    def test_eligibility_rejects_permanent_us_work_auth_and_opt_cpt_exclusion(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="jpmc-style-1",
            url="https://example.com/jpmc-style-1",
            title="Data & AI Summer Analyst",
            company="Example",
            location="United States",
            is_internship=True,
            posted_at=recent_posted_at(),
            description=(
                "Authorized to work permanently in the United States. "
                "We do not offer any type of employment-based immigration sponsorship for this program. "
                "We will not provide any assistance or sign any documentation in support of optional practical training (OPT) "
                "or curricular practical training (CPT)."
            ),
            ingested_at="now",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("permanent_us_work_authorization_required", negative)
        self.assertIn("employment_sponsorship_opt_cpt_unavailable", negative)
        self.assertEqual(positive, [])

    def test_eligibility_allows_cpt_or_sponsorship_despite_generic_us_work_auth_requirement(self) -> None:
        job = JobRecord(
            source="handshake",
            external_id="4c",
            url="https://app.joinhandshake.com/jobs/11202635",
            title="Software Engineer, Internship - Infrastructure - Palo Alto",
            company="Palantir Technologies",
            location="Palo Alto, CA",
            is_internship=True,
            posted_at="2026-07-13",
            description=(
                "US work authorization required. "
                "Eligible for visa sponsorship. "
                "Software engineering internship."
            ),
            ingested_at="2026-07-14T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "sponsorship_friendly")
        self.assertEqual(confidence, 0.95)
        self.assertIn("us_work_auth_required", negative)
        self.assertIn("visa_sponsorship", positive)

    def test_eligibility_marks_generic_us_work_auth_requirement_as_ambiguous(self) -> None:
        job = JobRecord(
            source="x",
            external_id="generic-auth-1",
            url="https://example.com/generic-auth-1",
            title="Data Engineering Intern",
            company="Example",
            location="United States",
            is_internship=True,
            posted_at="2026-07-26",
            description="U.S. work authorization required. Internship with Python and SQL.",
            ingested_at="2026-07-26T00:00:00+00:00",
        )
        status, confidence, signals, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "ambiguous")
        self.assertEqual(confidence, 0.6)
        self.assertIn("us_work_auth_required", signals)
        self.assertEqual(positive, [])

    def test_eligibility_rejects_itar_us_person_requirement(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="itar-1",
            url="https://example.com/itar-1",
            title="Software Engineering Intern",
            company="Example Aerospace",
            location="Golden, CO",
            is_internship=True,
            posted_at="2026-07-23",
            description=(
                "ITAR Requirements: To conform to U.S. Government export regulations, applicant must be a "
                "U.S. citizen or national, U.S. lawful permanent resident, refugee, or asylee."
            ),
            ingested_at="2026-07-23T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("itar_us_person_required", negative)
        self.assertEqual(positive, [])

    def test_eligibility_rejects_us_citizenship_required_for_security_clearance(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="citizenship-clearance-1",
            url="https://example.com/citizenship-clearance-1",
            title="Software Engineering Intern",
            company="Example Defense",
            location="State College, PA",
            is_internship=True,
            posted_at="2026-07-28",
            description=(
                "U.S. citizenship is required, as only U.S. citizens are eligible for a security clearance."
            ),
            ingested_at="2026-07-28T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("citizen_or_pr_required", negative)
        self.assertIn("citizens_only_security_clearance", negative)
        self.assertEqual(positive, [])

    def test_eligibility_rejects_github_tracker_us_citizenship_marker(self) -> None:
        job = JobRecord(
            source="github_repo",
            external_id="github-citizens-only-1",
            url="https://example.com/github-citizens-only-1",
            title="Software Engineer Intern 🇺🇸",
            company="Example",
            location="United States",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Imported from GitHub internship repository.",
            ingested_at="now",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("github_repo_us_citizens_only", negative)
        self.assertEqual(positive, [])

    def test_internship_and_us_scope_filters(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Data Science Intern",
            company="Example",
            location="United States",
            is_internship=False,
            posted_at=None,
            description="Python and SQL",
            ingested_at="now",
        )
        self.assertTrue(_is_internship(job))
        self.assertTrue(_is_us_scope(job))

    def test_us_scope_accepts_city_state_locations(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Data Science Intern",
            company="Example",
            location="Washington, DC",
            is_internship=True,
            posted_at=None,
            description="Python and SQL",
            ingested_at="now",
        )
        self.assertTrue(_is_us_scope(job))

    def test_us_scope_accepts_accented_city_state_locations(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1a",
            url="https://example.com/1a",
            title="Machine Learning Engineer Project Intern",
            company="Example",
            location="San José, CA",
            is_internship=True,
            posted_at=None,
            description="Python and SQL",
            ingested_at="now",
        )
        self.assertTrue(_is_us_scope(job))

    def test_description_based_internship_match(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Machine Learning Program Participant",
            company="Example",
            location="United States",
            is_internship=False,
            posted_at=None,
            description="Join our summer internship program for AI research.",
            ingested_at="now",
        )
        self.assertTrue(_is_internship(job))

    def test_false_positive_non_intern_role_is_filtered(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Business Transformation Lead",
            company="Example",
            location="USA",
            is_internship=False,
            posted_at=None,
            description=(
                "Lead initiatives across international pharmacy operations and "
                "optimize workflows with AI/ML tooling."
            ),
            ingested_at="now",
            skills=["AI/ML", "automation"],
        )
        self.assertFalse(_is_internship(job))

    def test_eligibility_without_explicit_us_auth_requirement_is_ambiguous(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Data Science Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description="We currently do not provide visa sponsorship.",
            ingested_at="now",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertTrue(negative)
        self.assertEqual(positive, [])

    def test_relevance_scoring(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Machine Learning Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at="2026-05-20",
            description="NLP, Deep Learning, Python, SQL",
            ingested_at="now",
        )
        score, hits = _score_relevance(job)
        self.assertGreaterEqual(score, 5.0)
        self.assertIn("machine learning", hits)

    def test_relevance_keyword_word_boundaries(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Generalist",
            company="Example",
            location="US",
            is_internship=False,
            posted_at=None,
            description="Build HTML interfaces and optimize systems.",
            ingested_at="now",
        )
        score, hits = _score_relevance(job)
        self.assertEqual(score, 0.0)
        self.assertEqual(hits, [])

    def test_relevance_unknown_age_penalty(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Machine Learning Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description="Machine learning internship",
            ingested_at="now",
        )
        score, _ = _score_relevance(job)
        self.assertGreaterEqual(score, 2.75)

    def test_data_role_gate_rejects_non_data_title(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Developer Advocacy Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description="Build developer communities with Python tutorials.",
            ingested_at="now",
        )
        self.assertFalse(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE)],
                min_data_signal_count=2,
            )
        )

    def test_data_role_gate_accepts_backend_adjacent_software_intern(self) -> None:
        job = JobRecord(
            source="x",
            external_id="2",
            url="https://example.com/backend",
            title="Software Development Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at=None,
            description=(
                "Build and maintain backend systems and APIs for warehouse operations. "
                "Work with relational and non-relational databases, Kafka, Docker, and Kubernetes."
            ),
            ingested_at="now",
        )
        self.assertTrue(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[
                    re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE),
                    re.compile(r"\b(frontend|front-end|ios|android|mobile app|react native)\b", re.IGNORECASE),
                ],
                min_data_signal_count=2,
            )
        )

    def test_data_role_gate_accepts_generic_software_engineer_intern_for_semantic_review(self) -> None:
        job = JobRecord(
            source="x",
            external_id="software-engineer-intern-1",
            url="https://example.com/software-engineer-intern-1",
            title="Software Engineer Intern (Summer 2027)",
            company="Example",
            location="Austin, TX",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Build production applications with a software engineering team.",
            ingested_at="now",
        )
        self.assertTrue(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata engineer\b", re.IGNORECASE)],
                non_data_role_title_regexes=[],
                min_data_signal_count=2,
            )
        )
        score, hits = _score_relevance(job)
        self.assertGreaterEqual(score, 2.75)
        self.assertIn("software_engineering", hits)

    def test_data_role_gate_accepts_full_stack_ai_software_intern_from_title(self) -> None:
        job = JobRecord(
            source="x",
            external_id="2b",
            url="https://example.com/fullstack-ai",
            title="Full-Stack Software Engineering Intern, AI - Fall 2026",
            company="Example",
            location="Onsite - US",
            is_internship=True,
            posted_at=None,
            description=(
                "Build product features for an AI application. "
                "Work with engineering teams on software delivery and product systems."
            ),
            ingested_at="now",
        )
        self.assertTrue(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[
                    re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE),
                    re.compile(r"\b(frontend|front-end|ios|android|mobile app|react native)\b", re.IGNORECASE),
                ],
                min_data_signal_count=2,
            )
        )

    def test_data_role_gate_rejects_frontend_only_software_intern(self) -> None:
        job = JobRecord(
            source="x",
            external_id="3",
            url="https://example.com/frontend",
            title="Software Engineer Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at=None,
            description=(
                "Build frontend interfaces in React Native for mobile experiences. "
                "Focus on UI polish and client-side interactions."
            ),
            ingested_at="now",
        )
        self.assertFalse(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[
                    re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE),
                    re.compile(r"\b(frontend|front-end|ios|android|mobile app|react native)\b", re.IGNORECASE),
                ],
                min_data_signal_count=2,
            )
        )

    def test_policy_gate_rejects_phd_research_roles(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="PhD Fall Machine Learning Intern",
            company="Pinterest",
            location="US",
            is_internship=True,
            posted_at=None,
            description="Publications and causal inference research required.",
            ingested_at="now",
        )
        self.assertTrue(
            _fails_policy_gate(
                job,
                policy_reject_regexes=[
                    re.compile(r"\bph\.?d\.?\b", re.IGNORECASE),
                ],
            )
        )

    def test_policy_gate_allows_inclusive_bachelors_masters_or_phd_requirement(self) -> None:
        job = JobRecord(
            source="x",
            external_id="inclusive-degree-1",
            url="https://example.com/inclusive-degree-1",
            title="Software Engineer Intern",
            company="Example",
            location="Austin, TX",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="A student pursuing a bachelor's, master's, or PhD in Computer Science is eligible.",
            ingested_at="now",
        )
        self.assertFalse(_fails_policy_gate(job, [re.compile(r"\bph\.?d\.?\b", re.IGNORECASE)]))

    def test_policy_gate_rejects_phd_pursuit_requirement(self) -> None:
        job = JobRecord(
            source="x",
            external_id="phd-pursuit-1",
            url="https://example.com/phd-pursuit-1",
            title="AI Applied Intern",
            company="Example",
            location="Hillsboro, OR",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Minimum qualifications: Active student pursuing a PhD in Computer Science.",
            ingested_at="now",
        )
        self.assertTrue(_fails_policy_gate(job, [re.compile(r"\bph\.?d\.?\b", re.IGNORECASE)]))

    def test_policy_gate_rejects_undergraduate_only_roles(self) -> None:
        job = JobRecord(
            source="x",
            external_id="2",
            url="https://example.com/2",
            title="Data Science Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description="Currently enrolled as an undergraduate student at an accredited university. Undergraduate students only.",
            ingested_at="now",
        )
        self.assertTrue(_fails_policy_gate(job, policy_reject_regexes=[]))

    def test_policy_gate_rejects_undergraduate_candidate_requirement(self) -> None:
        job = JobRecord(
            source="x",
            external_id="undergraduate-candidate-1",
            url="https://example.com/undergraduate-candidate-1",
            title="AI/ML Research Support Intern",
            company="Example",
            location="State College, PA",
            is_internship=True,
            posted_at=recent_posted_at(),
            description=(
                "We are searching for a motivated undergraduate student. "
                "Undergraduate students in a STEM major are encouraged to apply."
            ),
            ingested_at="now",
        )
        self.assertTrue(_fails_policy_gate(job, policy_reject_regexes=[]))

    def test_policy_gate_allows_bs_ms_language_without_exclusive_undergrad_restriction(self) -> None:
        job = JobRecord(
            source="x",
            external_id="3",
            url="https://example.com/3",
            title="AI/ML Software Engineer Intern (BS/MS)",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description="Currently pursuing a bachelor's or master's degree in computer science, data science, or a related field.",
            ingested_at="now",
        )
        self.assertFalse(_fails_policy_gate(job, policy_reject_regexes=[]))
        self.assertTrue(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE)],
                min_data_signal_count=2,
            )
        )

    def test_policy_gate_rejects_undergraduate_intern_with_three_years_coursework(self) -> None:
        job = JobRecord(
            source="x",
            external_id="3b",
            url="https://example.com/3b",
            title="LSE Undergraduate Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description=(
                "Must be a U.S. citizen. Completion of at least three years of college coursework "
                "in Computer Science, Engineering, Physics, Mathematics, or a related STEM field."
            ),
            ingested_at="now",
        )
        self.assertTrue(_fails_policy_gate(job, policy_reject_regexes=[]))

    def test_dedupe_stability(self) -> None:
        j1 = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com/job?ref=abc",
            title="Data Engineer Intern",
            company="Acme",
            location="US",
            is_internship=True,
            posted_at=None,
            description="",
            ingested_at="now",
        )
        j2 = JobRecord(
            source="x",
            external_id="2",
            url="https://example.com/job?ref=zzz",
            title="Data Engineer Intern",
            company="Acme",
            location="United States",
            is_internship=True,
            posted_at=None,
            description="",
            ingested_at="now",
        )
        self.assertEqual(_dedupe_key(j1), _dedupe_key(j2))

    def test_dedupe_matches_branded_and_greenhouse_ats_urls(self) -> None:
        common = {
            "source": "github_repo",
            "external_id": "appian-8041237",
            "title": "Software Engineering Intern",
            "company": "Appian",
            "location": "McLean, VA",
            "is_internship": True,
            "posted_at": recent_posted_at(),
            "description": "Software engineering internship.",
            "ingested_at": "now",
        }
        greenhouse = JobRecord(
            **common,
            url="https://job-boards.greenhouse.io/appian/jobs/8041237?utm_source=github",
        )
        branded = JobRecord(
            **common,
            url="https://careers.appian.com/jobs/8041237-software-engineering-intern?utm_source=github",
        )
        self.assertEqual(_dedupe_key(greenhouse), _dedupe_key(branded))

    def test_dedupe_key_uses_external_apply_url_for_reposted_listings(self) -> None:
        first = JobRecord(
            source="linkedin",
            external_id="111",
            url="https://www.linkedin.com/jobs/view/111",
            title="AI Engineer Intern",
            company="Jobright.ai",
            location="United States",
            is_internship=True,
            posted_at="2026-07-22",
            description="AI internship",
            ingested_at="now",
            source_metadata={"external_apply_url": "https://jobright.ai/jobs/info/abc?utm_source=linkedin"},
        )
        repost = JobRecord(
            source="linkedin",
            external_id="222",
            url="https://www.linkedin.com/jobs/view/222",
            title="AI Engineer Intern",
            company="Jobright.ai",
            location="United States",
            is_internship=True,
            posted_at="2026-07-25",
            description="AI internship",
            ingested_at="now",
            source_metadata={"external_apply_url": "https://jobright.ai/jobs/info/abc?utm_source=repost"},
        )

        self.assertEqual(_dedupe_key(first), _dedupe_key(repost))


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "test.db")
        self.settings = make_settings(db_path)
        self.store = JobStore(db_path)
        self.semantic_scorer_patcher = patch(
            "job_hunter.pipeline._build_semantic_shadow_scorer",
            return_value=FakeSemanticScorer(),
        )
        self.semantic_scorer_patcher.start()

    def tearDown(self) -> None:
        self.semantic_scorer_patcher.stop()
        self.store.close()
        self.temp_dir.cleanup()

    def test_linkedin_description_identity_dedupes_distinct_listing_ids(self) -> None:
        first = JobRecord(
            source="linkedin",
            external_id="listing-1",
            url="https://www.linkedin.com/jobs/view/listing-1",
            title="Data Engineering Intern",
            company="Staffline Solutions",
            location="United States",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Build ETL pipelines with Python and SQL. Set alert for similar jobs. Applicants: 115.",
            ingested_at="2026-07-30T00:00:00+00:00",
        )
        first_key = _dedupe_key(first)
        self.assertTrue(self.store.insert_job(first, first_key))

        duplicate_key = self.store.resolve_existing_dedupe_key(
            source="linkedin",
            dedupe_key="different-listing-key",
            url="https://www.linkedin.com/jobs/view/listing-2",
            title="Data Engineering Intern",
            company="Staffline Solutions",
            description="Build ETL pipelines with Python and SQL. Set alert for similar jobs. Applicants: 111.",
        )
        self.assertEqual(duplicate_key, first_key)

    def test_cross_source_dedupe_uses_normalized_external_apply_url(self) -> None:
        linkedin = JobRecord(
            source="linkedin",
            external_id="linkedin-1",
            url="https://www.linkedin.com/jobs/view/123",
            title="Data Engineering Internship (Summer 2027)",
            company="Castleton Commodities International",
            location="Stamford, CT",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Data engineering internship.",
            ingested_at="2026-07-31T00:00:00+00:00",
            source_metadata={
                "external_apply_url": "https://osv-cci.wd1.myworkdayjobs.com/CCICareers/job/Stamford-CT/Data-Engineering-Internship--Summer-2027-_R1346?source=LinkedIn"
            },
        )
        linkedin_key = _dedupe_key(linkedin)
        self.assertTrue(self.store.insert_job(linkedin, linkedin_key))

        existing_key = self.store.resolve_existing_dedupe_key(
            source="github_repo",
            dedupe_key="github-key",
            url="https://osv-cci.wd1.myworkdayjobs.com/en-US/CCICareers/job/Stamford-CT/Data-Engineering-Internship--Summer-2027-_R1346?utm_source=github",
            application_url="https://osv-cci.wd1.myworkdayjobs.com/en-US/CCICareers/job/Stamford-CT/Data-Engineering-Internship--Summer-2027-_R1346?utm_source=github",
            title="Data Engineering Intern",
            company="Castleton Commodities International",
        )
        self.assertEqual(existing_key, linkedin_key)

    def test_end_to_end_and_idempotency(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-1",
                "url": "https://example.com/job-1",
                "title": "Machine Learning Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Build NLP models in Python for our summer internship program.",
                "skills": ["python", "nlp"],
            }
        ]
        notifier = FakeNotifier()

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome1 = run_pipeline(self.settings, self.store, notifier)
            outcome2 = run_pipeline(self.settings, self.store, notifier)

        self.assertEqual(outcome1.normalized_count, 1)
        self.assertEqual(outcome1.rejected_missing_core_fields_count, 0)
        self.assertEqual(outcome1.after_stage_1a_count, 1)
        self.assertEqual(outcome1.after_stage_1b_count, 1)
        self.assertEqual(outcome1.after_stage_1c_count, 1)
        self.assertEqual(outcome1.persisted_count, 1)
        self.assertEqual(outcome1.notified_count, 1)
        self.assertEqual(outcome2.persisted_count, 0)
        self.assertGreaterEqual(outcome2.duplicate_count, 1)
        self.assertEqual(notifier.sent, 1)

    def test_pipeline_fails_closed_when_semantic_scorer_unavailable(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-semantic-required-1",
                "url": "https://example.com/job-semantic-required-1",
                "title": "Data Engineering Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Build ETL pipelines with Python and SQL.",
                "skills": ["python", "sql"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            with patch("job_hunter.pipeline._build_semantic_shadow_scorer", return_value=None):
                with self.assertRaises(RuntimeError):
                    run_pipeline(self.settings, self.store, None)

    def test_db_false_positive_regression(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-2",
                "url": "https://example.com/job-2",
                "title": "Business Transformation Lead",
                "company": "Expion Health",
                "location": "USA",
                "posted_at": recent_posted_at(),
                "description": (
                    "Expion Health is building the future of pharmacy economics. "
                    "Work across international teams and optimize business operations "
                    "with AI/ML automation."
                ),
                "skills": ["AI/ML", "automation", "healthcare"],
            }
        ]
        notifier = FakeNotifier()

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, notifier)

        self.assertEqual(outcome.passed_filter_count, 0)
        self.assertEqual(outcome.persisted_count, 0)
        self.assertEqual(outcome.notified_count, 0)

    def test_age_window_filters_old_postings(self) -> None:
        stale_payload = [
            {
                "source": "fake",
                "external_id": "old-1",
                "url": "https://example.com/old-1",
                "title": "Data Science Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": "2020-01-01T00:00:00+00:00",
                "description": "Summer internship program for ML and analytics",
                "skills": ["python"],
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(stale_payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.normalized_count, 1)
        self.assertEqual(outcome.after_stage_1a_count, 0)
        self.assertEqual(outcome.persisted_count, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_age_count, 1)

    def test_duplicate_can_notify_when_previously_unnotified(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-3",
                "url": "https://example.com/job-3",
                "title": "Data Science Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Summer internship program for ML and Python",
                "skills": ["python"],
            }
        ]

        notifier1 = FakeNotifier()
        settings_no_ambiguous = replace(self.settings, notify_on_ambiguous_eligibility=False)
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome1 = run_pipeline(settings_no_ambiguous, self.store, notifier1)
        self.assertEqual(outcome1.persisted_count, 1)
        self.assertEqual(outcome1.notified_count, 0)
        self.assertEqual(notifier1.sent, 0)

        notifier2 = FakeNotifier()
        settings_with_ambiguous = replace(self.settings, notify_on_ambiguous_eligibility=True)
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome2 = run_pipeline(settings_with_ambiguous, self.store, notifier2)
        self.assertEqual(outcome2.persisted_count, 0)
        self.assertGreaterEqual(outcome2.duplicate_count, 1)
        self.assertEqual(outcome2.notified_count, 1)
        self.assertEqual(notifier2.sent, 1)

    def test_duplicate_refreshes_enriched_description(self) -> None:
        first_payload = [
            {
                "source": "handshake",
                "external_id": "job-5",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Finz",
                "location": "Remote",
                "posted_at": recent_posted_at(),
                "description": "Finz Data Engineering Intern Unpaid · Internship Remote 5d ago",
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern",
            }
        ]
        second_payload = [
            {
                "source": "handshake",
                "external_id": "job-5",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Finz",
                "location": "Remote, based in United States",
                "posted_at": recent_posted_at(),
                "description": (
                    "We are looking for a Data Engineering Intern to help build a multi-tenant "
                    "data lakehouse from the ground up."
                ),
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(first_payload)]):
            outcome1 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome1.persisted_count, 1)

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(second_payload)]):
            outcome2 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome2.persisted_count, 0)
        row = self.store.get_job_for_labeling(1)
        self.assertIsNotNone(row)
        self.assertIn("multi-tenant data lakehouse", row["description"])

    def test_duplicate_refresh_prefers_cleaner_handshake_description(self) -> None:
        first_payload = [
            {
                "source": "handshake",
                "external_id": "job-5b",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote",
                "posted_at": recent_posted_at(),
                "description": (
                    "Summary Beta This role as a Data Engineer Intern aligns closely with the user's query. "
                    "Build ETL pipelines and data workflows for analytics."
                ),
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern",
            }
        ]
        second_payload = [
            {
                "source": "handshake",
                "external_id": "job-5b",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote",
                "posted_at": recent_posted_at(),
                "description": "Build ETL pipelines and data workflows for analytics.",
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(first_payload)]):
            outcome1 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome1.persisted_count, 1)

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(second_payload)]):
            outcome2 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome2.persisted_count, 0)
        row = self.store.get_job_for_labeling(1)
        self.assertIsNotNone(row)
        self.assertNotIn("Summary Beta", row["description"])
        self.assertIn("Build ETL pipelines and data workflows for analytics.", row["description"])
        snapshot_row = self.store._conn.execute("SELECT job_text_snapshot FROM jobs WHERE id = 1").fetchone()
        self.assertIsNotNone(snapshot_row)
        self.assertNotIn("Summary Beta", snapshot_row["job_text_snapshot"])
        self.assertIn("Build ETL pipelines and data workflows for analytics", snapshot_row["job_text_snapshot"])

    def test_duplicate_refresh_rebuilds_polluted_snapshot_even_when_description_is_clean(self) -> None:
        payload = [
            {
                "source": "handshake",
                "external_id": "job-5c",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote",
                "posted_at": recent_posted_at(),
                "description": "Build ETL pipelines and data workflows for analytics.",
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome1 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome1.persisted_count, 1)

        self.store._conn.execute(
            """
            UPDATE jobs
            SET job_text_snapshot = ?
            WHERE id = 1
            """,
            ("TITLE: Data Engineering Intern\nSUMMARY:\nSummary Beta fake text",),
        )
        self.store._conn.commit()

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome2 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome2.persisted_count, 0)
        snapshot_row = self.store._conn.execute("SELECT job_text_snapshot FROM jobs WHERE id = 1").fetchone()
        self.assertIsNotNone(snapshot_row)
        self.assertNotIn("Summary Beta", snapshot_row["job_text_snapshot"])
        self.assertIn("Build ETL pipelines and data workflows for analytics", snapshot_row["job_text_snapshot"])

    def test_duplicate_refresh_preserves_existing_shadow_scores_when_incoming_refresh_is_unscored(self) -> None:
        scored_job = JobRecord(
            source="handshake",
            external_id="job-5d",
            url="https://app.joinhandshake.com/jobs/11168432",
            title="Data Engineering & ETL Automation Intern",
            company="GreenPoint Global",
            location="Remote, based in United States",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Build ETL pipelines and automate data workflows.",
            compensation_type="unpaid",
            ingested_at=recent_posted_at(),
            relevance_score=4.2,
            eligibility_confidence=0.95,
            eligibility_status="sponsorship_friendly",
            relevance_hits=["python", "etl"],
            role_relevance_label="pass",
            role_relevance_reason_codes=["data_role_gate_pass"],
            policy_gate_status="pass",
            policy_gate_reason_codes=[],
            profile_match_score=0.95,
            profile_match_label="pass",
            profile_match_reason_codes=["builder_signal_alignment"],
            profile_version="default_v1",
            scorer_version="shadow_rules_v1",
            job_text_version="job_text_v1",
            job_text_snapshot="TITLE: Data Engineering & ETL Automation Intern",
            semantic_match_score=0.67,
            semantic_match_label="pass",
            semantic_match_reason_codes=["semantic_similarity_pass"],
            semantic_base_score=0.7,
            semantic_research_heaviness_score=0.05,
            semantic_adjustment_reason_codes=[],
            semantic_profile_id="data_engineering",
            semantic_model_name="fake-semantic-model",
            semantic_scorer_version="semantic_shadow_v1",
            semantic_text_hash="hash-1",
            age_days=1.0,
            age_unknown=False,
            source_detail="https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
            source_metadata={"detail_quality_status": "detail_mismatch"},
            source_quality_status="detail_mismatch",
            source_quality_reason_codes=["handshake_detail_mismatch"],
        )
        dedupe_key = _dedupe_key(scored_job)
        inserted = self.store.insert_job(scored_job, dedupe_key)
        self.assertTrue(inserted)

        refresh_job = JobRecord(
            source="handshake",
            external_id="job-5d",
            url="https://app.joinhandshake.com/jobs/11168432",
            title="Data Engineering & ETL Automation Intern",
            company="GreenPoint Global",
            location="Remote, based in United States",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Build ETL pipelines and automate data workflows.",
            compensation_type="unpaid",
            ingested_at=recent_posted_at(),
            relevance_score=4.2,
            eligibility_confidence=0.95,
            eligibility_status="sponsorship_friendly",
            relevance_hits=["python", "etl"],
            role_relevance_label="pass",
            role_relevance_reason_codes=["data_role_gate_pass"],
            policy_gate_status="pass",
            policy_gate_reason_codes=[],
            age_days=1.0,
            age_unknown=False,
            source_detail="https://app.joinhandshake.com/job-search/11120409?query=analytics+engineer&page=1",
            source_metadata={"detail_quality_status": "detail_complete"},
            source_quality_status="detail_complete",
            source_quality_reason_codes=["handshake_detail_complete"],
        )
        refresh_meta = self.store.update_existing_job(refresh_job, dedupe_key)
        self.assertTrue(refresh_meta["source_quality_recovered"])

        row = self.store.get_stage2_job(1)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(float(row["profile_match_score"]), 0.95)
        self.assertEqual(row["profile_match_label"], "pass")
        self.assertEqual(row["profile_version"], "default_v1")
        self.assertEqual(row["scorer_version"], "shadow_rules_v1")
        self.assertAlmostEqual(float(row["semantic_match_score"]), 0.67)
        self.assertEqual(row["semantic_match_label"], "pass")
        self.assertEqual(row["semantic_profile_id"], "data_engineering")
        self.assertEqual(row["semantic_scorer_version"], "semantic_shadow_v1")
        self.assertEqual(row["source_quality_status"], "detail_complete")
        self.assertEqual(row["source_quality_prev_status"], "detail_mismatch")

    def test_persisted_jobs_include_stage2_shadow_fields(self) -> None:
        class FakeSemanticResult:
            semantic_base_score = 0.88
            semantic_match_score = 0.81
            semantic_match_label = "pass"
            semantic_match_reason_codes = ["semantic_profile_data_engineering", "semantic_similarity_high"]
            semantic_research_heaviness_score = 0.07
            semantic_adjustment_reason_codes = ["semantic_penalty_masters_signal"]
            semantic_profile_id = "data_engineering"
            semantic_model_name = "fake-semantic-model"
            semantic_scorer_version = "semantic_shadow_v1"
            semantic_text_hash = "abc123"

        class FakeSemanticScorer:
            def score(self, job):
                _ = job
                return FakeSemanticResult()

        payload = [
            {
                "source": "fake",
                "external_id": "job-stage2-1",
                "url": "https://example.com/job-stage2-1",
                "title": "AI/ML Data Engineering Intern",
                "company": "Example",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": (
                    "Build production ML systems.\n"
                    "Requirements\n"
                    "- Python\n"
                    "- SQL\n"
                    "Responsibilities\n"
                    "- Build ETL pipelines\n"
                ),
                "skills": ["python", "sql"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            with patch("job_hunter.pipeline._build_semantic_shadow_scorer", return_value=FakeSemanticScorer()):
                outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.persisted_count, 1)
        row = self.store._conn.execute(
            """
            SELECT role_relevance_label, role_relevance_reason_codes, policy_gate_status,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version, job_text_snapshot,
                   semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                   semantic_profile_id, semantic_model_name, semantic_scorer_version,
                   semantic_text_hash
            FROM jobs
            WHERE id = 1
            """
        ).fetchone()
        self.assertEqual(row["role_relevance_label"], "pass")
        self.assertEqual(row["policy_gate_status"], "pass")
        self.assertGreaterEqual(float(row["profile_match_score"]), 0.0)
        self.assertIn(row["profile_match_label"], {"pass", "review", "reject"})
        self.assertEqual(row["profile_version"], "default_v1")
        self.assertEqual(row["scorer_version"], "shadow_rules_v1")
        self.assertEqual(row["job_text_version"], "job_text_v1")
        self.assertIn("TITLE: AI/ML Data Engineering Intern", row["job_text_snapshot"])
        self.assertAlmostEqual(float(row["semantic_match_score"]), 0.81)
        self.assertEqual(row["semantic_match_label"], "pass")
        self.assertIn("semantic_similarity_high", str(row["semantic_match_reason_codes"]))
        self.assertEqual(row["semantic_profile_id"], "data_engineering")
        self.assertEqual(row["semantic_model_name"], "fake-semantic-model")
        self.assertEqual(row["semantic_scorer_version"], "semantic_shadow_v1")
        self.assertEqual(row["semantic_text_hash"], "abc123")

    def test_source_meta_counters_are_recorded(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-4",
                "url": "https://example.com/job-4",
                "title": "Machine Learning Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Summer internship program for ML and Python",
                "skills": ["python"],
            }
        ]

        class FakeMetaSource(FakeSource):
            def get_fetch_meta(self) -> dict[str, int]:
                return {"dead_token_count": 3, "feed_error_count": 2, "security_verification_blocked_count": 4}

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeMetaSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.source_stats["fake"].normalized_count, 1)
        self.assertEqual(outcome.source_stats["fake"].after_stage_1a_count, 1)
        self.assertEqual(outcome.source_stats["fake"].after_stage_1b_count, 1)
        self.assertEqual(outcome.source_stats["fake"].after_stage_1c_count, 1)
        self.assertEqual(outcome.source_stats["fake"].dead_token_count, 3)
        self.assertEqual(outcome.source_stats["fake"].feed_error_count, 2)
        self.assertEqual(outcome.source_stats["fake"].security_verification_blocked_count, 4)

    def test_missing_core_fields_are_tracked_separately(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "missing-url",
                "url": "",
                "title": "Machine Learning Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Summer internship program for ML and Python",
                "skills": ["python"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.normalized_count, 1)
        self.assertEqual(outcome.rejected_missing_core_fields_count, 1)
        self.assertEqual(outcome.after_stage_1a_count, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_missing_core_fields_count, 1)

    def test_title_blacklist_blocks_non_target_roles(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-5",
                "url": "https://example.com/job-5",
                "title": "University Recruiter (Contract)",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Join internship program operations for campus hiring",
                "skills": ["coordination"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.persisted_count, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_title_blacklist_count, 1)

    def test_data_role_gate_blocks_twilio_style_non_data_internships(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "twilio-1",
                "url": "https://example.com/twilio-1",
                "title": "Developer Advocacy Intern",
                "company": "Twilio",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Empower developers and create Python-focused content.",
                "skills": ["python"],
            },
            {
                "source": "fake",
                "external_id": "twilio-2",
                "url": "https://example.com/twilio-2",
                "title": "Technical Video Content Intern, Developer Ecosystem",
                "company": "Twilio",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Produce technical videos for developer ecosystem analytics dashboards.",
                "skills": ["analytics"],
            },
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.persisted_count, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_data_role_count, 2)

    def test_research_heavy_ms_role_is_not_hard_rejected(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "pinterest-ms-1",
                "url": "https://example.com/pinterest-ms-1",
                "title": "Master's Fall Machine Learning Internship (ATG - Visual Search)",
                "company": "Pinterest",
                "location": "US",
                "posted_at": recent_posted_at(),
                "description": (
                    "Working towards a Master's degree in Computer Science. "
                    "Preferred qualifications: Publications in machine learning and strong passion for research."
                ),
                "skills": ["python", "pytorch"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.source_stats["fake"].rejected_policy_gate_count, 0)
        self.assertEqual(outcome.persisted_count, 1)

    def test_handshake_card_only_rows_are_persisted_but_not_notified(self) -> None:
        payload = [
            {
                "source": "handshake",
                "external_id": "job-hs-card-only",
                "url": "https://app.joinhandshake.com/jobs/11161550",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote, based in United States",
                "posted_at": recent_posted_at(),
                "description": (
                    "Data Engineering Intern Internship Remote, based in United States "
                    "Build ETL pipelines with Python, SQL, databases, and analytics workflows."
                ),
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "source_metadata": {
                    "detail_fetch_attempted": True,
                    "detail_click_succeeded": False,
                    "detail_panel_found": False,
                    "detail_contains_job_description": False,
                    "detail_contains_at_a_glance": False,
                    "detail_text_length": 0,
                    "detail_title_matches_card": True,
                    "detail_quality_status": "card_only",
                    "detail_fallback_reason": "missing_detail_text",
                    "resolved_job_url": "https://app.joinhandshake.com/jobs/11161550",
                },
            }
        ]

        notifier = FakeNotifier()
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, notifier)

        self.assertEqual(outcome.persisted_count, 1)
        self.assertEqual(outcome.notified_count, 0)
        self.assertEqual(notifier.sent, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_source_quality_count, 1)
        row = self.store._conn.execute(
            """
            SELECT source_quality_status, source_quality_reason_codes, notified
            FROM jobs
            WHERE id = 1
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_quality_status"], "card_only")
        self.assertIn("handshake_card_only", row["source_quality_reason_codes"])
        self.assertEqual(int(row["notified"] or 0), 0)

    def test_handshake_source_quality_recovery_notifies_clean_duplicate(self) -> None:
        first_payload = [
            {
                "source": "handshake",
                "external_id": "job-hs-recovery-1",
                "url": "https://app.joinhandshake.com/jobs/11161550",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote, based in United States",
                "posted_at": recent_posted_at(),
                "description": (
                    "Data Engineering Intern Internship Remote, based in United States "
                    "Build ETL pipelines with Python, SQL, databases, and analytics workflows."
                ),
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "source_metadata": {
                    "detail_fetch_attempted": True,
                    "detail_click_succeeded": False,
                    "detail_panel_found": False,
                    "detail_contains_job_description": False,
                    "detail_contains_at_a_glance": False,
                    "detail_text_length": 0,
                    "detail_title_matches_card": True,
                    "detail_quality_status": "card_only",
                    "detail_fallback_reason": "missing_detail_text",
                    "resolved_job_url": "https://app.joinhandshake.com/jobs/11161550",
                },
            }
        ]
        second_payload = [
            {
                "source": "handshake",
                "external_id": "job-hs-recovery-1",
                "url": "https://app.joinhandshake.com/jobs/11161550",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote, based in United States",
                "posted_at": recent_posted_at(),
                "description": (
                    "Data Engineering Intern Internship Remote, based in United States "
                    "Build ETL pipelines with Python, SQL, databases, analytics workflows, and orchestration systems."
                ),
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/jobs/11161550",
                "source_metadata": {
                    "detail_fetch_attempted": True,
                    "detail_click_succeeded": True,
                    "detail_panel_found": True,
                    "detail_contains_job_description": True,
                    "detail_contains_at_a_glance": True,
                    "detail_text_length": 2400,
                    "detail_title_matches_card": True,
                    "detail_quality_status": "detail_complete",
                    "detail_fallback_reason": "",
                    "resolved_job_url": "https://app.joinhandshake.com/jobs/11161550",
                },
            }
        ]

        notifier1 = FakeNotifier()
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(first_payload)]):
            outcome1 = run_pipeline(self.settings, self.store, notifier1)
        self.assertEqual(outcome1.persisted_count, 1)
        self.assertEqual(outcome1.notified_count, 0)
        self.assertEqual(notifier1.sent, 0)

        notifier2 = FakeNotifier()
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(second_payload)]):
            outcome2 = run_pipeline(self.settings, self.store, notifier2)
        self.assertEqual(outcome2.persisted_count, 0)
        self.assertEqual(outcome2.duplicate_count, 1)
        self.assertEqual(outcome2.notified_count, 1)
        self.assertEqual(notifier2.sent, 1)
        self.assertEqual(outcome2.source_stats["fake"].recovered_source_quality_count, 1)

        row = self.store._conn.execute(
            """
            SELECT source_quality_status, source_quality_prev_status, source_quality_recovered_at, notified
            FROM jobs
            WHERE id = 1
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_quality_status"], "detail_complete")
        self.assertEqual(row["source_quality_prev_status"], "card_only")
        self.assertTrue(str(row["source_quality_recovered_at"] or "").strip())
        self.assertEqual(int(row["notified"] or 0), 1)

    def test_linkedin_closed_job_is_quarantined(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="li-closed-1",
            url="https://www.linkedin.com/jobs/view/4434342327",
            title="Data Ops-Intern",
            company="Innovaccer",
            location="United States",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Data ops internship with SQL and Python.",
            ingested_at=datetime.now(timezone.utc).isoformat(),
            source_metadata={
                "detail_fetch_attempted": True,
                "detail_quality_status": "detail_complete",
                "accepting_applications": False,
            },
        )
        status, reasons, notify_allowed = _evaluate_source_quality(job)
        self.assertEqual(status, "closed")
        self.assertEqual(reasons, ["linkedin_closed"])
        self.assertFalse(notify_allowed)

    def test_linkedin_missing_posted_at_is_quarantined(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="li-missing-posted-at-1",
            url="https://www.linkedin.com/jobs/view/4310239488",
            title="Machine Learning Co-Op (Fall 2026)",
            company="Hendrickson",
            location="United States",
            is_internship=True,
            posted_at=None,
            description="Machine learning co-op with Python and SQL.",
            ingested_at=datetime.now(timezone.utc).isoformat(),
            source_metadata={
                "detail_fetch_attempted": True,
                "detail_quality_status": "detail_complete",
                "accepting_applications": True,
            },
        )
        status, reasons, notify_allowed = _evaluate_source_quality(job)
        self.assertEqual(status, "missing_posted_at")
        self.assertEqual(reasons, ["linkedin_missing_posted_at"])
        self.assertFalse(notify_allowed)

    def test_linkedin_card_only_job_is_quarantined(self) -> None:
        job = JobRecord(
            source="linkedin",
            external_id="li-card-only-1",
            url="https://www.linkedin.com/jobs/view/4411253060",
            title="Software Engineering Intern",
            company="Example",
            location="United States",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Software engineering internship.",
            ingested_at=datetime.now(timezone.utc).isoformat(),
            source_metadata={
                "detail_fetch_attempted": True,
                "detail_quality_status": "card_only",
                "accepting_applications": True,
            },
        )
        status, reasons, notify_allowed = _evaluate_source_quality(job)
        self.assertEqual(status, "card_only")
        self.assertEqual(reasons, ["linkedin_card_only"])
        self.assertFalse(notify_allowed)

    def test_github_summary_only_job_is_not_notifiable(self) -> None:
        job = JobRecord(
            source="github_repo",
            external_id="github-summary-only-1",
            url="https://example.com/job",
            title="Machine Learning Engineer Intern",
            company="Example",
            location="United States",
            is_internship=True,
            posted_at=recent_posted_at(),
            description="Imported from GitHub internship repository.",
            ingested_at=datetime.now(timezone.utc).isoformat(),
            source_metadata={"detail_quality_status": "summary_only"},
        )
        status, reasons, notify_allowed = _evaluate_source_quality(job)
        self.assertEqual(status, "summary_only")
        self.assertEqual(reasons, ["github_repo_summary_only"])
        self.assertFalse(notify_allowed)

    def test_query_level_run_logs_record_linkedin_search_url_stats(self) -> None:
        payload = [
            {
                "source": "linkedin",
                "external_id": "li-query-1",
                "url": "https://www.linkedin.com/jobs/view/4405987988",
                "title": "AI/ML Software Engineer Intern (Data Platform) - 2026 Summer (BS/MS)",
                "company": "TikTok",
                "location": "San Jose, CA",
                "posted_at": recent_posted_at(),
                "description": "Build AI/ML systems on a large-scale data platform internship with Python and SQL.",
                "skills": [],
                "source_detail": "https://www.linkedin.com/jobs/search-results/?keywords=data+engineer+intern&f_TPR=r86400&sortBy=DD",
                "source_metadata": {
                    "detail_fetch_attempted": True,
                    "detail_quality_status": "detail_complete",
                    "accepting_applications": True,
                },
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.source_stats["fake"].fetched_count, 1)
        self.assertIn("fake", outcome.source_query_stats)
        query_key = "https://www.linkedin.com/jobs/search-results/?keywords=data+engineer+intern&f_TPR=r86400&sortBy=DD"
        self.assertIn(query_key, outcome.source_query_stats["fake"])
        query_stats = outcome.source_query_stats["fake"][query_key]
        self.assertEqual(query_stats.fetched_count, 1)
        self.assertEqual(query_stats.after_stage_1b_count, 1)

        row = self.store._conn.execute(
            """
            SELECT source_name, query_key, fetched_count, after_stage_1b_count
            FROM source_query_run_logs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_name"], "fake")
        self.assertEqual(row["query_key"], query_key)
        self.assertEqual(int(row["fetched_count"] or 0), 1)
        self.assertEqual(int(row["after_stage_1b_count"] or 0), 1)

    def test_configured_query_key_with_zero_rows_is_still_logged(self) -> None:
        query_key = "https://www.linkedin.com/jobs/search-results/?keywords=ai+engineering+intern&f_TPR=r86400&sortBy=DD"
        with patch(
            "job_hunter.pipeline.build_sources",
            return_value=[FakeSource([], fetch_meta={"configured_query_keys": [query_key]})],
        ):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertIn("fake", outcome.source_query_stats)
        self.assertIn(query_key, outcome.source_query_stats["fake"])
        query_stats = outcome.source_query_stats["fake"][query_key]
        self.assertEqual(query_stats.fetched_count, 0)

        row = self.store._conn.execute(
            """
            SELECT source_name, query_key, fetched_count
            FROM source_query_run_logs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_name"], "fake")
        self.assertEqual(row["query_key"], query_key)
        self.assertEqual(int(row["fetched_count"] or 0), 0)

    def test_handshake_refresh_updates_existing_row_by_url_even_when_rejected_later(self) -> None:
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 0)
            """,
            (
                "old-polluted-key",
                "job-old",
                "https://app.joinhandshake.com/jobs/11161752?searchId=bb316b92-9d56-4ffb-9279-be3f051dcb78",
                "Commercialization Intern",
                "CRH",
                "Remote, based in United States",
                recent_posted_at(),
                "Summary Beta polluted text",
                datetime.now(timezone.utc).isoformat(),
                "detail_polluted",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO seen_events (dedupe_key, first_seen_at, last_seen_at, seen_count, notified)
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, 0)
            """,
            ("old-polluted-key",),
        )
        self.store._conn.commit()

        payload = [
            {
                "source": "handshake",
                "external_id": "job-refresh",
                "url": "https://app.joinhandshake.com/jobs/11161752?searchId=bb316b92-9d56-4ffb-9279-be3f051dcb78",
                "title": "AI Engineering Intern, Voice & LLM Systems",
                "company": "CRH",
                "location": "Remote, based in United States",
                "posted_at": recent_posted_at(),
                "description": "Support commercialization efforts and maintain documentation.",
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/jobs/11161752?searchId=bb316b92-9d56-4ffb-9279-be3f051dcb78",
                "source_metadata": {
                    "detail_fetch_attempted": True,
                    "detail_click_succeeded": True,
                    "detail_panel_found": True,
                    "detail_contains_job_description": True,
                    "detail_contains_at_a_glance": True,
                    "detail_text_length": 1200,
                    "detail_title_matches_card": True,
                    "detail_quality_status": "detail_complete",
                    "detail_fallback_reason": "",
                    "resolved_job_url": "https://app.joinhandshake.com/jobs/11161752?searchId=bb316b92-9d56-4ffb-9279-be3f051dcb78",
                },
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.source_stats["fake"].rejected_data_role_count, 0)
        self.assertEqual(outcome.source_stats["fake"].after_stage_1b_count, 1)
        row = self.store._conn.execute(
            """
            SELECT title, description, source_quality_status
            FROM jobs
            WHERE dedupe_key = 'old-polluted-key'
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "AI Engineering Intern, Voice & LLM Systems")
        self.assertEqual(row["source_quality_status"], "detail_complete")
        self.assertNotIn("Summary Beta", row["description"])
        self.assertIn("Support commercialization efforts", row["description"])

    def test_handshake_refresh_prefers_cleaner_shorter_description_without_page_chrome(self) -> None:
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 0)
            """,
            (
                "old-presto-key",
                "job-old-presto",
                "https://app.joinhandshake.com/jobs/11149721",
                "Engineering Intern",
                "Presto",
                "Remote, based in United States",
                recent_posted_at(),
                "Skip to content Explore Jobs Inbox Feed AI showcase Events People Employers Career center AI work Get the app 28 Presto old noisy body",
                datetime.now(timezone.utc).isoformat(),
                "detail_complete",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO seen_events (dedupe_key, first_seen_at, last_seen_at, seen_count, notified)
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, 0)
            """,
            ("old-presto-key",),
        )
        self.store._conn.commit()

        payload = [
            {
                "source": "handshake",
                "external_id": "job-refresh-presto",
                "url": "https://app.joinhandshake.com/jobs/11149721",
                "title": "AI Engineering Intern, Voice & LLM Systems",
                "company": "Presto",
                "location": "Remote, based in United States",
                "posted_at": recent_posted_at(),
                "description": "AI Engineering Intern, Voice & LLM Systems\nAbout Presto Phoenix, Inc.\nPresto is the leading Voice AI company for restaurant drive-thrus.",
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/jobs/11149721",
                "source_metadata": {
                    "detail_fetch_attempted": True,
                    "detail_click_succeeded": True,
                    "detail_panel_found": True,
                    "detail_contains_job_description": True,
                    "detail_contains_at_a_glance": True,
                    "detail_text_length": 1200,
                    "detail_title_matches_card": True,
                    "detail_quality_status": "detail_complete",
                    "detail_fallback_reason": "",
                    "resolved_job_url": "https://app.joinhandshake.com/jobs/11149721",
                },
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.source_stats["fake"].after_stage_1b_count, 1)
        row = self.store._conn.execute(
            """
            SELECT title, description
            FROM jobs
            WHERE dedupe_key = 'old-presto-key'
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "AI Engineering Intern, Voice & LLM Systems")
        self.assertNotIn("Skip to content", row["description"])
        self.assertIn("About Presto Phoenix, Inc.", row["description"])

    def test_stage2_deterministic_reject_suppresses_notification(self) -> None:
        class FakeStage2Result:
            profile_match_score = 0.1
            profile_match_label = "reject"
            profile_match_reason_codes = ["business_analyst_negative"]
            profile_version = "default_v1"
            scorer_version = "shadow_rules_v1"
            job_text_version = "job_text_v1"
            job_text_snapshot = "TITLE: Data Engineering Intern"

        payload = [
            {
                "source": "fake",
                "external_id": "job-stage2-reject-1",
                "url": "https://example.com/job-stage2-reject-1",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": (
                    "Open to candidates with OPT/CPT. "
                    "Build ETL pipelines with Python and SQL."
                ),
                "skills": ["python", "sql"],
            }
        ]

        notifier = FakeNotifier()
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            with patch("job_hunter.pipeline.ShadowProfileScorer.score", return_value=FakeStage2Result()):
                outcome = run_pipeline(self.settings, self.store, notifier)

        self.assertEqual(outcome.persisted_count, 1)
        self.assertEqual(outcome.notified_count, 0)
        self.assertEqual(notifier.sent, 0)
        row = self.store._conn.execute(
            """
            SELECT profile_match_label, notified
            FROM jobs
            WHERE id = 1
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["profile_match_label"], "reject")
        self.assertEqual(int(row["notified"] or 0), 0)

    def test_stage2_semantic_reject_suppresses_notification(self) -> None:
        class FakeSemanticResult:
            semantic_base_score = 0.2
            semantic_match_score = 0.1
            semantic_match_label = "reject"
            semantic_match_reason_codes = ["semantic_negative_business_analyst"]
            semantic_research_heaviness_score = 0.0
            semantic_adjustment_reason_codes = []
            semantic_profile_id = "data_engineering"
            semantic_model_name = "fake-semantic-model"
            semantic_scorer_version = "semantic_shadow_v1"
            semantic_text_hash = "semantic-reject"

        class FakeSemanticScorer:
            def score(self, job):
                _ = job
                return FakeSemanticResult()

        payload = [
            {
                "source": "fake",
                "external_id": "job-stage2-reject-2",
                "url": "https://example.com/job-stage2-reject-2",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Open to candidates with OPT/CPT. Build ETL pipelines with Python and SQL.",
                "skills": ["python", "sql"],
            }
        ]

        notifier = FakeNotifier()
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            with patch("job_hunter.pipeline._build_semantic_shadow_scorer", return_value=FakeSemanticScorer()):
                outcome = run_pipeline(self.settings, self.store, notifier)

        self.assertEqual(outcome.persisted_count, 1)
        self.assertEqual(outcome.notified_count, 0)
        self.assertEqual(notifier.sent, 0)
        row = self.store._conn.execute(
            """
            SELECT profile_match_label, semantic_match_label, notified
            FROM jobs
            WHERE id = 1
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["profile_match_label"], "pass")
        self.assertEqual(row["semantic_match_label"], "reject")
        self.assertEqual(int(row["notified"] or 0), 0)


if __name__ == "__main__":
    unittest.main()
