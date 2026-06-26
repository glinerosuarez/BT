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
                vectors.append([1.0, 0.0, 0.0])
            elif "ideal internship centered on machine learning engineering" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            elif "target internship for an ms cs student focused on building ml and data systems" in lowered:
                vectors.append([0.7, 0.7, 0.0])
            elif "data engineering" in lowered or "etl" in lowered or "lakehouse" in lowered:
                vectors.append([1.0, 0.0, 0.0])
            elif "model deployment" in lowered or "applied ml" in lowered or "llm" in lowered or "production ml systems" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
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
        self.assertIn("semantic_penalty_mentions_research", result.semantic_adjustment_reason_codes)
        self.assertIn("semantic_penalty_masters_signal", result.semantic_adjustment_reason_codes)
        self.assertIn("semantic_penalty_research_heavy_signal", result.semantic_adjustment_reason_codes)
        self.assertIn("semantic_penalty_degree_track_title", result.semantic_adjustment_reason_codes)
        self.assertIn("semantic_penalty_research_degree_title_stack", result.semantic_adjustment_reason_codes)
        self.assertEqual(result.semantic_match_label, "reject")


if __name__ == "__main__":
    unittest.main()
