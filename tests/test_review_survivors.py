import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from marquee.pipeline.runner import (
    NEUTRAL_REVIEW_ORDER,
    SCORED_REVIEW_ORDER,
    build_run_payload,
)
from marquee.pipeline.types import CandidateScore, FeatureVector


def _features() -> FeatureVector:
    return FeatureVector(
        knn_sim=0.0,
        aesthetic=0.8,
        title_colorfulness=0.5,
        text_residual=0.0,
        resolution=1.0,
        sharpness=0.8,
        face_area=0.0,
        provenance=0.5,
        lang_match=1.0,
    )


def test_run_payload_separates_diagnostics_from_ordered_review_survivors() -> None:
    accepted = CandidateScore(
        image_path=Path("accepted.jpg"),
        orig_filename="accepted.jpg",
        features=_features(),
        gate_decision="passed",
    )
    rejected = CandidateScore(
        image_path=Path("rejected.jpg"),
        orig_filename="rejected.jpg",
        features=_features(),
        gate_decision="gated",
        rejection_reason="ocr_residual_text",
    )

    payload = build_run_payload(
        movie=SimpleNamespace(id=1, title="Fixture", tmdb_id=2),
        started_at="2026-07-23T00:00:00+00:00",
        status="completed",
        timings={},
        records={"accepted.jpg": accepted, "rejected.jpg": rejected},
        total_duration=0.0,
        run_id="a" * 32,
        review_survivors=[accepted],
    )

    assert "candidates" not in payload
    assert [
        candidate["orig_filename"] for candidate in payload["diagnostic_ledger"]["candidates"]
    ] == [
        "accepted.jpg",
        "rejected.jpg",
    ]
    review = payload["review"]
    assert review["order_algorithm"] == "source_family_round_robin_sha256_v1"
    assert review["survivors"] == [
        {
            "candidate_id": "f95b365339e21c61d3d1c93596734001848282849f5dfbc41f2327a7c7ca10c3",
            "reference": "accepted.jpg",
            "position": 0,
            "objective_eligible": True,
        }
    ]


@pytest.mark.parametrize(
    "reason",
    [
        "resolution_floor",
        "style_aesthetic_floor",
        "ocr_residual_text",
        "detail_feature_error",
    ],
)
def test_run_payload_rejects_each_objective_rejection_from_review(reason: str) -> None:
    rejected = CandidateScore(
        image_path=Path("rejected.jpg"),
        orig_filename="rejected.jpg",
        features=_features(),
        gate_decision="gated",
        rejection_reason=reason,
    )

    with pytest.raises(ValueError, match="every objective gate"):
        build_run_payload(
            movie=SimpleNamespace(id=1, title="Fixture", tmdb_id=2),
            started_at="2026-07-23T00:00:00+00:00",
            status="completed",
            timings={},
            records={"rejected.jpg": rejected},
            total_duration=0.0,
            run_id="a" * 32,
            review_survivors=[rejected],
        )


def test_review_order_preserves_the_explicit_neutral_survivor_order() -> None:
    first = CandidateScore(
        image_path=Path("zebra.jpg"),
        orig_filename="zebra.jpg",
        features=_features(),
        gate_decision="passed",
    )
    second = CandidateScore(
        image_path=Path("alpha.jpg"),
        orig_filename="alpha.jpg",
        features=_features(),
        gate_decision="passed",
    )

    payload = build_run_payload(
        movie=SimpleNamespace(id=1, title="Fixture", tmdb_id=2),
        started_at="2026-07-23T00:00:00+00:00",
        status="completed",
        timings={},
        records={"alpha.jpg": second, "zebra.jpg": first},
        total_duration=0.0,
        run_id="b" * 32,
        review_survivors=[first, second],
    )

    assert [item["reference"] for item in payload["review"]["survivors"]] == [
        "zebra.jpg",
        "alpha.jpg",
    ]


