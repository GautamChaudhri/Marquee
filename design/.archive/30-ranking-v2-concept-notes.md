# Ranking v2 — Concept Notes (supersedes design/29 Group C)

> Status: high-concept discussion, not yet a build plan. design/29's Group C section
> proposed collapsing a drag-and-drop order into a single favorites tier — that's wrong
> (confirmed by reading `head_trainer.py`: ties within a tier generate zero training
> pairs). This doc captures the replacement direction worked out in conversation.
> design/29 itself couldn't be edited in place — it's owned by a different local user
> (`cptbandit`, mode 600) and unreadable/unwritable from this session's account.

## Core idea

A single sortable list, seeded with the pipeline's own RANK order (agreement with the
pipeline = no drag = no training signal from that poster). The user drags posters to
correct the order. Only *disagreements* with the pipeline's prior order carry information.

This is, independently arrived at, the same idea behind **LambdaRank/LambdaMART**: weight
each pairwise training gradient by how much fixing that disagreement would matter, rather
than treating every pairwise correction as equally informative. design/29's own "Future
Phase 2" note already named LambdaMART as the eventual upgrade path — this gets there
through the existing pairwise substrate instead of a tree model.

## Mechanism: rank inversions, not absolute position deltas

Pairwise training examples come from **inversions** — pairs `(i, j)` where the pipeline's
order and the user's final order disagree on which comes first. Agreement → zero pairs.
This was checked against both worked examples from the original discussion:
- Swap ranks 7↔8 → exactly **1** inverted pair.
- Move rank 9 → rank 2 → **7** inverted pairs (the mover vs. everything it now precedes
  that the pipeline had ranked better) — and critically, positions 2–8 don't need separate
  "small" treatment; their mutual order didn't change, so the cascade is already fully
  captured by those 7 pairs. No special-casing needed.

**Using inversions (not raw position numbers) as the unit of signal is also what makes the
hate-pile ripple effect (see below) a non-issue**: removing an item from the list shifts
everyone below it by one absolute position number, but does not change their relative
order to each other — so no spurious inversions are generated among untouched posters.
This was a specific concern raised and resolved during the discussion.

**Correction made during discussion**: the existing trainer's per-movie weight
normalization (`movie_norm = 1/pairs-in-movie`, in `head_trainer.py:182`) means that,
as-is, a 1-rank swap and a 7-rank jump *from the same ranking session* would land at the
same per-pair weight — inversion-counting alone gives "no signal on agreement" for free,
but does **not** give "bigger jumps matter more" for free. That needs an explicit
magnitude term multiplied in before movie-normalization.

## Decisions made so far

1. **Position discounting: build it in from day one.** Confirmed preference. Rationale:
   a correction at the top of the list (rank 1 vs 2 — who the literal auto-pick is) is
   higher-stakes than a same-sized correction deep in a long tail nobody scrutinized
   carefully, and large candidate counts (32 in the example run) have combinatorially more
   tail pairs than head pairs, so undiscounted weighting risks the tail dominating by
   sheer pair count if a user ever fully reorders a long list. Start from the standard
   DCG-style discount family (`1/log2(rank+1)`) rather than inventing one; treat the exact
   curve as a tunable knob (same pattern as `FEEDBACK_INDIFF_HATE_PAIR_WEIGHT` today) to
   be calibrated once real events exist, not locked down speculatively now.

