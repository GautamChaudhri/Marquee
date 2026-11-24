from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import cv2
import numpy as np
import pytest

from marquee import cli
from marquee.library.folders import scan_movie_roots
from marquee.ml.artifact_codec import unicode_array
from marquee.ml.calibration import CALIB_NAMES_KEY, CALIB_VALUES_KEY
from marquee.ml.visual_features import standardize_width
from marquee.taste.seed import SeedError, SeedPaths, plan_seed, run_seed


def _image(width: int, height: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (height, width, 3), dtype=np.uint8)


def _movie(root: Path, quality: str, name: str, poster: np.ndarray | bytes | None, *extra: str):
    folder = root / quality / name
    folder.mkdir(parents=True)
    (folder / f"{name} {{imdbid-tt{sum(map(ord, name)):07d}}} - [Bluray-1080p].mkv").write_bytes(
        b""
    )
    if isinstance(poster, bytes):
        (folder / "poster.jpg").write_bytes(poster)
    elif poster is not None:
        assert cv2.imwrite(str(folder / "poster.jpg"), poster)
    for file_name in extra:
        assert cv2.imwrite(str(folder / file_name), _image(40, 60, 99))
    return folder


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "Movies"
    _movie(root, "1080p", "Alien (1979)", _image(800, 1200, 1))
    _movie(root, "1080p", "Narrow (2001)", _image(300, 450, 2))
    _movie(root, "1080p", "No Poster (2002)", None, "fanart.jpg")
    _movie(root, "1080p", "Broken (2003)", b"not a jpeg at all")
    alien_bytes = (root / "1080p" / "Alien (1979)" / "poster.jpg").read_bytes()
    _movie(root, "4K", "Alien Copy (1979)", alien_bytes)
    _movie(root, "4K", "Narrow (2001)", _image(600, 900, 3))
    name = "File Named (2024)"
    folder = _movie(root, "4K", name, None)
    video = next(folder.glob("*.mkv"))
    assert cv2.imwrite(str(video.with_suffix(".jpg")), _image(500, 750, 4))
    return root


@pytest.fixture
def paths(tmp_path: Path) -> SeedPaths:
    return SeedPaths(seed_dir=tmp_path / "data" / "ml" / "seed", profile=tmp_path / "profile.npz")


@pytest.fixture
def models(tmp_path: Path) -> dict[str, Path]:
    model = tmp_path / "model.onnx"
    model.write_bytes(b"m")
    return {"DINOv2": model, "YOLO person": model, "zero-shot axes": model}


class StubTrainer:
    """Stands in for trainer.rebuild_profile; records its call and writes a tiny profile."""

    def __init__(self, profile: Path, title_boxes: int = 1) -> None:
        self.profile = profile
        self.title_boxes = title_boxes
        self.calls: list[tuple[Path, dict[str, float], list[str]]] = []

    def __call__(self, training_dir: Path, weights: Mapping[str, float]) -> Path:
        files = sorted(p.name for p in training_dir.iterdir())
        self.calls.append((training_dir, dict(weights), files))
        n = len(files)
        title = np.full(n, np.nan)
        title[: self.title_boxes] = 0.2
        np.savez(
            self.profile,
            embeddings=np.ones((n, 512), dtype=np.float32),
            dino_embeddings=np.ones((n, 384), dtype=np.float32),
            **{
                CALIB_NAMES_KEY: unicode_array(["aesthetic", "title_height_frac"]),
                CALIB_VALUES_KEY: np.vstack([np.ones(n), title]),
            },
        )
        return self.profile


def _seed(library: Path, paths: SeedPaths, models: dict[str, Path], **kwargs):
    lines: list[str] = []
    trainer = kwargs.pop("trainer", StubTrainer(paths.profile))
    code = run_seed(
        roots=[library], paths=paths, models=models, build=trainer, echo=lines.append, **kwargs
    )
    return code, lines, trainer


