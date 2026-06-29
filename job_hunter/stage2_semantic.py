from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

import numpy as np

from job_hunter.models import JobRecord
from job_hunter.stage2 import build_job_text_v1, extract_job_flags, _has_research_heavy_signals
from job_hunter.stage2_local_embeddings import (
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    LocalEmbeddingBackend,
    stable_text_hash,
)

SEMANTIC_PROFILE_VERSION = "semantic_profile_v1"
SEMANTIC_SCORER_VERSION = "semantic_shadow_v1"
NEGATIVE_PROFILE_PENALTY_SCALE = 0.35
BUILDER_SPARSE_PENALTY_ONE_BUCKET = 0.06
BUILDER_SPARSE_PENALTY_ZERO_BUCKETS = 0.12


@dataclass(frozen=True, slots=True)
class SemanticProfile:
    profile_id: str
    text: str
    polarity: str = "positive"


@dataclass(frozen=True, slots=True)
class SemanticStage2Result:
    semantic_match_score: float
    semantic_match_label: str
    semantic_match_reason_codes: list[str]
    semantic_base_score: float
    semantic_research_heaviness_score: float
    semantic_adjustment_reason_codes: list[str]
    semantic_profile_id: str
    semantic_model_name: str
    semantic_scorer_version: str
    semantic_text_hash: str


class EmbeddingBackendProtocol(Protocol):
    model_name: str

    def embed_texts(
        self,
        texts: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool = True,
    ):
        ...


DEFAULT_SEMANTIC_PROFILES: tuple[SemanticProfile, ...] = (
    SemanticProfile(
        profile_id="builder_ml_data",
        text=(
            "Target internship for an MS CS student focused on building ML and data systems. "
            "Strong fit means data engineering, machine learning engineering, analytics engineering, "
            "ETL pipelines, SQL, Python, AWS, production ML, deployed models, workflow automation, "
            "platform work, experimentation infrastructure, and applied AI systems. "
            "Lower fit means research-heavy roles, publications, pure academia, recruiting, sales, "
            "content creation, or non-technical business tracks."
        ),
        polarity="positive",
    ),
    SemanticProfile(
        profile_id="data_engineering",
        text=(
            "Ideal internship centered on data engineering: ETL, ELT, data pipelines, lakehouse, "
            "warehousing, orchestration, SQL, Python, Spark, Airflow, dbt, AWS, reliability, "
            "and production data platforms."
        ),
        polarity="positive",
    ),
    SemanticProfile(
        profile_id="ml_engineering",
        text=(
            "Ideal internship centered on machine learning engineering: applied ML, model deployment, "
            "model serving, feature pipelines, training infrastructure, LLM applications, evaluation, "
            "Python, production systems, and builder-oriented AI work."
        ),
        polarity="positive",
    ),
    SemanticProfile(
        profile_id="business_analyst_consulting",
        text=(
            "Low-fit internship centered on business analyst or consulting work: case teams, client meetings, "
            "presentations and reports, workshops and interviews, strategy consulting, commercialization, "
            "business development, market analysis, stakeholder communication, and generalist business problem solving "
            "without building software, data pipelines, or ML systems."
        ),
        polarity="negative",
    ),
    SemanticProfile(
        profile_id="marketing_content_social",
        text=(
            "Low-fit internship centered on marketing, content creation, social media, TikTok, brand campaigns, "
            "filming, editing, creator partnerships, trendspotting, and communications rather than data engineering or ML systems."
        ),
        polarity="negative",
    ),
    SemanticProfile(
        profile_id="web_frontend_product",
        text=(
            "Low-fit internship centered on web development, frontend product work, websites, user experience, "
            "SEO, page performance, newsletters, content workflows, React, Next.js, Vue, JavaScript, TypeScript, "
            "and general product engineering without primary focus on data engineering, analytics engineering, "
            "production ML, ETL pipelines, or ML systems."
        ),
        polarity="negative",
    ),
)

