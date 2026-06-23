# 21 — Job progress bar persistence

## Why this doc exists

Three job-progress bars have now been built in this codebase (letterbox,
poster pipeline, subtitles). The first two got persistence right from the
start. The subtitles bar didn't, and two prior fix attempts on it focused on
backend progress-percentage math without touching the actual defect — the bar
disappeared on refresh/navigation regardless of how accurate the percentage
was, because the underlying job reference was never being recovered at all.

This doc names the failure pattern once, names the fix once, and gives a
checklist so the next progress bar doesn't repeat either mistake.

## The failure pattern

A progress bar that's implemented as pure local component state
(`let runningJobId = $state(...)`) dies the moment its component unmounts.
That happens on:

- a hard page refresh (obviously — the whole JS context is torn down), and
- **same-app navigation**, less obviously — this app's root layout wraps
  routed content in `{#key page.url.pathname}`
  ([+layout.svelte](../frontend/src/routes/+layout.svelte)), which
  *intentionally* destroys and recreates the page component on every route
  change, including navigating away and back to the same page.

If the only place a job's id lives is a local variable, both of those events
erase it. The backend job is fine — it's still queued or running — but the UI
has no way to find it again. This was exactly the subtitles bug
([+page.svelte](../frontend/src/routes/subtitles/[id]/+page.svelte)'s old
`monitorJob()`): `runningJobId`/`progressPercent`/etc. were locals, there was
no `onMount`, and nothing was ever persisted to `localStorage`. Fixing the
backend's percentage math (the two prior attempts) couldn't have helped —
the component holding that math was already gone.

## The three mandatory ingredients

Every working bar in this codebase (letterbox, pipeline, and now subtitles)
follows the same shape. Skipping any one of these three reproduces the bug:

### 1. Persist the job id the instant it's created

Not when tracking starts, not on some later tick — in the same handler that
gets the job id back from the create/confirm call:

```ts
function storeJobId(id: string | null) {
	if (!browser) return;
	if (id) localStorage.setItem(KEY, id);
	else localStorage.removeItem(KEY);
}
```

Use a key scoped to the resource if the bar is resource-scoped (subtitles:
`` `marquee:subtitles:activeJob:${movieId}` ``, letterbox:
`lb.activeBatch`-style singleton if the bar is page-global). Clear it only on
terminal status — never just because the component happened to unmount.

### 2. Give the page loader a server-side way to find an active job

`onMount` runs *after* the component exists, which is too late to avoid a
flash of "nothing's running" on a slow connection. The `+page.ts` loader runs
before render and can hand the component a known-active job up front. Two
valid shapes, pick based on what the bar is scoped to:

- **Resource-scoped bar, loader already fetches that resource** — embed the
  active job inline. Letterbox's `getLetterboxStatus` includes
  `batch_active`; subtitles' `inspect_movie_subtitles` now includes
  `active_job` (the latest `queued`/`running` `MediaJob` for that
  `media_file_id`) for exactly this reason — `+page.ts` was already calling
  it once per load, so there was no new endpoint to add.
- **Page-global bar, no single owning resource** — query the job list
  directly. Pipeline's loader does
  `listJobs(fetch, { type: 'poster_pipeline_batch', status: 'running', limit: 1 })`.

Either way, the *job-type's snapshot endpoint must return `events_url`*. The
generic `/jobs/{id}` always has — `letterbox.py`, `pipeline.py`, `jobs.py` all
include it. `/media-jobs/{id}` didn't until this fix
([marquee/core/media_jobs/serialize.py](../marquee/core/media_jobs/serialize.py)),
which meant a rehydrated snapshot had no way to re-attach SSE. This is a
checklist item for any *new* job type, not just media-jobs: if a bar can be
rehydrated, its snapshot endpoint needs `events_url`, or `trackJob()` has
nothing to subscribe to after the fact.

### 3. `onMount` priority chain → one `rehydrateX()` → the shared `trackJob()`

```ts
onMount(() => {
	const active = data.<loader-supplied-job>?.job_id ?? null;
	if (active) { void rehydrateJob(active); return; }
	if (browser) {
		const stored = localStorage.getItem(KEY);
		if (stored) void rehydrateJob(stored);
	}
});
```

Server-supplied job wins (it's authoritative and fresher); `localStorage` is
the fallback for when the loader's query missed it for any reason. `rehydrateJob()`
fetches the snapshot once, and either shows the terminal result (job finished
while the tab was closed) or seeds the bar's state *from that snapshot* — not
from zero — before resuming tracking. Resetting to 0%/"queued" on rehydration
is a giveaway that this step was skipped.

**Always resume through `trackJob()`** ([frontend/src/lib/jobs.ts](../frontend/src/lib/jobs.ts)),
never a hand-rolled `EventSource` + `setInterval` pair. It exists because
hand-rolled tracking reliably gets one of these wrong:

- seeding the bar immediately (don't wait for the first SSE message — poll
  once right away),
- letting *either* SSE or the snapshot poll finalize the job (SSE can drop;
  the poll is the backstop),
- closing the `EventSource` on teardown. The old subtitles `monitorJob()`
  never stored its `unsub` anywhere `onDestroy` could reach, so navigating
  away mid-job leaked the connection. `trackJob()`'s returned `stop()` must
  be captured in a component-scoped variable and called from `onDestroy`.

`trackJob()` now takes an optional `fetchJob` (defaulting to the generic
`getJob`) so job types with their own snapshot endpoint — `getMediaJob` for
media-jobs — share the exact same poll-plus-SSE engine instead of each
re-implementing it:

```ts
trackJob<MediaJob>(fetch, jobId, handlers, { eventsUrl, fetchJob: getMediaJob });
```

## Checklist for adding a new progress bar

- [ ] Job id written to `localStorage` the instant it's known, not when
      tracking starts.
- [ ] The job type's snapshot endpoint (`GET /.../{id}`) returns `events_url`.
- [ ] The page loader (`+page.ts`) can find an already-active job for this
      bar — embedded in a resource you already fetch, or a dedicated
      `status=running` list query.
- [ ] `onMount` checks the loader's job first, `localStorage` second, and
      calls one `rehydrateX()` for both cases.
- [ ] `rehydrateX()` seeds progress from the fetched snapshot — never resets
      to 0%/queued for a job that's already underway.
- [ ] Tracking goes through `trackJob()`, not a hand-rolled
      `EventSource`/`setInterval`.
- [ ] `trackJob()`'s `stop()` is captured and called from `onDestroy`.
- [ ] The stored job id is cleared only on terminal status (or explicit
      dismiss for bars with a results summary) — never on unmount.
- [ ] Manually verified: trigger the job, hard-refresh mid-job, confirm the
      bar reappears; navigate away and back mid-job, confirm the same.
