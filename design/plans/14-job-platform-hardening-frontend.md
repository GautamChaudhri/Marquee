# 14 — Job Platform + Re-encode Hardening — Frontend

> **For the implementing agent:** Executed only after
> `design/plans/14-job-platform-hardening-backend.md` is fully implemented — FIRST verify it
> shipped (the backend timeline records it; `_active_tv_job_ids` in
> `marquee/api/routes/letterbox.py` includes media-file-subject jobs). STOP and report if
> not. Route code is the source of truth. Never modify anything under `marquee/`.
> `design/plans/05-television-frontend.md` §2 rules apply verbatim (trackJob poll-first,
> null guards, job labels, reuse over forking). Gates per commit: `npm run check`,
> `npm run lint`, `npm run build`. Re-locate every line anchor before editing.
>
> **Timeline doc (shared with the backend plan):**
> `design/plans/14-job-platform-hardening-timeline.md` already exists when you start (the
> backend agent created it) — read it in full, verify against `git log`, then APPEND your
> frontend progress to the same file after every commit. Never create a second timeline.

**Goal:** Small, surgical: make single-episode re-encodes visible as persistent progress
bars on the TV show page (the backend now returns their job ids), and make every Cancel
button give honest feedback instead of failing silently.

Primary files: `frontend/src/lib/job-labels.ts`,
`frontend/src/routes/letterbox/tv/[id]/+page.svelte`,
`frontend/src/routes/projection-room/jobs/[job_id]/+page.svelte`,
`frontend/src/routes/projection-room/+page.svelte` (verify it has cancel actions before
touching), `frontend/src/lib/api/jobs.ts` (types only if needed).

---

## 1. Locked decisions

| # | Decision |
|---|---|
| F1 | **Job labels.** Register `letterbox_reencode` in `$lib/job-labels.ts` (label: "Letterbox Re-encode"). Check the registry against every operation bridged in `marquee/core/jobs/legacy_media.py:203-215` and add any missing (subtitle ops etc.) with sensible labels — the show page and Projection Room both display them. |
| F2 | **Show-page bars handle non-batch jobs.** `rehydrateJob` in `letterbox/tv/[id]/+page.svelte` (:236, progress math ~:255-260) computes percent as `children_completed/children_total` — batch-only. Add a fallback: when the snapshot's progress/detail carries `percent` (media bridge jobs mirror the encode percent into `Job.progress` via legacy_media's emit — verify the exact field shape with a live/recorded snapshot before coding), use it directly. A media-file re-encode bar must show real encode percent, the stage text, and the existing cancel ✕. No other bar behavior changes; bars still rehydrate from `detail.active_job_ids` (which now includes these jobs — backend H9). |
| F3 | **Honest cancel feedback.** Show-page bar cancel (`cancelActiveRun`), Projection Room detail cancel (projection-room/jobs/[job_id]/+page.svelte:70-72 — currently sets `cancelling` optimistically BEFORE the await and swallows failure), and any list-row cancel: flip UI state to "cancelling" only AFTER the cancel call resolves (202); on rejection show an error toast and revert the optimistic state. While `cancelling`, disable the button. Terminal states arrive via the existing polling/SSE — no new tracking. |
| F4 | **No scope creep.** Shows-list page banner untouched. No new endpoints, no changes to `trackJob`, no styling overhaul (plan 15 owns visual changes). Movie letterbox pages untouched. |

## 2. Build order (commit per step, gates green each time)

1. **F1** — labels commit.
2. **F2** — percent fallback + verify a re-encode bar renders and survives reload
   (kick a short re-encode on the GPU box if available; otherwise verify with a mocked
   snapshot and record the gap in the timeline).
3. **F3** — cancel feedback in the three surfaces.

## 3. Manual smoke (record honestly in the timeline; GPU box + live media)

Start a single-episode re-encode from the show page → a labeled bar with real percent
appears; refresh mid-encode → bar reappears (rehydrated); cancel from the bar → button
disables, job reaches `cancelled` ≤ ~30s, ffmpeg gone (`nvidia-smi` idle), reservations
released; cancel from the Projection Room detail page behaves the same; a cancel against a
finished job shows the error toast instead of silently pretending.

## 4. Out of scope

Any backend change. Plan 15's terminology/color work. The BatchReencodeModal, artifact
surfaces, and batch flows (shipped in plan 13). New job-tracking helpers.