_BUILDER_EVIDENCE_BUCKETS: dict[str, tuple[str, ...]] = {
    "data_platform": (
        r"\betl\b",
        r"\belt\b",
        r"\bpipelines?\b",
        r"\bdata warehouse\b",
        r"\bwarehousing\b",
        r"\blakehouse\b",
        r"\bspark\b",
        r"\bairflow\b",
        r"\bdbt\b",
    ),
    "programming": (
        r"\bpython\b",
        r"\bsql\b",
        r"\bjava\b",
        r"\bscala\b",
    ),
    "infra_production": (
        r"\baws\b",
        r"\bgcp\b",
        r"\bazure\b",
        r"\bdeployment\b",
        r"\bproduction\b",
        r"\borchestration\b",
        r"\bterraform\b",
        r"\bkubernetes\b",
    ),
    "software_systems": (
        r"\bbackend\b",
        r"\bapis?\b",
        r"\bmicroservices?\b",
        r"\bdistributed systems?\b",
        r"\bautomation\b",
        r"\bsystems?\b",
    ),
    "ml_systems": (
        r"\bmodel deployment\b",
        r"\bmodel serving\b",
        r"\bfeature pipelines?\b",
        r"\btraining infrastructure\b",
        r"\bllm\b",
        r"\bevaluation\b",
        r"\bml systems\b",
    ),
}

_GENERALIST_ANALYTICAL_PATTERNS: tuple[str, ...] = (
    r"\bbusiness analyst\b",
    r"\bclient meetings?\b",
    r"\bpresentations? and reports?\b",
    r"\bworkshops? and interviews?\b",
    r"\bstrategy consulting\b",
    r"\bcommercialization\b",
    r"\bbusiness development\b",
    r"\bstakeholders?\b",
    r"\bmarket analysis\b",
    r"\bcommunications?\b",
    r"\breports?\b",
)

_NEGATIVE_PROFILE_LEXICAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "business_analyst_consulting": (
        r"\bbusiness analyst\b",
        r"\bclient meetings?\b",
        r"\bpresentations? and reports?\b",
        r"\bworkshops? and interviews?\b",
        r"\bstrategy consulting\b",
        r"\bcommercialization\b",
        r"\bbusiness development\b",
        r"\bstakeholders?\b",
        r"\bmarket analysis\b",
    ),
    "marketing_content_social": (
        r"\bcontent creation\b",
        r"\bsocial media\b",
        r"\btiktok\b",
        r"\bbrand campaigns?\b",
        r"\bfilming\b",
        r"\bediting\b",
        r"\bcreator partnerships?\b",
        r"\btrendspotting\b",
        r"\bcommunications?\b",
    ),
    "web_frontend_product": (
        r"\bweb(?:site)?\b",
        r"\bfrontend\b",
        r"\buser experience\b",
        r"\bseo\b",
        r"\bpage performance\b",
        r"\bnewsletters?\b",
        r"\bcontent workflows?\b",
        r"\breact\b",
        r"\bnext\.js\b",
        r"\bvue\b",
        r"\bjavascript\b",
        r"\btypescript\b",
        r"\bproduct features?\b",
    ),
}


