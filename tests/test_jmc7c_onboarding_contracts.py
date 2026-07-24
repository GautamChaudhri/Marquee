"""JMC7C public onboarding API contract closure."""

from __future__ import annotations

from pathlib import Path

from marquee.main import app


def _success_schema(path: str, method: str) -> dict[str, object]:
    document = app.openapi()
    return document["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]


def test_every_public_onboarding_success_response_is_a_named_openapi_schema() -> None:
    expected = {
        ("/api/onboarding/status", "get"): "OnboardingStatusResponse",
        ("/api/onboarding/start", "post"): "OnboardingStartResponse",
        ("/api/onboarding/runs/{run_id}/review", "get"): "OnboardingReviewResponse",
        ("/api/onboarding/choose", "post"): "OnboardingDecisionResponse",
        ("/api/onboarding/hate", "post"): "OnboardingDecisionResponse",
        ("/api/onboarding/complete", "post"): "OnboardingCompletionResponse",
    }

    for (path, method), model_name in expected.items():
        schema = _success_schema(path, method)
        assert schema == {"$ref": f"#/components/schemas/{model_name}"}


def test_onboarding_browser_client_uses_generated_contract_types() -> None:
    root = Path(__file__).resolve().parents[1]
    client = (root / "frontend/src/lib/api/onboarding.ts").read_text()
    legacy_types = (root / "frontend/src/lib/api/types.ts").read_text()

    assert "./generated/openapi" in client
    assert "interface StartResult" not in client
    assert "OnboardingDecisionIntent = {" not in client
    assert "OnboardingStatus" not in legacy_types
    assert "OnboardingReview" not in legacy_types
