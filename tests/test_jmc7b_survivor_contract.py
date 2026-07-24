import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from marquee.pipeline.runner import build_run_payload
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
    assert [candidate["orig_filename"] for candidate in payload["diagnostic_ledger"]["candidates"]] == [
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
    assert review["checksum"] == hashlib.sha256(
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