class SemanticShadowScorer:
    def __init__(
        self,
        *,
        backend: EmbeddingBackendProtocol | None = None,
        profiles: tuple[SemanticProfile, ...] = DEFAULT_SEMANTIC_PROFILES,
        batch_size: int = 32,
        profile_version: str = SEMANTIC_PROFILE_VERSION,
        scorer_version: str = SEMANTIC_SCORER_VERSION,
    ) -> None:
        self.backend = backend or LocalEmbeddingBackend(device="cpu", local_files_only=True)
        self.profiles = profiles
        self.batch_size = batch_size
        self.profile_version = profile_version
        self.scorer_version = scorer_version
        self._profile_matrix: np.ndarray | None = None
        self._positive_profile_indices = [idx for idx, profile in enumerate(self.profiles) if profile.polarity == "positive"]
        self._negative_profile_indices = [idx for idx, profile in enumerate(self.profiles) if profile.polarity == "negative"]

    def score(self, job: JobRecord) -> SemanticStage2Result:
        job_text = build_job_text_v1(job)
        return self.score_job_text(job_text)

    def score_job_text(self, job_text: str) -> SemanticStage2Result:
        text_hash = stable_text_hash(job_text)
        job_vectors = self.backend.embed_texts([job_text], batch_size=1, normalize_embeddings=True).vectors
        if job_vectors.size == 0:
            return SemanticStage2Result(
                semantic_match_score=0.0,
                semantic_match_label="reject",
                semantic_match_reason_codes=["semantic_empty_embedding"],
                semantic_base_score=0.0,
                semantic_research_heaviness_score=0.0,
                semantic_adjustment_reason_codes=[],
                semantic_profile_id="",
                semantic_model_name=self.backend.model_name,
                semantic_scorer_version=self.scorer_version,
                semantic_text_hash=text_hash,
            )

        profile_matrix = self._get_profile_matrix()
        similarity_scores = np.matmul(profile_matrix, job_vectors[0])
        positive_index, base_score = _best_profile_match(similarity_scores, self._positive_profile_indices)
        best_profile = self.profiles[positive_index]
        negative_penalty, negative_reason_codes = _negative_profile_adjustment(
            similarity_scores,
            self.profiles,
            self._negative_profile_indices,
            job_text,
        )
        research_heaviness_score, adjustment_reason_codes = _research_heaviness_adjustment(job_text)
        builder_evidence_penalty, builder_adjustment_reason_codes = _builder_evidence_adjustment(
            job_text,
            pre_adjustment_score=max(0.0, min(base_score - negative_penalty - research_heaviness_score, 1.0)),
        )
        adjusted_score = max(
            0.0,
            min(base_score - negative_penalty - research_heaviness_score - builder_evidence_penalty, 1.0),
        )
        label = _semantic_label(adjusted_score)
        reasons = _semantic_reason_codes(adjusted_score, best_profile.profile_id)
        reasons.extend(negative_reason_codes)
        reasons.extend(adjustment_reason_codes)
        reasons.extend(builder_adjustment_reason_codes)
        return SemanticStage2Result(
            semantic_match_score=adjusted_score,
            semantic_match_label=label,
            semantic_match_reason_codes=sorted(set(reasons)),
            semantic_base_score=base_score,
            semantic_research_heaviness_score=research_heaviness_score,
            semantic_adjustment_reason_codes=sorted(set(adjustment_reason_codes)),
            semantic_profile_id=best_profile.profile_id,
            semantic_model_name=self.backend.model_name,
            semantic_scorer_version=self.scorer_version,
            semantic_text_hash=text_hash,
        )

    def _get_profile_matrix(self) -> np.ndarray:
        if self._profile_matrix is None:
            profile_texts = [profile.text for profile in self.profiles]
            result = self.backend.embed_texts(
                profile_texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
            )
            self._profile_matrix = np.asarray(result.vectors, dtype=np.float32)
        return self._profile_matrix


def _semantic_label(score: float) -> str:
    if score >= 0.62:
        return "pass"
    if score >= 0.52:
        return "review"
    return "reject"


def _semantic_reason_codes(score: float, profile_id: str) -> list[str]:
    reasons = [f"semantic_profile_{profile_id}"]
    if score >= 0.70:
        reasons.append("semantic_similarity_high")
    elif score >= 0.62:
        reasons.append("semantic_similarity_pass")
    elif score >= 0.52:
        reasons.append("semantic_similarity_borderline")
    else:
        reasons.append("semantic_similarity_low")
    return reasons


def _best_profile_match(similarity_scores: np.ndarray, indices: list[int]) -> tuple[int, float]:
    if not indices:
        return 0, 0.0
    best_index = max(indices, key=lambda idx: float(similarity_scores[idx]))
    return best_index, float(similarity_scores[best_index])


def _negative_profile_adjustment(
    similarity_scores: np.ndarray,
    profiles: tuple[SemanticProfile, ...],
    negative_indices: list[int],
    job_text: str,
) -> tuple[float, list[str]]:
    if not negative_indices:
        return 0.0, []
    supported_candidates = [
        idx
        for idx in negative_indices
        if _has_negative_profile_lexical_support(job_text, profiles[idx].profile_id)
    ]
    if not supported_candidates:
        return 0.0, []
    best_index, best_score = _best_profile_match(similarity_scores, supported_candidates)
    if best_score < 0.52:
        return 0.0, []
    penalty = max(0.0, best_score - 0.52) * NEGATIVE_PROFILE_PENALTY_SCALE
    profile_id = profiles[best_index].profile_id
    return penalty, [
        f"semantic_negative_profile_{profile_id}",
        "semantic_penalty_negative_profile_match",
    ]


