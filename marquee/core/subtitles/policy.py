"""Subtitle policy evaluation (design §21.2) — pure, filesystem-free logic.

Given a file's tracks + audio and a policy snapshot, decide which tracks to
remove, which to protect (and why), and which need human review. Conservative
by construction (§16.9): external files are never auto-removed unless the policy
opts in; forced/default/last-full-dialogue tracks are protected; unknown-language
tracks follow an explicit action. Returns coverage before/after so the caller
can warn about lost coverage. Heavily unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from marquee.core.subtitles import languages
from marquee.core.subtitles.coverage import compute_coverage, is_full_dialogue


@dataclass
class PolicyEvaluation:
    removals: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    review_required: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    coverage_before: dict = field(default_factory=dict)
    coverage_after: dict = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)

    @property
    def has_review(self) -> bool:
        return bool(self.review_required)

    @property
    def changes(self) -> bool:
        return bool(self.removals)


def _wanted_by_mode(lang: str, mode: str, langs: set[str]) -> bool:
    """Whether a language is kept by the mode (before protections)."""
    if mode == "allowlist":
        return lang in langs
    return lang not in langs  # blocklist: keep unless explicitly listed


def evaluate_policy(tracks: list[dict], audio: list[dict], policy: dict) -> PolicyEvaluation:
    """Evaluate *policy* against a file's *tracks*. Pure; no I/O."""
    mode = policy.get("mode", "blocklist")
    langs = {languages.normalize(x)[0] for x in (policy.get("languages") or [])}
    unknown_action = policy.get("unknown_action", "keep")
    protect_forced = policy.get("protect_forced", True)
    protect_default = policy.get("protect_default", True)
    protect_last_full = policy.get("protect_last_full_dialogue", True)
    include_external = policy.get("include_external", False)

    full_dialogue_total = sum(1 for t in tracks if is_full_dialogue(t))

    ev = PolicyEvaluation()
    ev.coverage_before = compute_coverage(tracks, audio)

    for track in tracks:
        tid = track["id"]
        lang = track.get("language_tag") or languages.UNDETERMINED

        # External sidecars are never auto-removed unless the policy opts in.
        if track.get("source") == "external" and not include_external:
            ev.protected.append(tid)
            ev.reasons[tid] = "external_protected"
            continue

        # Unknown-language tracks follow their explicit action.
        target_remove: bool
        if lang == languages.UNDETERMINED:
            if unknown_action == "keep":
                ev.protected.append(tid)
                ev.reasons[tid] = "unknown_kept"
                continue
            if unknown_action == "review":
                ev.review_required.append(tid)
                ev.reasons[tid] = "unknown_review"
                continue
            target_remove = True  # unknown_action == "remove"
        else:
            target_remove = not _wanted_by_mode(lang, mode, langs)

        if not target_remove:
            ev.protected.append(tid)
            ev.reasons[tid] = "kept_by_policy"
            continue

        # Removal candidate — apply protections.
        if protect_forced and track.get("is_forced"):
            ev.protected.append(tid)
            ev.reasons[tid] = "forced_protected"
            continue
        if protect_default and track.get("is_default"):
            ev.protected.append(tid)
            ev.reasons[tid] = "default_protected"
            continue
        if protect_last_full and is_full_dialogue(track) and full_dialogue_total <= 1:
            ev.protected.append(tid)
            ev.reasons[tid] = "last_full_dialogue_protected"
            continue

        ev.removals.append(tid)
        ev.reasons[tid] = "policy_remove"

    kept = [t for t in tracks if t["id"] not in set(ev.removals)]
    ev.coverage_after = compute_coverage(kept, audio)

    # Warnings.
    subtitle_total = len(tracks)
    if ev.removals and len(ev.removals) == subtitle_total:
        ev.warnings.append({"code": "all_subtitles_removed", "requires_override": True})
    lost = set(ev.coverage_before["full_dialogue_languages"]) - set(
        ev.coverage_after["full_dialogue_languages"]
    )
    if lost:
        ev.warnings.append(
            {"code": "full_dialogue_coverage_lost", "languages": sorted(lost), "requires_override": True}
        )
    if ev.review_required:
        ev.warnings.append({"code": "review_required", "count": len(ev.review_required)})

    return ev
