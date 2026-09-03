<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { posterThumbUrl } from '$lib/api/poster-urls';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import PosterLibraryToggle from '$lib/components/PosterLibraryToggle.svelte';
	import PosterBatchPopover from '$lib/components/PosterBatchPopover.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import {
		approveReviewQueueAutoPicks,
		getMovieRunQueue,
		getPipelineMetrics,
		getReviewQueue,
		resetReviewQueuePosters,
		runBatch,
		triggerRun
	} from '$lib/api/pipeline';
	import { ApiError } from '$lib/api/client';
	import { batchOptionsFor } from '$lib/pipeline/batch-options';
	import { posterBatchChunkSize, posterBatchMode } from '$lib/pipeline/batch-prefs';
	import { relativeAge, reviewCandidateSummary } from '$lib/pipeline/review-copy';
	import { toast } from '$lib/toast';
	import type { BatchScope, MovieListItem, PipelineMetrics, ReviewQueue } from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Tab = 'run' | 'review' | 'metrics';
	const PAGE_SIZE = 60;
	let tab = $state<Tab>((page.url.searchParams.get('tab') as Tab) ?? 'run');
	function setTab(id: string) {
		tab = id as Tab;
		if (tab === 'review') requestQueueRefresh();
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('tab', id);
		goto(`/pipeline/movies?${sp.toString()}`, {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	}

	// svelte-ignore state_referenced_locally
	let queue = $state<ReviewQueue>(data.queue);
	// svelte-ignore state_referenced_locally
	let metrics = $state<PipelineMetrics | null>(data.metrics);
	// svelte-ignore state_referenced_locally
	let missing = $state<MovieListItem[]>(data.missing.items);
	// svelte-ignore state_referenced_locally
	let missingTotal = $state<number>(data.missing.total);
	let missingPage = $state(1);
	let loadingMore = $state(false);
	let queuePage = $state(1);
	let reviewLoadingMore = $state(false);
	const selected = new SvelteSet<number>();
	let selectMode = $state(false);

	const tabs = $derived([
		{ id: 'run', label: 'Run', count: missingTotal },
		{ id: 'review', label: 'Review', count: queue.total },
		{ id: 'metrics', label: 'Metrics' }
	]);

	// The subtitle carries the count the deleted in-body action bars used to show.
	const headSubtitle = $derived(
		tab === 'review'
			? `${queue.total} run${queue.total === 1 ? '' : 's'} awaiting review`
			: `${missingTotal} film${missingTotal === 1 ? '' : 's'} missing posters`
	);

	async function refreshAll() {
		try {
			const [qd, md, mv] = await Promise.all([
				getReviewQueue(fetch, { page_size: PAGE_SIZE }),
				getPipelineMetrics(fetch, { limit: 500 }).catch(() => metrics),
				getMovieRunQueue(fetch, { page_size: PAGE_SIZE }).catch(() => null)
			]);
			queue = qd;
			queuePage = 1;
			metrics = md;
			if (mv) {
				missing = mv.items;
				missingTotal = mv.total;
				missingPage = 1;
			}
		} catch {
			/* keep stale data on a transient failure */
		}
	}

	let queueRefreshPromise: Promise<void> | null = null;
	let queueRefreshQueued = false;

	async function refreshQueues() {
		try {
			const [qd, mv] = await Promise.all([
				getReviewQueue(fetch, { page_size: PAGE_SIZE }),
				getMovieRunQueue(fetch, { page_size: PAGE_SIZE }).catch(() => null)
			]);
			queue = qd;
			queuePage = 1;
			if (mv) {
				missing = mv.items;
				missingTotal = mv.total;
				missingPage = 1;
			}
		} catch {
			/* keep stale data on a transient failure */
		}
	}

	function requestQueueRefresh() {
		if (queueRefreshPromise) {
			queueRefreshQueued = true;
			return;
		}
		queueRefreshPromise = refreshQueues().finally(() => {
			queueRefreshPromise = null;
			if (queueRefreshQueued) {
				queueRefreshQueued = false;
				requestQueueRefresh();
			}
		});
	}

	// ── Auto-approve all review queue items ───────────────────────────────────
	let approveAllOpen = $state(false);
	let approveAllBusy = $state(false);
	let resetOpen = $state(false);
	let resetBusy = $state(false);

	async function approveAllAutoPicks() {
		if (queue.total === 0) return;
		approveAllBusy = true;
		try {
			const res = await approveReviewQueueAutoPicks(fetch, { deploy: true });
			const parts = [`${res.approved} approved`];
			if (res.skipped_no_auto) parts.push(`${res.skipped_no_auto} skipped`);
			if (res.failed) parts.push(`${res.failed} failed`);
			toast(parts.join(' · '), res.failed ? 'info' : 'good');
			if (res.failed && res.errors.length) {
				const first = res.errors[0];
				toast(
					`${first.title ?? first.run_id.slice(0, 8)}: ${String(first.error ?? 'failed')}`,
					'bad',
					5000
				);
			}
			approveAllOpen = false;
			await refreshAll();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Bulk approval failed', 'bad');
		} finally {
			approveAllBusy = false;
		}
	}

	async function confirmResetAll() {
		if (queue.total === 0) return;
		resetBusy = true;
		try {
			const movies = queue.total;
			await resetReviewQueuePosters(fetch);
			toast(`Reset queued for ${movies} movie${movies === 1 ? '' : 's'}`, 'good');
			resetOpen = false;
			await refreshAll();
			setTab('run');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Reset failed', 'bad');
		} finally {
			resetBusy = false;
		}
	}

	// ── Runs (batch + single) ────────────────────────────────────────────────────
	let initiatedJobIds = $state<string[]>([]);
	let batchRunning = $state(false);

	/** Per-job settle handlers can update local state without showing the generic
	 *  poster-work completion toast. */
	const settledHandlers = new SvelteMap<
		string,
		(snapshot: JobSnapshotResponse) => void | Promise<void>
	>();

	function trackJob(
		jobId: string,
		onSettled?: (snapshot: JobSnapshotResponse) => void | Promise<void>
	) {
		initiatedJobIds = [...new Set([...initiatedJobIds, jobId])];
		if (onSettled) settledHandlers.set(jobId, onSettled);
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		const handler = settledHandlers.get(snapshot.job_id);
		if (handler) {
			settledHandlers.delete(snapshot.job_id);
			await handler(snapshot);
			return;
		}
		batchRunning = false;
		toast(
			`Poster work ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		await refreshAll();
	}

	function handleJobUpdated(snapshot: JobSnapshotResponse) {
		if (snapshot.type === 'poster_pipeline_batch' && snapshot.phase !== 'terminal') {
			requestQueueRefresh();
		}
	}

	const eligibleLoaded = $derived(missing.filter((m) => m.tmdb_id != null));
	const selectedCount = $derived(selected.size);
	const allSelected = $derived(
		eligibleLoaded.length > 0 && eligibleLoaded.every((m) => selected.has(m.id))
	);

	function toggleSelect(id: number) {
		if (selected.has(id)) selected.delete(id);
		else selected.add(id);
	}
	function toggleAll() {
		if (allSelected) {
			for (const m of missing) selected.delete(m.id);
		} else {
			for (const m of eligibleLoaded) selected.add(m.id);
		}
	}
	// Leaving selection mode discards the selection: a hidden checkbox that is still
	// checked would arm a bulk command nobody can see.
	function endSelectMode() {
		selectMode = false;
		selected.clear();
	}

	async function startBatch(scope: BatchScope, movieIds?: number[]) {
		if (batchRunning) return;
		if (scope === 'selected' && (!movieIds || movieIds.length === 0)) return;
		batchRunning = true;
		try {
			const job = await runBatch(fetch, {
				scope,
				movie_ids: movieIds,
				...batchOptionsFor($posterBatchMode, $posterBatchChunkSize)
			});
			trackJob(job.job_id);
			const selectedCount = scope === 'selected' ? (movieIds?.length ?? 0) : null;
			toast(
				selectedCount === null
					? 'Poster-analysis batch queued'
					: `Batch queued — ${selectedCount} movie${selectedCount === 1 ? '' : 's'}`,
				'info'
			);
			if (scope === 'selected') selected.clear();
			await refreshQueues();
		} catch (e) {
			batchRunning = false;
			const msg =
				e instanceof ApiError && e.status === 404
					? 'No eligible movies for that scope'
					: e instanceof Error
						? e.message
						: 'Batch failed to start';
			toast(msg, 'bad');
		}
	}

	async function runSingle(m: MovieListItem) {
		if (m.tmdb_id == null || batchRunning) return;
		try {
			const ref = await triggerRun(fetch, m.id);
			trackJob(ref.job_id);
			toast('Poster analysis queued', 'info');
			await refreshQueues();
		} catch (e) {
			if (e instanceof ApiError && e.status === 409) {
				toast('The same poster analysis was already submitted', 'info');
				await refreshQueues();
			} else {
				toast(e instanceof Error ? e.message : 'Run failed to start', 'bad');
			}
		}
	}

	async function loadMore() {
		if (loadingMore) return;
		loadingMore = true;
		try {
			const next = await getMovieRunQueue(fetch, {
				page_size: PAGE_SIZE,
				page: missingPage + 1
			});
			missingPage += 1;
			missing = [...missing, ...next.items];
			missingTotal = next.total;
		} catch {
			toast('Could not load more', 'bad');
		} finally {
			loadingMore = false;
		}
	}

	async function loadMoreReview() {
		if (reviewLoadingMore) return;
		reviewLoadingMore = true;
		try {
			const next = await getReviewQueue(fetch, {
				page_size: PAGE_SIZE,
				page: queuePage + 1
			});
			queuePage += 1;
			queue = { ...next, items: [...queue.items, ...next.items] };
		} catch {
			toast('Could not load more review items', 'bad');
		} finally {
			reviewLoadingMore = false;
		}
	}

	function fmtSecs(s: number | null | undefined): string {
		if (s == null) return '—';
		if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
		if (s < 60) return `${s.toFixed(1)}s`;
		return `${(s / 60).toFixed(1)}m`;
	}
	const stageMax = $derived(metrics ? Math.max(1, ...Object.values(metrics.avg_stage_seconds)) : 1);
	const stageRows = $derived(
		metrics ? Object.entries(metrics.avg_stage_seconds).sort((a, b) => b[1] - a[1]) : []
	);
</script>

<header class="workspace-head">
	<div class="head-top">
		<div class="titles">
			<h1>Film Posters</h1>
			<p>{headSubtitle}</p>
		</div>
		<div class="scope">
			<PosterLibraryToggle active="films" />
		</div>
	</div>

	<!-- One toolbar: what you are looking at on the left, what you can do to it on the right. -->
	<div class="head-bar">
		<TabBar {tabs} active={tab} onSelect={setTab} />
		<div class="head-actions">
			{#if tab === 'run'}
				<PosterBatchPopover
					bind:mode={$posterBatchMode}
					bind:chunkSize={$posterBatchChunkSize}
					disabled={batchRunning}
				/>
				<button
					class="pill {selectMode ? 'quiet' : 'ghost'}"
					aria-pressed={selectMode}
					onclick={() => (selectMode ? endSelectMode() : (selectMode = true))}
				>
					{selectMode ? 'Done' : 'Select'}
				</button>
				{#if selectMode}
					<button class="pill ghost" onclick={toggleAll} disabled={eligibleLoaded.length === 0}>
						{allSelected ? 'None' : 'All'}
					</button>
				{/if}
				{#if selectedCount > 0}
					<button
						class="pill ghost"
						onclick={() => startBatch('selected', [...selected])}
						disabled={batchRunning}
					>
						Run selected ({selectedCount})
					</button>
				{/if}
				<button class="pill quiet" onclick={() => startBatch('all')} disabled={batchRunning}>
					Re-run whole library
				</button>
				<button class="pill primary" onclick={() => startBatch('missing')} disabled={batchRunning}>
					Run all missing
				</button>
			{:else if tab === 'review'}
				<button class="pill danger" onclick={() => (resetOpen = true)} disabled={queue.total === 0}>
					Reset all in review
				</button>
				<button
					class="pill primary"
					onclick={() => (approveAllOpen = true)}
					disabled={queue.total === 0}
				>
					Approve all auto-picks
				</button>
			{/if}
		</div>
	</div>
</header>

{#if data.onboarding && data.onboarding.state !== 'personalized'}
	<a
		href="/onboarding"
		style="display:flex; align-items:center; gap:12px; padding:12px 16px; margin-bottom:16px; border:1px solid var(--gold-deep); border-radius:var(--radius-sm); background:var(--gold-soft); color:var(--text); text-decoration:none;"
	>
		<span style="color:var(--gold); display:flex;"><Icon name="taste" size={18} /></span>
		<div style="flex:1; display:flex; flex-direction:column;">
			<strong style="color:var(--text);">Teach Marquee your poster taste</strong>
			<span style="font-size:12px; color:var(--muted);">
				Choose and deploy real posters — {data.onboarding.active_positive_subjects}/{data.onboarding
					.thresholds.required} required choices confirmed.
			</span>
		</div>
		<span style="color:var(--gold); font-weight:600;">Start →</span>
	</a>
{/if}

<!-- Kept outside the tab branches so tracked work can refresh every workspace view. -->
<FeatureActivityPanel
	scopeKey="feature:pipeline:movies"
	queries={[
		{ feature_area: 'ai_posters', type: 'poster_pipeline_batch' },
		{ feature_area: 'ai_posters', type: 'poster_pipeline', subject_kind: 'movie' }
	]}
	jobIds={initiatedJobIds}
	heading="Movie poster activity"
	onUpdated={handleJobUpdated}
	onSettled={handleJobSettled}
/>

<!-- ═══ REVIEW ═══ -->
{#if tab === 'review'}
	{#if queue.items.length === 0}
		<div class="empty">
			<Icon name="pipeline" size={34} stroke={1} />
			<strong>Nothing to review</strong>
			<span>Runs awaiting a poster decision will collect here. Start one from the Run tab.</span>
		</div>
	{:else}
		<div class="rev-grid">
			{#each queue.items as item (item.run.run_id)}
				{@const c = item.run.counts ?? {}}
				{@const candidateSummary = reviewCandidateSummary(
					c.ranked,
					Boolean(item.auto_pick_poster_url)
				)}
				<button class="rev-card" onclick={() => goto(`/pipeline/runs/${item.run.run_id}`)}>
					<div class="rev-poster">
						<PosterThumb
							title={item.movie.title}
							year={item.movie.year}
							posterUrl={item.auto_pick_poster_url ?? null}
						/>
					</div>
					<div class="rev-meta">
						<div class="rev-title">{item.movie.title}</div>
						<div class="rev-sub">{item.movie.year ?? '—'}</div>
						<div class="rev-stats-row">
							<div
								class="rev-stats mono"
								class:good={candidateSummary.tone === 'good'}
								class:warn={candidateSummary.tone === 'warn'}
								class:bad={candidateSummary.tone === 'bad'}
							>
								{candidateSummary.label}
							</div>
							<span class="rev-age">{relativeAge(item.run.started_at)}</span>
						</div>
					</div>
				</button>
			{/each}
		</div>
		{#if queue.items.length < queue.total}
			<div class="loadmore">
				<button class="pill quiet" onclick={loadMoreReview} disabled={reviewLoadingMore}>
					{reviewLoadingMore
						? 'Loading…'
						: `Load more review items — ${queue.items.length} of ${queue.total}`}
				</button>
			</div>
		{/if}
	{/if}

	<!-- ═══ RUN ═══ -->
{:else if tab === 'run'}
	{#if missing.length === 0}
		<div class="empty">
			<Icon name="pipeline" size={34} stroke={1} />
			<strong>Every film has a poster</strong>
			<span>
				Nothing is missing artwork right now — use <strong>Re-run whole library</strong> above to re-evaluate
				everything.
			</span>
		</div>
	{:else}
		<div class="rev-grid">
			{#each missing as m (m.id)}
				{@const ok = m.tmdb_id != null}
				<div class="run-card" class:sel={selected.has(m.id)} class:dis={!ok}>
					<div class="run-poster-wrap">
						<button
							class="run-card-main"
							disabled={selectMode && (!ok || batchRunning)}
							onclick={() => (selectMode ? toggleSelect(m.id) : goto(`/films/${m.id}`))}
						>
							<div class="run-poster">
								<PosterThumb
									title={m.title}
									year={m.year}
									posterUrl={posterThumbUrl(m.poster_url)}
									centerTitle={false}
								/>
							</div>
						</button>

						{#if selectMode}
							<label class="run-card-select">
								<input
									type="checkbox"
									checked={selected.has(m.id)}
									disabled={!ok || batchRunning}
									aria-label={`Select ${m.title}`}
									onchange={() => toggleSelect(m.id)}
								/>
							</label>
						{/if}

						<!-- Sibling of the poster button, not a child: a button cannot nest in a button. -->
						<button
							class="pill primary run-card-run"
							onclick={(event) => {
								event.stopPropagation();
								runSingle(m);
							}}
							disabled={!ok || batchRunning}
							aria-label={`Run poster pipeline for ${m.title}`}
							title={ok ? 'Run pipeline for this film' : 'No TMDB id'}
						>
							Run
						</button>
					</div>
					<div class="run-card-meta">
						<div class="rev-title">{m.title}</div>
						<div class="rev-sub">{m.year ?? '—'}</div>
						{#if !ok}<div class="run-hint">No TMDB id — sync first</div>{/if}
					</div>
				</div>
			{/each}
		</div>

		{#if missing.length < missingTotal}
			<div class="loadmore">
				<button class="pill quiet" onclick={loadMore} disabled={loadingMore}>
					{loadingMore ? 'Loading…' : `Load more — ${missing.length} of ${missingTotal}`}
				</button>
			</div>
		{/if}
	{/if}

	<!-- ═══ METRICS ═══ -->
{:else if tab === 'metrics'}
	{#if !metrics || metrics.window_runs === 0}
		<div class="empty">
			<Icon name="pipeline" size={34} stroke={1} />
			<strong>No metrics yet</strong>
			<span>Run the pipeline and aggregates over recent runs will appear here.</span>
		</div>
	{:else}
		<div class="stat-grid">
			<StatCard label="Runs (window)" value={metrics.window_runs} tone="gold" />
			<StatCard label="Batches" value={metrics.distinct_batches} tone="info" />
			<StatCard
				label="Avg duration"
				value={fmtSecs(metrics.duration_seconds.avg)}
				sub={`p50 ${fmtSecs(metrics.duration_seconds.p50)} · p90 ${fmtSecs(metrics.duration_seconds.p90)}`}
				tone="dovi"
			/>
			<StatCard
				label="Avg candidates"
				value={metrics.avg_counts.total_candidates?.toFixed(1) ?? '—'}
				sub={`${metrics.avg_counts.ranked?.toFixed(1) ?? '—'} ranked`}
				tone="good"
			/>
		</div>

		<div class="chip-rows">
			<div class="chip-row">
				<span class="chip-label">Status</span>
				{#each Object.entries(metrics.by_status) as [k, v] (k)}
					<span class="m-chip">{k}<b>{v}</b></span>
				{/each}
			</div>
			{#if Object.keys(metrics.by_scorer).length}
				<div class="chip-row">
					<span class="chip-label">Scorer</span>
					{#each Object.entries(metrics.by_scorer) as [k, v] (k)}
						<span class="m-chip">{k}<b>{v}</b></span>
					{/each}
				</div>
			{/if}
		</div>

		{#if stageRows.length}
			<div class="panel">
				<div class="panel-head">Mean stage time</div>
				{#each stageRows as [stage, secs] (stage)}
					<div class="stage-row">
						<span class="stage-name">{stage}</span>
						<div class="stage-bar"><ProgressBar value={(secs / stageMax) * 100} tone="gold" /></div>
						<span class="stage-val mono">{fmtSecs(secs)}</span>
					</div>
				{/each}
			</div>
		{/if}

		{#if Object.keys(metrics.avg_counts).length}
			<div class="panel">
				<div class="panel-head">Avg counts per run</div>
				<div class="counts-grid">
					{#each Object.entries(metrics.avg_counts) as [k, v] (k)}
						<div class="count-cell">
							<span class="count-k">{k.replace(/_/g, ' ')}</span>
							<span class="count-v mono">{v.toFixed(1)}</span>
						</div>
					{/each}
				</div>
			</div>
		{/if}
	{/if}
{/if}

<ConfirmDialog
	open={resetOpen}
	title={`Reset ${queue.total} movie${queue.total === 1 ? '' : 's'}?`}
	message="Deletes the deployed posters for every movie awaiting review (backups are kept), discards their runs and stored candidates, and returns them all to the Run tab to be analysed again."
	confirmLabel={`Reset ${queue.total} movie${queue.total === 1 ? '' : 's'}`}
	tone="bad"
	busy={resetBusy}
	onConfirm={confirmResetAll}
	onCancel={() => (resetOpen = false)}
/>

<ConfirmDialog
	open={approveAllOpen}
	title="Approve all auto-picks"
	message="This will approve the auto-pick for all {queue.total} run{queue.total === 1
		? ''
		: 's'} in the review queue, deploy posters to movie folders, and skip any run that does not have an auto-pick. Continue?"
	confirmLabel={approveAllBusy ? 'Approving…' : `Approve all ${queue.total}`}
	tone="bad"
	busy={approveAllBusy}
	onConfirm={approveAllAutoPicks}
	onCancel={() => (approveAllOpen = false)}
/>

<style>
	/* Header chrome and the .pill button vocabulary live in $lib/styles/workspace.css —
	   the Television workspace renders the same toolbar. */

	/* ── Empty ── */
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 8px;
		padding: 70px 24px;
		text-align: center;
		color: var(--faint);
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.empty strong {
		color: var(--text);
		font-size: 15px;
	}
	.empty span {
		font-size: 13px;
		max-width: 380px;
		line-height: 1.5;
	}
	.empty span strong {
		font-size: inherit;
	}

	/* ── Review grid ── */
	.rev-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 16px;
	}
	.rev-card {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding: 0;
		border: none;
		background: transparent;
		text-align: left;
		cursor: pointer;
	}
	.rev-poster {
		transition: transform 0.14s ease;
	}
	.rev-card:hover .rev-poster {
		transform: translateY(-2px);
	}
	.rev-title {
		font-size: 13px;
		font-weight: 600;
		color: var(--text);
		line-height: 1.25;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.rev-sub {
		font-size: 11px;
		color: var(--muted);
	}
	.rev-stats-row {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 8px;
		margin-top: 2px;
		min-width: 0;
	}
	.rev-stats {
		min-width: 0;
		font-size: 11px;
		color: var(--faint);
		white-space: nowrap;
	}
	.rev-stats.good {
		color: var(--good);
	}
	.rev-stats.warn {
		color: var(--warn);
	}
	.rev-stats.bad {
		color: var(--bad);
	}
	.rev-age {
		flex: none;
		font-size: 10.5px;
		color: var(--faint);
		white-space: nowrap;
	}

	/* ── Run: missing-poster grid ── */
	.run-card {
		display: flex;
		flex-direction: column;
		gap: 8px;
		min-width: 0;
	}
	.run-card.dis {
		opacity: 0.55;
	}
	/* Anchors the checkbox and the Run overlay over the poster. Not interactive itself —
	   the two buttons inside it are siblings, because a button cannot nest in a button. */
	.run-poster-wrap {
		position: relative;
	}
	/* Dims the artwork while the Run pill is showing, so the pill reads against a busy
	   poster. Sits under both overlays and never takes a click. */
	.run-poster-wrap::after {
		content: '';
		position: absolute;
		inset: 0;
		z-index: 1;
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--ink) 55%, transparent);
		opacity: 0;
		pointer-events: none;
		transition: opacity 0.14s ease;
	}
	.run-poster-wrap:hover::after,
	.run-poster-wrap:focus-within::after {
		opacity: 1;
	}
	/* No pill to make room for means no scrim. */
	.run-card.dis .run-poster-wrap::after {
		content: none;
	}
	.run-card-select {
		position: absolute;
		top: 8px;
		left: 8px;
		z-index: 2;
		display: flex;
		align-items: center;
		justify-content: center;
		width: 24px;
		height: 24px;
		border: 1px solid var(--line2);
		border-radius: 6px;
		background: color-mix(in srgb, var(--panel) 90%, transparent);
	}
	.run-card-select input {
		accent-color: var(--gold);
		width: 15px;
		height: 15px;
	}
	.run-card-main {
		display: block;
		width: 100%;
		padding: 0;
		border: none;
		background: transparent;
		/* A <button> centres its text by default, which was centring the gradient
		   placeholder's title. FilmGrid and the review cards both reset this. */
		text-align: left;
		cursor: pointer;
	}
	.run-card-main:disabled {
		cursor: default;
	}
	.run-poster {
		transition:
			transform 0.14s ease,
			box-shadow 0.14s ease;
	}
	.run-card-main:hover:not(:disabled) .run-poster {
		transform: translateY(-2px);
	}
	.run-card.sel .run-poster {
		border-radius: var(--radius-sm);
		box-shadow: 0 0 0 2px var(--gold);
	}
	/* Revealed on hover or focus. Kept at opacity 0 rather than display:none so it stays
	   in the tab order; focusing it fires :focus-within, which reveals it before it is
	   ever used. pointer-events is load-bearing — an invisible button would otherwise
	   swallow clicks meant for the poster. */
	/* Centred, not on the bottom edge: the gradient placeholder writes its title there. */
	.run-card-run {
		position: absolute;
		left: 50%;
		top: 50%;
		z-index: 2;
		transform: translate(-50%, calc(-50% + 4px));
		padding: 6px 16px;
		font-size: 12px;
		font-weight: 600;
		box-shadow: 0 6px 18px var(--shadow);
		opacity: 0;
		pointer-events: none;
		transition:
			opacity 0.14s ease,
			transform 0.14s ease;
	}
	/* Never reveal a control that cannot act. */
	.run-card-run:disabled {
		display: none;
	}
	.run-poster-wrap:hover .run-card-run,
	.run-poster-wrap:focus-within .run-card-run {
		opacity: 1;
		transform: translate(-50%, -50%);
		pointer-events: auto;
	}
	/* Coarse pointers have no hover state to reveal it with. */
	@media (hover: none) {
		.run-card-run {
			opacity: 1;
			transform: translate(-50%, -50%);
			pointer-events: auto;
		}
	}
	.run-card-meta {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.run-hint {
		font-size: 11px;
		color: var(--low);
	}
	.loadmore {
		display: flex;
		justify-content: center;
		margin-top: 14px;
	}

	/* ── Metrics ── */
	.stat-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: 12px;
		margin-bottom: 18px;
	}
	.chip-rows {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin-bottom: 18px;
	}
	.chip-row {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.chip-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
		min-width: 54px;
	}
	.m-chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 3px 9px;
		border-radius: 99px;
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 12px;
		color: var(--muted);
	}
	.m-chip b {
		font-family: var(--font-mono);
		color: var(--text);
	}
	.panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px 16px;
		margin-bottom: 16px;
	}
	.panel-head {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
		margin-bottom: 12px;
	}
	.stage-row {
		display: grid;
		grid-template-columns: 140px 1fr 56px;
		align-items: center;
		gap: 10px;
		margin-bottom: 8px;
	}
	.stage-name {
		font-size: 12px;
		color: var(--text);
		text-transform: capitalize;
	}
	.stage-val {
		font-size: 11.5px;
		color: var(--muted);
		text-align: right;
	}
	.counts-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
		gap: 10px;
	}
	.count-cell {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.count-k {
		font-size: 11px;
		color: var(--faint);
		text-transform: capitalize;
	}
	.count-v {
		font-size: 16px;
		color: var(--text);
	}

	.mono {
		font-family: var(--font-mono);
	}
</style>
