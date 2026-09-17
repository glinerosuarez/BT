from __future__ import annotations

from urllib.parse import urlparse

from job_hunter.apply.types import Blocker, StepSnapshot, SubmitResult

_APPLY_LABELS = (
    "Apply",
    "Apply now",
)
_SUBMIT_LABELS = (
    "Submit Application",
    "Submit application",
    "Submit",
)
_CONFIRMATION_MARKERS = (
    "application submitted",
    "thank you for applying",
    "your application has been submitted",
    "you've successfully applied",
    "application submitted!",
)
_POST_SUBMIT_STATE_MARKERS = (
    "applied on ",
    "withdraw application",
)


class HandshakeAdapter:
    adapter_name = "handshake"

    def is_handshake_target(self, url: str, page=None) -> bool:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if "app.joinhandshake.com" in host and "/jobs/" in path:
            return True
        current_url = str(getattr(page, "url", "") or "").strip() if page is not None else ""
        if current_url:
            current = urlparse(current_url)
            return "app.joinhandshake.com" in current.netloc.lower() and "/jobs/" in current.path.lower()
        return False

    def submit(self, *, page, resolver, context) -> SubmitResult:
        _ = resolver
        steps: list[StepSnapshot] = []

        confirmation = self._extract_confirmation(page, use_extractor=False)
        if confirmation:
            return SubmitResult(
                status="submitted",
                current_url=str(getattr(page, "url", "") or ""),
                confirmation_payload=confirmation,
                steps=steps,
                adapter_name=self.adapter_name,
            )

        if self._has_login_wall(page):
            return self._blocked("login_wall", page, steps)
        if self._has_captcha(page):
            return self._blocked("captcha", page, steps)

        if not self._open_apply(page):
            return self._blocked("apply_button_missing", page, steps)

        if not self._upload_document(page, section_name="Attach your resume", upload_path=context.resume_pdf_path):
            return self._blocked("resume_upload_missing", page, steps)
        steps.append(
            StepSnapshot(
                step_key="handshake:resume",
                step_label="Upload resume",
                status="completed",
                field_name="resume",
                field_type="file",
                question_text="Attach your resume",
                answer_source="artifact",
                answer_value=context.resume_pdf_path,
            )
        )

        if self._has_document_section(page, section_name="Attach your cover letter"):
            if not self._upload_document(page, section_name="Attach your cover letter", upload_path=context.cover_letter_pdf_path):
                return self._blocked("cover_letter_upload_missing", page, steps)
            steps.append(
                StepSnapshot(
                    step_key="handshake:cover_letter",
                    step_label="Upload cover letter",
                    status="completed",
                    field_name="cover_letter",
                    field_type="file",
                    question_text="Attach your cover letter",
                    answer_source="artifact",
                    answer_value=context.cover_letter_pdf_path,
                )
            )

        blocker = self._fill_form_fields(page, resolver=resolver, context=context, steps=steps)
        if blocker is not None:
            return blocker

        if not self._wait_until_ready_to_submit(page):
            return self._blocked("submit_not_ready", page, steps)

        if not self._submit(page):
            return self._blocked("submit_button_missing", page, steps)

        confirmation = self._extract_confirmation(page)
        if not confirmation:
            return self._blocked("ambiguous_confirmation", page, steps)

        return SubmitResult(
            status="submitted",
            current_url=str(getattr(page, "url", "") or ""),
            confirmation_payload=confirmation,
            steps=steps,
            adapter_name=self.adapter_name,
        )

    def _open_apply(self, page) -> bool:
        click_button = getattr(page, "click_button", None)
        if callable(click_button):
            for label in _APPLY_LABELS:
                if click_button(label):
                    return True
        return self._click_labeled_control(page, _APPLY_LABELS)

    def _upload_document(self, page, *, section_name: str, upload_path: str) -> bool:
        self._wait_for_document_slot(page, section_name=section_name)
        if self._has_attached_document(page, section_name=section_name):
            return True
        selectors = self._document_file_inputs(page, section_name)
        for selector in selectors:
            if self._set_input_files(page, selector, upload_path):
                if self._wait_for_uploaded_document(page, section_name=section_name, upload_path=upload_path):
                    return True
        if self._upload_via_button(page, section_name=section_name, upload_path=upload_path):
            return self._wait_for_uploaded_document(page, section_name=section_name, upload_path=upload_path)
        return False

    def _wait_for_document_slot(self, page, *, section_name: str) -> None:
        locator_factory = getattr(page, "locator", None)
        wait = getattr(page, "wait_for_timeout", None)
        if not callable(locator_factory):
            return
        for _ in range(5):
            try:
                fieldset = locator_factory("fieldset").filter(has_text=section_name).first
                if fieldset.count() == 0:
                    if callable(wait):
                        wait(500)
                    continue
                input_count = fieldset.locator("input[type='file']").count()
                button_count = fieldset.locator("button").filter(has_text="Upload new").count()
                if input_count > 0 or button_count > 0:
                    return
            except Exception:
                return
            if callable(wait):
                wait(500)

    def _document_file_inputs(self, page, section_name: str) -> list[str]:
        extractor = getattr(page, "document_file_inputs", None)
        if callable(extractor):
            inputs = extractor(section_name)
            return [str(item) for item in inputs if str(item).strip()]
        if not hasattr(page, "evaluate"):
            return []
        try:
            selectors = page.evaluate(
                """
                ({ sectionName }) => {
                  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                  const makeSelector = (input, index) => {
                    const id = (input.getAttribute('id') || '').trim();
                    if (id) return `#${CSS.escape(id)}`;
                    input.setAttribute('data-jobhunter-handshake-upload-index', String(index));
                    return `input[data-jobhunter-handshake-upload-index="${index}"]`;
                  };
                  const target = normalize(sectionName);
                  const allInputs = Array.from(document.querySelectorAll('input[type="file"]'));
                  let counter = 0;
                  const matchingFieldset = Array.from(document.querySelectorAll('fieldset')).find((fieldset) =>
                    normalize(fieldset.textContent || '').includes(target)
                  );
                  const matched = matchingFieldset
                    ? Array.from(matchingFieldset.querySelectorAll('input[type="file"]')).map((input) => {
                        counter += 1;
                        return makeSelector(input, counter);
                      })
                    : [];
                  if (matched.length) return matched;
                  const byName = allInputs.filter((input) => {
                    const name = normalize(input.getAttribute('name') || '');
                    return target.includes('cover') ? name.includes('cover') : name.includes('resume');
                  });
                  if (byName.length) {
                    return byName.map((input) => {
                      counter += 1;
                      return makeSelector(input, counter);
                    });
                  }
                  if (allInputs.length === 2) {
                    const selected = target.includes('cover') ? [allInputs[1]] : [allInputs[0]];
                    return selected.map((input) => {
                      counter += 1;
                      return makeSelector(input, counter);
                    });
                  }
                  return [];
                }
                """,
                {"sectionName": section_name},
            )
        except Exception:
            return []
        if not isinstance(selectors, list):
            return []
        return [str(item) for item in selectors if str(item).strip()]

    def _has_document_section(self, page, *, section_name: str) -> bool:
        extractor = getattr(page, "document_file_inputs", None)
        if callable(extractor):
            return bool(extractor(section_name))
        locator_factory = getattr(page, "locator", None)
        if callable(locator_factory):
            try:
                fieldset = locator_factory("fieldset").filter(has_text=section_name).first
                return fieldset.count() > 0
            except Exception:
                return False
        return False

    def _has_attached_document(self, page, *, section_name: str) -> bool:
        locator_factory = getattr(page, "locator", None)
        if callable(locator_factory):
            try:
                fieldset = locator_factory("fieldset").filter(has_text=section_name).first
                if fieldset.count() == 0:
                    return False
                text = str(fieldset.inner_text(timeout=1000) or "").lower()
                if "preview document" in text:
                    return True
            except Exception:
                return False
        return False

    def _set_input_files(self, page, selector: str, upload_path: str) -> bool:
        setter = getattr(page, "set_input_files", None)
        if callable(setter):
            setter(selector, upload_path)
            return True
        if not hasattr(page, "locator"):
            return False
        try:
            locator = page.locator(selector).first
            locator.set_input_files(upload_path)
            wait = getattr(page, "wait_for_timeout", None)
            if callable(wait):
                wait(1000)
            return True
        except Exception:
            return False

    def _upload_via_button(self, page, *, section_name: str, upload_path: str) -> bool:
        if not hasattr(page, "locator"):
            return False
        try:
            fieldset = page.locator("fieldset").filter(has_text=section_name).first
            if fieldset.count() == 0:
                return False
            upload_button = fieldset.locator("button").filter(has_text="Upload new").first
            if upload_button.count() == 0:
                return False
            expect_file_chooser = getattr(page, "expect_file_chooser", None)
            if callable(expect_file_chooser):
                with expect_file_chooser() as chooser_info:
                    upload_button.click()
                chooser_info.value.set_files(upload_path)
                wait = getattr(page, "wait_for_timeout", None)
                if callable(wait):
                    wait(1500)
                return True
            upload_button.click()
        except Exception:
            return False
        return False

    def _wait_for_uploaded_document(self, page, *, section_name: str, upload_path: str) -> bool:
        filename = urlparse(upload_path).path.rsplit("/", 1)[-1]
        wait = getattr(page, "wait_for_timeout", None)
        locator_factory = getattr(page, "locator", None)
        if not callable(locator_factory):
            values = getattr(page, "values", None)
            if isinstance(values, dict) and upload_path in {str(value) for value in values.values()}:
                return True
        if callable(locator_factory):
            for _ in range(5):
                try:
                    fieldset = locator_factory("fieldset").filter(has_text=section_name).first
                    if fieldset.count() > 0:
                        text = fieldset.inner_text(timeout=1000)
                        lowered = str(text or "").lower()
                        if filename.lower() in lowered:
                            return True
                    if callable(wait):
                        wait(1000)
                except Exception:
                    break
        try:
            lowered = str(page.content() or "").lower()
        except Exception:
            lowered = ""
        return filename.lower() in lowered

    def _submit(self, page) -> bool:
        submit_application = getattr(page, "submit_application", None)
        if callable(submit_application):
            submit_application()
            return True
        click_button = getattr(page, "click_button", None)
        if callable(click_button):
            for label in _SUBMIT_LABELS:
                if click_button(label):
                    return True
        submit_button = self._active_submit_button(page)
        if submit_button is not None:
            try:
                submit_button.click()
                wait = getattr(page, "wait_for_timeout", None)
                if callable(wait):
                    wait(1500)
                return True
            except Exception:
                return False
        if self._click_labeled_control(page, _SUBMIT_LABELS):
            wait = getattr(page, "wait_for_timeout", None)
            if callable(wait):
                wait(1500)
            return True
        return False

    def _wait_until_ready_to_submit(self, page) -> bool:
        wait = getattr(page, "wait_for_timeout", None)
        locator_factory = getattr(page, "locator", None)
        if not callable(locator_factory):
            return True
        for _ in range(20):
            try:
                modal = self._active_apply_modal(page)
                if modal is None:
                    return False
                submit_button = self._active_submit_button(page, modal=modal)
                if submit_button is None:
                    return False
                modal_text = str(modal.inner_text(timeout=1000) or "").lower()
                if "converting..." not in modal_text and not self._locator_is_disabled(submit_button):
                    return True
            except Exception:
                return False
            if callable(wait):
                wait(1000)
        return False

    def _active_apply_modal(self, page):
        locator_factory = getattr(page, "locator", None)
        if not callable(locator_factory):
            return None
        try:
            modals = locator_factory("[data-hook='apply-modal-content']")
            count = modals.count()
            if count == 0:
                return None
            for index in range(count - 1, -1, -1):
                candidate = modals.nth(index)
                try:
                    if candidate.is_visible():
                        return candidate
                except Exception:
                    continue
            return modals.last
        except Exception:
            return None

    def _active_submit_button(self, page, *, modal=None):
        if modal is None:
            modal = self._active_apply_modal(page)
        if modal is None:
            return None
        for label in _SUBMIT_LABELS:
            try:
                candidate = modal.locator("button").filter(has_text=label).first
                if candidate.count() > 0:
                    return candidate
            except Exception:
                continue
        return None

    def _locator_is_disabled(self, locator) -> bool:
        try:
            return bool(locator.is_disabled())
        except Exception:
            pass
        try:
            disabled = locator.get_attribute("disabled")
            aria_disabled = locator.get_attribute("aria-disabled")
            return disabled is not None or aria_disabled == "true"
        except Exception:
            return True

    def _extract_confirmation(self, page, *, use_extractor: bool = True) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        if use_extractor and callable(extractor):
            payload = dict(extractor() or {})
            if payload:
                return payload
        lowered = self._visible_text(page).lower()
        if any(marker in lowered for marker in _CONFIRMATION_MARKERS) or all(
            marker in lowered for marker in _POST_SUBMIT_STATE_MARKERS
        ):
            return {
                "message": "Application submitted",
                "url": str(getattr(page, "url", "") or ""),
                "source": "handshake",
            }
        applied = self._button_text(page).lower()
        if "applied" in applied:
            return {
                "message": "Application submitted",
                "url": str(getattr(page, "url", "") or ""),
                "source": "handshake",
            }
        return {}

    def _button_text(self, page) -> str:
        if not hasattr(page, "evaluate"):
            return ""
        try:
            return str(
                page.evaluate(
                    """
                    () => {
                      const controls = Array.from(document.querySelectorAll('button, [role="button"]'));
                      return controls.map((node) => (node.innerText || node.getAttribute('aria-label') || '').trim()).join(' | ');
                    }
                    """
                )
                or ""
            )
        except Exception:
            return ""

    def _visible_text(self, page) -> str:
        locator = getattr(page, "locator", None)
        if callable(locator):
            try:
                text = locator("body").inner_text(timeout=1000)
                if text:
                    return str(text)
            except Exception:
                pass
        try:
            return str(page.content() or "")
        except Exception:
            return ""

    def _click_labeled_control(self, page, labels: tuple[str, ...]) -> bool:
        if not hasattr(page, "locator"):
            return False
        selectors = ("button", "a", "[role='button']")
        for label in labels:
            for selector in selectors:
                try:
                    candidate = page.locator(selector).filter(has_text=label).first
                    if candidate.count() == 0:
                        continue
                    candidate.click()
                    wait = getattr(page, "wait_for_timeout", None)
                    if callable(wait):
                        wait(1000)
                    return True
                except Exception:
                    continue
        return False

    def _has_login_wall(self, page) -> bool:
        detector = getattr(page, "detect_login_wall", None)
        return bool(detector()) if callable(detector) else False

    def _has_captcha(self, page) -> bool:
        detector = getattr(page, "detect_captcha", None)
        return bool(detector()) if callable(detector) else False

    def _fill_form_fields(self, page, *, resolver, context, steps: list[StepSnapshot]) -> SubmitResult | None:
        if not hasattr(page, "locator"):
            return None

        # 1. Text / Phone / LinkedIn inputs
        try:
            inputs = page.locator(
                "form input:not([type='hidden']):not([type='radio']):not([type='file']):not([type='checkbox']), form textarea"
            ).all()
        except Exception:
            inputs = []

        for inp in inputs:
            try:
                if not inp.is_visible():
                    continue
                name = str(inp.get_attribute("name") or "").strip()
                input_id = str(inp.get_attribute("id") or "").strip()
                current_val = str(inp.input_value() or "").strip()
                if current_val:
                    continue

                label = ""
                if input_id:
                    lbl = page.locator(f"label[for='{input_id}']").first
                    if lbl.count() > 0:
                        label = str(lbl.inner_text() or "").strip()
                if not label:
                    label = str(inp.get_attribute("aria-label") or inp.get_attribute("placeholder") or name).strip()

                is_phone = "phone" in name.lower() or "phone" in label.lower()
                is_linkedin = "linkedin" in name.lower() or "linkedin" in label.lower()

                if is_phone:
                    phone_val = getattr(getattr(context, "profile", None), "identity", None)
                    raw_phone = getattr(phone_val, "phone", "") if phone_val else ""
                    raw_phone = raw_phone or "213-774-1818"
                    clean_digits = "".join(ch for ch in raw_phone if ch.isdigit())
                    if len(clean_digits) == 11 and clean_digits.startswith("1"):
                        formatted_phone = f"{clean_digits[1:4]}-{clean_digits[4:7]}-{clean_digits[7:]}"
                    elif len(clean_digits) == 10:
                        formatted_phone = f"{clean_digits[0:3]}-{clean_digits[3:6]}-{clean_digits[6:]}"
                    else:
                        formatted_phone = raw_phone
                    inp.fill(formatted_phone)
                    steps.append(
                        StepSnapshot(
                            step_key=f"handshake:{name or 'phone'}",
                            step_label="Fill phone field",
                            status="completed",
                            field_name=name,
                            field_type="text",
                            question_text=label,
                            answer_source="structured:identity.phone",
                            answer_value=formatted_phone,
                        )
                    )
                elif is_linkedin:
                    identity = getattr(getattr(context, "profile", None), "identity", None)
                    li_val = getattr(identity, "linkedin_url", "") if identity else ""
                    li_val = li_val or "https://www.linkedin.com/in/glinerosuarez"
                    inp.fill(li_val)
                    steps.append(
                        StepSnapshot(
                            step_key=f"handshake:{name or 'linkedin'}",
                            step_label="Fill LinkedIn profile",
                            status="completed",
                            field_name=name,
                            field_type="text",
                            question_text=label,
                            answer_source="structured:identity.linkedin_url",
                            answer_value=li_val,
                        )
                    )
                else:
                    is_required = "required" in label.lower() or inp.get_attribute("required") is not None
                    res = None
                    try:
                        res = resolver.resolve(question_text=label, field_name=name, field_type="text")
                    except Exception:
                        if is_required:
                            return self._blocked("missing_required_answer", page, steps, question_text=label, field_name=name, details={"question": label, "field": name})
                    if res and res.answer:
                        inp.fill(res.answer)
                        steps.append(
                            StepSnapshot(
                                step_key=f"handshake:{name}",
                                step_label="Fill text field",
                                status="completed",
                                field_name=name,
                                field_type="text",
                                question_text=label,
                                answer_source=res.source,
                                answer_value=res.answer,
                            )
                        )
            except Exception:
                continue

        # 2. Radio groups
        try:
            fieldsets = page.locator("fieldset.rosetta-radio-group, form fieldset").all()
        except Exception:
            fieldsets = []

        for fs in fieldsets:
            try:
                if not fs.is_visible():
                    continue
                legend = fs.locator("legend").first
                if legend.count() == 0:
                    continue
                question_text = str(legend.inner_text() or "").strip()
                radios = fs.locator("input[type='radio']").all()
                if not radios:
                    continue
                if any(r.is_checked() for r in radios):
                    continue

                res = resolver.resolve(question_text=question_text, field_name=radios[0].get_attribute("name") or "", field_type="radio")
                desired = res.answer.strip().lower()

                selected = False
                for r in radios:
                    val = str(r.get_attribute("value") or "").strip().lower()
                    aria = str(r.get_attribute("aria-label") or "").strip().lower()
                    lbl = ""
                    r_id = r.get_attribute("id")
                    if r_id:
                        lbl_el = fs.locator(f"label[for='{r_id}']").first
                        if lbl_el.count() > 0:
                            lbl = str(lbl_el.inner_text() or "").strip().lower()
                    if desired in (val, aria, lbl) or (desired == "yes" and "yes" in (val, aria, lbl)) or (desired == "no" and "no" in (val, aria, lbl)):
                        r.check(force=True)
                        selected = True
                        steps.append(
                            StepSnapshot(
                                step_key=f"handshake:radio:{radios[0].get_attribute('name')}",
                                step_label="Select radio option",
                                status="completed",
                                field_name=radios[0].get_attribute("name") or "",
                                field_type="radio",
                                question_text=question_text,
                                answer_source=res.source,
                                answer_value=res.answer,
                            )
                        )
                        break
                if not selected:
                    return self._blocked("unsupported_widget", page, steps, question_text=question_text, details={"question": question_text, "desired": res.answer})
            except Exception:
                continue

        # 3. Comboboxes (Rosetta selects)
        try:
            comboboxes = page.locator("form [role='combobox']").all()
        except Exception:
            comboboxes = []

        for cb in comboboxes:
            try:
                if not cb.is_visible():
                    continue
                label_id = str(cb.get_attribute("aria-labelledby") or "").strip()
                if not label_id:
                    continue
                lbl_el = page.locator(f"#{label_id}").first
                if lbl_el.count() == 0:
                    continue
                question_text = str(lbl_el.inner_text() or "").strip()
                if not question_text:
                    continue

                current_text = str(cb.inner_text() or "").strip()
                if current_text and current_text != "Select One":
                    continue

                listbox_id = str(cb.get_attribute("aria-controls") or "").strip()
                is_required = "required" in question_text.lower()

                res = None
                try:
                    res = resolver.resolve(question_text=question_text, field_name=listbox_id, field_type="select")
                except Exception:
                    if is_required:
                        return self._blocked("missing_required_answer", page, steps, question_text=question_text, field_name=listbox_id, details={"question": question_text, "combobox": listbox_id})
                    continue

                if not res or not res.answer:
                    continue

                desired = res.answer.strip()
                cb.click()
                wait = getattr(page, "wait_for_timeout", None)
                if callable(wait):
                    wait(300)

                listbox = page.locator(f"#{listbox_id}").first
                if listbox.count() == 0:
                    continue

                options = listbox.locator("[role='option']").all()
                selected = False
                for opt in options:
                    opt_text = str(opt.inner_text() or "").strip()
                    if opt_text.lower() == desired.lower():
                        opt.click()
                        selected = True
                        break
                if not selected:
                    for opt in options:
                        opt_text = str(opt.inner_text() or "").strip()
                        if desired.lower() in opt_text.lower() or opt_text.lower() in desired.lower():
                            if opt_text.lower() != "select one":
                                opt.click()
                                selected = True
                                break

                if selected:
                    if callable(wait):
                        wait(300)
                    steps.append(
                        StepSnapshot(
                            step_key=f"handshake:combobox:{listbox_id}",
                            step_label="Select combobox option",
                            status="completed",
                            field_name=listbox_id,
                            field_type="select",
                            question_text=question_text,
                            answer_source=res.source,
                            answer_value=desired,
                        )
                    )
                elif is_required:
                    return self._blocked("unsupported_widget", page, steps, question_text=question_text, details={"question": question_text, "desired": desired})
            except Exception:
                continue

        return None

    def _blocked(
        self,
        reason: str,
        page,
        steps: list[StepSnapshot],
        *,
        question_text: str = "",
        field_name: str = "",
        field_type: str = "",
        details: dict[str, object] | None = None,
    ) -> SubmitResult:
        return SubmitResult(
            status="blocked",
            current_url=str(getattr(page, "url", "") or ""),
            blocker=Blocker(
                reason=reason,
                question_text=question_text,
                field_name=field_name,
                field_type=field_type,
                details=details or {},
            ),
            steps=steps,
            adapter_name=self.adapter_name,
        )

