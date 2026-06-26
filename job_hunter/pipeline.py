from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from job_hunter.config import Settings
from job_hunter.keywords import (
    DATA_ROLE_TITLE_PATTERNS,
    HIGH_SIGNAL_ML_DATA_KEYWORDS,
    INTERNSHIP_DESCRIPTION_PATTERNS,
    INTERNSHIP_TITLE_PATTERNS,
    ML_DATA_KEYWORDS,
    NEGATIVE_WORK_AUTH_PATTERNS,
    NON_DATA_ROLE_TITLE_PATTERNS,
    POSITIVE_SPONSORSHIP_PATTERNS,
    US_LOCATION_HINTS,
)
from job_hunter.models import JobRecord, PipelineOutcome, SourceRunStats
from job_hunter.notify import TelegramNotifier
from job_hunter.stage2 import ShadowProfileScorer
from job_hunter.sources import (
    AdzunaSource,
    AshbySource,
    ArbeitnowSource,
    GithubRepoSource,
    HandshakeSource,
    GreenhouseSource,
    LeverSource,
    RemotiveSource,
    RssSource,
    SourceConnector,
    TheMuseSource,
    USAJobsSource,
)
from job_hunter.storage import JobStore

LOG = logging.getLogger(__name__)
WORD_BOUNDARY_PATTERN = r"(?<![a-z0-9])%s(?![a-z0-9])"
ML_KEYWORD_PATTERNS = {
    keyword: re.compile(WORD_BOUNDARY_PATTERN % re.escape(keyword))
    for keyword in ML_DATA_KEYWORDS
}
HIGH_SIGNAL_KEYWORD_PATTERNS = {
    keyword: re.compile(WORD_BOUNDARY_PATTERN % re.escape(keyword))
    for keyword in HIGH_SIGNAL_ML_DATA_KEYWORDS
}
INTERNSHIP_TITLE_REGEXES = {
    name: re.compile(pattern, flags=re.IGNORECASE)
    for name, pattern in INTERNSHIP_TITLE_PATTERNS.items()
}
DEFAULT_DATA_ROLE_TITLE_REGEXES = {
    name: re.compile(pattern, flags=re.IGNORECASE)
    for name, pattern in DATA_ROLE_TITLE_PATTERNS.items()
}
DEFAULT_NON_DATA_ROLE_TITLE_REGEXES = {
    name: re.compile(pattern, flags=re.IGNORECASE)
    for name, pattern in NON_DATA_ROLE_TITLE_PATTERNS.items()
}
INTERNSHIP_DESCRIPTION_REGEXES = {
    name: re.compile(pattern, flags=re.IGNORECASE)
    for name, pattern in INTERNSHIP_DESCRIPTION_PATTERNS.items()
}
NEGATIVE_WORK_AUTH_REGEXES = {
    name: re.compile(pattern, flags=re.IGNORECASE)
    for name, pattern in NEGATIVE_WORK_AUTH_PATTERNS.items()
}
POSITIVE_SPONSORSHIP_REGEXES = {
    name: re.compile(pattern, flags=re.IGNORECASE)
    for name, pattern in POSITIVE_SPONSORSHIP_PATTERNS.items()
}
US_CITY_STATE_RE = re.compile(
    r"\b[a-z][a-z .'-]+,\s*(al|ak|az|ar|ca|co|ct|de|dc|fl|ga|hi|ia|id|il|in|ks|ky|la|ma|md|me|mi|mn|mo|ms|mt|nc|nd|ne|nh|nj|nm|nv|ny|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|va|vt|wa|wi|wv|wy)\b",
    flags=re.IGNORECASE,
)
NEGATED_SPONSORSHIP_REGEXES = {
    "no_sponsorship": re.compile(r"\b(no|not|without)\s+(visa\s+)?sponsorship\b", flags=re.IGNORECASE),
    "cannot_sponsor": re.compile(r"\b(cannot|can't|unable to)\s+sponsor\b", flags=re.IGNORECASE),
    "do_not_sponsor": re.compile(r"\b(do not|does not|don't|doesn't)\s+.*\bsponsor(ship)?\b", flags=re.IGNORECASE),
    "no_current_future_sponsorship": re.compile(
        r"\bwithout the need for current or future sponsorship\b",
        flags=re.IGNORECASE,
    ),
    "no_current_future_sponsorship_company": re.compile(
        r"\bwithout the need for current or future sponsorship by the company\b",
        flags=re.IGNORECASE,
    ),
    "future_sponsorship_not_available": re.compile(
        r"\b(no|not|without)\s+(current|future)\s+sponsorship\b",
        flags=re.IGNORECASE,
    ),
}


