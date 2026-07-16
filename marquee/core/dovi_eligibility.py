"""Pure display policy for the bounded Dolby Vision conversion scope."""

from __future__ import annotations


def conversion_eligibility(profile: int | None, el_type: str | None) -> dict[str, object]:
    if profile is None:
        return {"eligible": False, "target": None, "kind": None, "reason": "Not analyzed yet."}
    if profile == 8:
        return {
            "eligible": False,
            "target": None,
            "kind": None,
            "reason": "Already a single-layer profile 8 stream — broadly compatible.",
        }
    if profile == 5:
        return {
            "eligible": True,
            "target": "8.1",
            "kind": "p5_to_p81",
            "reason": (
                "Profile 5 shows green/purple tints on non-DV hardware. Re-encode the "
                "base layer to BT.2020 and convert the RPU to profile 8.1 for compatibility."
            ),
        }
    if profile == 7:
        if el_type == "MEL":
            reason = (
                "Profile 7 MEL — the minimal enhancement layer carries no picture data, "
                "so it can be dropped losslessly and the RPU converted to profile 8.1."
            )
            eligible: bool | str = True
        elif el_type == "FEL":
            reason = (
                "Profile 7 FEL — the full enhancement layer can force real-time transcoding. "
                "Stripping it to profile 8.1 fixes playback but discards the FEL residual."
            )
            eligible = "lossy"
        else:
            reason = (
                "Profile 7 has an undetermined enhancement-layer type. Conversion drops the "
                "layer; run analysis with dovi_tool to confirm FEL versus MEL."
            )
            eligible = "lossy"
        return {
            "eligible": eligible,
            "target": "8.1",
            "kind": "p7_strip_el",
            "reason": reason,
        }
    return {
        "eligible": False,
        "target": None,
        "kind": None,
        "reason": f"Profile {profile} has no supported single-layer conversion.",
    }
