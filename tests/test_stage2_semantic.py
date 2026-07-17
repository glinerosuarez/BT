from __future__ import annotations

import unittest

import numpy as np

from job_hunter.models import JobRecord
from job_hunter.stage2_semantic import SemanticShadowScorer


class FakeEmbeddingResult:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = np.asarray(vectors, dtype=np.float32)


class FakeEmbeddingBackend:
    def __init__(self) -> None:
        self.model_name = "fake-semantic-model"
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str], *, batch_size: int, normalize_embeddings: bool = True):
        _ = batch_size, normalize_embeddings
        self.calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            if "ideal internship centered on data engineering" in lowered:
                vectors.append([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            elif "ideal internship centered on data science" in lowered:
                vectors.append([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            elif "ideal internship centered on backend engineering" in lowered:
                vectors.append([0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
            elif "ideal internship centered on machine learning engineering" in lowered:
                vectors.append([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
            elif "low-fit internship centered on academic or research-heavy work" in lowered:
                vectors.append([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0])
            elif "target internship for an ms cs student focused on building ml and data systems" in lowered:
                vectors.append([0.7, 0.4, 0.5, 0.6, 0.0, 0.0, 0.0])
            elif "low-fit internship centered on business analyst or consulting work" in lowered:
                vectors.append([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])
            elif "low-fit internship centered on marketing, content creation, social media" in lowered:
                vectors.append([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])
            elif "low-fit internship centered on web development, frontend product work" in lowered:
                vectors.append([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])
            elif "low-fit internship centered on quantitative research, trading, market making" in lowered:
                vectors.append([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            elif any(
                phrase in lowered
                for phrase in (
                    "data science",
                    "statistical analysis",
                    "hypothesis testing",
                    "predictive modeling",
                    "model evaluation",
                    "a/b testing",
                    "experimentation",
                )
            ):
                vectors.append([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            elif "data engineering" in lowered or "etl" in lowered or "lakehouse" in lowered:
                vectors.append([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            elif any(
                phrase in lowered
                for phrase in (
                    "backend developer",
                    "backend services",
                    "server-side",
                    "restful",
                    "graphql",
                    "django rest framework",
                    "express",
                    "apis",
                    "postgresql",
                    "mongodb",
                    "docker",
                    "azure cloud",
                )
            ):
                vectors.append([0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
            elif any(
                phrase in lowered
                for phrase in (
                    "research background preferred",
                    "publications are a plus",
                    "original research",
                    "research scientist",
                )
            ):
                vectors.append([0.0, 0.0, 0.0, 0.6, 0.0, 0.9, 0.0])
            elif any(
                phrase in lowered
                for phrase in (
                    "quantitative research",
                    "market making",
                    "options theory",
                    "stat arb",
                    "trading intuition",
                )
            ):
                vectors.append([0.0, 0.0, 0.0, 0.2, 0.0, 0.0, 0.95])
            elif "model deployment" in lowered or "applied ml" in lowered or "llm" in lowered or "production ml systems" in lowered:
                vectors.append([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
            elif "summarize insights" in lowered or "documenting findings" in lowered:
                vectors.append([0.3, 0.6, 0.1, 0.0, 0.0, 0.0, 0.0])
            elif any(
                phrase in lowered
                for phrase in (
                    "business analyst",
                    "client meetings",
                    "presentations and reports",
                    "strategy consulting",
                    "commercialization",
                    "brand campaigns",
                    "tiktok",
                    "filming",
                    "editing",
                    "react",
                    "next.js",
                    "vue",
                    "seo",
                    "page performance",
                    "website",
                    "newsletter",
                )
            ):
                vectors.append([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])
        return FakeEmbeddingResult(vectors)


class Stage2SemanticTests(unittest.TestCase):
    def test_semantic_scorer_prefers_data_engineering_profile(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="1",
            url="https://example.com/1",
            title="Data Engineering Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at="2026-06-25",
            description="Build ETL pipelines and lakehouse infrastructure with Python and SQL.",
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertEqual(result.semantic_match_label, "pass")
        self.assertEqual(result.semantic_profile_id, "data_engineering")
        self.assertGreaterEqual(result.semantic_match_score, 0.99)
        self.assertGreaterEqual(result.semantic_base_score, result.semantic_match_score)
        self.assertEqual(result.semantic_research_heaviness_score, 0.0)
        self.assertIn("semantic_similarity_high", result.semantic_match_reason_codes)
        self.assertEqual(result.semantic_model_name, "fake-semantic-model")
        self.assertEqual(len(backend.calls), 2)

    def test_semantic_scorer_prefers_data_science_profile(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="1ds",
            url="https://example.com/1ds",
            title="Data Science Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at="2026-06-25",
            description=(
                "Run statistical analysis and experimentation on product datasets. "
                "Build predictive models in Python and SQL, evaluate model quality, "
                "and communicate insights to cross-functional teams."
            ),
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertEqual(result.semantic_match_label, "pass")
        self.assertEqual(result.semantic_profile_id, "data_science")

    def test_semantic_scorer_prefers_backend_engineering_profile(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="1b",
            url="https://example.com/1b",
            title="Backend Developer Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at="2026-06-25",
            description=(
                "Build backend services and RESTful APIs using Django REST Framework and Express. "
                "Work with PostgreSQL, MongoDB, Docker, and Azure cloud deployment."
            ),
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertEqual(result.semantic_match_label, "pass")
        self.assertEqual(result.semantic_profile_id, "backend_engineering")

    def test_semantic_scorer_rejects_unaligned_text(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="2",
            url="https://example.com/2",
            title="University Recruiter",
            company="Example",
            location="Remote - US",
            is_internship=False,
            posted_at="2026-06-25",
            description="Campus recruiting and employer branding role.",
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertEqual(result.semantic_match_label, "reject")
        self.assertEqual(result.semantic_profile_id, "no_positive_match")
        self.assertIn("semantic_no_positive_profile_match", result.semantic_match_reason_codes)
        self.assertIn("semantic_similarity_low", result.semantic_match_reason_codes)
        self.assertTrue(result.semantic_text_hash)

    def test_semantic_scorer_downgrades_research_heavy_role(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="3",
            url="https://example.com/3",
            title="Master's Fall Machine Learning Internship",
            company="Example",
            location="US",
            is_internship=True,
            posted_at="2026-06-25",
            description=(
                "Build production ML systems for visual search. "
                "Research background preferred. Publications are a plus. "
                "Working towards a Master's degree in Computer Science or Statistics."
            ),
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertGreater(result.semantic_base_score, result.semantic_match_score)
        self.assertGreater(result.semantic_research_heaviness_score, 0.0)
        self.assertIn("semantic_research_profile_academic_research", result.semantic_adjustment_reason_codes)
        self.assertIn("semantic_penalty_academic_research_profile", result.semantic_adjustment_reason_codes)
        self.assertIn("semantic_penalty_research_background", result.semantic_adjustment_reason_codes)
        self.assertEqual(result.semantic_match_label, "reject")

    def test_semantic_scorer_penalizes_business_analyst_consulting_match(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="4",
            url="https://example.com/4",
            title="Summer Business Analyst Intern, Advanced Degree",
            company="Example",
            location="US",
            is_internship=True,
            posted_at="2026-06-25",
            description=(
                "Assist case teams with client meetings, presentations and reports. "
                "Support workshops and interviews for strategy consulting engagements. "
                "Analyze business problems and communicate findings to stakeholders."
            ),
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertEqual(result.semantic_profile_id, "no_positive_match")
        self.assertEqual(result.semantic_match_label, "reject")
        self.assertIn("semantic_no_positive_profile_match", result.semantic_match_reason_codes)
        self.assertIn(
            "semantic_negative_profile_business_analyst_consulting",
            result.semantic_match_reason_codes,
        )
        self.assertIn(
            "semantic_penalty_negative_profile_match",
            result.semantic_match_reason_codes,
        )
        self.assertLessEqual(result.semantic_match_score, result.semantic_base_score)

    def test_semantic_scorer_penalizes_quant_research_trading_match(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="4b",
            url="https://example.com/4b",
            title="Quantitative Research Intern (BS/MS) - Summer 2027",
            company="IMC Trading",
            location="US",
            is_internship=True,
            posted_at="2026-07-01",
            description=(
                "Work alongside quantitative researchers to explore new research ideas. "
                "Enhance your understanding of options theory, market making and trades analysis. "
                "Python experience is highly desired."
            ),
            ingested_at="2026-07-02T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertEqual(result.semantic_profile_id, "no_positive_match")
        self.assertIn("semantic_no_positive_profile_match", result.semantic_match_reason_codes)
        self.assertIn("semantic_research_profile_quant_research_trading", result.semantic_adjustment_reason_codes)
        self.assertIn("semantic_penalty_quant_research_trading_profile", result.semantic_adjustment_reason_codes)
        self.assertIn("semantic_penalty_quant_research_title", result.semantic_adjustment_reason_codes)
        self.assertEqual(result.semantic_match_label, "reject")

    def test_semantic_scorer_penalizes_analyst_program_title(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="4c",
            url="https://example.com/4c",
            title="Quantitative Data Analytics Summer Analyst Program",
            company="Example Bank",
            location="US",
            is_internship=True,
            posted_at="2026-07-01",
            description=(
                "Support analytics projects and present findings to business partners. "
                "Participate in a structured summer analyst program for quantitative talent."
            ),
            ingested_at="2026-07-02T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertIn("semantic_penalty_analyst_program_title", result.semantic_adjustment_reason_codes)
        self.assertEqual(result.semantic_profile_id, "no_positive_match")
        self.assertEqual(result.semantic_match_label, "reject")

    def test_semantic_scorer_keeps_applied_ai_role_out_of_research_reject(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="111",
            url="https://example.com/111",
            title="Agentic AI Intern",
            company="DataRobot",
            location="Remote - US",
            is_internship=True,
            posted_at="2026-07-15",
            description=(
                "Build and deploy agentic AI workflows for engineering automation. "
                "Use AutoML and MLOps capabilities to design, evaluate, and ship solutions. "
                "Currently pursuing or recently completed a Bachelor's or Master's degree in Computer Science, "
                "Data Science, Statistics, Engineering, or a related quantitative field. "
                "Prior internship, research, or project experience applying ML to real-world problems is helpful."
            ),
            ingested_at="2026-07-16T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertIn(result.semantic_profile_id, {"ml_engineering", "data_science"})
        self.assertIn(result.semantic_match_label, {"review", "pass"})
        self.assertLess(result.semantic_research_heaviness_score, 0.1)

    def test_semantic_scorer_penalizes_missing_builder_evidence(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="5",
            url="https://example.com/5",
            title="Data Insights Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at="2026-06-25",
            description=(
                "Analyze data and summarize insights for internal teams. "
                "Support decision making by reviewing trends and documenting findings."
            ),
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertIn(
            "semantic_penalty_missing_builder_evidence",
            result.semantic_match_reason_codes,
        )
        self.assertTrue(
            any(
                reason in result.semantic_match_reason_codes
                for reason in (
                    "semantic_penalty_builder_bucket_count_0",
                    "semantic_penalty_builder_bucket_count_1",
                )
            )
        )

    def test_semantic_scorer_penalizes_web_frontend_product_match(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="6",
            url="https://example.com/6",
            title="Web Developer Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at="2026-06-25",
            description=(
                "Build website features with React and Next.js. "
                "Improve SEO, page performance, newsletters, and frontend user experience. "
                "Work on content workflows and product features."
            ),
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertIn(
            "semantic_penalty_negative_profile_match",
            result.semantic_match_reason_codes,
        )
        self.assertEqual(result.semantic_match_label, "reject")
        self.assertEqual(result.semantic_profile_id, "no_positive_match")

    def test_semantic_scorer_requires_lexical_support_for_negative_profile_penalty(self) -> None:
        backend = FakeEmbeddingBackend()
        scorer = SemanticShadowScorer(backend=backend)
        job = JobRecord(
            source="fake",
            external_id="7",
            url="https://example.com/7",
            title="AI & Data Scientist Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at="2026-06-25",
            description=(
                "Collaborate on AI and data science initiatives for manufacturing teams. "
                "Support experiments, analyze datasets, and communicate findings."
            ),
            ingested_at="2026-06-26T00:00:00+00:00",
        )

        result = scorer.score(job)

        self.assertNotIn(
            "semantic_penalty_negative_profile_match",
            result.semantic_match_reason_codes,
        )
        self.assertNotIn(
            "semantic_negative_profile_web_frontend_product",
            result.semantic_match_reason_codes,
        )


if __name__ == "__main__":
    unittest.main()