def build_sources(settings: Settings, store: JobStore | None = None) -> list[SourceConnector]:
    sources: list[SourceConnector] = []
    greenhouse_boards = settings.greenhouse_boards
    lever_companies = settings.lever_companies
    rss_feeds = settings.rss_feeds
    github_repo_readmes = settings.github_repo_readmes
    ashby_boards = settings.ashby_boards
    handshake_search_urls = settings.handshake_search_urls

    if store is not None:
        greenhouse_boards = _filter_suppressed_items(
            store=store,
            source_name="greenhouse",
            items=greenhouse_boards,
            min_failures=settings.source_failure_quarantine_threshold,
        )
        lever_companies = _filter_suppressed_items(
            store=store,
            source_name="lever",
            items=lever_companies,
            min_failures=settings.source_failure_quarantine_threshold,
        )
        rss_feeds = _filter_suppressed_items(
            store=store,
            source_name="rss",
            items=rss_feeds,
            min_failures=settings.source_failure_quarantine_threshold,
        )

    if settings.use_arbeitnow:
        sources.append(ArbeitnowSource())
    if settings.use_remotive:
        sources.append(RemotiveSource())
    if settings.use_themuse:
        sources.append(TheMuseSource(pages=settings.themuse_pages))
    if settings.use_greenhouse and greenhouse_boards:
        sources.append(GreenhouseSource(board_tokens=greenhouse_boards))
    if settings.use_lever and lever_companies:
        sources.append(LeverSource(companies=lever_companies))
    if settings.use_rss and rss_feeds:
        sources.append(RssSource(feeds=rss_feeds))
    if settings.use_github_repos and github_repo_readmes:
        sources.append(GithubRepoSource(readme_urls=github_repo_readmes))
    if settings.use_ashby and ashby_boards:
        sources.append(AshbySource(board_slugs=ashby_boards))
    if settings.use_handshake and handshake_search_urls:
        sources.append(
            HandshakeSource(
                search_urls=handshake_search_urls,
                profile_dir=settings.handshake_profile_dir,
                headless=settings.handshake_headless,
                max_results=settings.handshake_max_results,
                page_timeout_seconds=settings.handshake_page_timeout_seconds,
                fetch_details=settings.handshake_fetch_details,
            )
        )

    if settings.use_usajobs:
        if settings.usajobs_user_agent and settings.usajobs_auth_key:
            sources.append(
                USAJobsSource(
                    user_agent=settings.usajobs_user_agent,
                    auth_key=settings.usajobs_auth_key,
                    results_per_page=settings.usajobs_results_per_page,
                )
            )
        else:
            LOG.warning("usajobs_skipped_missing_credentials")

    if settings.use_adzuna:
        if settings.adzuna_app_id and settings.adzuna_app_key:
            sources.append(
                AdzunaSource(
                    country=settings.adzuna_country,
                    app_id=settings.adzuna_app_id,
                    app_key=settings.adzuna_app_key,
                    pages=settings.adzuna_pages,
                )
            )
        else:
            LOG.warning("adzuna_skipped_missing_credentials")

    return sources


