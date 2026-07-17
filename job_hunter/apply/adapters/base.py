from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from job_hunter.apply.resolver import AnswerResolver
from job_hunter.apply.types import ApplicationProfile, SubmitResult


@dataclass(slots=True)
class AdapterContext:
    resume_pdf_path: str
    cover_letter_pdf_path: str
    output_dir: Path
    profile: ApplicationProfile | None = None
    workday_account_store_path: Path | None = None


class ApplyAdapter(Protocol):
    adapter_name: str

    def submit(self, *, page, resolver: AnswerResolver, context: AdapterContext) -> SubmitResult: ...
