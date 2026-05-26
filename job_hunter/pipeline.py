from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone

from job_hunter.config import Settings
from job_hunter.keywords import (
    INTERNSHIP_KEYWORDS,
    ML_DATA_KEYWORDS,
    NEGATIVE_WORK_AUTH_PATTERNS,
    POSITIVE_SPONSORSHIP_PATTERNS,
    US_LOCATION_HINTS,
)
from job_hunter.models import JobRecord, PipelineOutcome
from job_hunter.notify import TelegramNotifier
from job_hunter.sources import ArbeitnowSource, RemotiveSource, SourceConnector, TheMuseSource
from job_hunter.storage import JobStore

LOG = logging.getLogger(__name__)


def build_sources(settings: Settings) -> list[SourceConnector]:
    sources: list[SourceConnector] = []
    if settings.use_arbeitnow:
        sources.append(ArbeitnowSource())
    if settings.use_remotive:
        sources.append(RemotiveSource())
    if settings.use_themuse:
        sources.append(TheMuseSource())
    return sources


def run_pipeline(settings: Settings, store: JobStore, notifier: TelegramNotifier | None) -> PipelineOutcome:
    outcome = PipelineOutcome()
    now_iso = datetime.now(timezone.utc).isoformat()

    for source in build_sources(settings):
        try:
            raw_jobs = source.fetch(settings.request_timeout_seconds)
        except Exception:
            LOG.exception("source_fetch_failed", extra={"source": source.name})
            outcome.error_count += 1
            continue

        outcome.source_count += len(raw_jobs)
        for raw_job in raw_jobs:
            job = _normalize_record(raw_job, ingested_at=now_iso)
            if not job.url or not job.title:
                continue

            if not _is_internship(job):
                continue
            if not _is_us_scope(job):
                continue

            eligibility_status, eligibility_confidence, work_auth_hits, sponsor_hits = _evaluate_eligibility(job)
            job.eligibility_status = eligibility_status
            job.eligibility_confidence = eligibility_confidence
            job.work_auth_signals = work_auth_hits
            job.sponsorship_signals = sponsor_hits

            relevance_score, relevance_hits = _score_relevance(job)
            job.relevance_score = relevance_score
            job.relevance_hits = relevance_hits

            if job.relevance_score < settings.min_relevance_score:
                continue
            if job.eligibility_confidence < settings.min_eligibility_confidence:
                continue

            if job.eligibility_status == "ambiguous" and not settings.notify_on_ambiguous_eligibility:
                pass_notify = False
            else:
                pass_notify = True

            outcome.passed_filter_count += 1
            dedupe_key = _dedupe_key(job)
            if store.is_seen(dedupe_key):
                outcome.duplicate_count += 1
                continue

            persisted = store.insert_job(job, dedupe_key)
            if not persisted:
                outcome.duplicate_count += 1
                continue
            outcome.persisted_count += 1

            if notifier is not None and pass_notify:
                sent = notifier.send(job)
                store.mark_notified(dedupe_key, sent)
                if sent:
                    outcome.notified_count += 1

    store.log_run(outcome)
    LOG.info("pipeline_completed %s", json.dumps(asdict(outcome), sort_keys=True))
    return outcome


def _normalize_record(raw: dict, ingested_at: str) -> JobRecord:
    description = _clean_text(str(raw.get("description", "")))
    title = _clean_text(str(raw.get("title", "")))
    company = _clean_text(str(raw.get("company", "")))
    location = _clean_text(str(raw.get("location", "")))

    return JobRecord(
        source=str(raw.get("source", "")),
        external_id=str(raw.get("external_id", "")),
        url=str(raw.get("url", "")),
        title=title,
        company=company,
        location=location,
        is_internship=False,
        posted_at=_nullable_str(raw.get("posted_at")),
        description=description,
        skills=[str(x) for x in raw.get("skills", []) if str(x).strip()],
        ingested_at=ingested_at,
    )


def _clean_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _nullable_str(value: object) -> str | None:
    if value is None:
        return None
    as_str = str(value).strip()
    return as_str or None


def _job_blob(job: JobRecord) -> str:
    return " ".join([job.title, job.description, " ".join(job.skills)]).lower()


def _is_internship(job: JobRecord) -> bool:
    blob = _job_blob(job)
    job.is_internship = any(word in blob for word in INTERNSHIP_KEYWORDS)
    return job.is_internship


def _is_us_scope(job: JobRecord) -> bool:
    location = job.location.lower()
    if not location:
        return True
    if "remote" in location:
        return True
    return any(hint in location for hint in US_LOCATION_HINTS)


def _evaluate_eligibility(job: JobRecord) -> tuple[str, float, list[str], list[str]]:
    blob = _job_blob(job)
    negative = [pat for pat in NEGATIVE_WORK_AUTH_PATTERNS if pat in blob]
    positive = [pat for pat in POSITIVE_SPONSORSHIP_PATTERNS if pat in blob]

    if negative:
        return "reject", 0.0, negative, positive
    if positive:
        return "sponsorship_friendly", 0.95, negative, positive
    return "ambiguous", 0.45, negative, positive


def _score_relevance(job: JobRecord) -> tuple[float, list[str]]:
    blob = _job_blob(job)
    score = 0.0
    hits: list[str] = []
    for keyword, weight in ML_DATA_KEYWORDS.items():
        if keyword in blob:
            score += weight
            hits.append(keyword)

    if job.posted_at:
        # Simple recency boost based on known posted timestamp presence.
        score += 0.75

    return score, sorted(set(hits))


def _canonical_url(url: str) -> str:
    url = url.strip().lower()
    return re.sub(r"\?.*$", "", url)


def _norm_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _dedupe_key(job: JobRecord) -> str:
    base = "|".join(
        [
            _norm_token(job.company),
            _norm_token(job.title),
            _canonical_url(job.url),
        ]
    )
    if not base.strip("|"):
        base = "|".join([job.source, job.external_id, job.url])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
