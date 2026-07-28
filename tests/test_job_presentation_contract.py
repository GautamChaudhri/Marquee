"""Presentation contract: the vocabulary, cursors, and errors the UI is built against.

The presentation layer is a frozen contract — job kinds, headline vocabulary, pagination
cursors bound to their view and sort, and typed errors. A cursor minted for one view must
not decode under another."""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from marquee.core.jobs.api_errors import JobApiErrorDetail
from marquee.core.jobs.contracts import FeatureArea, JobAction, TriggerKind
from marquee.core.jobs.pagination import (
    InvalidCursorError,
    cursor_contract,
    decode_cursor,
    encode_cursor,
)
from marquee.core.jobs.presentation import (
    PRESENTATION_SECTION_ADAPTER,
    SECTION_KINDS,
    VALUE_TYPES,
    DiagnosticLinks,
    Fact,
    FactsSection,
    JobPresentation,
    LinkValue,
    PresentationAction,
    PresentationAttention,
    PresentationSection,
    PresentationStatus,
    PresentationSubject,
    PresentationTrigger,
    PresentationValue,
    TextValue,
)


def _minimal_presentation(**overrides) -> JobPresentation:
    base = {
        "presenter_key": "jobs.system_noop",
        "presenter_version": 1,
        "job_id": "abc123",
        "job_type": "system_noop",
        "label": "System check",
        "label_key": "jobs.system_noop.label",
        "feature_area": FeatureArea.SYSTEM,
        "presentation_family": "system",
        "subject": PresentationSubject(
            kind="system_work", display_id="system:noop", display_name="System no-op"
        ),
        "action": PresentationAction(headline="Run a system no-op check"),
        "trigger": PresentationTrigger(kind=TriggerKind.SYSTEM, label="System"),
        "attention": PresentationAttention(),
        "status": PresentationStatus(
            phase="terminal",
            outcome="succeeded",
            label="Succeeded",
            label_key="jobs.status.succeeded",
            tone="positive",
        ),
        "links": DiagnosticLinks(
            detail="/projection-room/jobs/abc123",
            snapshot="/api/jobs/abc123/snapshot",
            presentation="/api/jobs/abc123/presentation",
            attempts="/api/jobs/abc123/attempts",
            events="/api/jobs/abc123/events",
            artifacts="/api/jobs/abc123/artifacts",
        ),
    }
    base.update(overrides)
    return JobPresentation(**base)


def test_section_vocabulary_is_frozen():
    kinds = {
        option.model_fields["kind"].default
        for option in TypeAdapter(PresentationSection).core_schema["schema"]["choices"].values()
        if hasattr(option, "model_fields")
    }
    expected = {
        "facts",
        "before_after",
        "change_list",
        "metric_cards",
        "warnings",
        "failures",
        "steps",
        "artifacts",
        "children",
        "notice",
    }
    assert expected == SECTION_KINDS
    if kinds:
        assert kinds == expected


def test_value_vocabulary_is_frozen():
    assert {
        "text",
        "number",
        "duration",
        "bytes",
        "timestamp",
        "boolean",
        "badge",
        "subject",
        "link",
    } == VALUE_TYPES
    adapter = TypeAdapter(PresentationValue)
    assert adapter.validate_python({"type": "text", "text": "hello"}) == TextValue(text="hello")
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "html", "html": "<b>no</b>"})


def test_presentation_rejects_extra_fields_and_unknown_section():
    presentation = _minimal_presentation()
    assert presentation.version == 1
    with pytest.raises(ValidationError):
        JobPresentation(**{**presentation.model_dump(), "raw_pgqueuer_row": {"id": 5}})
    with pytest.raises(ValidationError):
        PRESENTATION_SECTION_ADAPTER.validate_python({"kind": "raw_json", "data": {}})


def test_sections_round_trip_and_are_typed():
    section = FactsSection(
        facts=(Fact(label="Candidates", value={"type": "number", "value": 47.0}),)
    )
    presentation = _minimal_presentation(sections=(section,))
    dumped = presentation.model_dump(mode="json")
    restored = JobPresentation.model_validate(dumped)
    assert restored.sections[0].kind == "facts"
    assert restored.sections[0].facts[0].value.value == 47.0