2. **Keep a hate pile.** A per-poster action (button on/under the card) throws it into a
   separate pile, removed from the orderable list.
   - **Resolved concern**: hating one poster does *not* generate spurious signal against
     untouched posters below it — that was the absolute-position-shift fear, and
     inversion-based (not absolute-position-based) signal construction handles it by
     construction (see Mechanism section above).
   - **Open / proposed**: hating a poster is still a real, deliberate claim that it's worse
     than everything the pipeline had ranked below it too (not just "removed, no opinion"),
     so it's reasonable for hate to generate corrective pairs against those — but at
     reduced/implicit confidence (mirroring today's `FEEDBACK_INDIFF_HATE_PAIR_WEIGHT`
     precedent) rather than full deliberate-comparison weight, since it followed from one
     click, not N explicit judgments. **Not yet confirmed with user — see open questions.**
   - Hard-negative-exemplar mining (hate + pipeline-rank ≤ `FEEDBACK_HARD_NEGATIVE_RANK_MAX`
     → negative exemplar for the k-NN taste profile) is a separate channel from the
     pairwise ranking signal and is expected to carry over largely as-is, just re-hosted
     under whatever the new event schema turns out to be.

3. **Full backend replacement, no legacy fallback.** Confirmed: throw out the current
   tier/bucket pairwise machinery (`action='rank'` handling in `feedback.py`, the
   tier-group pairing logic in `build_pairwise_training_data()`, the bucket UI) entirely
   rather than keeping it dormant/available behind a toggle. Build the new mechanism from
   a clean slate. (A kanban-tier-columns drag UI was built and explicitly rejected by the
   user — implementation was reverted via `git stash`; not part of this direction.)

## Resolved: hate-pair confidence, hate UX, historical data

4. **Hate generates reduced-confidence ranking pairs** against everything the pipeline
   ranked below it (not fully decoupled from the ranking signal) — confirmed.
5. **Hate removes the poster from the sortable list and compacts the rest** (matches the
   "pile" framing literally) — confirmed. No spurious signal results from this, per the
   inversion-based mechanism above.
6. **Historical v3 rank events in `data/feedback/labels.jsonl` are abandoned, not
   migrated.** Cleanest implementation: bump the event version/type so the new trainer
   structurally never reads old-shape rows — no migration code, no deletion needed, old
   rows just become inert.

## Resolved: discount by final position, not baseline — one formula for both UI surfaces

Two ML subsystems are in play and it's worth being precise about which this concerns:
- **Taste profile / k-NN exemplar store** (`taste_store.py`) — liked/hated *images* feed
  `knn_sim`/`dino_knn`. Entirely unaffected by this redesign; onboarding keeps doing this
  unchanged regardless of what follows.
- **Learned ranking head** (`head_trainer.py`/`learned_head.py`) — the pairwise model this
  whole doc is about.

The open question was whether a single discount formula could serve both the live
pipeline run page (`/pipeline/runs/[run_id]`, which always has a real predicted order —
`select_scorer()` always picks *something*, learned head or Phase-0 weighted fallback,
for every run regardless of how much training data exists) and onboarding's Rank Test
(`marquee/onboarding/service.py:200-330`, whose bundled manifest is built offline and
never goes through scoring — there is no `pipeline_rank` there, structurally, regardless
of user data volume; it's a one-time bootstrap screen, not a data-volume problem).

**Resolution**: discount each pair's weight by position **in the user's final order**,
not the baseline order. The baseline (real pipeline rank, or onboarding's arbitrary
manifest listing) still decides *which* pairs count as inversions (disagreements) at all
— that part is unchanged and still needs a baseline to gate against. But the weight
magnitude comes from where the pair lands in the *final* order (e.g. `discount(r1_i)`,
`discount(r1_j))`, combined somehow — exact combination TBD at build time). This works
identically whether the baseline was a meaningful prediction or an arbitrary listing, so
no special-casing is needed between the two screens.

## Open questions (not yet resolved)

- **Exact discount formula and how the two endpoints' discounts combine into one pair
  weight** (plain DCG `1/log2(rank+1)` vs. something else; min/max/sum/average of the two
  endpoints) — deferred to build/calibration time, not a pre-build decision.
- **Schema**: today's v3 event (`favorites: list[list[str]]`, `hated: list[str]`) is
  shaped for buckets, not a precise final position per poster. The replacement needs to
  record `(orig_filename, pipeline_rank_or_null, user_rank_or_hated)` per candidate at
  minimum. Concrete schema TBD during planning.
