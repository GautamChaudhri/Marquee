"""Cold-start onboarding for the Key Art Engine (design 20).

A fresh install has no taste profile, so the pipeline cannot rank. This package
provides the guided "Rank Test" that seeds both engine layers:

  - Layer A (taste profile / k-NN exemplars) — from the user's library posters
    (``source="library"`` rebuild) or the bundled taste test's favorites.
  - Layer B (pairwise learned head) — from the within-movie rankings the user
    submits during the test.

The bulk of the work reuses existing machinery (taste rebuild, batch runner,
the ``action="rank"`` feedback flow, the pairwise trainer). This package adds
onboarding state, a stratified sampler, and the bundled taste test.
"""

from marquee.onboarding import service

__all__ = ["service"]
