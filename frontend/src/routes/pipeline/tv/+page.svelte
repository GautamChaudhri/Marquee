<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import PosterBatchModeControl from '$lib/components/PosterBatchModeControl.svelte';
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
	import { toast } from '$lib/toast';
	import type { PosterBatchMode, PosterBatchOptions } from '$lib/api/types';
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
	let batchMode = $state<PosterBatchMode>('chunked');
	let chunkSize = $state(8);
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

	function currentBatchOptions(): PosterBatchOptions {
		if (batchMode === 'all_at_once') return { batch_mode: batchMode };
		return {
			batch_mode: batchMode,
			chunk_size: Math.min(16, Math.max(1, Math.round(Number(chunkSize) || 8)))
		};
	}

	async function startBatch(scope: 'missing' | 'all' | 'selected', seriesIds?: number[]) {
		if (batchRunning) return;
		batchRunning = true;
		try {
			const job = await runTvBatch(fetch, {
				scope,
				series_ids: seriesIds,
				...currentBatchOptions()
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
				...currentBatchOptions()
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
			<h1>TV Posters</h1>
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
	{#if tab === 'run'}
		<div class="batch-options">
			<PosterBatchModeControl bind:mode={batchMode} bind:chunkSize disabled={batchRunning} />
		</div>
	{/if}
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
								centerTitle={placeholder.centerTitle}
								posterStatus="missing"
							/>
							<span>{placeholder.label}</span>
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
									<img src={preview.url} alt={`${item.series.title} ${preview.label} poster`} />
								{:else}
									<div class="preview-placeholder">
										<PosterThumb
											title="No pick"
											gradientKey={item.series.title}
											centerTitle
											posterStatus="missing"
										/>
									</div>
								{/if}
								<span>{preview.label}</span>
							</div>
						{/each}
					</div>
				{:else}
					<div class="preview-fallback">
						<PosterThumb
							title={item.series.title}
							year={item.series.year}
							posterStatus={item.display_poster_url ? 'deployed' : 'missing'}
							posterUrl={item.display_poster_url}
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
	/* Header: title + library scope on one line, then a single toolbar that pairs
	   the tabs with the actions that belong to the tab you are on. */
	.workspace-head {
		display: flex;
		flex-direction: column;
		gap: 14px;
		margin-bottom: 18px;
	}
	.head-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		flex-wrap: wrap;
	}
	.titles h1 {
		margin: 0;
		font-size: 18px;
		font-weight: 650;
		letter-spacing: -0.01em;
	}
	.titles p {
		margin: 3px 0 0;
		font-size: 13px;
		color: var(--muted);
	}
	.scope :global(.library-switch) {
		margin-bottom: 0;
	}
	.head-bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		flex-wrap: wrap;
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: 999px;
		background: var(--panel);
	}
	.head-actions {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.batch-options {
		display: flex;
	}
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
		width: 96px;
		flex: 0 0 96px;
	}
	.preview-tile img {
		width: 100%;
		aspect-ratio: 2 / 3;
		height: 144px;
		object-fit: cover;
		border-radius: 10px;
		border: 1px solid var(--line2);
		background: var(--panel2);
	}
	.preview-tile span {
		font-size: 11px;
		color: var(--muted);
		text-align: center;
		white-space: nowrap;
	}
	.preview-placeholder :global(.poster),
	.preview-fallback :global(.poster) {
		width: 100%;
	}
	.preview-fallback {
		width: 96px;
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
	/* One button vocabulary for the whole workspace: pills, matching the tabs
	   and the library switch. Weight carries the hierarchy, not the shape. */
	.row-actions {
		display: flex;
		align-items: center;
		gap: 6px;
		flex: 0 0 auto;
	}
	.pill {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 8px 14px;
		border-radius: 999px;
		border: 1px solid transparent;
		background: transparent;
		color: var(--muted);
		font-size: 13px;
		font-weight: 500;
		white-space: nowrap;
		transition:
			background 0.12s,
			border-color 0.12s,
			color 0.12s;
	}
	.pill:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.pill.primary {
		border-color: var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
	}
	.pill.primary:hover:not(:disabled) {
		filter: brightness(1.06);
	}
	.pill.quiet {
		border-color: var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.pill.quiet:hover:not(:disabled) {
		border-color: color-mix(in srgb, var(--gold) 45%, var(--line2));
	}
	.pill.ghost:hover:not(:disabled) {
		background: var(--panel2);
		color: var(--text);
	}
	.pill.danger:hover:not(:disabled) {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 40%, transparent);
	}
	.chev {
		color: var(--gold);
		font-size: 15px;
		line-height: 1;
	}
	@media (max-width: 1000px) {
		.head-bar {
			border-radius: var(--radius);
			align-items: stretch;
		}
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
