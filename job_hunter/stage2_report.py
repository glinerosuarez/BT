from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_hunter.config import load_settings
from job_hunter.storage import JobStore, ensure_parent_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Stage 2 shadow-mode outputs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List jobs with Stage 2 shadow outputs")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--label", choices=("pass", "review", "reject"))
    list_parser.add_argument("--source")
    list_parser.add_argument("--format", choices=("text", "json"), default="text")

    show_parser = subparsers.add_parser("show", help="Show one job's Stage 2 shadow output in detail")
    show_parser.add_argument("--job-id", type=int, required=True)
    show_parser.add_argument("--format", choices=("text", "json"), default="text")

    export_parser = subparsers.add_parser("export-labeled", help="Export labeled Stage 2 rows for embeddings/eval work")
    export_parser.add_argument("--output", required=True)
    export_parser.add_argument("--limit", type=int, default=200)

    semantic_backfill_parser = subparsers.add_parser(
        "semantic-backfill",
        help="Backfill semantic shadow scores for persisted Stage 2 job_text_v1 rows",
    )
    semantic_backfill_parser.add_argument("--limit", type=int, default=200)
    semantic_backfill_parser.add_argument("--label", choices=("pass", "review", "reject"))
    semantic_backfill_parser.add_argument("--source")
    semantic_backfill_parser.add_argument("--labeled-only", action="store_true")
    semantic_backfill_parser.add_argument("--model-name")
    semantic_backfill_parser.add_argument("--device", default="cpu")
    semantic_backfill_parser.add_argument("--allow-network", action="store_true")
    semantic_backfill_parser.add_argument("--format", choices=("text", "json"), default="text")

    disagreement_parser = subparsers.add_parser(
        "disagreement-report",
        help="Report rows where deterministic, semantic, and manual fit labels disagree",
    )
    disagreement_parser.add_argument("--limit", type=int, default=200)
    disagreement_parser.add_argument("--source")
    disagreement_parser.add_argument("--labeled-only", action="store_true")
    disagreement_parser.add_argument("--format", choices=("text", "json"), default="text")

    diagnostics_parser = subparsers.add_parser(
        "embedding-diagnostics",
        help="Measure local embeddings truncation on persisted Stage 2 job_text_v1 rows",
    )
    diagnostics_parser.add_argument("--limit", type=int, default=200)
    diagnostics_parser.add_argument("--label", choices=("pass", "review", "reject"))
    diagnostics_parser.add_argument("--source")
    diagnostics_parser.add_argument("--labeled-only", action="store_true")
    diagnostics_parser.add_argument("--batch-size", type=int, default=32)
    diagnostics_parser.add_argument("--model-name")
    diagnostics_parser.add_argument("--device", default="cpu")
    diagnostics_parser.add_argument("--allow-network", action="store_true")
    diagnostics_parser.add_argument("--format", choices=("text", "json"), default="text")

    args = parser.parse_args()

    settings = load_settings()
    ensure_parent_dir(settings.db_path)
    store = JobStore(settings.db_path)
    try:
        if args.command == "list":
            rows = store.list_stage2_jobs(limit=args.limit, label=args.label, source=args.source)
            payload = [_serialize_list_row(row) for row in rows]
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_render_list_text(payload))
            return 0

        if args.command == "export-labeled":
            rows = store.list_stage2_labeled_jobs(limit=args.limit)
            payload = [_serialize_show_row(row) for row in rows]
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            print(f"Wrote {len(payload)} labeled Stage 2 rows to {output_path}")
            return 0

        if args.command == "embedding-diagnostics":
            try:
                backend_cls = _load_local_embedding_backend()
                rows = store.list_stage2_job_text_rows(
                    limit=args.limit,
                    label=args.label,
                    source=args.source,
                    labeled_only=args.labeled_only,
                )
                payload = _run_embedding_diagnostics(
                    rows,
                    batch_size=args.batch_size,
                    model_name=args.model_name,
                    device=args.device,
                    local_files_only=not args.allow_network,
                    backend_cls=backend_cls,
                )
            except (ModuleNotFoundError, RuntimeError) as exc:
                message = f"Embedding diagnostics could not start: {exc}"
                if isinstance(exc, ModuleNotFoundError):
                    message += "\nIf dependencies are missing, run: pip install -e '.[local-embeddings]'"
                print(message)
                return 1
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_render_embedding_diagnostics_text(payload))
            return 0

        if args.command == "semantic-backfill":
            try:
                scorer_cls = _load_semantic_shadow_scorer()
                rows = store.list_stage2_job_text_rows(
                    limit=args.limit,
                    label=args.label,
                    source=args.source,
                    labeled_only=args.labeled_only,
                )
                payload = _run_semantic_backfill(
                    store,
                    rows,
                    scorer_cls=scorer_cls,
                    model_name=args.model_name,
                    device=args.device,
                    local_files_only=not args.allow_network,
                )
            except (ModuleNotFoundError, RuntimeError) as exc:
                message = f"Semantic backfill could not start: {exc}"
                if isinstance(exc, ModuleNotFoundError):
                    message += "\nIf dependencies are missing, run: pip install -e '.[local-embeddings]'"
                print(message)
                return 1
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_render_semantic_backfill_text(payload))
            return 0

        if args.command == "disagreement-report":
            rows = store.list_stage2_comparison_rows(
                limit=args.limit,
                source=args.source,
                labeled_only=args.labeled_only,
            )
            payload = _build_disagreement_report(rows)
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_render_disagreement_report_text(payload))
            return 0

        row = store.get_stage2_job(args.job_id)
        if row is None:
            print(f"No Stage 2 job found for id={args.job_id}")
            return 1
        payload = _serialize_show_row(row)
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(_render_show_text(payload))
        return 0
    finally:
        store.close()


