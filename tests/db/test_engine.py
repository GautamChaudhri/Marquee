from __future__ import annotations

from marquee.db.engine import create_engine, create_session_factory


async def test_engine_uses_psycopg_and_masks_the_password() -> None:
    engine = create_engine("postgresql+psycopg://user:s3cret-value@db.invalid:5432/marquee")
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.dialect.is_async
        assert "s3cret-value" not in repr(engine)
        assert "s3cret-value" not in str(engine.url)
        factory = create_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False
    finally:
        await engine.dispose()
