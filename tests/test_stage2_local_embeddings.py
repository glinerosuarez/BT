from __future__ import annotations

import sys
import types
import unittest

import numpy as np

from job_hunter.stage2_local_embeddings import (
    LocalEmbeddingBackend,
    chunk_texts,
    detect_best_torch_device,
    resolve_torch_device,
    stable_text_hash,
)


class FakeTokenizer:
    def __call__(self, texts, add_special_tokens=True, truncation=False, padding=False):
        _ = add_special_tokens, truncation, padding
        input_ids = []
        for text in texts:
            input_ids.append(list(range(len(text.split()))))
        return {"input_ids": input_ids}


class FakeModel:
    def __init__(self, *, dim: int = 3, max_seq_length: int = 5) -> None:
        self.dim = dim
        self.max_seq_length = max_seq_length
        self.tokenizer = FakeTokenizer()
        self.calls: list[list[str]] = []

    def encode(self, texts, *, batch_size, convert_to_numpy, normalize_embeddings, show_progress_bar):
        _ = batch_size, convert_to_numpy, normalize_embeddings, show_progress_bar
        self.calls.append(list(texts))
        rows = []
        for idx, text in enumerate(texts):
            rows.append([float(len(text)), float(idx), 1.0])
        return np.asarray(rows, dtype=np.float32)


class RaisingSentenceTransformer:
    def __init__(self, *args, **kwargs) -> None:
        _ = args, kwargs
        raise RuntimeError("The MPS backend is supported on macOS 14.0+.")


class Stage2LocalEmbeddingsTests(unittest.TestCase):
    def test_chunk_texts_splits_input_list(self) -> None:
        chunks = list(chunk_texts(["a", "b", "c", "d", "e"], 2))
        self.assertEqual(chunks, [["a", "b"], ["c", "d"], ["e"]])

    def test_embed_texts_batches_and_concatenates_in_order(self) -> None:
        model = FakeModel()
        backend = LocalEmbeddingBackend(model=model, device="cpu")
        result = backend.embed_texts(["one", "two words", "three words here"], batch_size=2)

        self.assertEqual(model.calls, [["one", "two words"], ["three words here"]])
        self.assertEqual(result.vectors.shape, (3, 3))
        self.assertAlmostEqual(float(result.vectors[0][0]), len("one"))
        self.assertAlmostEqual(float(result.vectors[1][0]), len("two words"))
        self.assertAlmostEqual(float(result.vectors[2][0]), len("three words here"))
        self.assertEqual(result.diagnostics.total_batches, 2)
        self.assertEqual(result.diagnostics.total_texts, 3)
        self.assertEqual(result.diagnostics.embedding_dimension, 3)
        self.assertEqual(result.diagnostics.requested_device, "cpu")
        self.assertEqual(result.diagnostics.device, "cpu")
        self.assertEqual(result.diagnostics.device_source, "explicit")
        self.assertTrue(result.diagnostics.local_files_only)

    def test_embed_texts_reports_truncation_diagnostics(self) -> None:
        model = FakeModel(max_seq_length=2)
        backend = LocalEmbeddingBackend(model=model, device="cpu")
        result = backend.embed_texts(["one", "two words", "three words here"], batch_size=2)

        self.assertEqual(result.diagnostics.max_sequence_length, 2)
        self.assertEqual(result.diagnostics.token_lengths, [1, 2, 3])
        self.assertEqual(result.diagnostics.overflow_tokens_per_text, [0, 0, 1])
        self.assertEqual(result.diagnostics.total_input_tokens, 6)
        self.assertEqual(result.diagnostics.total_truncated_tokens, 1)
        self.assertEqual(result.diagnostics.max_observed_tokens, 3)
        self.assertEqual(result.diagnostics.truncated_count, 1)
        self.assertEqual(result.diagnostics.truncated_indices, [2])
        self.assertAlmostEqual(result.diagnostics.truncated_job_rate, 1 / 3)
        self.assertAlmostEqual(result.diagnostics.truncated_token_share, 1 / 6)
        self.assertAlmostEqual(result.diagnostics.avg_overflow_tokens_on_truncated_jobs, 1.0)
        self.assertEqual(result.diagnostics.p95_overflow_tokens, 1)

    def test_embed_texts_reports_high_percentile_overflow(self) -> None:
        model = FakeModel(max_seq_length=2)
        backend = LocalEmbeddingBackend(model=model, device="cpu")
        result = backend.embed_texts(
            ["one", "two words", "three words here", "four words are here now"],
            batch_size=2,
        )

        self.assertEqual(result.diagnostics.overflow_tokens_per_text, [0, 0, 1, 3])
        self.assertEqual(result.diagnostics.total_truncated_tokens, 4)
        self.assertAlmostEqual(result.diagnostics.avg_overflow_tokens_on_truncated_jobs, 2.0)
        self.assertEqual(result.diagnostics.p95_overflow_tokens, 3)

    def test_detect_best_torch_device_prefers_mps(self) -> None:
        fake_torch = types.SimpleNamespace(
            backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: True)),
            cuda=types.SimpleNamespace(is_available=lambda: True),
        )
        original = sys.modules.get("torch")
        sys.modules["torch"] = fake_torch
        try:
            self.assertEqual(detect_best_torch_device(), "mps")
        finally:
            if original is None:
                del sys.modules["torch"]
            else:
                sys.modules["torch"] = original

    def test_detect_best_torch_device_falls_back_to_cpu(self) -> None:
        original = sys.modules.get("torch")
        sys.modules["torch"] = types.SimpleNamespace(
            backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)),
            cuda=types.SimpleNamespace(is_available=lambda: False),
        )
        try:
            self.assertEqual(detect_best_torch_device(), "cpu")
        finally:
            if original is None:
                del sys.modules["torch"]
            else:
                sys.modules["torch"] = original

    def test_resolve_torch_device_marks_auto_as_detected(self) -> None:
        original = sys.modules.get("torch")
        sys.modules["torch"] = types.SimpleNamespace(
            backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)),
            cuda=types.SimpleNamespace(is_available=lambda: False),
        )
        try:
            self.assertEqual(resolve_torch_device("auto"), ("cpu", "detected"))
        finally:
            if original is None:
                del sys.modules["torch"]
            else:
                sys.modules["torch"] = original

    def test_resolve_torch_device_marks_explicit_override(self) -> None:
        self.assertEqual(resolve_torch_device("mps"), ("mps", "explicit"))

    def test_model_constructor_preserves_non_cache_runtime_error(self) -> None:
        fake_module = types.SimpleNamespace(SentenceTransformer=RaisingSentenceTransformer)
        original_import_module = __import__("importlib").import_module

        def fake_import_module(name: str):
            if name == "sentence_transformers":
                return fake_module
            return original_import_module(name)

        backend = LocalEmbeddingBackend(device="mps")
        with unittest.mock.patch("importlib.import_module", side_effect=fake_import_module):
            with self.assertRaisesRegex(RuntimeError, "Use --device cpu"):
                _ = backend.model

    def test_stable_text_hash_is_deterministic(self) -> None:
        self.assertEqual(stable_text_hash("abc"), stable_text_hash("abc"))
        self.assertNotEqual(stable_text_hash("abc"), stable_text_hash("abcd"))


if __name__ == "__main__":
    unittest.main()