def run_pipeline(settings: Settings, store: JobStore, notifier: TelegramNotifier | None) -> PipelineOutcome:
    outcome = PipelineOutcome()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    shadow_scorer = ShadowProfileScorer()
    semantic_scorer = _build_semantic_shadow_scorer()
    title_blacklist_regexes = _compile_title_blacklist(settings.title_blacklist_patterns)
    data_role_title_regexes = _compile_title_blacklist(settings.data_role_title_patterns)
    non_data_role_title_regexes = _compile_title_blacklist(settings.non_data_title_patterns)
    policy_reject_regexes = _compile_title_blacklist(settings.policy_reject_patterns)

    for source in build_sources(settings, store=store):
        source_stats = outcome.source_stats.setdefault(source.name, SourceRunStats())
        try:
            raw_jobs = source.fetch(settings.request_timeout_seconds)
        except Exception:
            LOG.exception("source_fetch_failed", extra={"source": source.name})
            outcome.error_count += 1
            source_stats.error_count += 1
            continue

        fetch_meta = source.get_fetch_meta() if hasattr(source, "get_fetch_meta") else {}
        source_stats.dead_token_count += int(fetch_meta.get("dead_token_count", 0))
        source_stats.feed_error_count += int(fetch_meta.get("feed_error_count", 0))
        raw_item_results = fetch_meta.get("item_results", [])
        if isinstance(raw_item_results, list):
            item_results = [item for item in raw_item_results if isinstance(item, dict)]
            if item_results:
                store.record_source_item_results(source.name, item_results)

        outcome.source_count += len(raw_jobs)
        source_stats.fetched_count += len(raw_jobs)

        for raw_job in raw_jobs:
            job = _normalize_record(raw_job, ingested_at=now_iso)
            outcome.normalized_count += 1
            source_stats.normalized_count += 1
            if not job.url or not job.title:
                outcome.rejected_missing_core_fields_count += 1
                source_stats.rejected_missing_core_fields_count += 1
                continue

            if _is_too_old(job, now, settings.max_posting_age_days):
                source_stats.rejected_age_count += 1
                continue
            outcome.after_stage_1a_count += 1
            source_stats.after_stage_1a_count += 1

            if not _is_internship(job):
                source_stats.rejected_internship_count += 1
                continue
            if not _is_us_scope(job):
                source_stats.rejected_us_scope_count += 1
                continue
            if _is_blacklisted_title(job, title_blacklist_regexes):
                source_stats.rejected_title_blacklist_count += 1
                continue
            if not _passes_data_role_gate(
                job,
                data_role_title_regexes=data_role_title_regexes,
                non_data_role_title_regexes=non_data_role_title_regexes,
                min_data_signal_count=settings.min_data_signal_count,
            ):
                source_stats.rejected_data_role_count += 1
                continue
            job.role_relevance_label = "pass"
            job.role_relevance_reason_codes = _role_relevance_reason_codes(job)
            outcome.after_stage_1b_count += 1
            source_stats.after_stage_1b_count += 1
            if _fails_policy_gate(job, policy_reject_regexes):
                source_stats.rejected_policy_gate_count += 1
                continue
            job.policy_gate_status = "pass"
            job.policy_gate_reason_codes = []
            outcome.after_stage_1c_count += 1
            source_stats.after_stage_1c_count += 1

            eligibility_status, eligibility_confidence, work_auth_hits, sponsor_hits = _evaluate_eligibility(job)
            job.eligibility_status = eligibility_status
            job.eligibility_confidence = eligibility_confidence
            job.work_auth_signals = work_auth_hits
            job.sponsorship_signals = sponsor_hits

            if job.eligibility_status == "reject" or job.eligibility_confidence < settings.min_eligibility_confidence:
                source_stats.rejected_eligibility_count += 1
                continue

            relevance_score, relevance_hits = _score_relevance(job)
            job.relevance_score = relevance_score
            job.relevance_hits = relevance_hits
            stage2_result = shadow_scorer.score(job)
            job.profile_match_score = stage2_result.profile_match_score
            job.profile_match_label = stage2_result.profile_match_label
            job.profile_match_reason_codes = stage2_result.profile_match_reason_codes
            job.profile_version = stage2_result.profile_version
            job.scorer_version = stage2_result.scorer_version
            job.job_text_version = stage2_result.job_text_version
            job.job_text_snapshot = stage2_result.job_text_snapshot
            if semantic_scorer is not None:
                try:
                    semantic_result = semantic_scorer.score(job)
                    job.semantic_match_score = semantic_result.semantic_match_score
                    job.semantic_match_label = semantic_result.semantic_match_label
                    job.semantic_match_reason_codes = semantic_result.semantic_match_reason_codes
                    job.semantic_base_score = semantic_result.semantic_base_score
                    job.semantic_research_heaviness_score = semantic_result.semantic_research_heaviness_score
                    job.semantic_adjustment_reason_codes = semantic_result.semantic_adjustment_reason_codes
                    job.semantic_profile_id = semantic_result.semantic_profile_id
                    job.semantic_model_name = semantic_result.semantic_model_name
                    job.semantic_scorer_version = semantic_result.semantic_scorer_version
                    job.semantic_text_hash = semantic_result.semantic_text_hash
                except Exception:
                    LOG.exception("semantic_shadow_scoring_failed", extra={"source": source.name, "url": job.url})

            if job.relevance_score < settings.min_relevance_score:
                source_stats.rejected_relevance_count += 1
                continue

            if job.eligibility_status == "ambiguous" and not settings.notify_on_ambiguous_eligibility:
                pass_notify = False
            else:
                pass_notify = True

            outcome.passed_filter_count += 1
            dedupe_key = _dedupe_key(job)
            if store.is_seen(dedupe_key):
                store.update_existing_job(job, dedupe_key)
                outcome.duplicate_count += 1
                source_stats.duplicate_count += 1
                if notifier is not None and pass_notify and not store.was_notified(dedupe_key):
                    sent = notifier.send(job)
                    store.mark_notified(dedupe_key, sent)
                    if sent:
                        outcome.notified_count += 1
                        source_stats.notified_count += 1
                continue

            persisted = store.insert_job(job, dedupe_key)
            if not persisted:
                outcome.duplicate_count += 1
                source_stats.duplicate_count += 1
                continue

            outcome.persisted_count += 1
            source_stats.persisted_count += 1

            if notifier is not None and pass_notify:
                sent = notifier.send(job)
                store.mark_notified(dedupe_key, sent)
                if sent:
                    outcome.notified_count += 1
                    source_stats.notified_count += 1

    store.log_run(outcome)
    LOG.info("pipeline_completed %s", json.dumps(asdict(outcome), sort_keys=True))
    return outcome