def _builder_evidence_adjustment(job_text: str, *, pre_adjustment_score: float) -> tuple[float, list[str]]:
    if pre_adjustment_score < 0.52:
        return 0.0, []
    blob = job_text.lower()
    builder_hits = _builder_evidence_hits(blob)
    bucket_count = len(builder_hits)
    if bucket_count >= 2:
        return 0.0, []
    has_generalist_signal = any(re.search(pattern, blob, flags=re.IGNORECASE) for pattern in _GENERALIST_ANALYTICAL_PATTERNS)
    if not has_generalist_signal and bucket_count == 1:
        return 0.0, []
    if bucket_count == 0:
        return BUILDER_SPARSE_PENALTY_ZERO_BUCKETS, [
            "semantic_penalty_missing_builder_evidence",
            "semantic_penalty_builder_bucket_count_0",
        ]
    return BUILDER_SPARSE_PENALTY_ONE_BUCKET, [
        "semantic_penalty_missing_builder_evidence",
        "semantic_penalty_builder_bucket_count_1",
    ]


def _builder_evidence_hits(blob: str) -> list[str]:
    hits: list[str] = []
    for bucket, patterns in _BUILDER_EVIDENCE_BUCKETS.items():
        if any(re.search(pattern, blob, flags=re.IGNORECASE) for pattern in patterns):
            hits.append(bucket)
    return hits


def _has_negative_profile_lexical_support(job_text: str, profile_id: str) -> bool:
    patterns = _NEGATIVE_PROFILE_LEXICAL_PATTERNS.get(profile_id, ())
    if not patterns:
        return False
    blob = job_text.lower()
    return any(re.search(pattern, blob, flags=re.IGNORECASE) for pattern in patterns)


def _research_heaviness_adjustment(job_text: str) -> tuple[float, list[str]]:
    blob = job_text.lower()
    flags = set(extract_job_flags(job_text))
    flags.update(_extract_snapshot_flags(job_text))
    penalty = 0.0
    reasons: list[str] = []
    title_line = next((line for line in job_text.splitlines() if line.startswith("TITLE: ")), "")
    title_blob = title_line.lower()

    has_research_signal = False
    has_degree_track_signal = False
    has_publication_signal = False
    has_degree_track_title_signal = False

    if "mentions_research" in flags:
        penalty += 0.05
        reasons.append("semantic_penalty_mentions_research")
        has_research_signal = True
    if _has_research_heavy_signals(blob):
        penalty += 0.20
        reasons.append("semantic_penalty_research_heavy_signal")
        has_research_signal = True
    if "mentions_masters" in flags:
        penalty += 0.02
        reasons.append("semantic_penalty_masters_signal")
        has_degree_track_signal = True
    if "mentions_phd" in flags:
        penalty += 0.30
        reasons.append("semantic_penalty_phd_signal")
        has_degree_track_signal = True
    if "mentions_causal_inference" in flags:
        penalty += 0.10
        reasons.append("semantic_penalty_causal_inference")

    if "publications" in blob or "publication" in blob:
        penalty += 0.25
        reasons.append("semantic_penalty_publications_signal")
        has_publication_signal = True
    if "research background" in blob:
        penalty += 0.20
        reasons.append("semantic_penalty_research_background")
        has_research_signal = True
    if "working towards a master's degree" in blob or "masters statistics major" in blob:
        penalty += 0.02
        reasons.append("semantic_penalty_degree_preference_mismatch")
        has_degree_track_signal = True
    if "title: master's" in title_blob or "title: phd" in title_blob:
        penalty += 0.08
        reasons.append("semantic_penalty_degree_track_title")
        has_degree_track_signal = True
        has_degree_track_title_signal = True
    if has_research_signal and has_degree_track_title_signal:
        penalty += 0.10
        reasons.append("semantic_penalty_research_degree_title_stack")
    if has_research_signal and has_degree_track_signal and has_publication_signal:
        penalty += 0.06
        reasons.append("semantic_penalty_research_degree_publication_stack")

    return min(penalty, 0.6), reasons


def _extract_snapshot_flags(job_text: str) -> set[str]:
    for line in job_text.splitlines():
        if not line.startswith("FLAGS:"):
            continue
        payload = line.partition(":")[2].strip()
        if not payload or payload == "none":
            return set()
        return {token.strip() for token in payload.split() if token.strip()}
    return set()