def _serialize_list_row(row) -> dict[str, object]:
    reasons = _decode_json_array(row["profile_match_reason_codes"])
    return {
        "id": int(row["id"]),
        "source": str(row["source"]),
        "company": str(row["company"]),
        "title": str(row["title"]),
        "location": str(row["location"] or ""),
        "posted_at": str(row["posted_at"] or ""),
        "compensation_type": str(row["compensation_type"] or "unknown"),
        "profile_match_score": float(row["profile_match_score"] or 0.0),
        "profile_match_label": str(row["profile_match_label"] or ""),
        "profile_match_reason_codes": reasons,
        "profile_version": str(row["profile_version"] or ""),
        "scorer_version": str(row["scorer_version"] or ""),
        "job_text_version": str(row["job_text_version"] or ""),
        "semantic_match_score": float(row["semantic_match_score"] or 0.0),
        "semantic_match_label": str(row["semantic_match_label"] or ""),
        "semantic_match_reason_codes": _decode_json_array(row["semantic_match_reason_codes"]),
        "semantic_base_score": float(row["semantic_base_score"] or 0.0),
        "semantic_research_heaviness_score": float(row["semantic_research_heaviness_score"] or 0.0),
        "semantic_adjustment_reason_codes": _decode_json_array(row["semantic_adjustment_reason_codes"]),
        "semantic_profile_id": str(row["semantic_profile_id"] or ""),
        "semantic_model_name": str(row["semantic_model_name"] or ""),
        "semantic_scorer_version": str(row["semantic_scorer_version"] or ""),
    }


