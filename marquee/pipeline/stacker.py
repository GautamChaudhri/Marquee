"""Stage 6b — poster *stacks*: group same-design variants, then rank.

Where the legacy pHash stage (``deduper.py``) *deletes* near-duplicate
posters before ranking, the stacker keeps every ranked survivor and instead
**groups** the variants of one base design (title moved, recolored, slightly
cropped) into a stack.  Every poster keeps its own individual score; stacks
are ranked against each other and members are ranked *within* their stack:

    Stack 1 (best design)  →  1A (best variant), 1B, 1C, ...
    Stack 2                →  2A, 2B, ...

The auto-pick is ``1A``.

Grouping signal (``STACK_SIGNAL``):
  - ``dino`` / ``clip`` — cosine similarity between the L2-normalized
    embeddings carried on each ``CandidateScore.embedding``.  Two posters
    share a stack when their cosine ≥ ``STACK_SIM_THRESHOLD``.
  - ``phash`` — perceptual-hash Hamming distance (``ImageHash`` carried on
    ``.embedding``); share a stack when distance ≤ ``STACK_PHASH_MAX_DISTANCE``.

Clustering is greedy **average-linkage**: repeatedly merge the two clusters
with the highest mean inter-cluster similarity while that mean clears the
threshold.  Average (not single) linkage avoids "chaining" two genuinely
different designs together through one borderline variant.  ``n`` per movie
is tiny (≤ a few dozen), so the naive O(n³) loop is free.

A stack's overall score is the mean of its top-``STACK_AGG_TOPK`` member
scores (a robust/trimmed mean — rewards a design with several strong variants
without letting one weak variant drag it down; ``K=1`` reduces to max).

Records that arrive without a usable signal value (e.g. DINO off and no
cached embedding) each become their own singleton stack, so a run never
crashes on missing data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.pipeline.types import CandidateScore

logger = logging.getLogger(__name__)

_PHASH_BITS = 64  # imagehash.phash default hash_size=8 → 64-bit hash


@dataclass
class Stack:
    """One design: its ranked members and aggregate score."""

    stack_id: int
    stack_rank: int
    stack_score: float
    members: list[CandidateScore] = field(default_factory=list)

    @property
    def representative(self) -> CandidateScore:
        return self.members[0]


def _pos_label(pos: int) -> str:
    """1 → 'A', 26 → 'Z', 27 → 'AA' (spreadsheet-style, 1-indexed)."""
    label = ""
    while pos > 0:
        pos, rem = divmod(pos - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


def _score(candidate: CandidateScore) -> float:
    return candidate.final_score if candidate.final_score is not None else 0.0


def _similarity_matrix(
    candidates: list[CandidateScore], config: PipelineSettings
) -> tuple[np.ndarray, list[int], float]:
    """Pairwise similarity over candidates that carry a usable signal value.

    Returns ``(sim, usable_indices, threshold)`` where ``sim`` is an
    ``m × m`` higher-is-closer matrix aligned to ``usable_indices`` (indices
    into ``candidates``), and ``threshold`` is the merge floor in the same
    units.  pHash Hamming distance is mapped to ``1 - dist/64`` so both
    signals share one "merge if mean similarity ≥ threshold" loop.
    """
    signal = config.STACK_SIGNAL
    if signal == "phash":
        usable = [i for i, c in enumerate(candidates) if c.embedding is not None]
        m = len(usable)
        sim = np.eye(m, dtype=np.float64)
        for a in range(m):
            for b in range(a + 1, m):
                dist = candidates[usable[a]].embedding - candidates[usable[b]].embedding
                s = 1.0 - (float(dist) / _PHASH_BITS)
                sim[a, b] = sim[b, a] = s
        threshold = 1.0 - (config.STACK_PHASH_MAX_DISTANCE / _PHASH_BITS)
        return sim, usable, threshold

    # dino / clip → cosine over L2-normalized vectors
    usable = [
        i for i, c in enumerate(candidates) if isinstance(c.embedding, np.ndarray)
    ]
    if not usable:
        return np.zeros((0, 0)), [], config.STACK_SIM_THRESHOLD
    vecs = np.stack([np.asarray(candidates[i].embedding, dtype=np.float64) for i in usable])
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.clip(norms, 1e-12, None)
    sim = vecs @ vecs.T
    return sim, usable, config.STACK_SIM_THRESHOLD


def _cluster(
    n_usable: int, sim: np.ndarray, threshold: float
) -> list[list[int]]:
    """Greedy average-linkage on a similarity matrix → clusters of indices.

    Indices are positions in ``sim`` (0..n_usable-1).  Merges the two
    clusters with the highest mean pairwise similarity until none clears
    ``threshold``.
    """
    clusters: list[list[int]] = [[i] for i in range(n_usable)]
    while len(clusters) > 1:
        best_avg = -np.inf
        best_pair: tuple[int, int] | None = None
        for ci in range(len(clusters)):
            for cj in range(ci + 1, len(clusters)):
                avg = float(
                    np.mean([sim[a, b] for a in clusters[ci] for b in clusters[cj]])
                )
                if avg > best_avg:
                    best_avg = avg
                    best_pair = (ci, cj)
        if best_pair is None or best_avg < threshold:
            break
        ci, cj = best_pair
        clusters[ci].extend(clusters[cj])
        del clusters[cj]
    return clusters


def assign_stacks(
    ranked: list[CandidateScore],
    *,
    config: PipelineSettings = pipeline_settings,
) -> list[Stack]:
    """Cluster ranked survivors into stacks and write stack fields back.

    Mutates each ``CandidateScore`` in ``ranked`` (``stack_id``,
    ``stack_rank``, ``stack_pos``, ``stack_label``, ``stack_size``,
    ``stack_score``) and returns the stacks ordered by ``stack_rank``.
    """
    if not ranked:
        return []

    # 1) Cluster the candidates that carry a usable signal value; everything
    #    else becomes its own singleton stack.
    sim, usable, threshold = _similarity_matrix(ranked, config)
    member_groups: list[list[CandidateScore]] = []
    for local_cluster in _cluster(len(usable), sim, threshold):
        member_groups.append([ranked[usable[i]] for i in local_cluster])
    usable_set = set(usable)
    for idx, candidate in enumerate(ranked):
        if idx not in usable_set:
            member_groups.append([candidate])

    # 2) Order members within each stack by individual score; compute the
    #    robust top-K-mean stack score.
    top_k = max(1, config.STACK_AGG_TOPK)
    scored_groups: list[tuple[float, float, list[CandidateScore]]] = []
    for members in member_groups:
        members.sort(key=_score, reverse=True)
        scores = [_score(m) for m in members]
        stack_score = float(np.mean(scores[:top_k]))
        scored_groups.append((stack_score, scores[0], members))

    # 3) Rank stacks: top-K mean, then best member, then size.
    scored_groups.sort(key=lambda g: (g[0], g[1], len(g[2])), reverse=True)

    stacks: list[Stack] = []
    for stack_rank, (stack_score, _best, members) in enumerate(scored_groups, 1):
        stack_id = stack_rank - 1
        for pos, member in enumerate(members, 1):
            member.stack_id = stack_id
            member.stack_rank = stack_rank
            member.stack_pos = pos
            member.stack_label = _pos_label(pos)
            member.stack_size = len(members)
            member.stack_score = stack_score
        stacks.append(
            Stack(
                stack_id=stack_id,
                stack_rank=stack_rank,
                stack_score=stack_score,
                members=members,
            )
        )

    logger.info(
        "STACKS | signal=%s | %d posters → %d stacks | sizes=%s",
        config.STACK_SIGNAL,
        len(ranked),
        len(stacks),
        [len(s.members) for s in stacks],
    )
    return stacks
