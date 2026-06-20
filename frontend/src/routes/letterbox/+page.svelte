<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { subscribe } from '$lib/sse';
	import { toast } from '$lib/toast';
	import {
		analyzeAll,
		healDrift,
		applyBatch,
		confirmLetterbox,
		listColumn
	} from '$lib/api/letterbox';
	import { cancelJob as cancelBatchJob, getJob, isTerminal } from '$lib/api/jobs';
	import type { LetterboxAnalyzeSummary, LetterboxColumnItem } from '$lib/api/types';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import LetterboxCard from '$lib/components/LetterboxCard.svelte';
	import LetterboxDetail from '$lib/components/LetterboxDetail.svelte';
	import type { PageData } from './$types';
	import type { LetterboxColumn } from '$lib/api/types';

	let { data }: { data: PageData } = $props();

	type Variant = 'candidates' | 'detected' | 'preview' | 'notlb' | 'processed';

	// ── Reactive tray state (initialized from load, updated via SSE) ───────────
	let cols = $state(data.columns);

	// Sync columns when data changes (e.g., from URL navigation)
	$effect(() => {
		cols = data.columns;
	});
	const selFromUrl = $derived(Number(page.url.searchParams.get('sel')) || null);
	const defaultSel = $derived(
		cols.detected.items[0]?.movie_id ?? cols.candidates.items[0]?.movie_id ?? null
	);
	const selected = $derived(selFromUrl ?? defaultSel);

	function select(id: number) {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('sel', String(id));
		goto(`/letterbox?${sp.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function toggleDsort() {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('dsort', data.detectedDesc ? 'asc' : 'desc');
		goto(`/letterbox?${sp.toString()}`, { keepFocus: true, noScroll: true });
	}

	// ── Board actions ──────────────────────────────────────────────────────────
	let healing = $state(false);
	let processing = $state(false);
	let confirming = $state(false);

	async function doHeal() {
		healing = true;
		try {
			const r = await healDrift(fetch);
			toast(`Heal complete — ${r.reapplied} re-applied of ${r.checked} checked`, 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Heal failed', 'bad');
		} finally {
			healing = false;
		}
	}

	async function processDetected() {
		const ids = cols.detected.items.map((i) => i.movie_id);
		if (ids.length === 0 || processing) return;
		processing = true;

		// Optimistic update: move all detected movies to preview tray
		const movedItems = [...cols.detected.items];
		cols.detected.items = [];
		cols.detected.total = 0;
		for (const item of movedItems) {
			item.status = 'tagged';
			item.reviewed = false;
		}
		cols.preview.items = [...movedItems, ...cols.preview.items];
		cols.preview.total += movedItems.length;

		try {
			await applyBatch(fetch, ids);
			toast(`Applied crop tags to ${ids.length} ${ids.length === 1 ? 'movie' : 'movies'}`, 'good');
			// Refresh to sync with backend state
			await refreshTrays();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Process failed', 'bad');
			// Rollback on error
			await refreshTrays();
		} finally {
			processing = false;
		}
	}

	async function confirmAll() {
		const ids = cols.preview.items.map((i) => i.movie_id);
		if (ids.length === 0 || confirming) return;
		confirming = true;

		// Optimistic update: move all preview movies to processed tray
		const movedItems = [...cols.preview.items];
		cols.preview.items = [];
		cols.preview.total = 0;
		for (const item of movedItems) {
			item.reviewed = true;
		}
		cols.processed.items = [...movedItems, ...cols.processed.items];
		cols.processed.total += movedItems.length;

		try {
			await Promise.all(ids.map((id) => confirmLetterbox(fetch, id)));
			toast(`Confirmed ${ids.length} ${ids.length === 1 ? 'movie' : 'movies'}`, 'good');
			// Refresh to sync with backend state
			await refreshTrays();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Confirm failed', 'bad');
			// Rollback on error
			await refreshTrays();
		} finally {
			confirming = false;
		}
	}

	// ── Analyze (batch frame analysis tracked via the durable job) ─────────────
	// The bar is driven by the `letterbox_detect_batch` job: it updates live from
	// per-child progress events, survives a refresh (re-attaches + the event log
	// replays), and stays until dismissed.
	const LB_BATCH_KEY = 'lb.activeBatch';
	const LB_BATCH_TOTAL_KEY = 'lb.activeBatch.total';
	let analyzing = $state(false);
	let progress = $state(0);
	let progressTotal = $state(0);
	let progressDone = $state(0);
	let currentBatchId = $state<string | null>(null);
	let batchStatus = $state<string | null>(null);
	let result = $state<LetterboxAnalyzeSummary | null>(null);
	let unsub: (() => void) | null = null;
	let refreshQueued = false;
	let pollInterval: ReturnType<typeof setInterval> | null = null;

	// Per-movie progress tracking
	let currentMovie = $state<{
		id: number;
		title: string;
		stage: string;
		progress: number;
	} | null>(null);

	function storeBatch(id: string | null, total?: number) {
		if (!browser) return;
		if (id) {
			localStorage.setItem(LB_BATCH_KEY, id);
			if (total !== undefined) localStorage.setItem(LB_BATCH_TOTAL_KEY, String(total));
		} else {
			localStorage.removeItem(LB_BATCH_KEY);
			localStorage.removeItem(LB_BATCH_TOTAL_KEY);
		}
	}

	/** Map the job's domain-neutral child-status tally to the UI summary. */
	function summaryFrom(s: Record<string, number> | null | undefined): LetterboxAnalyzeSummary | null {
		if (!s) return null;
		const total = Object.values(s).reduce((a, b) => a + (b ?? 0), 0);
		return {
			candidate: s.candidate ?? 0,
			not_letterboxed: s.not_letterboxed ?? 0,
			variable: s.variable_unsafe ?? 0,
			errored: s.errored ?? 0,
			failed: s.failed ?? s.dead_letter ?? 0,
			total,
			completed: total
		};
	}

	/** Move a movie from one tray to another based on its new status. */
	function moveMovieBetweenTrays(movieId: number, newStatus: string) {
		// Find and remove the movie from its current tray
		let movedItem: LetterboxColumnItem | null = null;

		for (const [key, col] of Object.entries(cols) as [keyof typeof cols, LetterboxColumn][]) {
			const idx = col.items.findIndex((i: LetterboxColumnItem) => i.movie_id === movieId);
			if (idx !== -1) {
				movedItem = col.items[idx];
				col.items = col.items.filter((_: LetterboxColumnItem, i: number) => i !== idx);
				col.total = Math.max(0, col.total - 1);
				break;
			}
		}

		if (!movedItem) return; // Movie not found in any tray

		// Update the item's status
		movedItem.status = newStatus;

		// Determine target tray based on status
		let targetTray: keyof typeof cols | null = null;
		if (newStatus === 'prefilter_candidate' || newStatus === 'prefilter_unknown') {
			targetTray = 'candidates';
		} else if (newStatus === 'candidate') {
			targetTray = 'detected';
		} else if (newStatus === 'tagged' && !movedItem.reviewed) {
			targetTray = 'preview';
		} else if (newStatus === 'tagged' && movedItem.reviewed) {
			targetTray = 'processed';
		} else if (newStatus === 'not_letterboxed' || newStatus === 'variable_unsafe' || newStatus === 'skipped') {
			targetTray = 'notLetterboxed';
		}

		if (targetTray && cols[targetTray]) {
			// Add to the target tray (prepend for recency)
			cols[targetTray].items = [movedItem, ...cols[targetTray].items];
			cols[targetTray].total += 1;

			// If we're displaying a limited view, keep it trimmed
			const CAP = 25;
			if (cols[targetTray].items.length > CAP) {
				cols[targetTray].items = cols[targetTray].items.slice(0, CAP);
			}
		}
	}

	/** Fetch updated tray data from the backend and merge into reactive state. */
	async function refreshTrays() {
		if (!browser) return;
		try {
			const [candidates, detected, preview, notLb, processed] = await Promise.all([
				listColumn(fetch, {
					status: 'prefilter_candidate,prefilter_unknown',
					sort: 'confidence',
					page_size: 25
				}),
				listColumn(fetch, {
					status: 'candidate',
					sort: 'confidence',
					desc: data.detectedDesc,
					page_size: 25
				}),
				listColumn(fetch, {
					status: 'tagged',
					reviewed: false,
					sort: 'recent',
					page_size: 25
				}),
				listColumn(fetch, {
					status: 'not_letterboxed',
					sort: 'recent',
					page_size: 25
				}),
				listColumn(fetch, {
					status: 'tagged',
					reviewed: true,
					sort: 'recent',
					page_size: 25
				})
			]);

			// Update reactive state
			cols = {
				candidates,
				detected,
				preview: { items: preview.items.filter(i => !i.reviewed), total: preview.items.filter(i => !i.reviewed).length },
				notLetterboxed: notLb,
				processed: { items: processed.items.filter(i => i.reviewed), total: processed.items.filter(i => i.reviewed).length }
			};
		} catch (e) {
			// Silently degrade - don't spam toasts during active polling
			console.warn('Tray refresh failed:', e);
		}
	}

	/** Start polling for tray updates while batch analysis is running. */
	function startPolling() {
		if (pollInterval) return;
		pollInterval = setInterval(refreshTrays, 3000); // Poll every 3 seconds
	}

	/** Stop polling when batch analysis completes. */
	function stopPolling() {
		if (pollInterval) {
			clearInterval(pollInterval);
			pollInterval = null;
		}
	}

	/** Refresh the trays so finished movies hop to their next tray. Coalesced so a
	 *  burst of replayed events (after a refresh) triggers a single reload. */
	function scheduleTrayRefresh() {
		if (refreshQueued) return;
		refreshQueued = true;
		setTimeout(() => {
			refreshQueued = false;
			refreshTrays(); // Direct refresh instead of invalidateAll
		}, 800); // Debounce to avoid rapid-fire during replayed events
	}

	function onBatchEvent(type: string, raw: unknown) {
		if (type === 'done') {
			finishAnalyze();
			return;
		}
		const ev = (raw ?? {}) as Record<string, unknown>;
		if (typeof ev.state === 'string' && ev.state !== 'progress') batchStatus = ev.state;
		const detail = (ev.detail ?? {}) as Record<string, unknown>;

		// Track per-movie progress
		if (ev.state === 'child_progress') {
			if (typeof detail.movie_id === 'number' && typeof detail.title === 'string') {
				currentMovie = {
					id: detail.movie_id as number,
					title: detail.title as string,
					stage: (detail.stage as string) || 'processing',
					progress: (detail.progress as number) || 0
				};
			}
		}

		// Batch-level progress
		if (typeof detail.children_completed === 'number') {
			progressDone = detail.children_completed as number;
			if (typeof detail.children_total === 'number') progressTotal = detail.children_total as number;
			progress = progressTotal ? (progressDone / progressTotal) * 100 : 0;
			scheduleTrayRefresh(); // a child finished → reflect its move

			// Clear current movie when a child completes
			if (currentMovie && currentMovie.progress >= 90) {
				setTimeout(() => {
					currentMovie = null;
				}, 1000); // Brief delay to show completion
			}
		}

		if (
			(ev.state === 'succeeded' ||
				ev.state === 'failed' ||
				ev.state === 'cancelled' ||
				ev.state === 'interrupted') &&
			detail.summary
		) {
			result = summaryFrom(detail.summary as Record<string, number>);
		}
	}

	/** Subscribe to a batch job's durable event stream. The stream replays all
	 *  prior events on connect, so this catches up to live progress after a refresh. */
	function attachBatch(eventsUrl: string) {
		unsub?.();
		unsub = subscribe(eventsUrl, ['message', 'done'], onBatchEvent);
	}

	async function doAnalyze() {
		if (analyzing) return;
		analyzing = true;
		batchStatus = 'waiting_external';
		result = null;
		progress = 0;
		progressDone = 0;
		progressTotal = 0;
		try {
			const ref = await analyzeAll(fetch);
			progressTotal = ref.total;
			if (ref.total === 0) {
				toast('No candidates to analyze', 'info');
				analyzing = false;
				currentBatchId = null;
				batchStatus = null;
				storeBatch(null);
				return;
			}
			currentBatchId = ref.job_id;
			storeBatch(ref.job_id, ref.total); // Store both ID and total for refresh persistence
			startPolling(); // Start real-time tray polling
			attachBatch(ref.events_url);
		} catch (e) {
			analyzing = false;
			currentBatchId = null;
			batchStatus = null;
			toast(e instanceof Error ? e.message : 'Analysis failed to start', 'bad');
		}
	}

	function finishAnalyze() {
		analyzing = false;
		stopPolling(); // Stop polling when analysis completes
		currentMovie = null; // Clear current movie display
		if (batchStatus === 'succeeded') progress = 100;
		unsub?.();
		unsub = null;
		if (batchStatus === 'cancelled') {
			toast(`Analysis cancelled at ${progressDone}/${progressTotal}`, 'info');
		} else if (batchStatus === 'interrupted') {
			toast(`Analysis stopped at ${progressDone}/${progressTotal}`, 'info');
		} else if (batchStatus === 'failed') {
			toast('Analysis failed', 'bad');
		} else if (result) {
			const nlb = result.not_letterboxed;
			toast(
				`Analysis complete — ${result.candidate} staged${nlb ? `, ${nlb} cleared` : ''}`,
				'good'
			);
		} else {
			toast('Analysis complete', 'good');
		}
		// Final refresh to ensure trays are in sync
		refreshTrays();
		// The batch id stays in localStorage so the summary survives a refresh
		// until the user dismisses it.
	}

	function dismissAnalyze() {
		result = null;
		analyzing = false;
		stopPolling(); // Ensure polling stops
		currentMovie = null; // Clear current movie display
		currentBatchId = null;
		batchStatus = null;
		progress = 0;
		progressDone = 0;
		progressTotal = 0;
		storeBatch(null);
	}

	async function cancelAnalyze() {
		if (!currentBatchId || batchStatus === 'cancelling') return;
		try {
			const job = await cancelBatchJob(fetch, currentBatchId);
			batchStatus = job.status;
			analyzing = !isTerminal(job.status);
			toast(job.status === 'cancelled' ? 'Analysis cancelled' : 'Cancellation requested', 'info');
			if (isTerminal(job.status)) {
				await rehydrateBatch(currentBatchId, { allowActive: true });
			}
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not cancel analysis', 'bad');
		}
	}

	/** Re-attach to an in-flight or just-finished batch after a (re)load. */
	async function rehydrateBatch(jobId: string, options: { allowActive: boolean }) {
		const { allowActive } = options;
		let job;
		try {
			job = await getJob(fetch, jobId);
		} catch {
			currentBatchId = null;
			batchStatus = null;
			storeBatch(null);
			return;
		}
		currentBatchId = jobId;
		batchStatus = job.status;
		const prog = job.progress ?? {};

		// Restore progressTotal from multiple sources to prevent 0/0 display after refresh
		// Priority: job.progress > localStorage > current state
		const storedTotal = browser ? localStorage.getItem(LB_BATCH_TOTAL_KEY) : null;
		const jobTotal = prog.children_total;
		const fallbackTotal = storedTotal ? parseInt(storedTotal, 10) : progressTotal;

		progressTotal = jobTotal ?? fallbackTotal;
		progressDone = prog.children_completed ?? 0;
		progress = progressTotal > 0 ? (progressDone / progressTotal) * 100 : 0;
		if (!allowActive && !isTerminal(job.status)) {
			dismissAnalyze();
			return;
		}
		if (isTerminal(job.status)) {
			analyzing = false;
			result = summaryFrom((job.result?.summary ?? null) as Record<string, number> | null);
			if (!result && !progressTotal) storeBatch(null); // nothing to show → forget it
		} else {
			analyzing = true;
			startPolling(); // Resume polling for rehydrated active batch
			attachBatch(job.events_url);
		}
	}

	onMount(() => {
		const active = data.status?.batch_active ?? null;
		const stored = browser ? localStorage.getItem(LB_BATCH_KEY) : null;
		if (active) {
			void rehydrateBatch(active, { allowActive: true });
			return;
		}
		if (stored) void rehydrateBatch(stored, { allowActive: false });

		// Refresh trays when user returns to tab (ensures data consistency)
		const onVisibilityChange = () => {
			if (document.visibilityState === 'visible' && !analyzing) {
				refreshTrays();
			}
		};
		document.addEventListener('visibilitychange', onVisibilityChange);

		return () => {
			document.removeEventListener('visibilitychange', onVisibilityChange);
		};
	});

	$effect(() => () => {
		unsub?.();
		stopPolling();
	});

	// ── Header chips ───────────────────────────────────────────────────────────
	function relTime(iso: string | null): string {
		if (!iso) return 'never';
		const diff = Date.now() - new Date(iso).getTime();
		const h = Math.floor(diff / 3.6e6);
		if (h < 1) return `${Math.max(1, Math.floor(diff / 6e4))}m ago`;
		if (h < 24) return `${h}h ago`;
		return `${Math.floor(h / 24)}d ago`;
	}

	const st = $derived(data.status);
	const detectedTotal = $derived(cols.detected.total);
	const showAnalyzeBanner = $derived(
		analyzing ||
			result !== null ||
			batchStatus === 'cancelled' ||
			batchStatus === 'interrupted' ||
			batchStatus === 'failed'
	);

	// ── "View all" modal ───────────────────────────────────────────────────────
	interface ModalCfg {
		variant: Variant;
		status: string;
		reviewed?: boolean;
		sort: 'title' | 'confidence' | 'crop' | 'recent';
		label: string;
	}
	let modal = $state<ModalCfg | null>(null);
	let modalItems = $state<LetterboxColumnItem[]>([]);
	let modalLoading = $state(false);

	async function openModal(cfg: ModalCfg) {
		modal = cfg;
		modalItems = [];
		modalLoading = true;
		try {
			const r = await listColumn(fetch, {
				status: cfg.status,
				reviewed: cfg.reviewed,
				sort: cfg.sort,
				page_size: 200
			});
			modalItems = r.items;
		} catch {
			// silently degrade
		} finally {
			modalLoading = false;
		}
	}

	function closeModal() {
		modal = null;
		modalItems = [];
	}

	const MODAL_CFGS: Record<Variant, ModalCfg> = {
		candidates: {
			variant: 'candidates',
			status: 'prefilter_candidate,prefilter_unknown',
			sort: 'confidence',
			label: 'Candidates'
		},
		detected: { variant: 'detected', status: 'candidate', sort: 'confidence', label: 'Staging' },
		preview: {
			variant: 'preview',
			status: 'tagged',
			reviewed: false,
			sort: 'recent',
			label: 'Preview & Confirm'
		},
		notlb: {
			variant: 'notlb',
			status: 'not_letterboxed',
			sort: 'recent',
			label: 'Cleared Candidates'
		},
		processed: {
			variant: 'processed',
			status: 'tagged',
			reviewed: true,
			sort: 'recent',
			label: 'Processed'
		}
	};
</script>

<SectionHeader title="Letterbox" subtitle="Black-bar detection & crop tagging">
	{#snippet action()}
		<button class="btn-sec" onclick={doHeal} disabled={healing}>
			{healing ? 'Healing…' : 'Heal drifted tags'}
		</button>
	{/snippet}
</SectionHeader>

{#if st}
	<div class="statusbar">
		<span class="chip">
			<StatusDot tone={st.enabled ? 'good' : 'bad'} size={7} />
			{st.enabled ? 'Detection enabled' : 'Disabled'}
		</span>
		<span class="chip mono">method: {st.method}</span>
		<span class="chip mono">Open Matte: {st.full_frame ?? 0}</span>
		{#each Object.entries(st.binaries) as [name, ok] (name)}
			<span class="chip mono" class:bad={!ok}>{name} {ok ? '✓' : '✗'}</span>
		{/each}
		<span class="chip mono faint">last scan {relTime(st.last_scan)}</span>
	</div>
{:else if data.error}
	<div class="banner err">{data.error} The backend may be offline.</div>
{/if}

<div class="info">
	<Icon name="letterbox" size={15} />
	<span>
		MKV pixel-crop tags are honored by <strong>Plex Desktop · VLC · mpv</strong> — but
		<strong>not</strong> Plex Web or Mobile. Detection never writes to media; tags are reversible.
	</span>
</div>

{#if showAnalyzeBanner}
	<div class="analyze-bar" class:done={!analyzing}>
		<div class="ab-row">
			<span class="ab-label">
				{#if analyzing}
					{#if batchStatus === 'cancelling'}
						<span class="spin">⟳</span> Cancelling analysis… {progressDone}/{progressTotal}
					{:else}
						<span class="spin">⟳</span> Analyzing candidates… {progressDone}/{progressTotal}
					{/if}
				{:else if batchStatus === 'cancelled' || batchStatus === 'interrupted'}
					Analysis stopped at <strong>{progressDone}</strong>/<strong>{progressTotal}</strong>
					{#if result}
						· <strong>{result.candidate}</strong> detected ·
						<strong>{result.not_letterboxed}</strong> not letterboxed · {result.variable} unsafe
						{#if result.errored || result.failed} · {result.errored + result.failed} failed{/if}
					{/if}
				{:else if batchStatus === 'failed'}
					Analysis failed at <strong>{progressDone}</strong>/<strong>{progressTotal}</strong>
					{#if result}
						· <strong>{result.candidate}</strong> detected ·
						<strong>{result.not_letterboxed}</strong> not letterboxed · {result.variable} unsafe
						{#if result.errored || result.failed} · {result.errored + result.failed} failed{/if}
					{/if}
				{:else if result}
					✓ Analysis complete — <strong>{result.candidate}</strong> detected ·
					<strong>{result.not_letterboxed}</strong> not letterboxed · {result.variable} unsafe
					{#if result.errored || result.failed} · {result.errored + result.failed} failed{/if}
				{/if}
			</span>
			<div class="ab-actions">
			{#if analyzing}
				<button
					class="ab-cancel"
					onclick={cancelAnalyze}
					disabled={batchStatus === 'cancelling' || !currentBatchId}
				>
					{batchStatus === 'cancelling' ? 'Cancelling…' : 'Cancel'}
				</button>
			{:else}
				<button class="ab-dismiss" onclick={dismissAnalyze} aria-label="Dismiss">
					<Icon name="x" size={14} />
				</button>
			{/if}
			</div>
		</div>
		{#if analyzing}
			<ProgressBar value={progress} tone="gold" />
			{#if currentMovie}
				<div class="current-movie-progress">
					<div class="cmp-header">
						<span class="cmp-title">{currentMovie.title}</span>
						<span class="cmp-stage">
							{#if currentMovie.stage === 'started'}
								Starting analysis…
							{:else if currentMovie.stage === 'probing'}
								Probing video file…
							{:else if currentMovie.stage === 'analyzing'}
								Analyzing frames…
							{:else if currentMovie.stage === 'consensus'}
								Calculating crop…
							{:else}
								{currentMovie.stage}
							{/if}
						</span>
					</div>
					<ProgressBar value={currentMovie.progress} tone="info" height={4} />
				</div>
			{/if}
		{/if}
	</div>
{/if}

<LetterboxDetail movieId={selected} onChanged={refreshTrays} onAnalyzeAll={doAnalyze} {analyzing} />

<!-- Stage breadcrumb -->
<div class="flow">
	<span class="pill" style="--c:var(--warn)">Candidates</span>
	<span class="arrow">→ analyze →</span>
	<span class="pill" style="--c:var(--bad)">Cleared Candidates</span>
	<span class="dotsep">·</span>
	<span class="pill" style="--c:var(--info)">Staging</span>
	<span class="arrow">→ select fix → process →</span>
	<span class="pill" style="--c:var(--dovi)">Preview &amp; Confirm</span>
	<span class="arrow">→ confirm →</span>
	<span class="pill" style="--c:var(--good)">Done</span>
</div>

{#snippet rows(items: typeof cols.candidates.items, total: number, variant: Variant)}
	{#if items.length === 0}
		<div class="tray-empty">—</div>
	{:else}
		{#each items as item (item.movie_id)}
			<LetterboxCard {item} {variant} selected={item.movie_id === selected} onSelect={select} />
		{/each}
	{/if}
	{#if total > items.length}
		<button class="tray-more" onclick={() => openModal(MODAL_CFGS[variant])}>
			view all {total}
		</button>
	{/if}
{/snippet}

<!-- Top trays: Candidates | Staging | Preview & Confirm -->
<div class="trays top">
	<section class="tray" style="--accent:var(--warn)">
		<div class="tray-head">
			<div class="tray-title-row">
				<span class="tray-title">Candidates</span>
				<span class="tray-count">{cols.candidates.total}</span>
				<span class="spacer"></span>
				<span class="tray-note">res probe</span>
			</div>
			<div class="tray-actions">
				<button class="tb gold" onclick={doAnalyze} disabled={analyzing}>
					{analyzing ? 'Analyzing…' : 'Analyze →'}
				</button>
			</div>
		</div>
		<div class="tray-body">{@render rows(cols.candidates.items, cols.candidates.total, 'candidates')}</div>
	</section>

	<section class="tray" style="--accent:var(--info)">
		<div class="tray-head">
			<div class="tray-title-row">
				<span class="tray-title">Staging</span>
				<span class="tray-count">{cols.detected.total}</span>
				<button
					class="sort-toggle"
					onclick={toggleDsort}
					title={data.detectedDesc ? 'Confidence: high → low' : 'Confidence: low → high'}
				>
					conf {data.detectedDesc ? '▼' : '▲'}
				</button>
				<span class="spacer"></span>
				<span class="tray-note ready">{detectedTotal}/{detectedTotal} ready</span>
			</div>
			<div class="tray-actions">
				<span class="tb quick-active">⚡ All Quick</span>
				<span class="tb perm-disabled" title="Re-encode coming soon">🔧 All Perm</span>
				<button class="tb gold" onclick={processDetected} disabled={processing || detectedTotal === 0}>
					{processing ? 'Processing…' : 'Process →'}
				</button>
			</div>
		</div>
		<div class="tray-body">{@render rows(cols.detected.items, cols.detected.total, 'detected')}</div>
	</section>

	<section class="tray" style="--accent:var(--dovi)">
		<div class="tray-head simple">
			<span class="tray-title">Preview &amp; Confirm</span>
			<span class="tray-count">{cols.preview.total}</span>
			<span class="spacer"></span>
			<button
				class="tb gold sm"
				onclick={confirmAll}
				disabled={confirming || cols.preview.total === 0}
			>
				<Icon name="refresh" size={13} />
				{confirming ? 'Confirming…' : 'Confirm all'}
			</button>
		</div>
		<div class="tray-body">{@render rows(cols.preview.items, cols.preview.total, 'preview')}</div>
	</section>
</div>

<!-- Bottom trays: Cleared Candidates | Processed -->
<div class="trays bottom">
	<section class="tray" style="--accent:var(--bad)">
		<div class="tray-head simple">
			<span class="tray-title">Cleared Candidates</span>
			<span
				class="info-dot"
				title="These movies looked like possible letterbox candidates from their resolution, but analysis verified no crop is needed."
				aria-label="These movies looked like possible letterbox candidates from their resolution, but analysis verified no crop is needed."
			>
				i
			</span>
			<span class="tray-count">{cols.notLetterboxed.total}</span>
			<span class="spacer"></span>
			<span class="tray-note">verified clear · Open Matte {st?.full_frame ?? 0}</span>
		</div>
		<div class="tray-body">
			{@render rows(cols.notLetterboxed.items, cols.notLetterboxed.total, 'notlb')}
		</div>
	</section>

	<section class="tray" style="--accent:var(--good)">
		<div class="tray-head simple">
			<span class="tray-title">Processed</span>
			<span class="tray-count">{cols.processed.total}</span>
		</div>
		<div class="tray-body">{@render rows(cols.processed.items, cols.processed.total, 'processed')}</div>
	</section>
</div>

<!-- "View all" modal -->
{#if modal}
	<div
		class="modal-backdrop"
		role="dialog"
		aria-modal="true"
		aria-label={modal.label}
		tabindex="-1"
		onclick={(e) => { if (e.target === e.currentTarget) closeModal(); }}
		onkeydown={(e) => e.key === 'Escape' && closeModal()}
	>
		<div class="modal-card">
			<div class="modal-head">
				<span class="modal-title">{modal.label}</span>
				<button class="modal-close" onclick={closeModal} aria-label="Close">
					<Icon name="x" size={16} />
				</button>
			</div>
			<div class="modal-body">
				{#if modalLoading}
					<div class="modal-loading">Loading…</div>
				{:else if modalItems.length === 0}
					<div class="modal-loading">No items found.</div>
				{:else}
					{#each modalItems as item (item.movie_id)}
						<LetterboxCard
							{item}
							variant={modal.variant}
							selected={item.movie_id === selected}
							onSelect={(id) => { select(id); closeModal(); }}
						/>
					{/each}
				{/if}
			</div>
		</div>
	</div>
{/if}

<style>
	.statusbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-bottom: 14px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-size: 11.5px;
		color: var(--muted);
		padding: 3px 9px;
		border: 1px solid var(--line);
		border-radius: 99px;
		background: var(--panel);
	}
	.chip.bad {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 30%, var(--line));
	}
	.chip.faint {
		color: var(--faint);
	}
	.mono {
		font-family: var(--font-mono);
	}
	.info {
		display: flex;
		align-items: flex-start;
		gap: 9px;
		padding: 11px 14px;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-left: 3px solid var(--info);
		border-radius: var(--radius-sm);
		font-size: 12px;
		color: var(--muted);
		line-height: 1.5;
		margin-bottom: 16px;
	}
	.info :global(svg) {
		flex: none;
		margin-top: 1px;
		color: var(--info);
	}
	.banner.err {
		padding: 11px 14px;
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--bad) 10%, transparent);
		border: 1px solid color-mix(in srgb, var(--bad) 30%, transparent);
		color: var(--bad);
		font-size: 13px;
		margin-bottom: 16px;
	}

	.analyze-bar {
		border: 1px solid var(--gold-deep);
		background: var(--gold-soft);
		border-radius: var(--radius-sm);
		padding: 11px 14px;
		margin-bottom: 16px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.analyze-bar.done {
		border-color: color-mix(in srgb, var(--good) 40%, var(--line2));
		background: color-mix(in srgb, var(--good) 8%, transparent);
	}
	.ab-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	.ab-label {
		font-size: 12.5px;
		color: var(--text);
	}
	.ab-label strong {
		color: var(--gold);
	}
	.analyze-bar.done .ab-label strong {
		color: var(--good);
	}
	.current-movie-progress {
		margin-top: 8px;
		padding: 10px 12px;
		background: color-mix(in srgb, var(--info) 8%, transparent);
		border: 1px solid color-mix(in srgb, var(--info) 20%, var(--line));
		border-radius: var(--radius-sm);
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.cmp-header {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 12px;
		font-size: 12px;
	}
	.cmp-title {
		font-weight: 500;
		color: var(--text);
		flex: 1;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.cmp-stage {
		color: var(--muted);
		font-size: 11px;
		flex: none;
	}
	.ab-dismiss {
		flex: none;
		display: grid;
		place-items: center;
		width: 24px;
		height: 24px;
		border: none;
		background: transparent;
		color: var(--muted);
		border-radius: 6px;
	}
	.ab-actions {
		flex: none;
		display: flex;
		align-items: center;
	}
	.ab-dismiss:hover {
		background: var(--panel2);
		color: var(--text);
	}
	.ab-cancel {
		flex: none;
		border: 1px solid color-mix(in srgb, var(--gold) 40%, var(--line2));
		background: color-mix(in srgb, var(--gold) 14%, transparent);
		color: var(--text);
		border-radius: 999px;
		padding: 6px 10px;
		font-size: 12px;
		font-weight: 600;
	}
	.ab-cancel:disabled {
		opacity: 0.7;
		cursor: default;
	}

	/* breadcrumb */
	.flow {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 7px;
		margin-bottom: 14px;
	}
	.pill {
		padding: 4px 11px;
		border-radius: 20px;
		font-size: 11px;
		font-weight: 700;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 12%, transparent);
	}
	.arrow,
	.dotsep {
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--faint);
	}

	/* trays */
	.trays {
		display: grid;
		gap: 14px;
	}
	.trays.top {
		grid-template-columns: 1fr 1fr 1fr;
		margin-bottom: 14px;
	}
	.trays.bottom {
		grid-template-columns: 2fr 1fr;
	}
	@media (max-width: 1000px) {
		.trays.top,
		.trays.bottom {
			grid-template-columns: 1fr;
		}
	}
	.tray {
		background: var(--panel);
		border: 1px solid var(--line);
		border-top: 2px solid var(--accent);
		border-radius: 12px;
		overflow: hidden;
		display: flex;
		flex-direction: column;
	}
	.tray-head {
		padding: 12px 14px 10px;
		border-bottom: 1px solid var(--panel2);
	}
	.tray-head.simple {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 14px;
	}
	.tray-title-row {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 9px;
	}
	.tray-title {
		font-size: 12.5px;
		font-weight: 700;
		color: var(--accent);
	}
	.tray-count {
		font-family: var(--font-mono);
		font-size: 11px;
		background: color-mix(in srgb, var(--accent) 14%, transparent);
		color: var(--accent);
		padding: 1px 7px;
		border-radius: 7px;
	}
	.info-dot {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 16px;
		height: 16px;
		border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
		border-radius: 50%;
		color: var(--accent);
		font-family: var(--font-mono);
		font-size: 10px;
		cursor: help;
	}
	.spacer {
		flex: 1;
	}
	.sort-toggle {
		font-family: var(--font-mono);
		font-size: 10.5px;
		color: var(--faint);
		background: transparent;
		border: 1px solid var(--panel2);
		border-radius: 6px;
		padding: 1px 6px;
		cursor: pointer;
	}
	.sort-toggle:hover {
		color: var(--text);
		border-color: var(--faint);
	}
	.tray-note {
		font-size: 11px;
		color: var(--faint);
	}
	.tray-note.ready {
		font-family: var(--font-mono);
		color: var(--good);
	}
	.tray-actions {
		display: flex;
		gap: 5px;
	}
	.tb {
		flex: 1;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 5px;
		border-radius: 7px;
		padding: 6px 8px;
		font-weight: 600;
		font-size: 11.5px;
		white-space: nowrap;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.tb.sm {
		flex: none;
		padding: 6px 13px;
	}
	.tb.gold {
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		border-color: var(--gold-deep);
	}
	.tb.quick-active {
		background: color-mix(in srgb, var(--info) 10%, transparent);
		color: var(--info);
		border-color: color-mix(in srgb, var(--info) 28%, transparent);
		cursor: default;
	}
	.tb.perm-disabled {
		background: color-mix(in srgb, var(--low) 8%, transparent);
		color: var(--low);
		border-color: color-mix(in srgb, var(--low) 22%, transparent);
		opacity: 0.55;
		cursor: not-allowed;
	}
	.tb:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.tray-body {
		display: flex;
		flex-direction: column;
		min-height: 60px;
		max-height: 390px;
		overflow-y: auto;
	}
	.tray-empty {
		text-align: center;
		color: var(--faint2);
		font-size: 13px;
		padding: 20px 0;
	}
	.tray-more {
		position: sticky;
		bottom: 0;
		flex-shrink: 0;
		width: 100%;
		text-align: center;
		font-size: 11.5px;
		color: var(--muted);
		padding: 8px 0;
		border: none;
		border-top: 1px solid var(--panel2);
		background: var(--panel);
		box-shadow: 0 -4px 10px rgba(0, 0, 0, 0.2);
		cursor: pointer;
	}
	.tray-more:hover {
		color: var(--text);
		background: var(--panel2);
	}

	.btn-sec {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		padding: 8px 14px;
		border-radius: 8px;
		font-size: 13px;
		font-weight: 500;
		white-space: nowrap;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.btn-sec:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.spin {
		display: inline-block;
		animation: spin 1s linear infinite;
	}

	/* modal */
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: color-mix(in srgb, var(--bg) 60%, transparent);
		backdrop-filter: blur(4px);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 100;
	}
	.modal-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		width: 100%;
		max-width: 480px;
		max-height: 70vh;
		display: flex;
		flex-direction: column;
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5);
	}
	.modal-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.modal-title {
		font-size: 14px;
		font-weight: 700;
		color: var(--text);
	}
	.modal-close {
		display: grid;
		place-items: center;
		width: 28px;
		height: 28px;
		border: none;
		background: transparent;
		color: var(--muted);
		border-radius: 6px;
		cursor: pointer;
	}
	.modal-close:hover {
		background: var(--panel2);
		color: var(--text);
	}
	.modal-body {
		flex: 1;
		overflow-y: auto;
	}
	.modal-loading {
		text-align: center;
		color: var(--faint);
		font-size: 13px;
		padding: 32px 0;
	}
</style>