def _serialize_show_row(row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "source": str(row["source"]),
        "company": str(row["company"]),
        "title": str(row["title"]),
        "location": str(row["location"] or ""),
        "posted_at": str(row["posted_at"] or ""),
        "url": str(row["url"]),
        "compensation_type": str(row["compensation_type"] or "unknown"),
        "relevance_score": float(row["relevance_score"] or 0.0),
        "eligibility_status": str(row["eligibility_status"] or ""),
        "eligibility_confidence": float(row["eligibility_confidence"] or 0.0),
        "profile_match_score": float(row["profile_match_score"] or 0.0),
        "profile_match_label": str(row["profile_match_label"] or ""),
        "profile_match_reason_codes": _decode_json_array(row["profile_match_reason_codes"]),
        "profile_version": str(row["profile_version"] or ""),
        "scorer_version": str(row["scorer_version"] or ""),
        "job_text_version": str(row["job_text_version"] or ""),
        "job_text_snapshot": str(row["job_text_snapshot"] or ""),
        "semantic_match_score": float(row["semantic_match_score"] or 0.0),
        "semantic_match_label": str(row["semantic_match_label"] or ""),
        "semantic_match_reason_codes": _decode_json_array(row["semantic_match_reason_codes"]),
        "semantic_base_score": float(row["semantic_base_score"] or 0.0),
        "semantic_research_heaviness_score": float(row["semantic_research_heaviness_score"] or 0.0),
        "semantic_adjustment_reason_codes": _decode_json_array(row["semantic_adjustment_reason_codes"]),
        "semantic_profile_id": str(row["semantic_profile_id"] or ""),
        "semantic_model_name": str(row["semantic_model_name"] or ""),
        "semantic_scorer_version": str(row["semantic_scorer_version"] or ""),
        "semantic_text_hash": str(row["semantic_text_hash"] or ""),
        "manual_fit_label": str(row["manual_fit_label"] or ""),
        "manual_fit_reason_codes": _decode_json_array(row["manual_fit_reason_codes"]),
    }


def _serialize_comparison_row(row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "source": str(row["source"]),
        "company": str(row["company"]),
        "title": str(row["title"]),
        "location": str(row["location"] or ""),
        "posted_at": str(row["posted_at"] or ""),
        "compensation_type": str(row["compensation_type"] or "unknown"),
        "profile_match_score": float(row["profile_match_score"] or 0.0),
        "profile_match_label": str(row["profile_match_label"] or ""),
        "profile_match_reason_codes": _decode_json_array(row["profile_match_reason_codes"]),
        "semantic_match_score": float(row["semantic_match_score"] or 0.0),
        "semantic_match_label": str(row["semantic_match_label"] or ""),
        "semantic_match_reason_codes": _decode_json_array(row["semantic_match_reason_codes"]),
        "semantic_base_score": float(row["semantic_base_score"] or 0.0),
        "semantic_research_heaviness_score": float(row["semantic_research_heaviness_score"] or 0.0),
        "semantic_adjustment_reason_codes": _decode_json_array(row["semantic_adjustment_reason_codes"]),
        "semantic_profile_id": str(row["semantic_profile_id"] or ""),
        "manual_fit_label": str(row["manual_fit_label"] or ""),
        "manual_fit_normalized_label": _normalize_manual_fit_label(row["manual_fit_label"]),
        "manual_fit_reason_codes": _decode_json_array(row["manual_fit_reason_codes"]),
    }


def _decode_json_array(value: object) -> list[str]:
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    if not isinstance(parsed, list):
        return [str(parsed)]
    return [str(item) for item in parsed]


def _load_local_embedding_backend():
    from job_hunter.stage2_local_embeddings import LocalEmbeddingBackend

    return LocalEmbeddingBackend


def _load_semantic_shadow_scorer():
    from job_hunter.stage2_semantic import SemanticShadowScorer

    return SemanticShadowScorer


def _run_embedding_diagnostics(
    rows,
    *,
    batch_size: int,
    model_name: str | None,
    device: str | None,
    local_files_only: bool,
    backend_cls,
) -> dict[str, object]:
    texts = [str(row["job_text_snapshot"] or "") for row in rows]
    backend_kwargs: dict[str, object] = {}
    if model_name:
        backend_kwargs["model_name"] = model_name
    if device:
        backend_kwargs["device"] = device
    backend_kwargs["local_files_only"] = local_files_only
    backend = backend_cls(**backend_kwargs)
    result = backend.embed_texts(texts, batch_size=batch_size)
    diagnostics = result.diagnostics

    top_truncated = []
    for index in diagnostics.truncated_indices[:10]:
        row = rows[index]
        top_truncated.append(
            {
                "id": int(row["id"]),
                "source": str(row["source"]),
                "company": str(row["company"]),
                "title": str(row["title"]),
                "posted_at": str(row["posted_at"] or ""),
                "profile_match_label": str(row["profile_match_label"] or ""),
                "manual_fit_label": str(row["manual_fit_label"] or ""),
                "token_length": diagnostics.token_lengths[index],
                "overflow_tokens": diagnostics.overflow_tokens_per_text[index],
            }
        )

    return {
        "sample_size": len(rows),
        "filters": {
            "batch_size": diagnostics.batch_size,
            "model_name": diagnostics.model_name,
            "requested_device": diagnostics.requested_device,
            "device": diagnostics.device,
            "device_source": diagnostics.device_source,
            "local_files_only": diagnostics.local_files_only,
        },
        "diagnostics": {
            "total_texts": diagnostics.total_texts,
            "total_batches": diagnostics.total_batches,
            "embedding_dimension": diagnostics.embedding_dimension,
            "max_sequence_length": diagnostics.max_sequence_length,
            "total_input_tokens": diagnostics.total_input_tokens,
            "total_truncated_tokens": diagnostics.total_truncated_tokens,
            "max_observed_tokens": diagnostics.max_observed_tokens,
            "truncated_count": diagnostics.truncated_count,
            "truncated_job_rate": diagnostics.truncated_job_rate,
            "truncated_token_share": diagnostics.truncated_token_share,
            "avg_overflow_tokens_on_truncated_jobs": diagnostics.avg_overflow_tokens_on_truncated_jobs,
            "p95_overflow_tokens": diagnostics.p95_overflow_tokens,
        },
        "top_truncated_jobs": top_truncated,
    }


