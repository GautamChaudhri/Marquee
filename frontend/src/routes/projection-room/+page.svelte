<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { SvelteMap } from 'svelte/reactivity';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import RunningJobCard from '$lib/components/RunningJobCard.svelte';
	import QueuedJobRow from '$lib/components/QueuedJobRow.svelte';
	import ResourcePoolPanel from '$lib/components/ResourcePoolPanel.svelte';
	import WorkerHealthPanel from '$lib/components/WorkerHealthPanel.svelte';
	import HistoryTable from '$lib/components/HistoryTable.svelte';
	import SystemMetricsPanel from '$lib/components/SystemMetricsPanel.svelte';
	import {
		cancelJob,
		getJobDetail,
		getJobMetrics,
		isTerminal,
		listJobs,
		setJobPriority
	} from '$lib/api/jobs';
	import type { JobListItem, JobMetrics } from '$lib/api/jobs';
	import { getMetrics, getMetricsHistory } from '$lib/api/system';
	import type { SystemMetrics, SystemMetricsHistory } from '$lib/api/types';
	import { jitterMs, trackJob, type JobProgressDetail } from '$lib/jobs';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Tab = 'live' | 'history' | 'system';
	let tab = $state<Tab>((page.url.searchParams.get('tab') as Tab) ?? 'live');
	function setTab(id: string) {
		tab = id as Tab;
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('tab', id);
		goto(`/projection-room?${sp.toString()}`, {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	}
	// ── Live: running jobs (multi-job design/21 pattern) ────────────────────
	const ACTIVE_KEY = 'marquee:projection-room:activeJobs';

	function readActiveIds(): string[] {
		if (!browser) return [];
		try {
			return JSON.parse(localStorage.getItem(ACTIVE_KEY) ?? '[]');
		} catch {
			return [];
		}
	}
	function addActiveId(id: string) {
		if (!browser) return;
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient dedup, discarded immediately
		const ids = new Set(readActiveIds());
		ids.add(id);
		localStorage.setItem(ACTIVE_KEY, JSON.stringify([...ids]));
	}
	function removeActiveId(id: string) {
		if (!browser) return;
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient dedup, discarded immediately
		const ids = new Set(readActiveIds());
		ids.delete(id);
		localStorage.setItem(ACTIVE_KEY, JSON.stringify([...ids]));
	}

	let trackedJobs = new SvelteMap<
		string,
		{ job: JobListItem; detail: JobProgressDetail; status: string }
	>();
	// Cleanup callbacks only — never read for rendering, so plain Map is fine.
	// eslint-disable-next-line svelte/prefer-svelte-reactivity -- non-reactive bookkeeping
	const stops = new Map<string, () => void>();

	/** Fetch the job's full current snapshot once, seed state from it (never
	 *  reset to 0%/queued), then resume live tracking through the shared
	 *  trackJob() engine. Mirrors pipeline/+page.svelte's rehydrateBatch. */
	async function rehydrateRunningJob(jobId: string) {
		if (trackedJobs.has(jobId)) return;
		let snapshot: JobListItem;
		try {
			snapshot = await getJobDetail(fetch, jobId);
			if (isTerminal(snapshot.status)) {
				removeActiveId(jobId);
				return;
			}
		} catch {
			removeActiveId(jobId);
			return;
		}
		addActiveId(jobId);
		trackedJobs.set(jobId, {
			job: snapshot,
			detail: (snapshot.progress ?? {}) as JobProgressDetail,
			status: snapshot.status
		});
		const stop = trackJob(
			fetch,
			jobId,
			{
				onProgress: ({ status, detail }) => {
					const entry = trackedJobs.get(jobId);
					if (entry) trackedJobs.set(jobId, { ...entry, status, detail });
				},
				onDone: () => {
					stops.get(jobId)?.();
					stops.delete(jobId);
					trackedJobs.delete(jobId);
					removeActiveId(jobId);
					void refreshTick();
				}
			},
			{ eventsUrl: snapshot.events_url }
		);
		stops.set(jobId, stop);
	}

	function cancelTracked(jobId: string) {
		const entry = trackedJobs.get(jobId);
		if (entry) trackedJobs.set(jobId, { ...entry, status: 'cancelling' });
		void cancelJob(fetch, jobId);
	}

	// ── Live: queued jobs + resource/worker health (plain periodic refresh —
	// these don't need their own SSE, just to stay reasonably fresh) ───────
	// svelte-ignore state_referenced_locally
	let queuedJobs = $state<JobListItem[]>(data.queued.jobs);
	// svelte-ignore state_referenced_locally
	let jobMetrics = $state<JobMetrics>(data.jobMetrics);

	const tabs = $derived([
		{ id: 'live', label: 'Live', count: trackedJobs.size + queuedJobs.length },
		{ id: 'history', label: 'History' },
		{ id: 'system', label: 'System' }
	]);

	async function refreshTick() {
		try {
			const [running, queued, metrics] = await Promise.all([
				listJobs(fetch, { active: true, limit: 50 }),
				listJobs(fetch, { queued_only: true, limit: 50 }),
				getJobMetrics(fetch)
			]);
			queuedJobs = queued.jobs;
			jobMetrics = metrics;
			for (const j of running.jobs) void rehydrateRunningJob(j.job_id);
		} catch {
			/* keep showing stale data on a transient failure */
		}
	}

	// ── System tab ───────────────────────────────────────────────────────
	// svelte-ignore state_referenced_locally
	let hostMetrics = $state<SystemMetrics | null>(data.hostMetrics);
	let historyWindow = $state<'15m' | '1h' | '6h' | '24h'>('1h');
	// svelte-ignore state_referenced_locally
	let hostHistory = $state<SystemMetricsHistory | null>(data.hostHistory);
	async function refreshHostMetrics() {
		try {
			hostMetrics = await getMetrics(fetch);
		} catch {
			/* keep stale reading */
		}
	}

	async function refreshHostHistory() {
		try {
			hostHistory = await getMetricsHistory(fetch, { window: historyWindow });
		} catch {
			/* keep stale history */
		}
	}

	async function changeHistoryWindow(next: '15m' | '1h' | '6h' | '24h') {
		historyWindow = next;
		await refreshHostHistory();
	}

	async function reprioritize(jobId: string, priority: number) {
		await setJobPriority(fetch, jobId, priority);
		await refreshTick();
	}

	let refreshTimer: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient, local to this callback
		const seen = new Set<string>();
		for (const j of data.running.jobs) {
			seen.add(j.job_id);
			void rehydrateRunningJob(j.job_id);
		}
		for (const id of readActiveIds()) {
			if (!seen.has(id)) void rehydrateRunningJob(id);
		}
		refreshTimer = setInterval(() => {
			void refreshTick();
			void refreshHostMetrics();
			void refreshHostHistory();
		}, jitterMs(4000));
	});

	onDestroy(() => {
		if (refreshTimer) clearInterval(refreshTimer);
		for (const stop of stops.values()) stop();
	});

	// ── History tab ──────────────────────────────────────────────────────
	const knownTypes = $derived(
		[
			...new Set([...Object.keys(data.byType.by_type), ...data.running.jobs.map((j) => j.type)])
		].sort()
	);
	const RESOURCE_KEYS = [
		'gpu',
		'media_read',
		'media_write',
		'transcode',
		'network_external',
		'maintenance_exclusive'
	];

	let selectedTypes = $state<Set<string>>(new Set());
	let statusFilter = $state('');
	let computeFilter = $state<'all' | 'gpu' | 'cpu_only' | 'other'>('all');
	let resourceFilter = $state('');
	let subjectQuery = $state('');
	let sinceDate = $state('');
	let untilDate = $state('');

	let historyJobs = $state<JobListItem[]>([]);
	let historyLoading = $state(false);

	function toggleType(t: string) {
		// Copy-then-reassign: selectedTypes ($state) is replaced wholesale below,
		// so the plain-Set reactivity caveat doesn't apply to this local copy.
		// eslint-disable-next-line svelte/prefer-svelte-reactivity
		const next = new Set(selectedTypes);
		if (next.has(t)) next.delete(t);
		else next.add(t);
		selectedTypes = next;
	}

	async function loadHistory() {
		historyLoading = true;
		try {
			const params: Parameters<typeof listJobs>[1] = { limit: 100 };
			if (selectedTypes.size === 1) params.type = [...selectedTypes][0];
			if (statusFilter) params.status = statusFilter;
			if (sinceDate) params.since = Math.floor(new Date(sinceDate).getTime() / 1000);
			if (untilDate) params.until = Math.floor(new Date(untilDate).getTime() / 1000);
			const res = await listJobs(fetch, params);
			let jobs = res.jobs;
			if (selectedTypes.size > 1) jobs = jobs.filter((j) => selectedTypes.has(j.type));
			if (computeFilter !== 'all') {
				jobs = jobs.filter((j) => {
					const keys = Object.keys(j.resource_request ?? {});
					if (computeFilter === 'gpu') return keys.includes('gpu');
					if (computeFilter === 'cpu_only') return keys.length === 0;
					return keys.length > 0 && !keys.includes('gpu');
				});
			}
			if (resourceFilter) {
				jobs = jobs.filter((j) => resourceFilter in (j.resource_request ?? {}));
			}
			if (subjectQuery.trim()) {
				const q = subjectQuery.trim().toLowerCase();
				jobs = jobs.filter((j) =>
					(j.subject?.title ?? j.subject?.id ?? '').toLowerCase().includes(q)
				);
			}
			historyJobs = jobs;
		} catch {
			historyJobs = [];
		} finally {
			historyLoading = false;
		}
	}

	$effect(() => {
		// Re-run whenever any filter changes. selectedTypes is always
		// reassigned to a new Set (never mutated in place), so plain $state
		// reactivity picks up the change like any other tracked dependency.
		void selectedTypes;
		void statusFilter;
		void computeFilter;
		void resourceFilter;
		void sinceDate;
		void untilDate;
		void loadHistory();
	});