def test_selection_rules(library: Path) -> None:
    plan = plan_seed(scan_movie_roots([library]))

    assert plan.scanned == 7
    assert [e.staged_name for e in plan.exemplars] == ["Alien (1979).png", "Narrow (2001).png"]
    assert {s.folder.name: s.reason for s in plan.skipped} == {
        "Broken (2003)": "poster.jpg does not decode",
        "No Poster (2002)": "no poster.jpg (holds 1 other image(s))",
        "File Named (2024)": "no poster.jpg (holds 1 other image(s))",
    }
    (duplicate,) = plan.duplicates
    assert duplicate.folder.name == "Alien Copy (1979)"
    assert duplicate.reason == "byte-identical to 1080p/Alien (1979)"
    (same_name,) = plan.same_name
    assert (same_name.folder.quality, same_name.folder.name) == ("4K", "Narrow (2001)")
    assert [f.name for f in plan.file_named] == ["File Named (2024)"]
    assert [e.folder.name for e in plan.exemplars if e.enlarged] == ["Narrow (2001)"]


def test_seed_stages_lossless_500px_pngs_and_builds(
    library: Path, paths: SeedPaths, models: dict[str, Path]
) -> None:
    code, lines, trainer = _seed(library, paths, models)

    assert code == 0
    staged = sorted(paths.staged_dir.iterdir())
    assert [p.name for p in staged] == ["Alien (1979).png", "Narrow (2001).png"]
    for path in staged:
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert cv2.imread(str(path)).shape[1] == 500
    source = cv2.imread(str(library / "1080p" / "Alien (1979)" / "poster.jpg"))
    np.testing.assert_array_equal(
        cv2.imread(str(paths.staged_dir / "Alien (1979).png")), standardize_width(source)
    )
    ((training_dir, weights, files),) = trainer.calls
    assert training_dir == paths.staged_dir
    assert weights == {"Alien (1979).png": 1.0, "Narrow (2001).png": 1.0}
    assert files == ["Alien (1979).png", "Narrow (2001).png"]
    assert "title box     1 of 2 exemplars (a finite title_height_frac)" in lines
    assert any(line.startswith("profile") and "DINOv2 embeddings: yes" in line for line in lines)
    assert not list(paths.seed_dir.glob(".staging-*"))


def test_seed_record(library: Path, paths: SeedPaths, models: dict[str, Path]) -> None:
    _seed(library, paths, models)

    record = json.loads(paths.record.read_text())
    alien = record["exemplars"][0]
    assert alien["staged_name"] == "Alien (1979).png"
    assert alien["source_path"] == str(library / "1080p" / "Alien (1979)" / "poster.jpg")
    assert len(alien["source_sha256"]) == 64
    assert (alien["source_width"], alien["source_height"]) == (800, 1200)
    assert (alien["quality"], alien["title"], alien["year"], alien["weight"]) == (
        "1080p",
        "Alien",
        1979,
        1.0,
    )
    assert alien["imdb_id"].startswith("tt")
    assert {s["folder"] for s in record["skipped"]} == {
        "Broken (2003)",
        "No Poster (2002)",
        "File Named (2024)",
        "Alien Copy (1979)",
        "Narrow (2001)",
    }
    assert record["seeded_at"].endswith("+00:00")


def test_dry_run_writes_nothing(
    library: Path, paths: SeedPaths, models: dict[str, Path], tmp_path: Path
) -> None:
    before = sorted(p for p in tmp_path.rglob("*"))

    code, lines, trainer = _seed(library, paths, models, dry_run=True)

    assert code == 0
    assert trainer.calls == []
    assert sorted(p for p in tmp_path.rglob("*")) == before
    assert lines[-1].startswith("dry run: nothing written")
    assert "exemplars     2 at weight 1.0" in lines


def test_refuses_when_a_staged_set_exists(
    library: Path, paths: SeedPaths, models: dict[str, Path]
) -> None:
    _seed(library, paths, models)

    with pytest.raises(SeedError, match="--reuse.*--rescan"):
        _seed(library, paths, models)