def _run_semantic_backfill(
    store: JobStore,
    rows,
    *,
    scorer_cls,
    model_name: str | None,
    device: str,
    local_files_only: bool,
) -> dict[str, object]:
    backend_cls = _load_local_embedding_backend()
    backend_kwargs: dict[str, object] = {
        "device": device,
        "local_files_only": local_files_only,
    }
    if model_name:
        backend_kwargs["model_name"] = model_name
    backend = backend_cls(**backend_kwargs)
    scorer = scorer_cls(backend=backend)

    updated_rows: list[dict[str, object]] = []
    for row in rows:
        result = scorer.score_job_text(str(row["job_text_snapshot"] or ""))
        store.update_semantic_shadow(
            int(row["id"]),
            semantic_match_score=result.semantic_match_score,
            semantic_match_label=result.semantic_match_label,
            semantic_match_reason_codes=result.semantic_match_reason_codes,
            semantic_base_score=result.semantic_base_score,
            semantic_research_heaviness_score=result.semantic_research_heaviness_score,
            semantic_adjustment_reason_codes=result.semantic_adjustment_reason_codes,
            semantic_profile_id=result.semantic_profile_id,
            semantic_model_name=result.semantic_model_name,
            semantic_scorer_version=result.semantic_scorer_version,
            semantic_text_hash=result.semantic_text_hash,
        )
        updated_rows.append(
            {
                "id": int(row["id"]),
                "source": str(row["source"]),
                "title": str(row["title"]),
                "semantic_match_label": result.semantic_match_label,
                "semantic_match_score": result.semantic_match_score,
                "semantic_base_score": result.semantic_base_score,
                "semantic_research_heaviness_score": result.semantic_research_heaviness_score,
                "semantic_adjustment_reason_codes": result.semantic_adjustment_reason_codes,
                "semantic_profile_id": result.semantic_profile_id,
            }
        )

    return {
        "updated_count": len(updated_rows),
        "device": backend.device,
        "requested_device": backend.requested_device,
        "model_name": backend.model_name,
        "local_files_only": backend.local_files_only,
        "rows": updated_rows,
    }


def _build_disagreement_report(rows) -> dict[str, object]:
    comparison_rows = [_serialize_comparison_row(row) for row in rows]
    disagreement_rows: list[dict[str, object]] = []
    deterministic_vs_semantic = 0
    deterministic_vs_manual = 0
    semantic_vs_manual = 0

    for row in comparison_rows:
        deterministic = row["profile_match_label"]
        semantic = row["semantic_match_label"]
        manual = row["manual_fit_normalized_label"]
        row_reasons: list[str] = []

        if deterministic and semantic and deterministic != semantic:
            deterministic_vs_semantic += 1
            row_reasons.append("deterministic_vs_semantic")
        if deterministic and manual and deterministic != manual:
            deterministic_vs_manual += 1
            row_reasons.append("deterministic_vs_manual")
        if semantic and manual and semantic != manual:
            semantic_vs_manual += 1
            row_reasons.append("semantic_vs_manual")

        if row_reasons:
            enriched = dict(row)
            enriched["disagreement_axes"] = row_reasons
            disagreement_rows.append(enriched)

    return {
        "sample_size": len(comparison_rows),
        "disagreement_count": len(disagreement_rows),
        "summary": {
            "deterministic_vs_semantic": deterministic_vs_semantic,
            "deterministic_vs_manual": deterministic_vs_manual,
            "semantic_vs_manual": semantic_vs_manual,
        },
        "rows": disagreement_rows,
    }