def test_links_must_be_relative_application_paths():
    with pytest.raises(ValidationError):
        LinkValue(href="https://example.com/x", label="external")
    with pytest.raises(ValidationError):
        LinkValue(href="//example.com/x", label="protocol-relative")
    with pytest.raises(ValidationError):
        DiagnosticLinks(
            detail="javascript://alert",
            snapshot="/api/jobs/a/snapshot",
            presentation="/api/jobs/a/presentation",
            attempts="/api/jobs/a/attempts",
            events="/api/jobs/a/events",
            artifacts="/api/jobs/a/artifacts",
        )
    assert LinkValue(href="/api/jobs/a/artifacts/1", label="report").href.startswith("/")


def test_allowed_actions_are_unique_and_typed():
    with pytest.raises(ValidationError):
        _minimal_presentation(allowed_actions=(JobAction.CANCEL, JobAction.CANCEL))
    presentation = _minimal_presentation(allowed_actions=(JobAction.OPEN_DETAIL,))
    assert presentation.allowed_actions == (JobAction.OPEN_DETAIL,)


def test_cursor_round_trip_binds_to_contract():
    contract = cursor_contract(
        view="history", filters={"type": "poster_pipeline"}, sort="-terminal_at"
    )
    token = encode_cursor(contract=contract, key=("2026-07-13T00:00:00+00:00", "job42"))
    assert decode_cursor(token, contract=contract) == ("2026-07-13T00:00:00+00:00", "job42")

    other = cursor_contract(view="queue", filters={"type": "poster_pipeline"}, sort="-terminal_at")
    with pytest.raises(InvalidCursorError):
        decode_cursor(token, contract=other)


def test_cursor_rejects_malformed_and_oversized_tokens():
    contract = cursor_contract(view="queue", filters={}, sort="default")
    with pytest.raises(InvalidCursorError):
        decode_cursor("not-base64!!", contract=contract)
    with pytest.raises(InvalidCursorError):
        decode_cursor("", contract=contract)
    with pytest.raises(InvalidCursorError):
        decode_cursor("a" * 600, contract=contract)
    with pytest.raises(InvalidCursorError):
        encode_cursor(contract=contract, key=({"nested": "object"},))  # type: ignore[arg-type]


def test_cursor_contract_is_deterministic_and_filter_sensitive():
    a = cursor_contract(view="queue", filters={"b": 1, "a": None}, sort="default")
    b = cursor_contract(view="queue", filters={"b": 1}, sort="default")
    c = cursor_contract(view="queue", filters={"b": 2}, sort="default")
    assert a == b
    assert a != c


def test_error_detail_is_bounded_and_secret_free():
    detail = JobApiErrorDetail(
        code="stale_job_version",
        message="The job changed while the command was in flight.",
        job_id="abc123",
        current_phase="terminal",
        current_version=4,
    )
    assert detail.code == "stale_job_version"
    with pytest.raises(ValidationError):
        JobApiErrorDetail(code="BadCode", message="x")
    with pytest.raises(ValidationError):
        JobApiErrorDetail(code="oops", message="x", context={"api_token": "leak"})
    with pytest.raises(ValidationError):
        JobApiErrorDetail(code="oops", message="x", context={str(i): i for i in range(17)})


def test_compact_progress_accepts_snapshot_measurements():
    from marquee.core.jobs.presentation import CompactProgress
    from marquee.core.jobs.progress import ProgressMeasurement

    progress = CompactProgress(
        headline="Scanning TV poster candidates",
        stage_key="execute",
        stage_label="Analyzing samples",
        overall=ProgressMeasurement.determinate(
            scope_id="batch", unit="shows", completed=12, total=44
        ),
        current=ProgressMeasurement.indeterminate(scope_id="episode:7", label="sample 4"),
        updated_at=datetime.now(UTC),
        sequence=9,
    )
    assert progress.overall.percent == round(100 * 12 / 44, 4)
    with pytest.raises(ValidationError):
        CompactProgress(sequence=0)
