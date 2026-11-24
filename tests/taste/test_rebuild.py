"""The taste profile registry and how a profile is published."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import TasteProfile
from marquee.ml.artifact_codec import unicode_array
from marquee.taste import rebuild


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


def _profile(path: Path, positives: int = 2, negatives: int = 0) -> str:
    payload = {
        "embeddings": np.ones((positives, 512), dtype=np.float32),
        "poster_names": unicode_array([f"P{n} (2000).png" for n in range(positives)]),
        "poster_sha256s": unicode_array([f"{n:x}" * 64 for n in range(positives)]),
    }
    if negatives:
        payload["neg_embeddings"] = np.ones((negatives, 512), dtype=np.float32)
        payload["neg_poster_names"] = unicode_array(
            [f"{'f' * 64}{n}.jpg" for n in range(negatives)]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **payload)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_read_profile_says_what_it_holds(tmp_path: Path) -> None:
    sha = _profile(tmp_path / "p.npz", positives=3, negatives=1)
    facts = rebuild.read_profile(tmp_path / "p.npz")
    assert (facts.sha256, facts.positives, facts.negatives) == (sha, 3, 1)
    assert facts.poster_names == ["P0 (2000).png", "P1 (2000).png", "P2 (2000).png"]
    assert len(facts.poster_sha256s) == 3 and len(facts.negative_names) == 1
    (tmp_path / "bad.npz").write_bytes(b"not a profile")
    with pytest.raises(rebuild.ProfileError):
        rebuild.read_profile(tmp_path / "bad.npz")


def test_keep_copy_and_install(tmp_path: Path, data_dir: Path) -> None:
    source = tmp_path / "built.npz"
    sha = _profile(source)
    name = rebuild.keep_copy(source, sha)
    assert name == f"{sha}.npz"
    assert (data_dir / "ml" / "profiles" / name).read_bytes() == source.read_bytes()
    assert rebuild.keep_copy(source, sha) == name

    destination = data_dir / "ml" / "taste_profile.clip-vit-b-32.npz"
    _profile(destination, positives=5)
    rebuild.install(name, sha, destination)
    assert rebuild.file_sha256(destination) == sha
    assert sorted(p.name for p in destination.parent.iterdir()) == [
        "profiles",
        "taste_profile.clip-vit-b-32.npz",
    ]

    (data_dir / "ml" / "profiles" / name).write_bytes(b"damaged")
    with pytest.raises(rebuild.ProfileError, match="does not hold its own bytes"):
        rebuild.install(name, sha, destination)
    assert rebuild.file_sha256(destination) == sha  # left as it was
    with pytest.raises(rebuild.ProfileError):
        rebuild.profile_copy("../x.npz")


@pytest.mark.db
async def test_register_keeps_one_active_profile(db_session: AsyncSession) -> None:
    def args(sha: str) -> dict:
        return {
            "sha256": sha,
            "revision": "b" * 64,
            "source": "rebuild",
            "positives": 2,
            "negatives": 0,
            "movie_ids": [3, 1, 3],
            "file_name": f"{sha}.npz",
        }

    first = await rebuild.register(db_session, **args("a" * 64))
    assert first.is_active and first.positive_movie_ids == [1, 3]
    second = await rebuild.register(db_session, **args("c" * 64))
    assert (first.is_active, second.is_active) == (False, True)
    again = await rebuild.register(db_session, **args("a" * 64))
    assert again is first and first.is_active and not second.is_active
    assert await rebuild.active_profile(db_session) is first
    assert await rebuild.profile_by_sha(db_session, "c" * 64) is second
    assert await rebuild.profile_by_sha(db_session, None) is None
    assert len((await db_session.scalars(select(TasteProfile))).all()) == 2
