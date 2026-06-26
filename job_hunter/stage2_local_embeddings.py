from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

DEFAULT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_BATCH_SIZE = 32


@dataclass(slots=True)
class EmbeddingBatchDiagnostics:
    model_name: str
    requested_device: str
    device: str
    device_source: str
    local_files_only: bool
    batch_size: int
    total_texts: int
    total_batches: int
    embedding_dimension: int
    max_sequence_length: int | None
    token_lengths: list[int]
    overflow_tokens_per_text: list[int]
    total_input_tokens: int
    total_truncated_tokens: int
    max_observed_tokens: int
    truncated_count: int
    truncated_indices: list[int]
    truncated_job_rate: float
    truncated_token_share: float
    avg_overflow_tokens_on_truncated_jobs: float
    p95_overflow_tokens: int


@dataclass(slots=True)
class EmbeddingBatchResult:
    vectors: np.ndarray
    diagnostics: EmbeddingBatchDiagnostics


class LocalEmbeddingBackend:
    def __init__(
        self,
        model_name: str = DEFAULT_LOCAL_EMBEDDING_MODEL,
        device: str | None = None,
        local_files_only: bool = True,
        model: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.requested_device = device or "auto"
        self.device, self.device_source = resolve_torch_device(self.requested_device)
        self.local_files_only = local_files_only
        self._model = model

    @property
    def model(self) -> Any:
        if self._model is None:
            sentence_transformers = importlib.import_module("sentence_transformers")
            model_cls = getattr(sentence_transformers, "SentenceTransformer")
            try:
                self._model = model_cls(
                    self.model_name,
                    device=self.device,
                    local_files_only=self.local_files_only,
                )
            except Exception as exc:
                if self.local_files_only and _is_likely_local_model_cache_error(exc):
                    raise RuntimeError(
                        f"Local embedding model '{self.model_name}' is not available in the local cache. "
                        "Run once with network enabled or local_files_only=False to download it."
                    ) from exc
                if self.device == "mps" and _is_likely_mps_runtime_error(exc):
                    raise RuntimeError(
                        "MPS was requested but is not available in this Python/Torch runtime. "
                        "Use --device cpu for this environment."
                    ) from exc
                raise
        return self._model

    def embed_texts(
        self,
        texts: list[str],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        normalize_embeddings: bool = True,
    ) -> EmbeddingBatchResult:
        if not texts:
            return EmbeddingBatchResult(
                vectors=np.zeros((0, 0), dtype=np.float32),
                diagnostics=EmbeddingBatchDiagnostics(
                    model_name=self.model_name,
                    requested_device=self.requested_device,
                    device=self.device,
                    device_source=self.device_source,
                    local_files_only=self.local_files_only,
                    batch_size=max(batch_size, 1),
                    total_texts=0,
                    total_batches=0,
                    embedding_dimension=0,
                    max_sequence_length=_get_max_sequence_length(self.model),
                    token_lengths=[],
                    overflow_tokens_per_text=[],
                    total_input_tokens=0,
                    total_truncated_tokens=0,
                    max_observed_tokens=0,
                    truncated_count=0,
                    truncated_indices=[],
                    truncated_job_rate=0.0,
                    truncated_token_share=0.0,
                    avg_overflow_tokens_on_truncated_jobs=0.0,
                    p95_overflow_tokens=0,
                ),
            )

        safe_batch_size = max(batch_size, 1)
        model = self.model
        token_lengths = _token_lengths(model, texts)
        max_sequence_length = _get_max_sequence_length(model)
        overflow_tokens_per_text = [
            max(length - max_sequence_length, 0) if max_sequence_length else 0 for length in token_lengths
        ]
        truncated_indices = [idx for idx, length in enumerate(token_lengths) if max_sequence_length and length > max_sequence_length]
        total_input_tokens = sum(token_lengths)
        total_truncated_tokens = sum(overflow_tokens_per_text)
        truncated_count = len(truncated_indices)
        truncated_job_rate = truncated_count / len(texts) if texts else 0.0
        truncated_token_share = total_truncated_tokens / total_input_tokens if total_input_tokens else 0.0
        truncated_overflows = [overflow for overflow in overflow_tokens_per_text if overflow > 0]
        avg_overflow_tokens_on_truncated_jobs = (
            sum(truncated_overflows) / len(truncated_overflows) if truncated_overflows else 0.0
        )
        p95_overflow_tokens = _percentile_int(truncated_overflows, 95)

        batch_vectors: list[np.ndarray] = []
        for batch in chunk_texts(texts, safe_batch_size):
            encoded = model.encode(
                batch,
                batch_size=safe_batch_size,
                convert_to_numpy=True,
                normalize_embeddings=normalize_embeddings,
                show_progress_bar=False,
            )
            batch_vectors.append(np.asarray(encoded, dtype=np.float32))

        vectors = np.concatenate(batch_vectors, axis=0) if batch_vectors else np.zeros((0, 0), dtype=np.float32)
        diagnostics = EmbeddingBatchDiagnostics(
            model_name=self.model_name,
            requested_device=self.requested_device,
            device=self.device,
            device_source=self.device_source,
            local_files_only=self.local_files_only,
            batch_size=safe_batch_size,
            total_texts=len(texts),
            total_batches=len(batch_vectors),
            embedding_dimension=int(vectors.shape[1]) if vectors.ndim == 2 and vectors.size else 0,
            max_sequence_length=max_sequence_length,
            token_lengths=token_lengths,
            overflow_tokens_per_text=overflow_tokens_per_text,
            total_input_tokens=total_input_tokens,
            total_truncated_tokens=total_truncated_tokens,
            max_observed_tokens=max(token_lengths, default=0),
            truncated_count=truncated_count,
            truncated_indices=truncated_indices,
            truncated_job_rate=truncated_job_rate,
            truncated_token_share=truncated_token_share,
            avg_overflow_tokens_on_truncated_jobs=avg_overflow_tokens_on_truncated_jobs,
            p95_overflow_tokens=p95_overflow_tokens,
        )
        return EmbeddingBatchResult(vectors=vectors, diagnostics=diagnostics)


def chunk_texts(texts: list[str], batch_size: int) -> Iterable[list[str]]:
    safe_batch_size = max(batch_size, 1)
    for start in range(0, len(texts), safe_batch_size):
        yield texts[start : start + safe_batch_size]


def detect_best_torch_device() -> str:
    return resolve_torch_device("auto")[0]


def resolve_torch_device(requested_device: str | None) -> tuple[str, str]:
    normalized = (requested_device or "auto").strip().lower()
    if normalized in {"auto", ""}:
        return _detect_best_torch_device(), "detected"
    return normalized, "explicit"


def _detect_best_torch_device() -> str:
    try:
        torch = importlib.import_module("torch")
    except ModuleNotFoundError:
        return "cpu"

    mps = getattr(getattr(torch, "backends", object()), "mps", None)
    if mps is not None and hasattr(mps, "is_available") and mps.is_available():
        return "mps"
    cuda = getattr(torch, "cuda", None)
    if cuda is not None and hasattr(cuda, "is_available") and cuda.is_available():
        return "cuda"
    return "cpu"


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_max_sequence_length(model: Any) -> int | None:
    value = getattr(model, "max_seq_length", None)
    if isinstance(value, int) and value > 0:
        return value
    return None


def _token_lengths(model: Any, texts: list[str]) -> list[int]:
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None:
        return [0 for _ in texts]
    encoded = tokenizer(texts, add_special_tokens=True, truncation=False, padding=False)
    input_ids = encoded.get("input_ids", [])
    lengths: list[int] = []
    for item in input_ids:
        try:
            lengths.append(len(item))
        except TypeError:
            lengths.append(0)
    return lengths


def _percentile_int(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    bounded_percentile = min(max(percentile, 0), 100)
    sorted_values = sorted(values)
    rank = int(np.ceil((bounded_percentile / 100) * len(sorted_values))) - 1
    rank = min(max(rank, 0), len(sorted_values) - 1)
    return int(sorted_values[rank])


def _is_likely_local_model_cache_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = (
        "couldn't connect",
        "could not connect",
        "local_files_only",
        "not found in local cache",
        "no such file or directory",
        "modules.json",
        "config.json",
        "file not found",
    )
    return any(marker in message for marker in markers)


def _is_likely_mps_runtime_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = (
        "mps backend",
        "not available for mps",
        "mps is not available",
        "not linked with support for mps",
    )
    return any(marker in message for marker in markers)
