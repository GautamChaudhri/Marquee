"""B2 typed audio/subtitle family document contracts (JMC5B B04/B13).

A02 forbids the generic built-in documents on an enabled mutating definition, so
these prove the family requests are typed, bounded, and free of execution policy,
and that a result cannot claim a published change without the authoritative
post-operation inventory.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marquee.core.jobs.audio_subtitle_documents import (
    AudioReorderRequestV1,
    MediaTrackMutationResultV1,
    SubtitleMetadataEditV1,
    SubtitleMetadataRequestV1,
    TrackRemoveRequestV1,
)
from marquee.core.jobs.mutation_documents import (
    MutationAtomicityV1,
    MutationJobOutcome,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
)
from marquee.core.jobs.track_selectors import TrackFactsV1, build_inventory, selector_for


def _facts(kind: str = "audio", language: str = "en", **kwargs) -> TrackFactsV1:
    return TrackFactsV1(
        kind=kind,
        source="embedded",
        language_tag=language,
        codec="ac3" if kind == "audio" else "subrip",
        channels=6 if kind == "audio" else None,
        **kwargs,
    )


def _inventory(*facts: TrackFactsV1):
    return build_inventory(
        signature="sha256:src", tracks=[(f, i, i) for i, f in enumerate(facts)]
    )


def _selectors(inventory):
    return [selector_for(entry, inventory) for entry in inventory.entries]


def test_remove_request_is_typed_bounded_and_policy_free() -> None:
    inventory = _inventory(_facts(), _facts(language="fr"))
    request = TrackRemoveRequestV1(media_file_id=9, selectors=tuple(_selectors(inventory)))
    dumped = request.model_dump(mode="json")
    # No container, tool arguments, path, or execution policy may be accepted.
    assert set(dumped) == {"media_file_id", "selectors"}
    assert "/" not in str(dumped["selectors"])


def test_remove_request_rejects_duplicate_and_empty_targets() -> None:
    inventory = _inventory(_facts())
    selector = _selectors(inventory)[0]
    with pytest.raises(ValidationError, match="only be requested once"):
        TrackRemoveRequestV1(media_file_id=1, selectors=(selector, selector))
    with pytest.raises(ValidationError):
        TrackRemoveRequestV1(media_file_id=1, selectors=())


def test_reorder_requires_complete_unique_audio_order() -> None:
    inventory = _inventory(_facts(), _facts(language="fr"))
    selectors = _selectors(inventory)
    assert AudioReorderRequestV1(
        media_file_id=2, ordered_selectors=(selectors[1], selectors[0])
    ).ordered_selectors[0].facts.language_tag == "fr"

    with pytest.raises(ValidationError, match="only appear once"):
        AudioReorderRequestV1(media_file_id=2, ordered_selectors=(selectors[0], selectors[0]))
    with pytest.raises(ValidationError):
        AudioReorderRequestV1(media_file_id=2, ordered_selectors=(selectors[0],))

    subtitle_inventory = _inventory(_facts(kind="subtitle"), _facts(kind="subtitle", language="fr"))
    with pytest.raises(ValidationError, match="only audio tracks"):
        AudioReorderRequestV1(
            media_file_id=2, ordered_selectors=tuple(_selectors(subtitle_inventory))
        )


def test_metadata_edit_requires_a_change_and_only_subtitles() -> None:
    subtitle = _inventory(_facts(kind="subtitle"))
    audio = _inventory(_facts())
    subtitle_selector = _selectors(subtitle)[0]

    with pytest.raises(ValidationError, match="at least one change"):
        SubtitleMetadataEditV1(selector=subtitle_selector)

    edit = SubtitleMetadataEditV1(selector=subtitle_selector, is_forced=True)
    assert SubtitleMetadataRequestV1(media_file_id=4, edits=(edit,)).edits[0].is_forced is True

    with pytest.raises(ValidationError, match="only subtitle tracks"):
        SubtitleMetadataRequestV1(
            media_file_id=4,
            edits=(SubtitleMetadataEditV1(selector=_selectors(audio)[0], is_default=True),),
        )


def test_metadata_request_rejects_editing_one_track_twice() -> None:
    inventory = _inventory(_facts(kind="subtitle"))
    selector = _selectors(inventory)[0]
    edits = (
        SubtitleMetadataEditV1(selector=selector, is_forced=True),
        SubtitleMetadataEditV1(selector=selector, is_default=True),
    )
    with pytest.raises(ValidationError, match="only be edited once"):
        SubtitleMetadataRequestV1(media_file_id=4, edits=edits)


def _result(*, bytes_changed: bool, actual) -> MediaTrackMutationResultV1:
    inventory = _inventory(_facts())
    target = MutationTargetV1(key="audio:x:0", kind="track", label="Audio", operation="remove")
    outcome = MutationTargetOutcomeV1(
        target=target,
        status=MutationTargetStatus.SUCCEEDED if bytes_changed else MutationTargetStatus.SKIPPED,
        stage="rescan",
        reason_code="applied" if bytes_changed else "not_selected",
        message="ok",
        bytes_changed=bytes_changed,
        product_state_changed=bytes_changed,
    )
    return MediaTrackMutationResultV1(
        outcome=MutationJobOutcome.SUCCEEDED if bytes_changed else MutationJobOutcome.NO_CHANGE,
        reason_code="applied" if bytes_changed else "nothing_selected",
        message="done",
        requested_targets=(target,),
        target_outcomes=(outcome,),
        validation=MutationValidationV1(verdict="passed"),
        atomicity=MutationAtomicityV1(
            group_id="g1",
            boundary="all_or_nothing",
            published=bytes_changed,
            rollback_available=False,
            uncertain_state=False,
        ),
        before_inventory=inventory,
        actual_inventory=actual,
    )


def test_published_change_requires_an_authoritative_actual_inventory() -> None:
    """B13: expected deltas alone can never declare success."""
    with pytest.raises(ValidationError, match="actual inventory"):
        _result(bytes_changed=True, actual=None)
    assert _result(bytes_changed=True, actual=_inventory(_facts())).actual_inventory is not None


def test_no_change_result_needs_no_actual_inventory() -> None:
    result = _result(bytes_changed=False, actual=None)
    assert result.outcome is MutationJobOutcome.NO_CHANGE
    assert result.actual_inventory is None