def _render_list_text(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "No Stage 2 jobs found."
    chunks: list[str] = []
    for row in rows:
        reasons = ",".join(row["profile_match_reason_codes"]) or "-"
        semantic_reasons = ",".join(row["semantic_match_reason_codes"]) or "-"
        semantic_adjustments = ",".join(row["semantic_adjustment_reason_codes"]) or "-"
        chunks.append(
            f"[{row['id']}] {row['title']}\n"
            f"  company={row['company']} source={row['source']}\n"
            f"  location={row['location']} posted_at={row['posted_at']} compensation={row['compensation_type']}\n"
            f"  stage2={row['profile_match_label']} score={row['profile_match_score']:.2f} reasons={reasons}\n"
            f"  semantic={row['semantic_match_label'] or '-'} score={row['semantic_match_score']:.2f} base={row['semantic_base_score']:.2f} research_penalty={row['semantic_research_heaviness_score']:.2f} profile={row['semantic_profile_id'] or '-'} reasons={semantic_reasons} adjustments={semantic_adjustments}\n"
            f"  versions: profile={row['profile_version']} scorer={row['scorer_version']} text={row['job_text_version']}"
        )
    return "\n\n".join(chunks)


def _render_show_text(row: dict[str, object]) -> str:
    reasons = ",".join(row["profile_match_reason_codes"]) or "-"
    semantic_reasons = ",".join(row["semantic_match_reason_codes"]) or "-"
    semantic_adjustments = ",".join(row["semantic_adjustment_reason_codes"]) or "-"
    manual_reasons = ",".join(row["manual_fit_reason_codes"]) or "-"
    return "\n".join(
        [
            f"id={row['id']}",
            f"source={row['source']}",
            f"company={row['company']}",
            f"title={row['title']}",
            f"location={row['location']}",
            f"posted_at={row['posted_at']}",
            f"compensation_type={row['compensation_type']}",
            f"url={row['url']}",
            f"relevance_score={row['relevance_score']}",
            f"eligibility={row['eligibility_status']} ({row['eligibility_confidence']:.2f})",
            f"stage2_label={row['profile_match_label']}",
            f"stage2_score={row['profile_match_score']:.2f}",
            f"stage2_reasons={reasons}",
            f"semantic_label={row['semantic_match_label'] or '-'}",
            f"semantic_score={row['semantic_match_score']:.2f}",
            f"semantic_base_score={row['semantic_base_score']:.2f}",
            f"semantic_research_heaviness_score={row['semantic_research_heaviness_score']:.2f}",
            f"semantic_reasons={semantic_reasons}",
            f"semantic_adjustments={semantic_adjustments}",
            f"semantic_profile_id={row['semantic_profile_id'] or '-'}",
            f"semantic_model_name={row['semantic_model_name'] or '-'}",
            f"semantic_scorer_version={row['semantic_scorer_version'] or '-'}",
            f"semantic_text_hash={row['semantic_text_hash'] or '-'}",
            f"profile_version={row['profile_version']}",
            f"scorer_version={row['scorer_version']}",
            f"job_text_version={row['job_text_version']}",
            f"manual_fit_label={row['manual_fit_label'] or '-'}",
            f"manual_fit_reason_codes={manual_reasons}",
            "",
            "job_text_snapshot:",
            str(row["job_text_snapshot"] or "-"),
        ]
    )


def _render_embedding_diagnostics_text(payload: dict[str, object]) -> str:
    diagnostics = payload["diagnostics"]
    filters = payload["filters"]
    rows = payload["top_truncated_jobs"]
    chunks = [
        f"sample_size={payload['sample_size']}",
        f"model_name={filters['model_name']}",
        f"requested_device={filters['requested_device']}",
        f"device={filters['device']}",
        f"device_source={filters['device_source']}",
        f"local_files_only={filters['local_files_only']}",
        f"batch_size={filters['batch_size']}",
        f"total_batches={diagnostics['total_batches']}",
        f"embedding_dimension={diagnostics['embedding_dimension']}",
        f"max_sequence_length={diagnostics['max_sequence_length']}",
        f"total_input_tokens={diagnostics['total_input_tokens']}",
        f"total_truncated_tokens={diagnostics['total_truncated_tokens']}",
        f"max_observed_tokens={diagnostics['max_observed_tokens']}",
        f"truncated_count={diagnostics['truncated_count']}",
        f"truncated_job_rate={diagnostics['truncated_job_rate']:.4f}",
        f"truncated_token_share={diagnostics['truncated_token_share']:.4f}",
        f"avg_overflow_tokens_on_truncated_jobs={diagnostics['avg_overflow_tokens_on_truncated_jobs']:.2f}",
        f"p95_overflow_tokens={diagnostics['p95_overflow_tokens']}",
    ]
    if not rows:
        chunks.append("")
        chunks.append("top_truncated_jobs: none")
        return "\n".join(chunks)

    chunks.append("")
    chunks.append("top_truncated_jobs:")
    for row in rows:
        chunks.append(
            f"[{row['id']}] {row['title']} | company={row['company']} source={row['source']} "
            f"posted_at={row['posted_at']} tokens={row['token_length']} overflow={row['overflow_tokens']} "
            f"stage2={row['profile_match_label'] or '-'} manual={row['manual_fit_label'] or '-'}"
        )
    return "\n".join(chunks)


def _render_semantic_backfill_text(payload: dict[str, object]) -> str:
    chunks = [
        f"updated_count={payload['updated_count']}",
        f"requested_device={payload['requested_device']}",
        f"device={payload['device']}",
        f"local_files_only={payload['local_files_only']}",
        f"model_name={payload['model_name']}",
    ]
    rows = payload["rows"]
    if not rows:
        chunks.append("")
        chunks.append("rows: none")
        return "\n".join(chunks)
    chunks.append("")
    chunks.append("rows:")
    for row in rows:
        chunks.append(
            f"[{row['id']}] {row['title']} source={row['source']} "
            f"semantic={row['semantic_match_label']} score={row['semantic_match_score']:.2f} "
            f"base={row['semantic_base_score']:.2f} penalty={row['semantic_research_heaviness_score']:.2f} "
            f"profile={row['semantic_profile_id']}"
        )
    return "\n".join(chunks)


def _render_disagreement_report_text(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    chunks = [
        f"sample_size={payload['sample_size']}",
        f"disagreement_count={payload['disagreement_count']}",
        f"deterministic_vs_semantic={summary['deterministic_vs_semantic']}",
        f"deterministic_vs_manual={summary['deterministic_vs_manual']}",
        f"semantic_vs_manual={summary['semantic_vs_manual']}",
    ]
    rows = payload["rows"]
    if not rows:
        chunks.append("")
        chunks.append("rows: none")
        return "\n".join(chunks)
    chunks.append("")
    chunks.append("rows:")
    for row in rows:
        axes = ",".join(row["disagreement_axes"])
        chunks.append(
            f"[{row['id']}] {row['title']} source={row['source']} "
            f"deterministic={row['profile_match_label']}({row['profile_match_score']:.2f}) "
            f"semantic={row['semantic_match_label'] or '-'}({row['semantic_match_score']:.2f}, base={row['semantic_base_score']:.2f}, penalty={row['semantic_research_heaviness_score']:.2f}) "
            f"manual={row['manual_fit_label'] or '-'} normalized_manual={row['manual_fit_normalized_label'] or '-'} axes={axes}"
        )
    return "\n".join(chunks)


def _normalize_manual_fit_label(value: object) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "good_fit": "pass",
        "borderline": "review",
        "bad_fit": "reject",
    }
    return mapping.get(raw, "")


if __name__ == "__main__":
    raise SystemExit(main())