def test_rescan_replaces_the_staged_set(
    library: Path, paths: SeedPaths, models: dict[str, Path]
) -> None:
    _seed(library, paths, models)
    (paths.staged_dir / "stale.png").write_bytes(b"old")
    _movie(library, "1080p", "Zulu (1964)", _image(500, 750, 5))

    code, _, trainer = _seed(library, paths, models, mode="rescan")

    assert code == 0
    assert sorted(p.name for p in paths.staged_dir.iterdir()) == [
        "Alien (1979).png",
        "Narrow (2001).png",
        "Zulu (1964).png",
    ]
    assert len(json.loads(paths.record.read_text())["exemplars"]) == 3


def test_reuse_rebuilds_without_reading_the_library(
    library: Path, paths: SeedPaths, models: dict[str, Path], tmp_path: Path
) -> None:
    _seed(library, paths, models)

    code, lines, trainer = _seed(tmp_path / "no-such-root", paths, models, mode="reuse")

    assert code == 0
    ((_, weights, _),) = trainer.calls
    assert weights == {"Alien (1979).png": 1.0, "Narrow (2001).png": 1.0}
    assert lines[0].startswith("reusing       2 staged exemplars")


def test_reuse_without_a_staged_set_fails(
    paths: SeedPaths, models: dict[str, Path], tmp_path: Path
) -> None:
    with pytest.raises(SeedError, match="no staged set"):
        _seed(tmp_path, paths, models, mode="reuse")


def test_refuses_without_the_style_models(
    library: Path, paths: SeedPaths, models: dict[str, Path], tmp_path: Path
) -> None:
    models["YOLO person"] = tmp_path / "absent.onnx"

    with pytest.raises(SeedError, match="missing YOLO person; run `marquee models fetch`"):
        _seed(library, paths, models)
    assert not paths.seed_dir.exists()


def test_dry_run_cannot_reuse(library: Path, paths: SeedPaths, models: dict[str, Path]) -> None:
    with pytest.raises(SeedError, match="--reuse"):
        _seed(library, paths, models, mode="reuse", dry_run=True)


def test_cli_rescan_and_reuse_are_exclusive() -> None:
    with pytest.raises(SystemExit) as exc:
        cli._build_parser().parse_args(["taste", "seed", "--rescan", "--reuse"])
    assert exc.value.code == 2


def test_cli_reports_a_refusal_in_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marquee.taste import seed

    def refuse(**_kwargs: object) -> int:
        raise SeedError("a staged set already exists")

    monkeypatch.setattr(seed, "run_seed", refuse)
    monkeypatch.setattr(cli, "_exemplar_refusal", lambda: None)  # no exemplar records

    assert cli.main(["taste", "seed"]) == 1
    assert capsys.readouterr().err.strip() == "marquee taste seed: a staged set already exists"


# ── marquee taste import-seed ──────────────────────────────────────────────

STAGED = {"Alien (1979).png": "tt0078748", "Heat (1995).png": "tt0113277"}


def _staged_seed(paths: SeedPaths, *, names: dict[str, str] = STAGED) -> dict[str, str]:
    """A staged set, its record, and the profile built from it; returns name -> hash."""
    import hashlib

    paths.staged_dir.mkdir(parents=True)
    hashes = {}
    for n, name in enumerate(sorted(names)):
        assert cv2.imwrite(str(paths.staged_dir / name), _image(500, 750, n))
        hashes[name] = hashlib.sha256((paths.staged_dir / name).read_bytes()).hexdigest()
    record = {
        "exemplars": [
            {"staged_name": name, "imdb_id": imdb, "weight": 1.0} for name, imdb in names.items()
        ]
    }
    paths.record.write_text(json.dumps(record))
    np.savez(
        paths.profile,
        embeddings=np.ones((len(names), 512), dtype=np.float32),
        poster_names=unicode_array(sorted(names)),
        poster_sha256s=unicode_array([hashes[name] for name in sorted(names)]),
    )
    return hashes


async def _movies(session, *imdb_ids: str) -> dict[str, int]:
    from datetime import UTC, datetime

    from marquee.db.models import Movie

    ids = {}
    for n, imdb in enumerate(imdb_ids, start=1):
        movie = Movie(
            radarr_id=n,
            title=f"Movie {n}",
            sort_title=f"movie {n}",
            radarr_path=f"/radarr/Movie {n}",
            imdb_id=imdb,
            has_file=True,
            last_seen_at=datetime.now(UTC),
        )
        session.add(movie)
        await session.flush()
        ids[imdb] = movie.id
    return ids


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from marquee.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


