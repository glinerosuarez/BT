from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from job_hunter.apply.resolver import AnswerResolver, ResolutionError
from job_hunter.apply.types import Blocker, StepSnapshot, SubmitResult

_CONFIRMATION_MARKERS = (
    "application submitted",
    "successfully submitted",
    "thank you for applying",
    "we have received your application",
    "your application was submitted successfully",
    "you are currently submitted to this job",
)
_LOGIN_MARKERS = (
    "log in to your account",
    "sign into existing account",
    "forgot password",
)
_WELCOME_MARKERS = (
    "welcome",
    "enter your information",
)


class ICIMSAdapter:
    adapter_name = "icims"

    def is_icims_target(self, url: str, page=None) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if "icims.com" in host:
            return True
        checker = getattr(page, "detect_icims", None) if page is not None else None
        if callable(checker):
            return bool(checker())
        content = self._page_text(page).lower() if page is not None else ""
        return any(marker in content for marker in ("software powered by icims", "window._jibe", "powered by icims"))

    def submit(self, *, page, resolver: AnswerResolver, context) -> SubmitResult:
        steps: list[StepSnapshot] = []
        for _ in range(3):
            confirmation = self._extract_confirmation(page)
            if confirmation:
                return SubmitResult(
                    status="submitted",
                    current_url=getattr(page, "url", ""),
                    confirmation_payload=confirmation,
                    steps=steps,
                    adapter_name=self.adapter_name,
                )

            if self._advance_from_landing_page(page):
                continue

            questions_scope = self._find_questionnaire_scope(page)
            if questions_scope is not None:
                return self._submit_questionnaire(
                    page=page,
                    scope=questions_scope,
                    resolver=resolver,
                    steps=steps,
                )

            candidate_scope = self._find_candidate_profile_scope(page)
            if candidate_scope is not None:
                return self._submit_candidate_profile(
                    page=page,
                    scope=candidate_scope,
                    resolver=resolver,
                    context=context,
                    steps=steps,
                )

            welcome_scope = self._find_email_welcome_scope(page)
            if welcome_scope is not None:
                if self._has_captcha(page):
                    return self._blocked(
                        "captcha",
                        page,
                        steps=steps,
                        question_text="hCaptcha challenge detected on iCIMS apply flow.",
                        details={"provider": "hcaptcha", **self._frame_diagnostics(page)},
                    )
                if self._submit_welcome_gate(scope=welcome_scope, resolver=resolver, steps=steps):
                    continue
                return self._blocked(
                    "account_gate",
                    page,
                    steps=steps,
                    question_text="iCIMS welcome gate requires email entry and consent before application flow.",
                    details={"flow": "welcome_gate", **self._frame_diagnostics(page)},
                )

            if self._has_visible_login_gate(page):
                return self._blocked(
                    "login_wall",
                    page,
                    steps=steps,
                    question_text="iCIMS login or account gate detected before the application form.",
                    details=self._frame_diagnostics(page),
                )

            if self._has_captcha(page):
                return self._blocked(
                    "captcha",
                    page,
                    steps=steps,
                    question_text="hCaptcha challenge detected on iCIMS apply flow.",
                    details={"provider": "hcaptcha", **self._frame_diagnostics(page)},
                )

            break

        return self._blocked(
            "unsupported_widget",
            page,
            steps=steps,
            question_text="Unsupported iCIMS flow shape.",
            details=self._frame_diagnostics(page),
        )

    def _submit_candidate_profile(self, *, page, scope, resolver: AnswerResolver, context, steps: list[StepSnapshot]) -> SubmitResult:
        self._replace_document(scope=scope, section_name="Resume", upload_path=context.resume_pdf_path, steps=steps)
        self._replace_document(scope=scope, section_name="Cover Letter", upload_path=context.cover_letter_pdf_path, steps=steps)

        blocker = self._fill_candidate_profile_fields(scope=scope, resolver=resolver, steps=steps)
        if blocker is not None:
            return self._blocked_from_blocker(page=page, blocker=blocker, steps=steps)
        blocker = self._fill_professional_experience_block(scope=scope, resolver=resolver, steps=steps)
        if blocker is not None:
            return self._blocked_from_blocker(page=page, blocker=blocker, steps=steps)

        password_blocker = self._required_password_blocker(scope)
        if password_blocker is not None:
            return self._blocked_from_blocker(page=page, blocker=password_blocker, steps=steps)

        if not self._click_update_profile(scope):
            return self._blocked(
                "unsupported_widget",
                page,
                steps=steps,
                question_text="Could not locate the iCIMS Update Profile action.",
                details=self._frame_diagnostics(page),
            )

        steps.append(
            StepSnapshot(
                step_key="candidate-profile:update-profile",
                step_label="Update profile",
                status="completed",
                answer_source="deterministic",
                answer_value="Update Profile",
            )
        )
        self._wait(scope, 2000)

        confirmation = self._extract_confirmation(page)
        if confirmation:
            return SubmitResult(
                status="submitted",
                current_url=getattr(page, "url", ""),
                confirmation_payload=confirmation,
                steps=steps,
                adapter_name=self.adapter_name,
            )

        next_scope = self._find_candidate_profile_scope(page)
        if next_scope is not None:
            return self._blocked(
                "ambiguous_confirmation",
                page,
                steps=steps,
                question_text="Candidate Profile remained visible after Update Profile.",
                details=self._frame_diagnostics(page),
            )

        questions_scope = self._find_questionnaire_scope(page)
        if questions_scope is not None:
            return self._submit_questionnaire(
                page=page,
                scope=questions_scope,
                resolver=resolver,
                steps=steps,
            )

        return self._blocked(
            "unsupported_widget",
            page,
            steps=steps,
            question_text="iCIMS advanced beyond Candidate Profile, but no supported final submit state was detected.",
            details=self._frame_diagnostics(page),
        )

    def _submit_questionnaire(self, *, page, scope, resolver: AnswerResolver, steps: list[StepSnapshot]) -> SubmitResult:
        page_kind = self._questionnaire_kind(scope)
        blocker = self._fill_required_fields(scope=scope, resolver=resolver, steps=steps, step_label=f"Fill iCIMS {page_kind.lower()}")
        if blocker is not None:
            return self._blocked_from_blocker(page=page, blocker=blocker, steps=steps)
        if not self._click_button(scope, "Submit"):
            return self._blocked(
                "unsupported_widget",
                page,
                steps=steps,
                question_text=f"Could not locate the iCIMS {page_kind} submit action.",
                details=self._frame_diagnostics(page),
            )
        steps.append(
            StepSnapshot(
                step_key=f"{page_kind.lower().replace(' ', '-')}:submit",
                step_label=f"Submit {page_kind.lower()}",
                status="completed",
                answer_source="deterministic",
                answer_value="Submit",
            )
        )
        self._wait(scope, 2000)

        confirmation = self._extract_confirmation(page)
        if confirmation:
            return SubmitResult(
                status="submitted",
                current_url=getattr(page, "url", ""),
                confirmation_payload=confirmation,
                steps=steps,
                adapter_name=self.adapter_name,
            )

        next_scope = self._find_questionnaire_scope(page)
        if next_scope is not None:
            next_kind = self._questionnaire_kind(next_scope)
            if next_kind != page_kind:
                return self._submit_questionnaire(
                    page=page,
                    scope=next_scope,
                    resolver=resolver,
                    steps=steps,
                )
            manual_checkpoint = self._manual_checkpoint_for_questionnaire(scope=next_scope, page_kind=next_kind)
            if manual_checkpoint is not None:
                return self._blocked_from_blocker(page=page, blocker=manual_checkpoint, steps=steps)
            return self._blocked(
                "ambiguous_confirmation",
                page,
                steps=steps,
                question_text=f"{page_kind} remained visible after Submit.",
                details=self._frame_diagnostics(page),
            )

        return self._blocked(
            "unsupported_widget",
            page,
            steps=steps,
            question_text=f"iCIMS advanced beyond {page_kind}, but no supported final confirmation state was detected.",
            details=self._frame_diagnostics(page),
        )

    def _submit_welcome_gate(self, *, scope, resolver: AnswerResolver, steps: list[StepSnapshot]) -> bool:
        filled_email = False
        accepted = False
        for field in self._extract_fields(scope):
            question_text = str(field.get("question_text") or field.get("field_name") or "").strip()
            field_name = str(field.get("field_name") or "").strip()
            field_type = str(field.get("field_type") or "text").strip()
            current_value = self._normalized_current_value(field_type=field_type, current_value=field.get("current_value"))
            if field_type == "text" and "email" in question_text.lower() and not current_value:
                try:
                    resolution = resolver.resolve(question_text="Email", field_name="identity.email", field_type="text")
                except ResolutionError:
                    return False
                if not self._set_field(scope, field, resolution.answer):
                    return False
                steps.append(
                    StepSnapshot(
                        step_key="welcome-gate:email",
                        step_label="Fill welcome gate email",
                        status="completed",
                        field_name=field_name,
                        field_type=field_type,
                        question_text=question_text,
                        answer_source=resolution.source,
                        answer_value=resolution.answer,
                    )
                )
                filled_email = True
                continue
            if field_type == "checkbox" and not current_value:
                if not self._set_field(scope, field, "true"):
                    return False
                steps.append(
                    StepSnapshot(
                        step_key="welcome-gate:consent",
                        step_label="Accept welcome gate consent",
                        status="completed",
                        field_name=field_name,
                        field_type=field_type,
                        question_text=question_text or "I accept",
                        answer_source="deterministic",
                        answer_value="true",
                    )
                )
                accepted = True
        if not (filled_email or accepted):
            return False
        if not self._click_button(scope, "Next"):
            return False
        steps.append(
            StepSnapshot(
                step_key="welcome-gate:next",
                step_label="Continue welcome gate",
                status="completed",
                answer_source="deterministic",
                answer_value="Next",
            )
        )
        self._wait(scope, 2000)
        return True

    def _fill_candidate_profile_fields(self, *, scope, resolver: AnswerResolver, steps: list[StepSnapshot]) -> Blocker | None:
        return self._fill_required_fields(scope=scope, resolver=resolver, steps=steps, step_label="Fill iCIMS field")

    def _fill_required_fields(self, *, scope, resolver: AnswerResolver, steps: list[StepSnapshot], step_label: str) -> Blocker | None:
        current_section = ""
        current_date_context = ""
        for field in self._extract_fields(scope):
            question_text = str(field.get("question_text") or field.get("label") or field.get("field_name") or "").strip()
            field_name = str(field.get("field_name") or "").strip()
            field_type = str(field.get("field_type") or "text").strip()
            required = bool(field.get("required", False))
            current_section = self._section_for_field(question_text=question_text, current_section=current_section)
            current_date_context = self._date_context_for_field(
                question_text=question_text,
                field_name=field_name,
                current_date_context=current_date_context,
            )
            if field_type == "file":
                continue
            if field_type == "password":
                continue
            if self._normalized_current_value(field_type=field_type, current_value=field.get("current_value")):
                continue
            if not required:
                continue
            try:
                resolution = resolver.resolve(
                    question_text=self._resolver_question_text(
                        question_text=question_text,
                        section=current_section,
                        date_context=current_date_context,
                        field_name=field_name,
                    ),
                    field_name=field_name,
                    field_type=field_type,
                )
            except ResolutionError as exc:
                return exc.blocker
            if not self._set_field(scope, field, resolution.answer):
                manual_checkpoint = self._manual_checkpoint_for_field(field)
                if manual_checkpoint is not None:
                    return manual_checkpoint
                return Blocker(
                    reason="unsupported_widget",
                    question_text=question_text,
                    field_name=field_name,
                    field_type=field_type,
                    details={"selector": str(field.get("selector") or ""), "answer": resolution.answer},
                )
            steps.append(
                StepSnapshot(
                    step_key=f"field:{field_name or question_text}",
                    step_label=step_label,
                    status="completed",
                    field_name=field_name,
                    field_type=field_type,
                    question_text=question_text,
                    answer_source=resolution.source,
                    answer_value=resolution.answer,
                )
            )
        return None

    def _fill_professional_experience_block(self, *, scope, resolver: AnswerResolver, steps: list[StepSnapshot]) -> Blocker | None:
        fields = self._extract_professional_experience_controls(scope)
        for field in fields:
            question_text = str(field.get("question_text") or "").strip()
            field_name = str(field.get("field_name") or "").strip()
            field_type = str(field.get("field_type") or "text").strip()
            current_value = self._normalized_current_value(field_type=field_type, current_value=field.get("current_value"))
            if current_value:
                continue
            try:
                resolution = resolver.resolve(
                    question_text=f"professional experience {question_text}".strip(),
                    field_name=field_name,
                    field_type=field_type,
                )
            except ResolutionError as exc:
                return exc.blocker
            if not self._set_field(scope, field, resolution.answer):
                manual_checkpoint = self._manual_checkpoint_for_field(field)
                if manual_checkpoint is not None:
                    return manual_checkpoint
                return Blocker(
                    reason="unsupported_widget",
                    question_text=question_text,
                    field_name=field_name,
                    field_type=field_type,
                    details={"selector": str(field.get("selector") or ""), "answer": resolution.answer},
                )
            steps.append(
                StepSnapshot(
                    step_key=f"professional-experience:{field_name or question_text}",
                    step_label="Fill professional experience field",
                    status="completed",
                    field_name=field_name,
                    field_type=field_type,
                    question_text=question_text,
                    answer_source=resolution.source,
                    answer_value=resolution.answer,
                )
            )
        return None

    def _extract_professional_experience_controls(self, scope) -> list[dict[str, object]]:
        extractor = getattr(scope, "extract_professional_experience_controls", None)
        if callable(extractor):
            return list(extractor())
        try:
            return list(
                scope.evaluate(
                    """
                    () => {
                      const block = Array.from(document.querySelectorAll('div, section, fieldset')).find((node) => {
                        const text = (node.textContent || '').toLowerCase();
                        return text.includes('professional experience') && text.includes('may we contact');
                      });
                      if (!block) return [];
                      const controls = [];
                      let counter = 0;
                      const push = (el, questionText, fieldType) => {
                        if (!el) return;
                        counter += 1;
                        el.setAttribute('data-jobhunter-profexp-index', String(counter));
                        const name = el.getAttribute('name') || '';
                        const id = el.getAttribute('id') || '';
                        const selector = `[data-jobhunter-profexp-index="${counter}"]`;
                        let currentValue = '';
                        if (fieldType.startsWith('select')) {
                          currentValue = (el.options[el.selectedIndex]?.textContent || el.value || '').trim();
                        } else {
                          currentValue = (el.value || '').trim();
                        }
                        controls.push({
                          selector,
                          field_name: name || id,
                          field_type: fieldType,
                          question_text: questionText,
                          current_value: currentValue,
                        });
                      };

                      const byName = (name) => block.querySelector(`[name="${name}"]`);
                      push(byName('-1_PersonProfileFields.rcf3218'), 'Country', 'select-one');
                      push(byName('-1_PersonProfileFields.rcf3217'), 'State/Province', 'select-one');
                      push(byName('-1_PersonProfileFields.rcf3214_Month'), 'Start Date (Month / Day / Year)', 'select-month');
                      push(byName('-1_PersonProfileFields.rcf3214_Date'), 'Start Date (Month / Day / Year)', 'select-day');
                      push(byName('-1_PersonProfileFields.rcf3214_Year'), 'Start Date (Month / Day / Year)', 'text');
                      push(byName('-1_PersonProfileFields.rcf3215_Month'), 'End Date (Month / Day / Year)', 'select-month');
                      push(byName('-1_PersonProfileFields.rcf3215_Date'), 'End Date (Month / Day / Year)', 'select-day');
                      push(byName('-1_PersonProfileFields.rcf3215_Year'), 'End Date (Month / Day / Year)', 'text');
                      push(byName('-1_PersonProfileFields.rcf3269'), 'May We Contact', 'select-one');

                      return controls.filter((control) => control.selector && control.question_text);
                    }
                    """
                )
            )
        except Exception:
            return []

    def _section_for_field(self, *, question_text: str, current_section: str) -> str:
        lowered = question_text.strip().lower()
        if lowered in {"school", "other school", "degree", "major", "gpa", "minor", "did you graduate?"}:
            return "education"
        if lowered in {
            "employer",
            "address",
            "city",
            "zip",
            "country",
            "state/province",
            "title",
            "reason for leaving",
            "may we contact",
        }:
            return "professional experience"
        if lowered.startswith("start date") or lowered.startswith("end date"):
            return current_section or "education"
        return current_section

    def _resolver_question_text(self, *, question_text: str, section: str, date_context: str, field_name: str) -> str:
        normalized_question = question_text.strip()
        if date_context and normalized_question.lower() in {"month", "day", "year", ""}:
            normalized_question = date_context
        if date_context and field_name.endswith(("_Month", "_Day", "_Year")):
            normalized_question = date_context
        if not section:
            return normalized_question
        return f"{section} {normalized_question}".strip()

    def _date_context_for_field(self, *, question_text: str, field_name: str, current_date_context: str) -> str:
        lowered = question_text.strip().lower()
        if lowered.startswith("start date"):
            return "Start Date (Month / Day / Year)"
        if lowered.startswith("end date"):
            return "End Date (Month / Day / Year)"
        if field_name.endswith(("_Month", "_Day", "_Year")):
            if "rcf3214" in field_name:
                return "Start Date (Month / Day / Year)"
            if "rcf3215" in field_name:
                return "End Date (Month / Day / Year)"
        return current_date_context

    def _replace_document(self, *, scope, section_name: str, upload_path: str, steps: list[StepSnapshot]) -> bool:
        path = Path(upload_path)
        if not path.exists():
            return False
        if not self._activate_document_replace(scope, section_name):
            return False
        inputs = self._document_file_inputs(scope, section_name)
        if not inputs:
            return False
        for selector in inputs:
            if self._set_input_files(scope, selector, upload_path):
                steps.append(
                    StepSnapshot(
                        step_key=f"upload:{section_name.lower().replace(' ', '-')}",
                        step_label=f"Upload {section_name}",
                        status="completed",
                        field_name=section_name.lower().replace(" ", "_"),
                        field_type="file",
                        question_text=section_name,
                        answer_source="artifact",
                        answer_value=upload_path,
                    )
                )
                self._wait(scope, 1500)
                return True
        return False

    def _activate_document_replace(self, scope, section_name: str) -> bool:
        actions = []
        if section_name.lower() == "resume":
            actions.extend(("Replace Resume", "Upload New Resume", "Upload Resume"))
        else:
            actions.extend(("Delete File", "Replace Cover Letter", "Upload Cover Letter"))
        for label in actions:
            if self._click_button(scope, label):
                self._wait(scope, 800)
                return True
        return True

    def _document_file_inputs(self, scope, section_name: str) -> list[str]:
        getter = getattr(scope, "document_file_inputs", None)
        if callable(getter):
            return list(getter(section_name))
        keyword = "resume" if section_name.lower() == "resume" else "cover"
        try:
            return list(
                scope.evaluate(
                    """
                    ({ keyword }) => {
                      const results = [];
                      const normalize = (value) => (value || '').toLowerCase();
                      for (const input of Array.from(document.querySelectorAll('input[type="file"]'))) {
                        const id = input.getAttribute('id') || '';
                        const name = input.getAttribute('name') || '';
                        const test = [
                          id,
                          name,
                          input.getAttribute('aria-label') || '',
                          input.closest('fieldset, section, div')?.textContent || '',
                        ].join(' ').toLowerCase();
                        if (!test.includes(keyword)) continue;
                        if (id) {
                          results.push(`#${CSS.escape(id)}`);
                          continue;
                        }
                        if (name) {
                          results.push(`input[type="file"][name="${name.replace(/"/g, '\\"')}"]`);
                        }
                      }
                      if (results.length) return results;
                      const fallback = Array.from(document.querySelectorAll('input[type="file"]')).map((input, index) => {
                        input.setAttribute('data-jobhunter-file-index', String(index + 1));
                        return `[data-jobhunter-file-index="${index + 1}"]`;
                      });
                      return fallback;
                    }
                    """,
                    {"keyword": keyword},
                )
            )
        except Exception:
            return []

    def _extract_fields(self, scope) -> list[dict[str, object]]:
        extractor = getattr(scope, "extract_fields", None)
        if callable(extractor):
            return list(extractor())
        return list(
            scope.evaluate(
                """
                () => {
                  const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                  };
                  const inferQuestionFromName = (name) => {
                    const lowered = (name || '').toLowerCase();
                    if (lowered.includes('rcf3214')) return 'Start Date (Month / Day / Year)';
                    if (lowered.includes('rcf3215')) return 'End Date (Month / Day / Year)';
                    if (lowered.includes('rcf3268')) return 'Reason for Leaving';
                    return '';
                  };
                  const findLabelText = (el) => {
                    const id = el.getAttribute('id') || '';
                    if (id) {
                      const direct = document.querySelector(`label[for="${id}"]`);
                      if (direct && direct.textContent) return direct.textContent.trim();
                    }
                    const wrappingLabel = el.closest('label');
                    if (wrappingLabel && wrappingLabel.textContent) return wrappingLabel.textContent.trim();
                    const fieldContainer = el.closest('tr, .row, .form-group, .field, fieldset, li, div');
                    const label = fieldContainer?.querySelector('label, legend, th');
                    if (label && label.textContent) return label.textContent.trim();
                    let sibling = el.previousElementSibling;
                    while (sibling) {
                      const text = (sibling.textContent || '').trim();
                      if (text) return text;
                      sibling = sibling.previousElementSibling;
                    }
                    const row = el.closest('tr, .row, div');
                    let rowSibling = row?.previousElementSibling || null;
                    while (rowSibling) {
                      const text = (rowSibling.textContent || '').trim();
                      if (text) return text;
                      rowSibling = rowSibling.previousElementSibling;
                    }
                    return (
                      inferQuestionFromName(el.getAttribute('name') || el.getAttribute('id') || '') ||
                      el.getAttribute('aria-label') ||
                      ''
                    ).trim();
                  };
                  const fields = [];
                  let counter = 0;
                  const monthTokens = new Set([
                    'january', 'february', 'march', 'april', 'may', 'june',
                    'july', 'august', 'september', 'october', 'november', 'december',
                    'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec'
                  ]);
                  const inferSelectType = (el) => {
                    const optionTexts = Array.from(el.options)
                      .map((option) => (option.textContent || '').trim())
                      .filter((value) => value && !/^select/i.test(value) && !/^--/.test(value));
                    if (!optionTexts.length) return 'select-one';
                    if (optionTexts.every((value) => monthTokens.has(value.toLowerCase()))) return 'select-month';
                    if (optionTexts.every((value) => /^\\d{1,2}$/.test(value))) {
                      const numbers = optionTexts.map((value) => Number(value));
                      if (numbers.every((value) => value >= 1 && value <= 31)) return 'select-day';
                    }
                    if (optionTexts.every((value) => /^\\d{4}$/.test(value))) return 'select-year';
                    return 'select-one';
                  };
                  const pushField = (el, fieldType) => {
                    counter += 1;
                    el.setAttribute('data-jobhunter-field-index', String(counter));
                    const selector = el.getAttribute('id')
                      ? `#${CSS.escape(el.getAttribute('id'))}`
                      : `[data-jobhunter-field-index="${counter}"]`;
                    let currentValue = '';
                    if (fieldType === 'checkbox') {
                      currentValue = el.checked ? 'true' : '';
                    } else if (fieldType === 'radio') {
                      currentValue = el.checked ? (el.value || 'true') : '';
                    } else if (fieldType === 'select-one') {
                      currentValue = (el.options[el.selectedIndex]?.textContent || el.value || '').trim();
                    } else {
                      currentValue = (el.value || '').trim();
                    }
                    const questionText = findLabelText(el);
                    const required =
                      el.required ||
                      el.getAttribute('aria-required') === 'true' ||
                      questionText.includes('*') ||
                      Boolean(inferQuestionFromName(el.getAttribute('name') || el.getAttribute('id') || ''));
                    fields.push({
                      selector,
                      field_name: el.getAttribute('name') || el.getAttribute('id') || '',
                      field_type: fieldType,
                      question_text: questionText,
                      required,
                      current_value: currentValue,
                    });
                  };

                  for (const el of Array.from(document.querySelectorAll('input, textarea, select'))) {
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    const tagName = el.tagName.toLowerCase();
                    if (type === 'hidden') continue;
                    if (!visible(el) && type !== 'file' && tagName !== 'select') continue;
                    if (el.disabled) continue;
                    if (type === 'file') {
                      pushField(el, 'file');
                      continue;
                    }
                    if (tagName === 'select') {
                      pushField(el, inferSelectType(el));
                      continue;
                    }
                    if (type === 'checkbox') {
                      pushField(el, 'checkbox');
                      continue;
                    }
                    if (type === 'radio') {
                      if (el.checked) {
                        pushField(el, 'radio');
                        continue;
                      }
                      const groupName = el.getAttribute('name') || '';
                      const previous = fields.find((field) => field.field_name === groupName && field.field_type === 'radio');
                      if (!previous) pushField(el, 'radio');
                      continue;
                    }
                    if (type === 'password') {
                      pushField(el, 'password');
                      continue;
                    }
                    pushField(el, 'text');
                  }
                  return fields;
                }
                """
            )
        )

    def _set_field(self, scope, field: dict[str, object], value: str) -> bool:
        setter = getattr(scope, "set_field", None)
        if callable(setter):
            try:
                setter(field, value)
                return True
            except Exception:
                return False
        selector = str(field.get("selector") or "")
        field_type = str(field.get("field_type") or "text")
        if not selector:
            return False
        try:
            locator = scope.locator(selector).first
            if field_type.startswith("select-"):
                return self._set_select(locator, value)
            if field_type == "checkbox":
                should_check = str(value).strip().lower() in {"true", "yes", "1"}
                locator.set_checked(should_check)
                return True
            if field_type == "radio":
                return self._set_radio(scope, field, value)
            locator.fill("")
            locator.fill(str(value))
            return True
        except Exception:
            return False

    def _set_select(self, locator, value: str) -> bool:
        desired_values = self._select_aliases(value)
        try:
            options = locator.evaluate(
                """
                (el) => Array.from(el.options).map((option, index) => ({
                  index,
                  value: option.value || '',
                  label: (option.textContent || '').trim(),
                }))
                """
            )
        except Exception:
            options = []
        for desired in desired_values:
            try:
                locator.select_option(label=desired)
                self._dispatch_select_events(locator)
                return True
            except Exception:
                pass
            try:
                locator.select_option(value=desired)
                self._dispatch_select_events(locator)
                return True
            except Exception:
                pass
            match = self._match_option(options, desired)
            if match is None:
                continue
            try:
                locator.select_option(index=int(match["index"]))
                self._dispatch_select_events(locator)
                return True
            except Exception:
                pass
            try:
                applied = locator.evaluate(
                    """
                    (el, optionIndex) => {
                      const option = el.options[optionIndex];
                      if (!option) return false;
                      el.selectedIndex = optionIndex;
                      el.value = option.value;
                      option.selected = true;
                      el.dispatchEvent(new Event('input', { bubbles: true }));
                      el.dispatchEvent(new Event('change', { bubbles: true }));
                      return true;
                    }
                    """,
                    int(match["index"]),
                )
                if applied:
                    return True
            except Exception:
                pass
        return False

    def _dispatch_select_events(self, locator) -> None:
        try:
            locator.evaluate(
                """
                (el) => {
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                  return true;
                }
                """
            )
        except Exception:
            return None

    def _set_radio(self, scope, field: dict[str, object], value: str) -> bool:
        field_name = str(field.get("field_name") or "")
        if not field_name:
            return False
        try:
            buttons = scope.locator(f'input[type="radio"][name="{field_name}"]')
            count = buttons.count()
            for index in range(count):
                option = buttons.nth(index)
                option_value = str(option.get_attribute("value") or "").strip()
                option_id = str(option.get_attribute("id") or "").strip()
                label = ""
                if option_id:
                    try:
                        label = str(scope.locator(f'label[for="{option_id}"]').first.inner_text()).strip()
                    except Exception:
                        label = ""
                if self._normalized_choice(option_value) == self._normalized_choice(value) or self._normalized_choice(label) == self._normalized_choice(value):
                    option.check()
                    return True
            return False
        except Exception:
            return False

    def _set_input_files(self, scope, selector: str, upload_path: str) -> bool:
        setter = getattr(scope, "set_input_files", None)
        if callable(setter):
            setter(selector, upload_path)
            return True
        try:
            scope.locator(selector).first.set_input_files(upload_path)
            return True
        except Exception:
            return False

    def _required_password_blocker(self, scope) -> Blocker | None:
        for field in self._extract_fields(scope):
            if str(field.get("field_type") or "") != "password":
                continue
            question_text = str(field.get("question_text") or "Password").strip()
            if self._normalized_current_value(field_type="password", current_value=field.get("current_value")):
                continue
            if not bool(field.get("required", False)):
                continue
            return Blocker(
                reason="account_setup_required",
                question_text=question_text,
                field_name=str(field.get("field_name") or ""),
                field_type="password",
                details={"message": "A required iCIMS password field is empty."},
            )
        return None

    def _manual_checkpoint_for_field(self, field: dict[str, object]) -> Blocker | None:
        field_name = str(field.get("field_name") or "").strip()
        field_type = str(field.get("field_type") or "").strip()
        question_text = str(field.get("question_text") or "").strip()
        professional_experience_dropdowns = {
            "-1_PersonProfileFields.rcf3218": "Country",
            "-1_PersonProfileFields.rcf3217": "State/Province",
            "-1_PersonProfileFields.rcf3269": "May We Contact",
            "-1_PersonProfileFields.rcf3214_Month": "Start Date (Month)",
            "-1_PersonProfileFields.rcf3214_Date": "Start Date (Day)",
            "-1_PersonProfileFields.rcf3215_Month": "End Date (Month)",
            "-1_PersonProfileFields.rcf3215_Date": "End Date (Day)",
        }
        if field_name not in professional_experience_dropdowns:
            return None
        if not (field_type.startswith("select-") or field_type == "select-one"):
            return None
        return Blocker(
            reason="manual_checkpoint_required",
            question_text=question_text or professional_experience_dropdowns[field_name],
            field_name=field_name,
            field_type=field_type,
            details={
                "checkpoint": "professional_experience_dropdowns",
                "checkpoint_label": "Professional Experience dropdown trio",
                "fields": list(professional_experience_dropdowns.keys()),
                "message": (
                    "Complete the Professional Experience dropdown fields manually, "
                    "then resume automation from the current candidate profile page."
                ),
            },
        )

    def _manual_checkpoint_for_questionnaire(self, *, scope, page_kind: str) -> Blocker | None:
        text = self._scope_text(scope).lower()
        if page_kind != "Job Specific Questions":
            return None
        if "undergraduate gpa" not in text:
            return None
        return Blocker(
            reason="manual_checkpoint_required",
            question_text="What is/was your undergraduate GPA on a 4.0 scale?",
            field_name="undergrad_gpa",
            field_type="select-one",
            details={
                "checkpoint": "job_specific_questions_gpa",
                "checkpoint_label": "Job Specific Questions GPA",
                "message": (
                    "Select the undergraduate GPA answer on the Job Specific Questions page, "
                    "submit the page manually if needed, then resume automation from the resulting page."
                ),
            },
        )

    def _click_update_profile(self, scope) -> bool:
        return self._click_button(scope, "Update Profile")

    def _click_button(self, scope, label: str) -> bool:
        clicker = getattr(scope, "click_button", None)
        if callable(clicker):
            return bool(clicker(label))
        try:
            button = scope.get_by_role("button", name=label, exact=False).first
            if button.count() > 0:
                button.click()
                return True
        except Exception:
            pass
        try:
            button = scope.locator(f"text=/{label}/i").first
            if button.count() > 0:
                button.click()
                return True
        except Exception:
            pass
        return False

    def _find_candidate_profile_scope(self, page):
        detector = getattr(page, "detect_candidate_profile", None)
        if callable(detector) and detector():
            return page
        for scope in self._scopes(page):
            text = self._scope_text(scope).lower()
            if "candidate profile" in text and ("update profile" in text or "resume" in text):
                return scope
            if "update profile" in text and ("enter your information" in text or "professional experience" in text):
                return scope
        return None

    def _find_questionnaire_scope(self, page):
        detector = getattr(page, "detect_candidate_questions", None)
        if callable(detector) and detector():
            return page
        for scope in self._scopes(page):
            text = self._scope_text(scope).lower()
            if (
                ("candidate questions" in text or "job specific questions" in text)
                and ("please answer the following questions" in text or "required field" in text or "submit" in text)
            ):
                return scope
        return None

    def _questionnaire_kind(self, scope) -> str:
        text = self._scope_text(scope).lower()
        if "job specific questions" in text:
            return "Job Specific Questions"
        return "Candidate Questions"

    def _advance_from_landing_page(self, page) -> bool:
        self._dismiss_cookie_banner(page)
        if not self._is_public_job_page(page):
            return False
        for label in ("Apply", "Apply Now"):
            if self._click_button(page, label):
                self._wait(page, 2500)
                return True
        return False

    def _dismiss_cookie_banner(self, page) -> None:
        for label in ("Okay", "OK", "Accept", "I Agree"):
            if self._click_button(page, label):
                self._wait(page, 500)
                return

    def _is_public_job_page(self, page) -> bool:
        url = str(getattr(page, "url", "") or "").lower()
        if "careers.medpace.com/jobs/" in url:
            return True
        text = self._scope_text(page).lower()
        return "job summary" in text and "qualifications" in text and "apply" in text

    def _has_captcha(self, page) -> bool:
        checker = getattr(page, "detect_captcha", None)
        if callable(checker) and checker():
            return True
        for scope in self._scopes(page):
            text = self._scope_text(scope).lower()
            if "hcaptcha" in text:
                return True
            try:
                if scope.locator("iframe[src*='hcaptcha']").count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _has_visible_login_gate(self, page) -> bool:
        detector = getattr(page, "detect_login_wall", None)
        if callable(detector) and detector():
            return True
        for scope in self._scopes(page):
            text = self._scope_text(scope).lower()
            if not text:
                continue
            if any(marker in text for marker in _LOGIN_MARKERS):
                return True
        return False

    def _has_visible_email_welcome_gate(self, page) -> bool:
        return self._find_email_welcome_scope(page) is not None

    def _find_email_welcome_scope(self, page):
        for scope in self._scopes(page):
            text = self._scope_text(scope).lower()
            if all(marker in text for marker in _WELCOME_MARKERS) and "candidate profile" not in text:
                return scope
        return None

    def _extract_confirmation(self, page) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        if callable(extractor):
            return dict(extractor() or {})
        page_url = str(getattr(page, "url", "") or "").strip()
        if "mode=submit_apply" in page_url:
            return {
                "message": "Application submitted (detected from iCIMS submit_apply confirmation URL).",
                "source": "icims",
                "inference": "url-based",
            }
        for scope in self._scopes(page):
            text = self._scope_text(scope)
            lowered = text.lower()
            if any(marker in lowered for marker in _CONFIRMATION_MARKERS):
                return {
                    "message": text.strip(),
                    "source": "icims",
                }
        return {}

    def _match_option(self, options: list[dict[str, str]], value: str) -> dict[str, str] | None:
        normalized_target = self._normalized_choice(value)
        for option in options:
            if self._normalized_choice(option.get("label", "")) == normalized_target:
                return option
            if self._normalized_choice(option.get("value", "")) == normalized_target:
                return option
        for option in options:
            if normalized_target and normalized_target in self._normalized_choice(option.get("label", "")):
                return option
        return None

    def _normalized_current_value(self, *, field_type: str, current_value) -> str:
        value = str(current_value or "").strip()
        lowered = value.lower()
        if field_type == "checkbox" and lowered == "false":
            return ""
        if lowered in {
            "",
            "select...",
            "select..",
            "select one",
            "choose",
            "choose one",
            "month",
            "day",
            "year",
            "please select a country",
            "please select a province",
            "please select a state",
            "make a selection",
            "— make a selection —",
            "-- make a selection --",
        }:
            return ""
        return value

    def _normalized_choice(self, value: str) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _select_aliases(self, value: str) -> list[str]:
        aliases = [str(value or "").strip()]
        normalized = self._normalized_choice(value)
        if normalized == "united states":
            aliases.extend(["United States of America", "USA", "US"])
        elif normalized == "new york":
            aliases.extend(["NY", "N.Y."])
        elif normalized == "california":
            aliases.extend(["CA", "Calif."])
        elif normalized == "no":
            aliases.extend(["N"])
        elif normalized == "yes":
            aliases.extend(["Y"])
        unique: list[str] = []
        for alias in aliases:
            if alias and alias not in unique:
                unique.append(alias)
        return unique

    def _scopes(self, page) -> list[object]:
        scopes: list[object] = [page]
        if hasattr(page, "frames"):
            try:
                scopes.extend(list(page.frames))
            except Exception:
                pass
        return scopes

    def _scope_text(self, scope) -> str:
        getter = getattr(scope, "frame_texts", None)
        if callable(getter):
            try:
                return "\n".join(str(value) for value in getter())
            except Exception:
                pass
        try:
            title = getattr(scope, "title", None)
            if callable(title):
                title_value = str(title() or "").strip()
            else:
                title_value = ""
        except Exception:
            title_value = ""
        try:
            body_text = str(scope.locator("body").inner_text(timeout=2000) or "").strip()
        except Exception:
            body_text = ""
        return "\n".join(part for part in (title_value, body_text) if part)

    def _page_text(self, page) -> str:
        return "\n".join(self._scope_text(scope) for scope in self._scopes(page))

    def _frame_diagnostics(self, page) -> dict[str, object]:
        diagnostics: dict[str, object] = {}
        frame_getter = getattr(page, "frame_texts", None)
        if callable(frame_getter):
            try:
                frame_texts = [str(value).strip() for value in frame_getter() if str(value).strip()]
                if frame_texts:
                    diagnostics["frame_texts"] = frame_texts[:5]
            except Exception:
                pass
        if hasattr(page, "frames"):
            try:
                frame_urls: list[str] = []
                for frame in page.frames:
                    url = str(getattr(frame, "url", "") or "").strip()
                    if url:
                        frame_urls.append(url)
                if frame_urls:
                    diagnostics["frame_urls"] = frame_urls[:10]
            except Exception:
                pass
        return diagnostics

    def _wait(self, scope, milliseconds: int) -> None:
        waiter = getattr(scope, "wait_for_timeout", None)
        if callable(waiter):
            waiter(milliseconds)

    def _blocked_from_blocker(self, *, page, blocker: Blocker, steps: list[StepSnapshot]) -> SubmitResult:
        return self._blocked(
            blocker.reason,
            page,
            steps=steps,
            question_text=blocker.question_text,
            field_name=blocker.field_name,
            field_type=blocker.field_type,
            details=blocker.details,
        )

    def _blocked(
        self,
        reason: str,
        page,
        *,
        steps: list[StepSnapshot] | None = None,
        question_text: str = "",
        field_name: str = "",
        field_type: str = "",
        details: dict[str, object] | None = None,
    ) -> SubmitResult:
        return SubmitResult(
            status="blocked",
            current_url=getattr(page, "url", ""),
            blocker=Blocker(
                reason=reason,
                question_text=question_text,
                field_name=field_name,
                field_type=field_type,
                details=details or {},
            ),
            steps=steps or [],
            adapter_name=self.adapter_name,
        )
