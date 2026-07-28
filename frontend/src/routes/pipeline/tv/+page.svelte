<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import MetricsChart from '$lib/components/MetricsChart.svelte';
	import {
		approveTvAuto,
		getTvMetrics,
		getTvReviewQueue,
		getTvRunQueue,
		runSeries,
		runTvBatch
	} from '$lib/api/pipeline-tv';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Tab = 'run' | 'review' | 'metrics';
	let tab = $state<Tab>((page.url.searchParams.get('tab') as Tab) ?? 'run');
	const tabs = $derived([
		{ id: 'run', label: 'Run', count: data.runQueue.total },
		{ id: 'review', label: 'Review', count: data.reviewQueue.total_series },
		{ id: 'metrics', label: 'Metrics' }
	]);

	function setTab(id: string) {
		tab = id as Tab;
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
	const selected = new SvelteSet<number>();

	let initiatedJobIds = $state<string[]>([]);
	let batchRunning = $state(false);
	const pendingSeriesIds = new SvelteSet<number>();
	const pendingSeriesJobs = new SvelteMap<string, number>();

	async function refresh() {
		try {
			[runQueue, reviewQueue, metrics] = await Promise.all([
				getTvRunQueue(fetch),
				getTvReviewQueue(fetch, { page_size: 200 }),
				getTvMetrics(fetch, { limit: 500 }).catch(() => metrics)
			]);
		} catch {
			/* keep stale */
		}
	}

	function seasonLabel(number: number): string {
		return number === 0 ? 'S00' : `S${String(number).padStart(2, '0')}`;
	}

	type PreviewPoster = {
		url: string;
		label: string;
	};

	function compressAssets(
		assets: { media_type: 'series' | 'season'; number?: number }[]
	): string[] {
		const out: string[] = [];
		const seasons = assets
			.filter((asset) => asset.media_type === 'season' && typeof asset.number === 'number')
			.map((asset) => asset.number as number)
			.sort((a, b) => a - b);
		if (assets.some((asset) => asset.media_type === 'series')) out.push('Show');
		let i = 0;
		while (i < seasons.length) {
			let j = i;
			while (j + 1 < seasons.length && seasons[j + 1] === seasons[j] + 1) j += 1;
			out.push(
				i === j ? seasonLabel(seasons[i]) : `${seasonLabel(seasons[i])}–${seasonLabel(seasons[j])}`
			);
			i = j + 1;
		}
		return out;
	}

	function previewPosters(item: (typeof reviewQueue.items)[number]): PreviewPoster[] {
		const previews: PreviewPoster[] = [];
		const showUrl = item.show_run?.auto_pick_poster_url;
		if (showUrl) {
			previews.push({ url: showUrl, label: 'Show' });
		}
		for (const seasonRun of item.season_runs) {
			if (!seasonRun.auto_pick_poster_url) continue;
			previews.push({
				url: seasonRun.auto_pick_poster_url,
				label: seasonLabel(seasonRun.season_number)
			});
		}
		return previews;
	}

	async function startBatch(scope: 'missing' | 'all' | 'selected', seriesIds?: number[]) {
		if (batchRunning) return;
		batchRunning = true;
		try {
			const job = await runTvBatch(fetch, { scope, series_ids: seriesIds });
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
			const job = await runSeries(fetch, seriesId, { include: 'all_missing' });
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
</script>

<SectionHeader
	title="TV posters"
	subtitle={`${data.summary.shows_fully_covered}/${data.summary.shows_total} shows fully covered`}
/>

<TabBar {tabs} active={tab} onSelect={setTab} />

<FeatureActivityPanel
	scopeKey="feature:pipeline:tv"
	queries={[
		{ feature_area: 'ai_posters', type: 'poster_pipeline_tv_batch' },
		{ feature_area: 'ai_posters', type: 'poster_pipeline', subject_kind: 'series' },
		{ feature_area: 'ai_posters', type: 'poster_pipeline', subject_kind: 'season' }
	]}
	jobIds={initiatedJobIds}
	heading="TV poster activity"
	onSettled={handleJobSettled}
/>

{#if tab === 'run'}
	<div class="run-head">
		<button class="btn-gold" onclick={() => startBatch('missing')} disabled={batchRunning}
			>Run all missing</button
		>
		<button class="btn-sec" onclick={() => startBatch('all')} disabled={batchRunning}
			>Re-run whole library</button
		>
		<button
			class="btn-sec"
			onclick={() => startBatch('selected', [...selected])}
			disabled={selected.size === 0 || batchRunning}
		>
			Run selected ({selected.size})
		</button>
	</div>
	<div class="run-list">
		{#each runQueue.items as item (item.series.id)}
			<div class="run-row">
				<label class="pick">
					<input
						type="checkbox"
						disabled={item.no_tmdb || batchRunning || pendingSeriesIds.has(item.series.id)}
						onchange={() =>
							selected.has(item.series.id)
								? selected.delete(item.series.id)
								: selected.add(item.series.id)}
					/>
				</label>
				<PosterThumb
					title={item.series.title}
					year={item.series.year}
					posterStatus={item.series.poster_url ? 'deployed' : 'missing'}
					posterUrl={item.series.poster_url}
				/>
				<div class="series-meta">
					<strong>{item.series.title}</strong>
					<span>{item.series.year ?? '—'}</span>
					<div class="chips">
						{#each compressAssets(item.assets_to_run) as chip (chip)}
							<span class="chip">{chip}</span>
						{/each}
					</div>
					{#if item.no_tmdb}<span class="note">No TMDB match — run sync.</span>{/if}
				</div>
				<button
					class="btn-sec"
					onclick={() => startOne(item.series.id)}
					disabled={item.no_tmdb || batchRunning || pendingSeriesIds.has(item.series.id)}
					>Run</button
				>
			</div>
		{/each}
	</div>
{:else if tab === 'review'}
	<div class="run-head">
		<button class="btn-gold" onclick={() => approveAll()}>Approve all auto-picks</button>
	</div>
	<div class="review-grid">
		{#each reviewQueue.items as item (item.series.id)}
			<div class="review-card">
				{#if previewPosters(item).length}
					<div class="preview-strip" aria-label={`${item.series.title} poster previews`}>
						{#each previewPosters(item) as preview (preview.url)}
							<div class="preview-tile" title={preview.label}>
								<img src={preview.url} alt={`${item.series.title} ${preview.label} poster`} />
								<span>{preview.label}</span>
							</div>
						{/each}
					</div>
				{:else}
					<PosterThumb
						title={item.series.title}
						year={item.series.year}
						posterStatus={item.display_poster_url ? 'deployed' : 'missing'}
						posterUrl={item.display_poster_url}
					/>
				{/if}
				<div class="series-meta">
					<strong>{item.series.title}</strong>
					<span
						>{item.seasons_only
							? 'Seasons only'
							: item.show_run
								? `Show + ${item.season_runs.length} seasons`
								: `${item.season_runs.length} seasons`}</span
					>
					{#if item.seasons_only}
						<span class="flag">Seasons only</span>
					{/if}
				</div>
				<div class="review-actions">
					<button class="btn-sec" onclick={() => approveAll(item.series.id)}> Approve all </button>
					<button class="btn-sec" onclick={() => goto(`/pipeline/tv/series/${item.series.id}`)}>
						Open review
					</button>
				</div>
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
	.run-head {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
		margin: 16px 0;
	}
	.run-list,
	.review-grid,
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
		padding: 14px;
	}
	.run-row {
		display: grid;
		grid-template-columns: auto 96px minmax(0, 1fr) auto;
		gap: 14px;
		align-items: center;
	}
	.review-card {
		display: grid;
		grid-template-columns: 96px minmax(0, 1fr) auto;
		gap: 14px;
		text-align: left;
	}
	.preview-strip {
		display: flex;
		gap: 10px;
		overflow-x: auto;
		padding-bottom: 4px;
		scrollbar-width: thin;
	}
	.preview-tile {
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 96px;
	}
	.preview-tile img {
		width: 96px;
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
	.series-meta {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.chip,
	.flag {
		padding: 4px 8px;
		border-radius: 999px;
		background: var(--panel2);
		border: 1px solid var(--line2);
		font-size: 11px;
	}
	.flag {
		color: var(--gold);
		border-color: color-mix(in srgb, var(--gold) 35%, var(--line2));
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
	.btn-gold,
	.btn-sec {
		padding: 9px 12px;
		border-radius: 8px;
		font-size: 13px;
	}
	.btn-gold {
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
	}
	.btn-sec {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	@media (max-width: 900px) {
		.review-card {
			grid-template-columns: minmax(0, 1fr);
		}
		.review-actions {
			display: flex;
			flex-wrap: wrap;
			gap: 10px;
		}
	}
</style>
