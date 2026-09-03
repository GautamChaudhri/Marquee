<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { posterThumbUrl } from '$lib/api/poster-urls';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import PosterBatchPopover from '$lib/components/PosterBatchPopover.svelte';
	import PosterLibraryToggle from '$lib/components/PosterLibraryToggle.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import MetricsChart from '$lib/components/MetricsChart.svelte';
	import {
		resolveTvPosterTab,
		reviewPosterPreviews,
		runPosterPlaceholders,
		type TvPosterTab
	} from '$lib/pipeline/tv-display';
	import {
		approveTvAuto,
		getTvMetrics,
		getTvReviewQueue,
		getTvRunQueue,
		getTvSummary,
		resetTvReviewQueuePosters,
		runSeries,
		runTvBatch
	} from '$lib/api/pipeline-tv';
	import { batchOptionsFor } from '$lib/pipeline/batch-options';
	import { posterBatchChunkSize, posterBatchMode } from '$lib/pipeline/batch-prefs';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Tab = TvPosterTab;
	// The default applies only when the workspace opens; refreshes and manual tab changes stay put.
	// svelte-ignore state_referenced_locally
	let tab = $state<Tab>(
		resolveTvPosterTab(
			page.url.searchParams.get('tab'),
			data.runQueue.total,
			data.reviewQueue.total_series
		)
	);
	function setTab(id: string) {
		if (id !== 'run' && id !== 'review' && id !== 'metrics') return;
		tab = id as Tab;
		if (tab === 'review') requestQueueRefresh();
		const url = new URL(page.url);
		const sp = url.searchParams;
		sp.set('tab', id);
		goto(`/pipeline/tv?${sp.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// svelte-ignore state_referenced_locally
	let runQueue = $state(data.runQueue);
	// svelte-ignore state_referenced_locally
	let reviewQueue = $state(data.reviewQueue);
	// svelte-ignore state_referenced_locally
	let metrics = $state(data.metrics);
	// svelte-ignore state_referenced_locally
	let summary = $state(data.summary);
	const selected = new SvelteSet<number>();
	let selectMode = $state(false);

	// Counts read the same local state the tab bodies render, so a refresh cannot leave
	// a badge disagreeing with the list underneath it.
	const tabs = $derived([
		{ id: 'run', label: 'Run', count: runQueue.total },
		{ id: 'review', label: 'Review', count: reviewQueue.total_series },
		{ id: 'metrics', label: 'Metrics' }
	]);

	let initiatedJobIds = $state<string[]>([]);
	let batchRunning = $state(false);
	let resetOpen = $state(false);
	let resetBusy = $state(false);
	const pendingSeriesIds = new SvelteSet<number>();
	const pendingSeriesJobs = new SvelteMap<string, number>();

	async function refresh() {
		try {
			[runQueue, reviewQueue, metrics, summary] = await Promise.all([
				getTvRunQueue(fetch),
				getTvReviewQueue(fetch, { page_size: 200 }),
				getTvMetrics(fetch, { limit: 500 }).catch(() => metrics),
				getTvSummary(fetch).catch(() => summary)
			]);
		} catch {
			/* keep stale */
		}
	}

	let queueRefreshPromise: Promise<void> | null = null;
	let queueRefreshQueued = false;

	async function refreshQueues() {
		try {
			[runQueue, reviewQueue, summary] = await Promise.all([
				getTvRunQueue(fetch),
				getTvReviewQueue(fetch, { page_size: 200 }),
				getTvSummary(fetch).catch(() => summary)
			]);
		} catch {
			/* keep stale */
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

	const eligibleRun = $derived(runQueue.items.filter((item) => !item.no_tmdb));
	const allSelected = $derived(
		eligibleRun.length > 0 && eligibleRun.every((item) => selected.has(item.series.id))
	);

	function toggleAll() {
		if (allSelected) selected.clear();
		else for (const item of eligibleRun) selected.add(item.series.id);
	}

	// Leaving selection mode discards the selection: a hidden checkbox that is still
	// checked would arm a bulk command nobody can see.
	function endSelectMode() {
		selectMode = false;
		selected.clear();
	}

	async function startBatch(scope: 'missing' | 'all' | 'selected', seriesIds?: number[]) {
		if (batchRunning) return;
		batchRunning = true;
		try {
			const job = await runTvBatch(fetch, {
				scope,
				series_ids: seriesIds,
				...batchOptionsFor($posterBatchMode, $posterBatchChunkSize)
			});
			initiatedJobIds = [...new Set([...initiatedJobIds, job.job_id])];
			await refresh();
		} catch (e) {
			batchRunning = false;
			toast(e instanceof Error ? e.message : 'Failed to start TV batch', 'bad');
		}
	}

	async function startOne(seriesId: number) {
		if (batchRunning || pendingSeriesIds.has(seriesId)) return;
		pendingSeriesIds.add(seriesId);
		try {
			const job = await runSeries(fetch, seriesId, {
				include: 'all_missing',
				...batchOptionsFor($posterBatchMode, $posterBatchChunkSize)
			});
			pendingSeriesJobs.set(job.job_id, seriesId);
			initiatedJobIds = [...new Set([...initiatedJobIds, job.job_id])];
			await refresh();
		} catch (e) {
			pendingSeriesIds.delete(seriesId);
			toast(e instanceof Error ? e.message : 'Failed to run series', 'bad');
		}
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		const seriesId = pendingSeriesJobs.get(snapshot.job_id);
		if (seriesId !== undefined) {
			pendingSeriesIds.delete(seriesId);
			pendingSeriesJobs.delete(snapshot.job_id);
		} else {
			batchRunning = false;
		}
		initiatedJobIds = initiatedJobIds.filter((jobId) => jobId !== snapshot.job_id);
		toast(
			`TV poster work ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		await refresh();
	}

	function handleJobUpdated(snapshot: JobSnapshotResponse) {
		if (snapshot.type === 'poster_pipeline_tv_batch' && snapshot.phase !== 'terminal') {
			requestQueueRefresh();
		}
	}

	async function approveAll(seriesId?: number) {
		try {
			const result = await approveTvAuto(fetch, { deploy: true, series_id: seriesId });
			toast(
				`${result.approved} approved${result.skipped_no_auto ? ` · ${result.skipped_no_auto} skipped` : ''}`,
				result.failed ? 'info' : 'good'
			);
			await refresh();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Bulk approval failed', 'bad');
		}
	}

	async function confirmResetAll() {
		resetBusy = true;
		try {
			const shows = reviewQueue.total_series;
			await resetTvReviewQueuePosters(fetch);
			toast(`Reset queued for ${shows} shows`, 'good');
			resetOpen = false;
			await refresh();
			setTab('run');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Reset failed', 'bad');
		} finally {
			resetBusy = false;
		}
	}
</script>

<header class="workspace-head">
	<div class="head-top">
		<div class="titles">
			<h1>Television Posters</h1>
			<p>{summary.shows_fully_covered}/{summary.shows_total} shows fully covered</p>
		</div>
		<div class="scope">
			<PosterLibraryToggle active="television" />
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
					<button class="pill ghost" onclick={toggleAll} disabled={eligibleRun.length === 0}>
						{allSelected ? 'None' : 'All'}
					</button>
				{/if}
				{#if selected.size > 0}
					<button
						class="pill ghost"
						onclick={() => startBatch('selected', [...selected])}
						disabled={batchRunning}
					>
						Run selected ({selected.size})
					</button>
				{/if}
				<button class="pill quiet" onclick={() => startBatch('all')} disabled={batchRunning}>
					Re-run whole library
				</button>
				<button class="pill primary" onclick={() => startBatch('missing')} disabled={batchRunning}>
					Run all missing
				</button>
			{:else if tab === 'review'}
				<button
					class="pill danger"
					disabled={reviewQueue.total_series === 0}
					onclick={() => (resetOpen = true)}
				>
					Reset all in review
				</button>
				<button
					class="pill primary"
					disabled={reviewQueue.total_series === 0}
					onclick={() => approveAll()}
				>
					Approve all auto-picks
				</button>
			{/if}
		</div>
	</div>
</header>

<ConfirmDialog
	open={resetOpen}
	title="Reset {reviewQueue.total_series} shows?"
	message="Deletes the deployed show and season posters for every show awaiting review (backups are kept), discards their runs and stored candidates, and returns them all to the Run tab to be analysed again."
	confirmLabel="Reset {reviewQueue.total_series} shows"
	tone="bad"
	busy={resetBusy}
	onConfirm={confirmResetAll}
	onCancel={() => (resetOpen = false)}
/>

<FeatureActivityPanel
	scopeKey="feature:pipeline:tv"
	queries={[
		{ feature_area: 'ai_posters', type: 'poster_pipeline_tv_batch' },
		{ feature_area: 'ai_posters', type: 'poster_pipeline', subject_kind: 'series' },
		{ feature_area: 'ai_posters', type: 'poster_pipeline', subject_kind: 'season' }
	]}
	jobIds={initiatedJobIds}
	heading="TV poster activity"
	onUpdated={handleJobUpdated}
	onSettled={handleJobSettled}
/>

{#if tab === 'run'}
	<div class="run-list">
		{#each runQueue.items as item (item.series.id)}
			{@const placeholders = runPosterPlaceholders(item)}
			<div class="run-row" class:picked={selected.has(item.series.id)}>
				<div class="card-head">
					{#if selectMode}
						<label class="pick">
							<input
								type="checkbox"
								checked={selected.has(item.series.id)}
								aria-label={`Select ${item.series.title}`}
								disabled={item.no_tmdb || batchRunning || pendingSeriesIds.has(item.series.id)}
								onchange={() =>
									selected.has(item.series.id)
										? selected.delete(item.series.id)
										: selected.add(item.series.id)}
							/>
						</label>
					{/if}
					<div class="series-meta">
						<strong title={item.series.title}>{item.series.title}</strong>
						<span class="sub">{item.series.year ?? '—'}</span>
						{#if item.no_tmdb}<span class="note">No TMDB match — run sync.</span>{/if}
					</div>
					<div class="row-actions">
						<button
							class="pill quiet"
							onclick={() => startOne(item.series.id)}
							disabled={item.no_tmdb || batchRunning || pendingSeriesIds.has(item.series.id)}
						>
							{pendingSeriesIds.has(item.series.id) ? 'Running…' : 'Run'}
						</button>
					</div>
				</div>
				<div class="preview-strip" aria-label={`${item.series.title} missing poster placeholders`}>
					{#each placeholders as placeholder (placeholder.label)}
						<div class="preview-tile preview-placeholder" title={placeholder.label}>
							<PosterThumb
								title={placeholder.title}
								year={placeholder.year}
								gradientKey={placeholder.gradientKey}
								fallbackPlacement={placeholder.label === 'Show' ? 'bottom-left' : 'center'}
							/>
							<span class="preview-label">{placeholder.label}</span>
						</div>
					{/each}
				</div>
			</div>
		{/each}
	</div>
{:else if tab === 'review'}
	<div class="review-grid">
		{#each reviewQueue.items as item (item.series.id)}
			{@const previews = reviewPosterPreviews(item)}
			<div class="review-card">
				<div class="card-head">
					<div class="series-meta">
						<strong title={item.series.title}>{item.series.title}</strong>
					</div>
					<div class="row-actions">
						<button class="pill ghost" onclick={() => approveAll(item.series.id)}
							>Approve all</button
						>
						<button
							class="pill quiet"
							onclick={() => goto(`/pipeline/tv/series/${item.series.id}`)}
						>
							Open review <span class="chev" aria-hidden="true">›</span>
						</button>
					</div>
				</div>
				{#if previews.length}
					<div class="preview-strip" aria-label={`${item.series.title} poster previews`}>
						{#each previews as preview (preview.label)}
							<div class="preview-tile" title={preview.label}>
								{#if preview.url}
									<img
										src={preview.url}
										alt={`${item.series.title} ${preview.label} poster`}
										loading="lazy"
										decoding="async"
										onerror={(e) => ((e.currentTarget as HTMLImageElement).hidden = true)}
									/>
								{:else}
									<div class="preview-placeholder">
										<PosterThumb
											title={preview.label === 'Show' ? 'No pick' : preview.label}
											gradientKey={item.series.title}
											fallbackPlacement={preview.label === 'Show' ? 'bottom-left' : 'center'}
										/>
									</div>
								{/if}
								<span class="preview-label">{preview.label}</span>
								<div class="preview-stats mono">
									<span
										class:good={preview.candidateSummary.tone === 'good'}
										class:warn={preview.candidateSummary.tone === 'warn'}
										class:bad={preview.candidateSummary.tone === 'bad'}
									>
										{preview.candidateSummary.label}
									</span>
									<span class="preview-age">{preview.age}</span>
								</div>
							</div>
						{/each}
					</div>
				{:else}
					<div class="preview-fallback">
						<PosterThumb
							title={item.series.title}
							year={item.series.year}
							posterUrl={posterThumbUrl(item.display_poster_url)}
						/>
					</div>
				{/if}
			</div>
		{/each}
	</div>
{:else if metrics}
	<div class="metrics-grid">
		<MetricsChart title="By status" points={[]} series={[]} />
		<div class="metric-card">
			<h3>Run window</h3>
			<div class="rows">
				<div><span>Runs</span><strong>{metrics.window_runs}</strong></div>
				<div><span>Statuses</span><strong>{Object.keys(metrics.by_status).length}</strong></div>
				<div><span>Scorers</span><strong>{Object.keys(metrics.by_scorer).length}</strong></div>
			</div>
		</div>
	</div>
{/if}

<style>
	/* Header chrome and the .pill button vocabulary live in $lib/styles/workspace.css —
	   the Films workspace renders the same toolbar. */

	/* Two columns of cards: a card is only as tall as one poster row, so half the
	   page height was going to empty space beside short titles. */
	.run-list,
	.review-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
		align-items: start;
	}
	.metrics-grid {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.run-row,
	.review-card,
	.metric-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px 16px;
	}
	/* Thin head row (title left, actions right), posters underneath it. */
	.run-row,
	.review-card {
		display: flex;
		flex-direction: column;
		gap: 12px;
		text-align: left;
		transition:
			border-color 0.12s,
			background 0.12s;
	}
	.card-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 14px;
		min-height: 34px;
	}
	.run-row:hover,
	.review-card:hover,
	.run-row.picked {
		border-color: var(--line2);
		background: color-mix(in srgb, var(--panel2) 55%, var(--panel));
	}
	.run-row.picked {
		border-color: color-mix(in srgb, var(--gold) 40%, var(--line2));
	}
	.pick {
		display: flex;
		align-items: center;
		flex: 0 0 auto;
	}
	.pick input {
		accent-color: var(--gold);
		width: 16px;
		height: 16px;
	}
	/* `auto` + non-shrinking tiles: the strip scrolls only when the posters
	   outgrow the card, and is left untouched when they all fit. */
	.preview-strip {
		display: flex;
		gap: 10px;
		overflow-x: auto;
		overscroll-behavior-x: contain;
		scrollbar-width: thin;
		scrollbar-color: var(--line2) transparent;
	}
	.preview-strip:hover {
		scrollbar-color: var(--faint) transparent;
	}
	.preview-strip::-webkit-scrollbar {
		height: 8px;
	}
	.preview-strip::-webkit-scrollbar-track {
		background: transparent;
	}
	.preview-strip::-webkit-scrollbar-thumb {
		border-radius: 99px;
		border: 2px solid transparent;
		background-clip: content-box;
		background-color: var(--line2);
	}
	.preview-strip:hover::-webkit-scrollbar-thumb {
		background-color: var(--faint);
	}
	.preview-tile {
		display: flex;
		flex-direction: column;
		gap: 6px;
		width: 126px;
		flex: 0 0 126px;
	}
	.preview-tile img {
		width: 100%;
		aspect-ratio: 2 / 3;
		height: 189px;
		object-fit: cover;
		border-radius: 10px;
		border: 1px solid var(--line2);
		background: var(--panel2);
	}
	.preview-label {
		font-size: 11px;
		color: var(--muted);
		text-align: center;
		white-space: nowrap;
	}
	.preview-stats {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 5px;
		font-size: 10px;
		line-height: 1.2;
		white-space: nowrap;
	}
	.preview-stats > span:first-child {
		min-width: 0;
		color: var(--faint);
	}
	.preview-stats > span:first-child.good {
		color: var(--good);
	}
	.preview-stats > span:first-child.warn {
		color: var(--warn);
	}
	.preview-stats > span:first-child.bad {
		color: var(--bad);
	}
	.preview-age {
		flex: none;
		color: var(--faint);
	}
	.preview-placeholder :global(.poster),
	.preview-fallback :global(.poster) {
		width: 100%;
	}
	.preview-fallback {
		width: 126px;
	}
	.series-meta {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 4px 8px;
		min-width: 0;
		flex: 1 1 auto;
	}
	/* Two lines max, so a long name never pushes the actions out of the head row. */
	.series-meta strong {
		font-size: 14.5px;
		font-weight: 600;
		line-height: 1.3;
		overflow-wrap: anywhere;
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		overflow: hidden;
	}
	.sub {
		font-size: 12.5px;
		color: var(--muted);
	}
	.note {
		font-size: 12px;
		color: var(--bad);
	}
	.rows {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.rows div {
		display: flex;
		justify-content: space-between;
	}
	.row-actions {
		display: flex;
		align-items: center;
		gap: 6px;
		flex: 0 0 auto;
	}
	@media (max-width: 1180px) {
		.run-list,
		.review-grid {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	@media (max-width: 620px) {
		.card-head {
			flex-wrap: wrap;
		}
		.row-actions {
			flex-wrap: wrap;
		}
	}
</style>