@pytest.mark.db
async def test_import_seed_records_every_staged_poster_and_registers_the_profile(
    db_session, paths: SeedPaths, data_dir: Path
) -> None:
    import hashlib

    from sqlalchemy import select

    from marquee.db.models import Exemplar, TasteProfile
    from marquee.taste import exemplars
    from marquee.taste.seed import import_seed

    hashes = _staged_seed(paths)
    ids = await _movies(db_session, *STAGED.values())
    profile_bytes = paths.profile.read_bytes()

    report = await import_seed(db_session, paths=paths)
    await db_session.commit()

    assert report.problems == [] and report.created == 2 and not report.already
    rows = list(await db_session.scalars(select(Exemplar).order_by(Exemplar.staged_name)))
    assert [(r.movie_id, r.staged_name, r.sha256, r.weight) for r in rows] == [
        (ids["tt0078748"], "Alien (1979).png", hashes["Alien (1979).png"], 1.0),
        (ids["tt0113277"], "Heat (1995).png", hashes["Heat (1995).png"], 1.0),
    ]
    assert {(r.polarity, r.source, r.state, r.event_id, r.source_file) for r in rows} == {
        ("positive", "seed", "active", None, None)
    }
    for row in rows:
        assert (
            exemplars.read_kept(row.file_name, row.sha256)
            == (paths.staged_dir / row.staged_name).read_bytes()
        )
    (profile,) = (await db_session.scalars(select(TasteProfile))).all()
    sha = hashlib.sha256(profile_bytes).hexdigest()
    assert (profile.sha256, profile.source, profile.positives, profile.negatives) == (
        sha,
        "seed",
        2,
        0,
    )
    assert profile.is_active and profile.positive_movie_ids == sorted(ids.values())
    assert profile.revision == exemplars.revision_of(rows) == report.revision
    assert (data_dir / "ml" / "profiles" / f"{sha}.npz").read_bytes() == profile_bytes
    assert paths.profile.read_bytes() == profile_bytes  # never touched

    again = await import_seed(db_session, paths=paths)
    assert again.already and again.created == 0 and again.problems == []
    assert again.profile_id == profile.id
    assert len((await db_session.scalars(select(Exemplar))).all()) == 2


@pytest.mark.db
async def test_import_seed_writes_nothing_when_anything_does_not_match(
    db_session, paths: SeedPaths, data_dir: Path
) -> None:
    from sqlalchemy import func, select

    from marquee.db.models import Exemplar, TasteProfile
    from marquee.taste.seed import import_seed

    _staged_seed(paths)
    await _movies(db_session, "tt0078748")  # Heat is missing
    np.savez(  # and the profile holds one exemplar more than the record lists
        paths.profile,
        embeddings=np.ones((3, 512), dtype=np.float32),
        poster_names=unicode_array(["Alien (1979).png", "Heat (1995).png", "Other.png"]),
    )

    report = await import_seed(db_session, paths=paths)

    assert report.created == 0
    assert report.problems == [
        "Heat (1995).png: no movie has IMDb id tt0113277",
        "the profile holds 3 exemplars and movies.json lists 2",
        "the profile's exemplars are not the files movies.json lists",
    ]
    assert await db_session.scalar(select(func.count()).select_from(Exemplar)) == 0
    assert await db_session.scalar(select(func.count()).select_from(TasteProfile)) == 0
    assert not (data_dir / "ml" / "exemplars").exists()


@pytest.mark.db
async def test_import_seed_notices_a_staged_file_that_changed(
    db_session, paths: SeedPaths, data_dir: Path
) -> None:
    from marquee.taste.seed import import_seed

    _staged_seed(paths)
    await _movies(db_session, *STAGED.values())
    assert cv2.imwrite(str(paths.staged_dir / "Heat (1995).png"), _image(500, 750, 9))

    report = await import_seed(db_session, paths=paths)

    assert report.problems == [
        "Heat (1995).png: the staged file is not the one the profile was built from"
    ]
