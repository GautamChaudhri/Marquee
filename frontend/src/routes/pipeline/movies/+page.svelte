<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { SvelteSet } from 'svelte/reactivity';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import {
		approveReviewQueueAutoPicks,
		clearPipelineCache,
		getPipelineCache,
		getPipelineMetrics,
		getReviewQueue,
		runBatch,
		triggerRun
	} from '$lib/api/pipeline';
	import { listMovies } from '$lib/api/library';
	import { cancelJob, getJob, isTerminal } from '$lib/api/jobs';
	import { ApiError } from '$lib/api/client';
	import { trackJob, type JobProgressDetail } from '$lib/jobs';
	import { toast } from '$lib/toast';
	import { bytesH } from '$lib/display';
	import type {
		BatchScope,
		CacheSizes,
		MovieListItem,
		PipelineMetrics,
		ReviewQueue
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Tab = 'run' | 'review' | 'metrics';
	const PAGE_SIZE = 60;
	let tab = $state<Tab>((page.url.searchParams.get('tab') as Tab) ?? 'run');
	function setTab(id: string) {
		tab = id as Tab;
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
	let cache = $state<CacheSizes | null>(data.cache);
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

	const tabs = $derived([
		{ id: 'run', label: 'Run', count: missingTotal },
		{ id: 'review', label: 'Review', count: queue.total },
		{ id: 'metrics', label: 'Metrics' }
	]);

	async function refreshAll() {
		try {
			const [qd, cd, md, mv] = await Promise.all([
				getReviewQueue(fetch, { page_size: PAGE_SIZE }),
				getPipelineCache(fetch).catch(() => cache),
				getPipelineMetrics(fetch, { limit: 500 }).catch(() => metrics),
				listMovies(fetch, {
					poster_status: 'missing',
					sort: 'title',
					page_size: PAGE_SIZE,
					exclude_in_review: true
				}).catch(() => null)
			]);
			queue = qd;
			queuePage = 1;
			cache = cd;
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

	// ── Auto-approve all review queue items ───────────────────────────────────
	let approveAllOpen = $state(false);
	let approveAllBusy = $state(false);

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

	// ── Runs (batch + single) ────────────────────────────────────────────────────
	const BATCH_STORAGE_KEY = 'marquee:pipeline:activeBatch';
	let batchJobId = $state<string | null>(null);
	let batchDetail = $state<JobProgressDetail>({});
	let batchStatus = $state('running');
	let batchRunning = $state(false);
	let stopBatch: (() => void) | null = null;

	/** Persist batch ID to localStorage so the bar survives a page refresh. */
	function storeBatchId(id: string | null) {
		if (!browser) return;
		if (id) {
			localStorage.setItem(BATCH_STORAGE_KEY, id);
		} else {
			localStorage.removeItem(BATCH_STORAGE_KEY);
		}
	}

	/** Re-attach to a batch after page load: fetch snapshot, then either show
	 *  the result summary or resume tracking via SSE + poll. Mirrors the
	 *  proven letterbox rehydrateBatch pattern. */
	async function rehydrateBatch(jobId: string) {
		let job;
		try {
			job = await getJob(fetch, jobId);
		} catch {
			batchRunning = false;
			batchJobId = null;
			batchStatus = '';
			storeBatchId(null);
			return;
		}
		batchJobId = jobId;
		batchStatus = job.status;

		if (isTerminal(job.status)) {
			batchRunning = false;
			stopBatch?.();
			stopBatch = null;
			storeBatchId(null);
			toast(
				`Batch ${job.status}`,
				job.status === 'succeeded' ? 'good' : job.status === 'cancelled' ? 'info' : 'bad'
			);
			void refreshAll();
		} else {
			batchRunning = true;
			batchDetail = {};
			stopBatch?.();
			stopBatch = trackJob(fetch, jobId, {
				onProgress: ({ status, detail }) => {
					batchStatus = status;
					batchDetail = detail;
				},
				onDone: (j) => {
					batchRunning = false;
					batchStatus = j.status;
					storeBatchId(null);
					toast(`Batch ${j.status}`, j.status === 'succeeded' ? 'good' : 'bad');
					void refreshAll();
				}
			});
		}
	}

	onMount(() => {
		// Priority 1: the load function found an active batch job.
		if (data.activeJob?.job_id) {
			void rehydrateBatch(data.activeJob.job_id);
			return;
		}
		// Priority 2: localStorage still holds a batch ID from before refresh.
		if (browser) {
			const stored = localStorage.getItem(BATCH_STORAGE_KEY);
			if (stored) void rehydrateBatch(stored);
		}
	});

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

	async function startBatch(scope: BatchScope, movieIds?: number[]) {
		if (batchRunning) return;
		if (scope === 'selected' && (!movieIds || movieIds.length === 0)) return;
		batchRunning = true;
		batchDetail = {};
		batchStatus = 'running';
		batchJobId = null;
		try {
			const job = await runBatch(fetch, { scope, movie_ids: movieIds });
			batchJobId = job.job_id;
			storeBatchId(job.job_id);
			const selectedCount = scope === 'selected' ? (movieIds?.length ?? 0) : null;
			toast(
				selectedCount === null
					? 'Poster-analysis batch queued'
					: `Batch queued — ${selectedCount} movie${selectedCount === 1 ? '' : 's'}`,
				'info'
			);
			if (scope === 'selected') selected.clear();
			stopBatch?.();
			stopBatch = trackJob(fetch, job.job_id, {
				onProgress: ({ status, detail }) => {
					batchStatus = status;
					batchDetail = detail;
				},
				onDone: (j) => {
					batchRunning = false;
					batchStatus = j.status;
					storeBatchId(null);
					toast(`Batch ${j.status}`, j.status === 'succeeded' ? 'good' : 'bad');
					void refreshAll();
				}
			});
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

	async function cancelBatch() {
		if (!batchJobId) return;
		try {
			await cancelJob(fetch, batchJobId);
			toast('Cancellation requested', 'info');
		} catch {
			toast('Could not cancel', 'bad');
		}
	}

	async function runSingle(m: MovieListItem) {
		if (m.tmdb_id == null || batchRunning) return;
		try {
			const ref = await triggerRun(fetch, m.id);
			await goto(ref.detail_url);
		} catch (e) {
			if (e instanceof ApiError && e.status === 409) {
				toast('The same poster analysis was already submitted', 'info');
			} else {
				toast(e instanceof Error ? e.message : 'Run failed to start', 'bad');
			}
		}
	}

	async function loadMore() {
		if (loadingMore) return;
		loadingMore = true;
		try {
			const next = await listMovies(fetch, {
				poster_status: 'missing',
				sort: 'title',
				page_size: PAGE_SIZE,
				exclude_in_review: true,
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

	// ── Cache clear ─────────────────────────────────────────────────────────────
	let clearOpen = $state(false);
	let clearBusy = $state(false);
	let inclEmbeddings = $state(true);
	let inclArchives = $state(false);

	async function doClear() {
		clearBusy = true;
		try {
			await clearPipelineCache(fetch, {
				include_embeddings: inclEmbeddings,
				include_archives: inclArchives
			});
			toast('Cache clear queued', 'good');
			clearOpen = false;
			setTimeout(() => void refreshAll(), 1500);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Clear failed', 'bad');
		} finally {
			clearBusy = false;
		}
	}

	// ── Helpers ─────────────────────────────────────────────────────────────────
	function ago(iso: string | null): string {
		if (!iso) return '—';
		const t = new Date(iso).getTime();
		if (Number.isNaN(t)) return '—';
		const s = Math.max(0, (Date.now() - t) / 1000);
		if (s < 60) return 'just now';
		if (s < 3600) return `${Math.floor(s / 60)}m ago`;
		if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
		return `${Math.floor(s / 86400)}d ago`;
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

	onDestroy(() => {
		stopBatch?.();
	});
</script>

<SectionHeader title="Movie posters" subtitle="Run, review, and tune poster selection">
	{#snippet action()}
		<button class="btn-sec clear-btn" onclick={() => (clearOpen = true)} disabled={!cache}>
			<Icon name="refresh" size={14} />
			Clear cache{#if cache}
				({bytesH(cache.clearable_bytes)}){/if}
		</button>
	{/snippet}
</SectionHeader>

{#if data.onboarding?.needs_onboarding}
	<a
		href="/onboarding"
		style="display:flex; align-items:center; gap:12px; padding:12px 16px; margin-bottom:16px; border:1px solid var(--gold-deep); border-radius:var(--radius-sm); background:var(--gold-soft); color:var(--text); text-decoration:none;"
	>
		<span style="color:var(--gold); display:flex;"><Icon name="taste" size={18} /></span>
		<div style="flex:1; display:flex; flex-direction:column;">
			<strong style="color:var(--text);">Jump-start the Key Art Engine</strong>
			<span style="font-size:12px; color:var(--muted);">
				Rank a few movies to teach it your taste — {data.onboarding.ranked}/{data.onboarding.min} so far.
			</span>
		</div>
		<span style="color:var(--gold); font-weight:600;">Start →</span>
	</a>
{/if}

<div class="tabwrap">
	<TabBar {tabs} active={tab} onSelect={setTab} />
</div>

<!-- ═══ REVIEW ═══ -->
{#if tab === 'review'}
	{#if queue.items.length === 0}
		<div class="empty">
			<Icon name="pipeline" size={34} stroke={1} />
			<strong>Nothing to review</strong>
			<span>Runs awaiting a poster decision will collect here. Start one from the Run tab.</span>
		</div>
	{:else}
		<div class="rev-bar">
			<span class="rev-count">{queue.total} run{queue.total === 1 ? '' : 's'} awaiting</span>
			<button class="btn-gold" onclick={() => (approveAllOpen = true)} disabled={queue.total === 0}>
				Approve all auto-picks
			</button>
		</div>
		<div class="rev-grid">
			{#each queue.items as item (item.run.run_id)}
				{@const c = item.run.counts ?? {}}
				<button class="rev-card" onclick={() => goto(`/pipeline/runs/${item.run.run_id}`)}>
					<div class="rev-poster">
						<PosterThumb
							title={item.movie.title}
							year={item.movie.year}
							posterStatus={item.movie.poster_status}
							posterUrl={item.auto_pick_poster_url ?? item.movie.poster_url}
							hdr={item.movie.hdr}
						/>
					</div>
					<div class="rev-meta">
						<div class="rev-title">{item.movie.title}</div>
						<div class="rev-sub">{item.movie.year ?? '—'}</div>
						<div class="rev-stats mono">
							{c.ranked ?? '?'} ranked · {c.total_candidates ?? '?'} cand.
						</div>
						<div class="rev-foot">
							<span class="rev-scorer">{item.run.scorer_name ?? 'scored'}</span>
							<span class="rev-age">{ago(item.run.started_at)}</span>
						</div>
					</div>
				</button>
			{/each}
		</div>
		{#if queue.items.length < queue.total}
			<div class="loadmore">
				<button class="btn-sec" onclick={loadMoreReview} disabled={reviewLoadingMore}>
					{reviewLoadingMore
						? 'Loading…'
						: `Load more review items — ${queue.items.length} of ${queue.total}`}
				</button>
			</div>
		{/if}
	{/if}

	<!-- ═══ RUN ═══ -->
{:else if tab === 'run'}
	{#if batchRunning || batchJobId}
		<div class="batch-status">
			<RunProgress
				detail={batchDetail}
				status={batchStatus}
				title="Batch running"
				onCancel={batchRunning ? cancelBatch : undefined}
			/>
		</div>
	{/if}

	{#if missing.length === 0}
		<div class="empty">
			<Icon name="pipeline" size={34} stroke={1} />
			<strong>Every movie has a poster</strong>
			<span>Nothing is missing artwork right now. You can still re-evaluate the whole library.</span
			>
			<button class="btn-sec" onclick={() => startBatch('all')} disabled={batchRunning}>
				Re-run whole library
			</button>
		</div>
	{:else}
		<div class="run-bar">
			<label class="selall">
				<input type="checkbox" checked={allSelected} onchange={toggleAll} disabled={batchRunning} />
				<span>
					{selectedCount > 0
						? `${selectedCount} selected`
						: `${missingTotal} movie${missingTotal === 1 ? '' : 's'} missing posters`}
				</span>
			</label>
			<div class="run-actions">
				<button class="btn-ghost" onclick={() => startBatch('all')} disabled={batchRunning}>
					Re-run whole library
				</button>
				<button
					class="btn-sec"
					onclick={() => startBatch('selected', [...selected])}
					disabled={batchRunning || selectedCount === 0}
				>
					Run selected ({selectedCount})
				</button>
				<button class="btn-gold" onclick={() => startBatch('missing')} disabled={batchRunning}>
					Run all missing ({missingTotal})
				</button>
			</div>
		</div>

		<div class="missing-list">
			{#each missing as m (m.id)}
				{@const ok = m.tmdb_id != null}
				<div class="mv-row" class:sel={selected.has(m.id)} class:dis={!ok}>
					<label class="mv-check">
						<input
							type="checkbox"
							checked={selected.has(m.id)}
							disabled={!ok || batchRunning}
							onchange={() => toggleSelect(m.id)}
						/>
					</label>
					<button class="mv-main" disabled={!ok || batchRunning} onclick={() => toggleSelect(m.id)}>
						<div class="mv-thumb">
							<PosterThumb
								title={m.title}
								year={m.year}
								posterStatus={m.poster_status}
								posterUrl={m.poster_url}
								hdr={m.hdr}
							/>
						</div>
						<div class="mv-meta">
							<span class="mv-title">{m.title}</span>
							<span class="mv-year mono">{m.year ?? ''}</span>
							{#if !ok}<span class="mv-hint">No TMDB id — sync first</span>{/if}
						</div>
					</button>
					<button
						class="btn-sec mv-run"
						onclick={() => runSingle(m)}
						disabled={!ok || batchRunning}
						title={ok ? 'Run pipeline for this movie' : 'No TMDB id'}
					>
						Run
					</button>
				</div>
			{/each}
		</div>

		{#if missing.length < missingTotal}
			<div class="loadmore">
				<button class="btn-sec" onclick={loadMore} disabled={loadingMore}>
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
	open={clearOpen}
	title="Clear poster-pipeline cache"
	confirmLabel="Clear cache"
	tone="bad"
	busy={clearBusy}
	onConfirm={doClear}
	onCancel={() => (clearOpen = false)}
>
	<p class="dlg-note">
		Removes downloaded poster candidates and working artifacts. Never touches your labels, taste
		profile, the Key Art Engine, or deployed posters.
	</p>
	{#if cache}
		<div class="size-line">
			<span>Work + staging</span><span class="mono"
				>{bytesH(cache.sizes_bytes.runs_work + cache.sizes_bytes.staging)}</span
			>
		</div>
		<div class="size-line">
			<span>Embedding cache</span><span class="mono">{bytesH(cache.sizes_bytes.embeddings)}</span>
		</div>
		<div class="size-line">
			<span>Run archives (history)</span><span class="mono"
				>{bytesH(cache.sizes_bytes.archives)}</span
			>
		</div>
	{/if}
	<label class="toggle">
		<input type="checkbox" bind:checked={inclEmbeddings} />
		Include embedding cache (re-derived next run)
	</label>
	<label class="toggle">
		<input type="checkbox" bind:checked={inclArchives} />
		Include run archives — <b>deletes results history</b>
	</label>
</ConfirmDialog>

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
	.tabwrap {
		padding-bottom: 14px;
		border-bottom: 1px solid var(--line);
		margin-bottom: 20px;
	}
	.clear-btn {
		display: inline-flex;
		align-items: center;
		gap: 7px;
	}

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
		max-width: 360px;
		line-height: 1.5;
	}
	.empty button {
		margin-top: 6px;
	}

	/* ── Review grid ── */
	.rev-bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		flex-wrap: wrap;
		margin-bottom: 14px;
	}
	.rev-count {
		font-size: 13px;
		color: var(--muted);
	}
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
	.rev-stats {
		font-size: 11px;
		color: var(--gold);
		margin-top: 2px;
	}
	.rev-foot {
		display: flex;
		justify-content: space-between;
		gap: 6px;
		font-size: 10.5px;
		color: var(--faint);
		margin-top: 2px;
	}

	/* ── Run: missing-poster list ── */
	.batch-status {
		margin-bottom: 16px;
	}
	.run-bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		flex-wrap: wrap;
		margin-bottom: 14px;
	}
	.selall {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		color: var(--muted);
		cursor: pointer;
	}
	.run-actions {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.missing-list {
		display: flex;
		flex-direction: column;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: var(--panel);
	}
	.mv-row {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 12px;
		border-bottom: 1px solid var(--line);
	}
	.mv-row:last-child {
		border-bottom: none;
	}
	.mv-row.sel {
		background: var(--gold-soft);
	}
	.mv-row.dis {
		opacity: 0.55;
	}
	.mv-check {
		display: flex;
		align-items: center;
	}
	.mv-main {
		flex: 1;
		display: flex;
		align-items: center;
		gap: 11px;
		min-width: 0;
		padding: 0;
		border: none;
		background: transparent;
		text-align: left;
		cursor: pointer;
		color: inherit;
	}
	.mv-main:disabled {
		cursor: default;
	}
	.mv-thumb {
		width: 34px;
		flex: none;
	}
	.mv-meta {
		display: flex;
		align-items: baseline;
		gap: 8px;
		min-width: 0;
	}
	.mv-title {
		font-size: 13px;
		font-weight: 550;
		color: var(--text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.mv-year {
		font-size: 11px;
		color: var(--faint);
		flex: none;
	}
	.mv-hint {
		font-size: 11px;
		color: var(--low);
		flex: none;
	}
	.mv-run {
		flex: none;
		padding: 6px 14px;
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

	/* ── Dialog body ── */
	.dlg-note {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
		line-height: 1.5;
	}
	.size-line {
		display: flex;
		justify-content: space-between;
		font-size: 12px;
		color: var(--muted);
		padding: 3px 0;
		border-bottom: 1px solid var(--line);
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12.5px;
		color: var(--text);
		margin-top: 4px;
	}

	/* ── Buttons ── */
	.btn-gold {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
	}
	.btn-gold:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.btn-sec {
		padding: 8px 14px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 13px;
	}
	.btn-sec:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn-ghost {
		padding: 8px 14px;
		border-radius: 8px;
		border: 1px solid transparent;
		background: transparent;
		color: var(--muted);
		font-size: 13px;
	}
	.btn-ghost:hover:not(:disabled) {
		color: var(--text);
		background: var(--panel2);
	}
	.btn-ghost:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
