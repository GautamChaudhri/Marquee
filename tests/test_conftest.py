"""The guard that keeps the database fixture away from every database but its own."""

from __future__ import annotations

from collections.abc import Callable

import pytest


@pytest.mark.parametrize(
    "name",
    [
        "marquee",
        "postgres",
        "template1",
        "marquee_test_",
        "marquee_test",
        "marquee_tests_abc",
        "xmarquee_test_abc",
        "marquee_test_ABC",
        "marquee_test_abc; drop database marquee",
        "marquee_test_abc\0",
    ],
)
def test_the_fixture_refuses_any_other_database(
    test_database_name_check: Callable[[str], str], name: str
) -> None:
    with pytest.raises(ValueError, match="refusing to create or drop"):
        test_database_name_check(name)


def test_the_fixture_accepts_its_own_names(test_database_name_check: Callable[[str], str]) -> None:
    assert test_database_name_check("marquee_test_0a1b2c3d4e5f") == "marquee_test_0a1b2c3d4e5f"
