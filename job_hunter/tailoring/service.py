from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from job_hunter.config import Settings
from job_hunter.storage import JobStore
from job_hunter.tailoring.types import (
    TailoringArtifactRecord,
    TailoringJobContext,
    TailoringProfile,
)

PROMPT_VERSION = "tailoring_v1"
_WHITESPACE_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_MARKDOWN_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_)([^*_]+?)\1")
_MARKDOWN_CODE_RE = re.compile(r"`([^`]+)`")
_DATE_RANGE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*-\s*(?:Present|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b"
)
_ROLE_SECTION_MARKERS = (
    "the role",
    "what you'll do",
    "what you will do",
    "what we're looking for",
    "requirements",
    "qualifications",
    "responsibilities",
)
_COMPANY_SECTION_MARKERS = (
    "about ",
    "about the employer",
    "why intern at ",
    "why join ",
    "our commitment",
    "who we are",
    "our values",
    "culture",
)


class TailoringService:
    def __init__(self, *, settings: Settings, store: JobStore, provider) -> None:
        self.settings = settings
        self.store = store
        self.provider = provider

    def load_profile(self, profile_name: str) -> TailoringProfile:
        profile_dir = Path(self.settings.tailoring_profile_root).expanduser() / profile_name
        default_profile_dir = Path(self.settings.tailoring_profile_root).expanduser() / "default"
        resume_path = profile_dir / "resume.md"
        cover_letter_path = profile_dir / "cover_letter.md"
        preferences_path = profile_dir / "preferences.md"
        shared_preferences_path = default_profile_dir / "preferences.md"
        if not resume_path.exists():
            raise RuntimeError(f"Missing required profile file: {resume_path}")
        if not cover_letter_path.exists():
            raise RuntimeError(f"Missing required profile file: {cover_letter_path}")

        resume_markdown = resume_path.read_text(encoding="utf-8").strip()
        cover_letter_markdown = cover_letter_path.read_text(encoding="utf-8").strip()
        profile_preferences_markdown = preferences_path.read_text(encoding="utf-8").strip() if preferences_path.exists() else ""
        shared_preferences_markdown = shared_preferences_path.read_text(encoding="utf-8").strip() if shared_preferences_path.exists() else ""
        preferences_markdown = _merge_preferences(
            shared_preferences_markdown=shared_preferences_markdown,
            profile_preferences_markdown=profile_preferences_markdown,
            profile_name=profile_name,
        )
        if not resume_markdown:
            raise RuntimeError(f"Profile resume is empty: {resume_path}")
        if not cover_letter_markdown:
            raise RuntimeError(f"Profile cover_letter is empty: {cover_letter_path}")

        return TailoringProfile(
            profile_name=profile_name,
            profile_dir=str(profile_dir),
            resume_markdown=resume_markdown,
            cover_letter_markdown=cover_letter_markdown,
            preferences_markdown=preferences_markdown,
            shared_preferences_markdown=shared_preferences_markdown,
            profile_preferences_markdown=profile_preferences_markdown,
            resume_source_hash=_hash_text(resume_markdown),
            cover_letter_source_hash=_hash_text(cover_letter_markdown),
            preferences_source_hash=_hash_text(preferences_markdown),
        )

    def build_job_context(self, job_id: int) -> TailoringJobContext:
        row = self.store.get_job_for_tailoring(job_id)
        if row is None:
            raise RuntimeError(f"Job id {job_id} not found.")
        job_text_snapshot = str(row["job_text_snapshot"] or "").strip() or _fallback_job_text(row)
        canonical_job_id = int(row["id"])
        payload = {
            "job_id": canonical_job_id,
            "source": str(row["source"] or ""),
            "title": str(row["title"] or ""),
            "company": str(row["company"] or ""),
            "location": str(row["location"] or ""),
            "posted_at": str(row["posted_at"] or ""),
            "url": str(row["url"] or ""),
            "description": str(row["description"] or ""),
            "company_context": _extract_company_context(
                str(row["description"] or ""),
                company=str(row["company"] or ""),
            ),
            "job_text_version": str(row["job_text_version"] or ""),
            "job_text_snapshot": job_text_snapshot,
            "profile_match_label": str(row["profile_match_label"] or ""),
            "profile_match_score": float(row["profile_match_score"] or 0.0),
        }
        return TailoringJobContext(
            **payload,
            job_context_hash=_hash_text(json.dumps(payload, sort_keys=True)),
        )

    def generate_for_job(self, *, job_id: int, profile_name: str, force: bool = False) -> TailoringArtifactRecord:
        profile = self.load_profile(profile_name)
        job_context = self.build_job_context(job_id)
        existing = self.store.find_tailoring_artifact(
            job_id=job_context.job_id,
            profile_name=profile_name,
            prompt_version=PROMPT_VERSION,
            resume_source_hash=profile.resume_source_hash,
            cover_letter_source_hash=profile.cover_letter_source_hash,
            preferences_source_hash=profile.preferences_source_hash,
            job_context_hash=job_context.job_context_hash,
        )
        if existing is not None and not force:
            return TailoringArtifactRecord(
                artifact_id=int(existing["id"]),
                job_id=job_context.job_id,
                profile_name=profile_name,
                output_dir=str(existing["output_dir"]),
                created=False,
                forced=False,
            )

        result = self.provider.generate(profile=profile, job_context=job_context)
        output_dir = self._artifact_output_dir(profile_name=profile_name, job_context=job_context)
        output_dir.mkdir(parents=True, exist_ok=True)
        resume_md_path = output_dir / "resume.md"
        cover_letter_md_path = output_dir / "cover_letter.md"
        resume_pdf_path = output_dir / "resume.pdf"
        cover_letter_pdf_path = output_dir / "cover_letter.pdf"
        resume_md_path.write_text(result.resume_markdown.strip() + "\n", encoding="utf-8")
        cover_letter_md_path.write_text(result.cover_letter_markdown.strip() + "\n", encoding="utf-8")
        _render_markdown_pdf(result.resume_markdown, resume_pdf_path, title=f"{job_context.company} resume")
        _render_markdown_pdf(result.cover_letter_markdown, cover_letter_pdf_path, title=f"{job_context.company} cover letter")
        artifact_id, created = self.store.upsert_tailoring_artifact(
            job_id=job_context.job_id,
            profile_name=profile_name,
            provider_name=result.provider_name,
            model_name=result.model_name,
            prompt_version=PROMPT_VERSION,
            resume_source_hash=profile.resume_source_hash,
            cover_letter_source_hash=profile.cover_letter_source_hash,
            preferences_source_hash=profile.preferences_source_hash,
            job_context_hash=job_context.job_context_hash,
            resume_markdown=result.resume_markdown,
            cover_letter_markdown=result.cover_letter_markdown,
            highlight_requirements=result.highlight_requirements,
            evidence_map=result.evidence_map,
            output_dir=str(output_dir),
        )
        metadata = {
            "artifact_id": artifact_id,
            "job_id": job_context.job_id,
            "profile_name": profile_name,
            "provider_name": result.provider_name,
            "model_name": result.model_name,
            "prompt_version": PROMPT_VERSION,
            "highlight_requirements": result.highlight_requirements,
            "evidence_map": result.evidence_map,
            "job_context_hash": job_context.job_context_hash,
            "resume_source_hash": profile.resume_source_hash,
            "cover_letter_source_hash": profile.cover_letter_source_hash,
            "preferences_source_hash": profile.preferences_source_hash,
            "company_context": job_context.company_context,
            "shared_preferences_markdown": profile.shared_preferences_markdown,
            "profile_preferences_markdown": profile.profile_preferences_markdown,
            "files": {
                "resume_md": resume_md_path.name,
                "cover_letter_md": cover_letter_md_path.name,
                "resume_pdf": resume_pdf_path.name,
                "cover_letter_pdf": cover_letter_pdf_path.name,
            },
        }
        (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return TailoringArtifactRecord(
            artifact_id=artifact_id,
            job_id=job_context.job_id,
            profile_name=profile_name,
            output_dir=str(output_dir),
            created=created,
            forced=force,
        )

    def _artifact_output_dir(self, *, profile_name: str, job_context: TailoringJobContext) -> Path:
        root = Path(self.settings.tailoring_output_root).expanduser()
        company_slug = _slugify(job_context.company)
        title_slug = _slugify(job_context.title)
        dirname = f"{job_context.job_id}-{company_slug}-{title_slug}".strip("-")
        return root / profile_name / dirname


def _fallback_job_text(row) -> str:
    parts = [
        f"TITLE: {str(row['title'] or '').strip()}",
        f"ORG: {str(row['company'] or '').strip()}",
        f"LOCATION: {str(row['location'] or '').strip() or 'unknown'}",
        f"URL: {str(row['url'] or '').strip()}",
        "DESCRIPTION:",
        str(row["description"] or "").strip() or "none",
    ]
    return "\n".join(parts).strip()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", value.strip().lower())
    slug = _SLUG_RE.sub("-", normalized).strip("-")
    return slug[:48] or "unknown"


def _merge_preferences(*, shared_preferences_markdown: str, profile_preferences_markdown: str, profile_name: str) -> str:
    shared = shared_preferences_markdown.strip()
    profile = profile_preferences_markdown.strip()
    if profile_name == "default":
        return profile or shared
    if shared and profile:
        return shared + "\n\n" + profile
    return profile or shared


def _extract_company_context(description: str, *, company: str) -> str:
    text = _normalize_extraction_text(description)
    if not text:
        return ""
    lowered = text.lower()
    markers = [marker + company.lower() for marker in ("about ", "why intern at ", "why join ")] if company.strip() else []
    markers.extend(_COMPANY_SECTION_MARKERS)
    start = _find_first_marker(lowered, markers)
    if start == -1:
        return ""
    tail = text[start:].strip()
    stop = _find_first_marker(tail.lower(), _ROLE_SECTION_MARKERS, start_at=1)
    if stop > 0:
        tail = tail[:stop].strip()
    return tail[:1600].strip()


def _find_first_marker(text: str, markers: tuple[str, ...] | list[str], start_at: int = 0) -> int:
    hits = [text.find(marker, start_at) for marker in markers if text.find(marker, start_at) != -1]
    return min(hits) if hits else -1


def _normalize_extraction_text(text: str) -> str:
    text = _HTML_TAG_RE.sub(" ", text or "")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _render_markdown_pdf(markdown_text: str, output_path: Path, *, title: str) -> None:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError as exc:
        raise RuntimeError("PDF generation requires reportlab. Run `pip install -e .` to install it.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = canvas.Canvas(str(output_path), pagesize=letter)
    width, height = letter
    margin_x = 40
    margin_top = 68
    margin_bottom = 34
    content_width = width - (2 * margin_x)
    y = height - margin_top
    justify_body = "cover letter" in title.lower()
    doc.setTitle(title)

    processed_lines = _preprocess_markdown_for_pdf(markdown_text)
    for index, raw_line in enumerate(processed_lines):
        line = raw_line.rstrip()
        if not line.strip():
            y -= 8
            if y <= margin_bottom:
                doc.showPage()
                y = height - margin_top
            continue

        style = _classify_pdf_line(line, line_index=index, total_lines=len(processed_lines))
        text = style["text"]
        if not text:
            continue

        font_name = style["font_name"]
        font_size = style["font_size"]
        indent = int(style["indent"])
        line_gap = int(style["line_gap"])
        after_gap = int(style["after_gap"])
        continuation_indent = int(style.get("continuation_indent", indent))
        right_text = str(style.get("right_text") or "").strip()
        x = margin_x + indent

        doc.setFont(font_name, font_size)
        if style.get("is_header_contact"):
            header_width = doc.stringWidth(text, font_name, font_size)
            while header_width > content_width and font_size > 7.5:
                font_size = round(font_size - 0.25, 2)
                doc.setFont(font_name, font_size)
                header_width = doc.stringWidth(text, font_name, font_size)
            if y <= margin_bottom:
                doc.showPage()
                y = height - margin_top
                doc.setFont(font_name, font_size)
            doc.drawString(x, y, text)
            y -= line_gap
            y -= after_gap
            if style.get("draw_rule_after"):
                y -= 2
                doc.setLineWidth(1)
                doc.setStrokeColorRGB(0.6, 0.6, 0.6)
                doc.line(margin_x + 4, y, width - margin_x + 4, y)
                y -= 14
            continue
        if right_text:
            right_width = doc.stringWidth(right_text, font_name, font_size)
            left_width = doc.stringWidth(text, font_name, font_size)
            if left_width + right_width + 16 <= content_width:
                if y <= margin_bottom:
                    doc.showPage()
                    y = height - margin_top
                    doc.setFont(font_name, font_size)
                doc.drawString(x, y, text)
                doc.drawRightString(width - margin_x, y, right_text)
                y -= line_gap
                y -= after_gap
                continue
            text = f"{text} {right_text}".strip()

        wrapped_lines = _wrap_text_to_width(
            text,
            max_width=content_width - indent,
            canvas_doc=doc,
            font_name=font_name,
            font_size=font_size,
        )
        should_justify = bool(
            justify_body
            and style.get("justify", True)
            and len(wrapped_lines) > 1
            and len(text) > 60
        )
        for wrapped_index, wrapped in enumerate(wrapped_lines):
            if y <= margin_bottom:
                doc.showPage()
                y = height - margin_top
                doc.setFont(font_name, font_size)
            current_x = margin_x + (indent if wrapped == wrapped_lines[0] else continuation_indent)
            if should_justify and wrapped_index < len(wrapped_lines) - 1:
                _draw_justified_line(
                    doc,
                    text=wrapped,
                    x=current_x,
                    y=y,
                    width=content_width - (indent if wrapped_index == 0 else continuation_indent),
                    font_name=font_name,
                    font_size=font_size,
                )
            else:
                doc.drawString(current_x, y, wrapped)
            y -= line_gap
        y -= after_gap
        if style.get("draw_rule_after"):
            y -= 6
            doc.setLineWidth(1)
            doc.setStrokeColorRGB(0.6, 0.6, 0.6)
            doc.line(margin_x, y, width - margin_x, y)
            y -= 12

    doc.save()


def _preprocess_markdown_for_pdf(markdown_text: str) -> list[str]:
    normalized = _HTML_BREAK_RE.sub("\n", markdown_text)
    raw_lines = normalized.splitlines()
    lines: list[str] = []
    for raw_line in raw_lines:
        line = raw_line.rstrip()
        if not line.strip():
            lines.append("")
            continue
        lines.append(line)
    return lines


def _strip_inline_markdown(text: str) -> str:
    text = _MARKDOWN_CODE_RE.sub(r"\1", text)
    previous = None
    current = text
    while previous != current:
        previous = current
        current = _MARKDOWN_EMPHASIS_RE.sub(r"\2", current)
    return current.replace("**", "").replace("__", "").replace("*", "").replace("_", "")


def _classify_pdf_line(line: str, *, line_index: int, total_lines: int) -> dict[str, object]:
    raw = line.rstrip()
    stripped = _strip_inline_markdown(_HTML_TAG_RE.sub("", raw).replace("<br>", " ").replace("<br/>", " "))
    stripped = _WHITESPACE_RE.sub(" ", stripped).strip()
    heading_text = stripped.lstrip("#").strip() if _MARKDOWN_HEADING_RE.match(raw) else stripped
    if not stripped:
        return {
            "text": "",
            "font_name": "Helvetica",
            "font_size": 11,
            "indent": 0,
            "continuation_indent": 0,
            "line_gap": 13,
            "after_gap": 0,
        }

    if line_index == 0 and heading_text.upper() == heading_text and len(heading_text) <= 40:
        return {
            "text": heading_text,
            "font_name": "Helvetica-Bold",
            "font_size": 26,
            "indent": 0,
            "continuation_indent": 0,
            "line_gap": 28,
            "after_gap": 16,
        }

    if _MARKDOWN_HEADING_RE.match(raw):
        level = len(raw) - len(raw.lstrip("#"))
        heading = heading_text
        size = 15 if level == 1 else 11 if level == 2 else 10
        return {
            "text": heading,
            "font_name": "Helvetica-Bold",
            "font_size": size,
            "indent": 0,
            "continuation_indent": 0,
            "line_gap": size + 3,
            "after_gap": 3 if level <= 2 else 1,
        }

    if line_index == 1 and ("linkedin.com/" in stripped or "github.com/" in stripped):
        compact = _normalize_contact_line(stripped)
        return {
            "text": compact,
            "font_name": "Helvetica",
            "font_size": 10.5,
            "indent": 0,
            "continuation_indent": 0,
            "line_gap": 12,
            "after_gap": 10,
            "draw_rule_after": True,
            "is_header_contact": True,
        }

    if raw.lstrip().startswith(("- ", "* ")):
        return {
            "text": "• " + stripped[2:].strip() if stripped.startswith(("- ", "* ")) else "• " + stripped,
            "font_name": "Helvetica",
            "font_size": 10,
            "indent": 8,
            "continuation_indent": 22,
            "line_gap": 13,
            "after_gap": 0,
        }

    if stripped.startswith("(") and stripped.endswith(")"):
        return {
            "text": stripped,
            "font_name": "Helvetica-Oblique",
            "font_size": 8,
            "indent": 12,
            "continuation_indent": 12,
            "line_gap": 10,
            "after_gap": 0,
        }

    if stripped.lower().startswith("technologies:"):
        return {
            "text": stripped,
            "font_name": "Helvetica-Oblique",
            "font_size": 8,
            "indent": 12,
            "continuation_indent": 12,
            "line_gap": 10,
            "after_gap": 1,
        }

    if _DATE_RANGE_RE.search(stripped):
        left_text, right_text = _split_role_and_date(stripped)
        return {
            "text": left_text,
            "right_text": right_text,
            "font_name": "Helvetica-Bold",
            "font_size": 10,
            "indent": 0,
            "continuation_indent": 0,
            "line_gap": 12,
            "after_gap": 0,
        }

    if len(stripped) <= 95 and stripped.count("|") >= 3:
        return {
            "text": stripped,
            "font_name": "Helvetica",
            "font_size": 9,
            "indent": 0,
            "continuation_indent": 0,
            "line_gap": 11,
            "after_gap": 1,
        }

    return {
        "text": stripped,
        "font_name": "Helvetica",
        "font_size": 10,
        "indent": 0,
        "continuation_indent": 0,
        "line_gap": 12,
        "after_gap": 0,
    }


def _split_role_and_date(text: str) -> tuple[str, str]:
    match = _DATE_RANGE_RE.search(text)
    if match is None:
        return text, ""
    left = text[: match.start()].rstrip(" -|")
    right = match.group(0).strip()
    return left, right


def _normalize_contact_line(text: str) -> str:
    values = [item.strip() for item in text.split("|") if item.strip()]
    if len(values) <= 1:
        return text
    return " | ".join(values)


def _wrap_text_to_width(text: str, *, max_width: float, canvas_doc, font_name: str, font_size: float) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if canvas_doc.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    lines.append(current)
    return lines


def _draw_justified_line(doc, *, text: str, x: float, y: float, width: float, font_name: str, font_size: float) -> None:
    words = text.split()
    if len(words) <= 1:
        doc.drawString(x, y, text)
        return
    words_width = sum(doc.stringWidth(word, font_name, font_size) for word in words)
    spaces = len(words) - 1
    extra_space = max(width - words_width, 0)
    gap = extra_space / spaces if spaces else 0
    cursor_x = x
    for index, word in enumerate(words):
        doc.drawString(cursor_x, y, word)
        cursor_x += doc.stringWidth(word, font_name, font_size)
        if index < spaces:
            cursor_x += gap