def _filter_suppressed_items(store: JobStore, source_name: str, items: list[str], min_failures: int) -> list[str]:
    suppressed = {value.strip().lower() for value in store.get_suppressed_items(source_name, min_failures)}
    if not suppressed:
        return items
    kept: list[str] = []
    for item in items:
        if item.strip().lower() in suppressed:
            continue
        kept.append(item)
    return kept


def _build_semantic_shadow_scorer():
    if importlib.util.find_spec("sentence_transformers") is None:
        LOG.info("semantic_shadow_scorer_unavailable_missing_sentence_transformers")
        return None
    try:
        from job_hunter.stage2_semantic import SemanticShadowScorer
    except Exception:
        LOG.info("semantic_shadow_scorer_unavailable")
        return None
    try:
        return SemanticShadowScorer()
    except Exception:
        LOG.info("semantic_shadow_scorer_init_failed", exc_info=True)
        return None


def _normalize_record(raw: dict, ingested_at: str) -> JobRecord:
    description = _clean_text(str(raw.get("description", "")))
    title = _clean_text(str(raw.get("title", "")))
    company = _clean_text(str(raw.get("company", "")))
    location = _clean_text(str(raw.get("location", "")))
    raw_compensation_type = str(raw.get("compensation_type", "")).strip().lower()
    if raw_compensation_type in {"paid", "unpaid", "unknown"}:
        compensation_type = raw_compensation_type
    else:
        compensation_type = _classify_compensation(title=title, description=description)

    raw_skills = raw.get("skills", [])
    if isinstance(raw_skills, list):
        skills = [str(x) for x in raw_skills if str(x).strip()]
    elif raw_skills:
        skills = [str(raw_skills)]
    else:
        skills = []

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
        compensation_type=compensation_type,
        skills=skills,
        ingested_at=ingested_at,
        source_detail=str(raw.get("source_detail", "")),
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


def _classify_compensation(title: str, description: str) -> str:
    primary_description = description
    for marker in ("Similar Jobs", "About the employer", "Alumni in similar roles", "Alumni at this employer"):
        if marker in primary_description:
            primary_description = primary_description.split(marker, 1)[0]
    blob = f"{title} {primary_description}".lower()
    if "unpaid" in blob:
        return "unpaid"
    if re.search(r"\$\s*\d", blob) or re.search(r"\b\d+\s*-\s*\d+\s*/\s*(hr|hour|year)\b", blob):
        return "paid"
    if re.search(r"\b(pay|paid|salary|stipend|compensation|hourly)\b", blob):
        return "paid"
    return "unknown"