def test_artifact_attachment_truncates_only_after_survivor_order(tmp_path: Path) -> None:
    # Import through the standard handler graph so the delivery registry has
    # completed its intentional circular bootstrap before this private helper.
    from marquee.core.jobs.handlers_posters import execute_poster_pipeline
    from marquee.core.jobs.poster_pipeline import _attach_candidate_artifacts

    assert callable(execute_poster_pipeline)
    survivors = [
        {
            "candidate_id": f"{index:064x}",
            "reference": f"candidate-{index:03}.jpg",
            "position": index,
            "objective_eligible": True,
        }
        for index in range(101)
    ]
    document = {
        "review": {
            "version": 1,
            "order_algorithm": "source_family_round_robin_sha256_v1",
            "survivors": survivors,
            "eligible_count": len(survivors),
            "archived_count": 0,
            "truncated_count": 0,
        }
    }
    (tmp_path / "run.json").write_text(json.dumps(document), encoding="utf-8")
    artifacts = {
        item["reference"]: SimpleNamespace(
            id=index + 1,
            checksum=hashlib.sha256(item["reference"].encode()).hexdigest(),
            storage_key=f"test/{index}.jpg",
        )
        for index, item in enumerate(survivors)
    }

    _attach_candidate_artifacts(tmp_path, artifacts)

    persisted = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    review = persisted["review"]
    assert [item["reference"] for item in review["survivors"]] == [
        f"candidate-{index:03}.jpg" for index in range(100)
    ]
    assert review["eligible_count"] == 101
    assert review["archived_count"] == 100
    assert review["truncated_count"] == 1
    assert (
        review["checksum"]
        == hashlib.sha256(
            json.dumps(
                {
                    "version": review["version"],
                    "order_algorithm": review["order_algorithm"],
                    "survivors": review["survivors"],
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )


def test_scored_runs_still_archive_survivors_under_a_ranked_order_label() -> None:
    """Candidate images are registered from the survivor list, in every scoring mode.

    Emptying it for personalized runs left the review UI with scores and no
    pictures, so the list is always archived; the order label is what tells a
    neutral review apart from a ranked one.
    """
    ranked = [
        CandidateScore(
            image_path=Path(f"poster_{index}.jpg"),
            orig_filename=f"poster_{index}.jpg",
            features=_features(),
            gate_decision="passed",
            rank=index + 1,
        )
        for index in range(3)
    ]

    payload = build_run_payload(
        movie=SimpleNamespace(id=1, title="Fixture", tmdb_id=2),
        started_at="2026-07-28T00:00:00+00:00",
        status="completed",
        timings={},
        records={record.orig_filename: record for record in ranked},
        total_duration=1.0,
        run_id="scoredrun0001",
        review_survivors=ranked,
        review_order_algorithm=SCORED_REVIEW_ORDER,
    )

    review = payload["review"]
    assert review["order_algorithm"] == SCORED_REVIEW_ORDER
    assert review["eligible_count"] == 3
    assert [survivor["reference"] for survivor in review["survivors"]] == [
        "poster_0.jpg",
        "poster_1.jpg",
        "poster_2.jpg",
    ]
    assert [survivor["position"] for survivor in review["survivors"]] == [0, 1, 2]


def test_review_evidence_collects_rejects_without_touching_survivors(tmp_path: Path) -> None:
    """Rejected candidates are archived for display, never for eligibility."""
    accepted_path = tmp_path / "accepted.jpg"
    accepted_path.write_bytes(b"accepted")
    rejected_path = tmp_path / "rejected.jpg"
    rejected_path.write_bytes(b"rejected")

    accepted = CandidateScore(
        image_path=accepted_path,
        orig_filename="accepted.jpg",
        features=_features(),
        gate_decision="passed",
        rank=1,
        original_download=True,
    )
    rejected = CandidateScore(
        image_path=rejected_path,
        orig_filename="rejected.jpg",
        features=_features(),
        gate_decision="gated",
        rejection_reason="ocr_residual_text",
    )
    # Gated on TMDB metadata before any download — no bytes ever existed.
    never_downloaded = CandidateScore(
        image_path=tmp_path / "missing.jpg",
        orig_filename="missing.jpg",
        gate_decision="gated",
        rejection_reason="resolution_floor",
    )

    payload = build_run_payload(
        movie=SimpleNamespace(id=1, title="Fixture", tmdb_id=2),
        started_at="2026-07-29T00:00:00+00:00",
        status="completed",
        timings={},
        records={
            "accepted.jpg": accepted,
            "rejected.jpg": rejected,
            "missing.jpg": never_downloaded,
        },
        total_duration=0.0,
        run_id="c" * 32,
        review_survivors=[accepted],
        review_order_algorithm=SCORED_REVIEW_ORDER,
    )

    assert [item["reference"] for item in payload["review"]["survivors"]] == ["accepted.jpg"]
    evidence = payload["review_evidence"]["candidates"]
    assert [item["reference"] for item in evidence] == ["rejected.jpg"]
    assert evidence[0]["objective_eligible"] is False
    assert evidence[0]["original_download"] is False


def test_review_evidence_is_bounded(tmp_path: Path) -> None:
    from marquee.pipeline.runner import MAX_REVIEW_EVIDENCE, build_review_evidence

    records = {}
    for index in range(MAX_REVIEW_EVIDENCE + 25):
        name = f"reject-{index:03}.jpg"
        path = tmp_path / name
        path.write_bytes(b"x")
        records[name] = CandidateScore(
            image_path=path,
            orig_filename=name,
            gate_decision="gated",
            rejection_reason="ocr_residual_text",
        )

    evidence = build_review_evidence(records)

    assert len(evidence) == MAX_REVIEW_EVIDENCE
    assert [item["position"] for item in evidence] == list(range(MAX_REVIEW_EVIDENCE))


def test_evidence_attachment_leaves_the_survivor_checksum_alone(tmp_path: Path) -> None:
    """Evidence must not be able to change what the survivor checksum certifies."""
    from marquee.core.jobs.handlers_posters import execute_poster_pipeline
    from marquee.core.jobs.poster_pipeline import _attach_candidate_artifacts

    assert callable(execute_poster_pipeline)
    document = {
        "review": {
            "version": 1,
            "order_algorithm": SCORED_REVIEW_ORDER,
            "survivors": [
                {
                    "candidate_id": "a" * 64,
                    "reference": "kept.jpg",
                    "position": 0,
                    "objective_eligible": True,
                }
            ],
            "eligible_count": 1,
            "archived_count": 0,
            "truncated_count": 0,
        },
        "review_evidence": {
            "version": 1,
            "candidates": [{"reference": "tossed.jpg", "position": 0, "objective_eligible": False}],
            "archived_count": 0,
            "truncated_count": 0,
        },
    }
    (tmp_path / "run.json").write_text(json.dumps(document), encoding="utf-8")

    def _artifact(index: int, reference: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=index,
            checksum=hashlib.sha256(reference.encode()).hexdigest(),
            storage_key=f"test/{reference}",
        )

    _attach_candidate_artifacts(
        tmp_path,
        {"kept.jpg": _artifact(1, "kept.jpg")},
        {"tossed.jpg": _artifact(2, "tossed.jpg")},
    )

    persisted = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    review = persisted["review"]
    evidence = persisted["review_evidence"]["candidates"]
    assert evidence[0]["artifact_id"] == 2
    assert evidence[0]["objective_eligible"] is False
    assert [item["reference"] for item in review["survivors"]] == ["kept.jpg"]
    assert (
        review["checksum"]
        == hashlib.sha256(
            json.dumps(
                {
                    "version": review["version"],
                    "order_algorithm": review["order_algorithm"],
                    "survivors": review["survivors"],
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )


def test_review_order_defaults_to_neutral() -> None:
    """The default must stay the onboarding-safe ordering."""
    payload = build_run_payload(
        movie=SimpleNamespace(id=1, title="Fixture", tmdb_id=2),
        started_at="2026-07-28T00:00:00+00:00",
        status="completed",
        timings={},
        records={},
        total_duration=1.0,
        run_id="defaultrun0001",
    )
    assert payload["review"]["order_algorithm"] == NEUTRAL_REVIEW_ORDER


def test_oversized_single_run_archive_is_reported_rather_than_silently_skipped(
    tmp_path: Path,
) -> None:
    """Skipping attachment leaves every survivor image 404ing in review.

    It used to `return` silently, so the run looked clean and its posters were
    simply dead. The caller now records the reason as a run warning.
    """
    from marquee.core.jobs.poster_pipeline import (
        MAX_RUN_ARCHIVE_BYTES,
        _attach_candidate_artifacts,
    )

    document = {
        "review": {"version": 1, "survivors": [], "archived_count": 0, "truncated_count": 0},
        "diagnostics": ["x" * (MAX_RUN_ARCHIVE_BYTES + 1024)],
    }
    (tmp_path / "run.json").write_text(json.dumps(document), encoding="utf-8")

    skipped = _attach_candidate_artifacts(tmp_path, {})
    assert skipped is not None
    assert "over the" in skipped


def test_large_catalogue_single_run_archive_still_attaches(tmp_path: Path) -> None:
    """A 2 MB archive is ordinary for a title with a big poster catalogue."""
    from marquee.core.jobs.poster_pipeline import _attach_candidate_artifacts

    reference = "candidate-000.jpg"
    document = {
        "review": {
            "version": 1,
            "order_algorithm": NEUTRAL_REVIEW_ORDER,
            "survivors": [
                {
                    "candidate_id": f"{0:064x}",
                    "reference": reference,
                    "position": 0,
                    "objective_eligible": True,
                }
            ],
            "eligible_count": 1,
            "archived_count": 0,
            "truncated_count": 0,
        },
        "diagnostics": ["x" * (2 * 1024 * 1024)],
    }
    path = tmp_path / "run.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert path.stat().st_size > 1024 * 1024

    artifacts = {
        reference: SimpleNamespace(
            id=1, checksum=hashlib.sha256(reference.encode()).hexdigest(), storage_key="test/0.jpg"
        )
    }
    assert _attach_candidate_artifacts(tmp_path, artifacts) is None

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["review"]["survivors"][0]["artifact_id"] == 1