</script>

<SectionHeader
	title="Projection Room"
	subtitle="Every job the platform is running, queued, or has run — plus live host metrics."
/>

<TabBar {tabs} active={tab} onSelect={setTab} />

<div class="content">
	{#if tab === 'live'}
		<section>
			<h3>Running ({trackedJobs.size})</h3>
			{#if trackedJobs.size === 0}
				<p class="empty">Nothing is running right now.</p>
			{:else}
				<div class="running-grid">
					{#each [...trackedJobs.entries()] as [jobId, t] (jobId)}
						<RunningJobCard
							job={t.job}
							detail={t.detail}
							status={t.status}
							onCancel={() => cancelTracked(jobId)}
						/>
					{/each}
				</div>
			{/if}
		</section>

		<section>
			<h3>Queued ({queuedJobs.length})</h3>
			<div class="panel-box">
				{#if queuedJobs.length === 0}
					<p class="empty">Nothing waiting.</p>
				{:else}
					{#each queuedJobs as job (job.job_id)}
						<QueuedJobRow {job} resources={jobMetrics.resources} onPriorityChange={reprioritize} />
					{/each}
				{/if}
			</div>
		</section>

		<div class="two-col">
			<section>
				<h3>Resource pools</h3>
				<ResourcePoolPanel resources={jobMetrics.resources} />
			</section>
			<section>
				<h3>Workers</h3>
				<div class="panel-box">
					<WorkerHealthPanel workers={jobMetrics.workers} />
				</div>
			</section>
		</div>
	{:else if tab === 'history'}
		<section>
			<div class="filters">
				<div class="filter-group">
					<span class="filter-label">Type</span>
					<div class="chips">
						{#each knownTypes as t (t)}
							<button class="chip" class:on={selectedTypes.has(t)} onclick={() => toggleType(t)}>
								{t}
							</button>
						{/each}
					</div>
				</div>
				<label class="filter-field">
					<span class="filter-label">Status</span>
					<select bind:value={statusFilter}>
						<option value="">Any</option>
						<option value="succeeded">Succeeded</option>
						<option value="failed">Failed</option>
						<option value="cancelled">Cancelled</option>
						<option value="interrupted">Interrupted</option>
						<option value="dead_letter">Dead letter</option>
					</select>
				</label>
				<label class="filter-field">
					<span class="filter-label">Compute</span>
					<select bind:value={computeFilter}>
						<option value="all">Any</option>
						<option value="gpu">GPU</option>
						<option value="cpu_only">CPU-only</option>
						<option value="other">Other resource</option>
					</select>
				</label>
				<label class="filter-field">
					<span class="filter-label">Resource</span>
					<select bind:value={resourceFilter}>
						<option value="">Any</option>
						{#each RESOURCE_KEYS as k (k)}
							<option value={k}>{k}</option>
						{/each}
					</select>
				</label>
				<label class="filter-field">
					<span class="filter-label">Movie / show</span>
					<input type="text" placeholder="Search title…" bind:value={subjectQuery} />
				</label>
				<label class="filter-field">
					<span class="filter-label">Since</span>
					<input type="date" bind:value={sinceDate} />
				</label>
				<label class="filter-field">
					<span class="filter-label">Until</span>
					<input type="date" bind:value={untilDate} />
				</label>
			</div>

			{#if Object.keys(data.byType.by_type).length > 0}
				<div class="stats-strip">
					{#each Object.entries(data.byType.by_type) as [type, stats] (type)}
						<span class="stat-chip">
							<strong>{type}</strong>
							{(stats.success_rate * 100).toFixed(0)}% success · avg {stats.duration_seconds.avg?.toFixed(
								0
							) ?? '—'}s
						</span>
					{/each}
				</div>
			{/if}

			<div class="panel-box table-box">
				{#if historyLoading}
					<p class="empty">Loading…</p>
				{:else}
					<HistoryTable jobs={historyJobs} onRetried={loadHistory} />
				{/if}
			</div>
		</section>
	{:else if tab === 'system'}
		<section>
			{#if hostMetrics}
				<SystemMetricsPanel
					metrics={hostMetrics}
					history={hostHistory}
					window={historyWindow}
					onWindowChange={changeHistoryWindow}
				/>
			{:else}
				<p class="empty">Host metrics unavailable.</p>
			{/if}
		</section>
	{/if}
</div>

<style>
	.content {
		display: flex;
		flex-direction: column;
		gap: 28px;
		margin-top: 18px;
	}
	section h3 {
		font-size: 13px;
		font-weight: 650;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
		margin: 0 0 10px;
	}
	.empty {
		color: var(--muted);
		font-size: 13px;
		margin: 0;
	}
	.running-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
		gap: 12px;
	}
	.panel-box {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 10px 14px;
	}
	.table-box {
		padding: 0;
		overflow: hidden;
	}
	.two-col {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 20px;
	}
	.filters {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 14px;
		margin-bottom: 14px;
	}
	.filter-field {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.filter-group {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.filter-label {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
	}
	select,
	input[type='text'],
	input[type='date'] {
		background: var(--panel2);
		border: 1px solid var(--line2);
		border-radius: 7px;
		color: var(--text);
		padding: 6px 9px;
		font-size: 12.5px;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		max-width: 480px;
	}
	.chip {
		padding: 3px 9px;
		border-radius: 99px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		font-size: 11.5px;
	}
	.chip.on {
		background: var(--gold-soft);
		border-color: var(--gold-deep);
		color: var(--gold);
	}
	.stats-strip {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin-bottom: 14px;
	}
	.stat-chip {
		padding: 5px 10px;
		border-radius: 8px;
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 11.5px;
		color: var(--muted);
		font-family: var(--font-mono);
	}
	.stat-chip strong {
		color: var(--text);
		font-family: inherit;
	}
</style>