def _compile_title_blacklist(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        text = pattern.strip()
        if not text:
            continue
        try:
            compiled.append(re.compile(text, flags=re.IGNORECASE))
        except re.error:
            # fallback to literal matching when an env-supplied pattern is invalid
            compiled.append(re.compile(re.escape(text), flags=re.IGNORECASE))
    return compiled


def _is_blacklisted_title(job: JobRecord, patterns: list[re.Pattern[str]]) -> bool:
    title = job.title or ""
    if not title:
        return False
    for pattern in patterns:
        if pattern.search(title):
            return True
    return False


def _passes_data_role_gate(
    job: JobRecord,
    data_role_title_regexes: list[re.Pattern[str]],
    non_data_role_title_regexes: list[re.Pattern[str]],
    min_data_signal_count: int,
) -> bool:
    title = job.title or ""
    desc_blob = (job.description or "").lower()

    positive_title = any(pattern.search(title) for pattern in data_role_title_regexes)
    negative_title = any(pattern.search(title) for pattern in non_data_role_title_regexes)
    if negative_title and not positive_title:
        return False
    if positive_title:
        return True

    high_signal_hits = 0
    for keyword, pattern in HIGH_SIGNAL_KEYWORD_PATTERNS.items():
        if pattern.search(desc_blob):
            high_signal_hits += 1
            if high_signal_hits >= max(min_data_signal_count, 1):
                return True
    return False


def _fails_policy_gate(job: JobRecord, policy_reject_regexes: list[re.Pattern[str]]) -> bool:
    if not policy_reject_regexes:
        return False
    blob = " ".join(
        [
            job.title or "",
            job.company or "",
            job.location or "",
            job.description or "",
            job.source_detail or "",
        ]
    )
    for pattern in policy_reject_regexes:
        if pattern.search(blob):
            return True
    return False


def _role_relevance_reason_codes(job: JobRecord) -> list[str]:
    reasons = ["internship_gate_pass", "us_scope_pass", "data_role_gate_pass"]
    title = (job.title or "").lower()
    if any(token in title for token in ("machine learning", "data science", "data engineer", "analytics engineer", "ai/ml")):
        reasons.append("target_title_signal")
    if any(pattern.search((job.description or "").lower()) for pattern in HIGH_SIGNAL_KEYWORD_PATTERNS.values()):
        reasons.append("high_signal_keyword_match")
    return sorted(set(reasons))


def _is_too_old(job: JobRecord, now: datetime, max_days: int) -> bool:
    age_days, age_unknown = _job_age_days(job.posted_at, now)
    job.age_days = age_days
    job.age_unknown = age_unknown
    if max_days <= 0:
        return False
    if age_unknown:
        return False
    if age_days is None:
        return False
    return age_days > max_days


def _job_age_days(posted_at: str | None, now: datetime) -> tuple[float | None, bool]:
    posted_dt = _parse_posted_at(posted_at)
    if posted_dt is None:
        return None, True
    delta = now - posted_dt
    age_days = max(delta.total_seconds() / 86400.0, 0.0)
    return age_days, False


def _parse_posted_at(posted_at: str | None) -> datetime | None:
    if posted_at is None:
        return None
    value = posted_at.strip()
    if not value:
        return None

    if value.isdigit():
        num = int(value)
        if num > 10_000_000_000:
            num = int(num / 1000)
        try:
            return datetime.fromtimestamp(num, tz=timezone.utc)
        except (OSError, ValueError):
            return None

    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _is_internship(job: JobRecord) -> bool:
    title = job.title or ""
    description = job.description or ""
    title_match = any(pattern.search(title) for pattern in INTERNSHIP_TITLE_REGEXES.values())
    description_match = any(pattern.search(description) for pattern in INTERNSHIP_DESCRIPTION_REGEXES.values())
    job.is_internship = bool(title_match or description_match)
    return job.is_internship


def _is_us_scope(job: JobRecord) -> bool:
    location = job.location.lower()
    if not location:
        return True
    if "remote" in location:
        return True
    if US_CITY_STATE_RE.search(location):
        return True
    return any(hint in location for hint in US_LOCATION_HINTS)


def _evaluate_eligibility(job: JobRecord) -> tuple[str, float, list[str], list[str]]:
    blob = _job_blob(job)
    negative = [name for name, pattern in NEGATIVE_WORK_AUTH_REGEXES.items() if pattern.search(blob)]
    negated_sponsorship = [name for name, pattern in NEGATED_SPONSORSHIP_REGEXES.items() if pattern.search(blob)]
    if negated_sponsorship:
        negative.extend(negated_sponsorship)
    positive = []
    if not negated_sponsorship:
        positive = [name for name, pattern in POSITIVE_SPONSORSHIP_REGEXES.items() if pattern.search(blob)]

    if negative:
        return "reject", 0.0, negative, positive
    if positive:
        return "sponsorship_friendly", 0.95, negative, positive
    return "ambiguous", 0.6, negative, positive


def _score_relevance(job: JobRecord) -> tuple[float, list[str]]:
    blob = _job_blob(job)
    score = 0.0
    hits: list[str] = []
    for keyword, weight in ML_DATA_KEYWORDS.items():
        if ML_KEYWORD_PATTERNS[keyword].search(blob):
            adjusted = weight
            if keyword in {"analytics", "python"}:
                adjusted = weight * 0.5
            score += adjusted
            hits.append(keyword)

    if job.age_unknown:
        score -= 0.25
    elif job.age_days is not None:
        if job.age_days <= 1:
            score += 1.0
        elif job.age_days <= 3:
            score += 0.75
        elif job.age_days <= 7:
            score += 0.5
        else:
            score += 0.1

    return max(score, 0.0), sorted(set(hits))


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
