<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { api, call } from '$lib/api/client';
	import type { Job, JobEvent, WorkerInfo } from '$lib/api/types';
	import {
		FEED_EVERY_MS,
		FEED_LIMIT,
		JOB_PAGE_SIZE,
		LIST_EVERY_MS,
		activityFromParams,
		digestFeed,
		paramsFromActivity,
		type ActivityQuery
	} from '$lib/features/jobs/feed';
	import JobDetail from '$lib/features/jobs/JobDetail.svelte';
	import { KIND_NAMES, describeJob, kindLabel, subjectLabel } from '$lib/features/jobs/jobs';
	import StateBadge from '$lib/features/jobs/StateBadge.svelte';
	import { formatWhen } from '$lib/format';
	import { poll } from '$lib/poll';
	import { toast } from '$lib/stores/toast';
	import Button from '$lib/ui/Button.svelte';
	import Notice from '$lib/ui/Notice.svelte';

	const query = $derived(activityFromParams(page.url.searchParams));

	let worker = $state<WorkerInfo | null>(null);
	let workerError = $state<string | null>(null);
	let jobs = $state<Job[]>([]);
	let total = $state<number | null>(null);
	let listError = $state<string | null>(null);
	let listLoading = $state(false);
	let detail = $state<Job | null>(null);
	let detailError = $state<string | null>(null);
	let events = $state<JobEvent[] | null>(null);
	let eventsError = $state<string | null>(null);
	let busy = $state<'cancel' | 'retry' | null>(null);
	let cursor: number | null = null;
	let listToken = 0;

	const pages = $derived(total === null ? 1 : Math.max(1, Math.ceil(total / JOB_PAGE_SIZE)));

	function go(changes: Partial<ActivityQuery>) {
		const params = paramsFromActivity({ ...query, ...changes });
		void goto(params.size ? `?${params}` : '/activity', {
			keepFocus: true,
			noScroll: true,
			replaceState: 'page' in changes ? false : true
		});
	}

	async function readWorker() {
		const result = await call(api.GET('/api/worker'));
		workerError = result.ok ? null : result.message;
		if (result.ok) worker = result.data;
	}

	async function readList(current: ActivityQuery = query) {
		const token = ++listToken;
		listLoading = true;
		const result = await call(
			api.GET('/api/jobs', {
				params: {
					query: {
						page: current.page,
						page_size: JOB_PAGE_SIZE,
						...(current.state ? { state: current.state } : {}),
						...(current.kind ? { kind: current.kind } : {})
					}
				}
			})
		);
		if (token !== listToken) return;
		listLoading = false;
		if (!result.ok) {
			listError = result.message;
			return;
		}
		listError = null;
		jobs = result.data.items;
		total = result.data.total;
	}

	async function readEvents(jobId: number) {
		const result = await call(
			api.GET('/api/jobs/{job_id}/events', { params: { path: { job_id: jobId } } })
		);
		if (detail?.id !== jobId && query.job !== jobId) return;
		eventsError = result.ok ? null : result.message;
		if (result.ok) events = result.data.items;
	}

	async function readJob(jobId: number): Promise<Job | null> {
		const result = await call(
			api.GET('/api/jobs/{job_id}', { params: { path: { job_id: jobId } } })
		);
		if (!result.ok) {
			if (query.job === jobId) detailError = result.message;
			return null;
		}
		const job = result.data;
		jobs = jobs.map((row) => (row.id === job.id ? job : row));
		if (query.job === job.id) {
			detail = job;
			detailError = null;
		}
		return job;
	}

	// Opening a job's detail changes the address but not the list.
	const listKey = $derived(`${query.state}|${query.kind}|${query.page}`);
	$effect(() => {
		void listKey;
		void readList(activityFromParams(page.url.searchParams));
	});

	$effect(() => {
		const jobId = query.job;
		detail = null;
		events = null;
		detailError = null;
		eventsError = null;
		if (jobId === null) return;
		void readJob(jobId);
		void readEvents(jobId);
	});

	/** Find where the feed ends, so the page follows what happens from now on. */
	async function feedEnd(): Promise<number | null> {
		let after = 0;
		for (let step = 0; step < 50; step += 1) {
			const result = await call(
				api.GET('/api/jobs/events', { params: { query: { after, limit: FEED_LIMIT } } })
			);
			if (!result.ok) return null;
			after = result.data.next_after;
			if (result.data.items.length < FEED_LIMIT) return after;
		}
		return after;
	}

	async function readFeed() {
		if (cursor === null) {
			cursor = await feedEnd();
			return;
		}
		const result = await call(
			api.GET('/api/jobs/events', { params: { query: { after: cursor, limit: FEED_LIMIT } } })
		);
		if (!result.ok || result.data.items.length === 0) return;
		cursor = result.data.next_after;
		const shown = jobs.map((job) => job.id);
		if (query.job !== null) shown.push(query.job);
		const { reread, listChanged } = digestFeed(result.data.items, shown);
		await Promise.all(reread.map((id) => readJob(id)));
		if (query.job !== null && reread.includes(query.job)) await readEvents(query.job);
		if (listChanged) await readList();
		await readWorker();
	}

	onMount(() => {
		const stopFeed = poll(readFeed, FEED_EVERY_MS);
		// Whatever the feed says, read the list again now and then: the feed can skip an event.
		let first = true;
		const stopList = poll(async () => {
			if (first) {
				first = false;
				await readWorker();
				return;
			}
			await Promise.all([readList(), readWorker()]);
			if (query.job !== null) await Promise.all([readJob(query.job), readEvents(query.job)]);
		}, LIST_EVERY_MS);
		return () => {
			stopFeed();
			stopList();
		};
	});

	async function act(kind: 'cancel' | 'retry') {
		if (!detail) return;
		busy = kind;
		const path = { params: { path: { job_id: detail.id } } };
		const result = await call(
			kind === 'cancel'
				? api.POST('/api/jobs/{job_id}/cancel', path)
				: api.POST('/api/jobs/{job_id}/retry', path)
		);
		busy = null;
		if (!result.ok) {
			toast(result.message, 'bad');
			return;
		}
		toast(
			kind === 'cancel'
				? `Job ${result.data.id}: ${describeJob(result.data)}`
				: `Job ${result.data.id} is queued again.`,
			'good'
		);
		await readJob(result.data.id);
		await readEvents(result.data.id);
		await readList();
	}
</script>

<svelte:head>
	<title>Activity · Marquee</title>
</svelte:head>

<h1 class="sr-only">Activity</h1>

<section class="worker" data-testid="worker-panel">
	{#if workerError}
		<Notice tone="bad" message="The worker's state could not be read. {workerError}" />
	{:else if !worker}
		<Notice tone="loading" message="Reading the worker’s state…" />
	{:else}
		<span class="dot" class:on={worker.running}></span>
		<strong>{worker.running ? 'The worker is running' : 'No worker is running'}</strong>
		{#if worker.running && worker.since}<span class="muted">since {formatWhen(worker.since)}</span
			>{/if}
		<span class="counts">
			{worker.queued} queued · {worker.running_jobs} running · {worker.failed} failed
		</span>
		{#if !worker.running && worker.queued}
			<span class="muted">Queued jobs wait until a worker starts.</span>
		{/if}
	{/if}
</section>

<div class="toolbar">
	<label class="field">
		State
		<select
			class="control"
			value={query.state ?? ''}
			onchange={(e) =>
				go({ state: (e.currentTarget.value || null) as ActivityQuery['state'], page: 1 })}
		>
			<option value="">Any</option>
			<option value="queued">Queued</option>
			<option value="running">Running</option>
			<option value="succeeded">Succeeded</option>
			<option value="failed">Failed</option>
			<option value="cancelled">Cancelled</option>
		</select>
	</label>
	<label class="field">
		Kind
		<select
			class="control"
			value={query.kind ?? ''}
			onchange={(e) => go({ kind: e.currentTarget.value || null, page: 1 })}
		>
			<option value="">Any</option>
			{#each KIND_NAMES as kind (kind)}
				<option value={kind}>{kindLabel(kind)}</option>
			{/each}
		</select>
	</label>
	<span class="total">{total === null ? '' : `${total} ${total === 1 ? 'job' : 'jobs'}`}</span>
</div>

<div class="layout" class:with-detail={query.job !== null}>
	<div class="list">
		{#if listError && jobs.length === 0}
			<Notice tone="bad" message="The jobs could not be loaded. {listError}">
				{#snippet action()}<Button onclick={() => readList()}>Try again</Button>{/snippet}
			</Notice>
		{:else if total === null}
			<Notice tone="loading" message="Loading the jobs…" />
		{:else if total === 0}
			<Notice message={query.state || query.kind ? 'No job matches.' : 'No job has run yet.'} />
		{:else}
			{#if listError}<Notice tone="bad" message="The list may be out of date. {listError}" />{/if}
			<table data-testid="jobs">
				<thead>
					<tr>
						<th>Job</th>
						<th>Kind</th>
						<th>Movie</th>
						<th>State</th>
						<th>Progress</th>
						<th>Queued</th>
						<th>Finished</th>
					</tr>
				</thead>
				<tbody>
					{#each jobs as job (job.id)}
						<tr class:selected={query.job === job.id} data-testid="job-row">
							<td><a href="?{paramsFromActivity({ ...query, job: job.id })}">#{job.id}</a></td>
							<td>{kindLabel(job.kind)}</td>
							<td class="movie">
								{#if job.subject}
									<a href="/movies/{job.subject.movie_id}">{subjectLabel(job.subject)}</a>
								{:else}
									<span class="none">—</span>
								{/if}
							</td>
							<td><StateBadge state={job.state} /></td>
							<td class="progress">{describeJob(job)}</td>
							<td class="when">{formatWhen(job.created_at)}</td>
							<td class="when">{formatWhen(job.finished_at)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			<nav class="pager" aria-label="Pages">
				<Button
					disabled={query.page <= 1 || listLoading}
					onclick={() => go({ page: query.page - 1 })}
				>
					Newer
				</Button>
				<span>Page {query.page} of {pages}</span>
				<Button
					disabled={query.page >= pages || listLoading}
					onclick={() => go({ page: query.page + 1 })}
				>
					Older
				</Button>
			</nav>
		{/if}
	</div>
	{#if query.job !== null}
		<div class="side">
			{#if detail}
				<JobDetail
					job={detail}
					{events}
					{eventsError}
					{busy}
					onCancel={() => act('cancel')}
					onRetry={() => act('retry')}
					onClose={() => go({ job: null })}
				/>
			{:else if detailError}
				<Notice tone="bad" message="Job {query.job} could not be loaded. {detailError}">
					{#snippet action()}<Button onclick={() => go({ job: null })}>Close</Button>{/snippet}
				</Notice>
			{:else}
				<Notice tone="loading" message="Loading job {query.job}…" />
			{/if}
		</div>
	{/if}
</div>

<style>
	.worker {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px 12px;
		padding: 12px 14px;
		margin-bottom: 16px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		font-size: 13px;
	}
	.worker :global(.notice) {
		flex: 1;
		border: 0;
		padding: 0;
	}
	.dot {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		background: var(--faint);
	}
	.dot.on {
		background: var(--good);
	}
	.muted {
		color: var(--muted);
	}
	.counts {
		color: var(--muted);
		font-variant-numeric: tabular-nums;
	}
	.toolbar {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 10px;
		margin-bottom: 12px;
	}
	.total {
		margin-left: auto;
		color: var(--muted);
		font-size: 12.5px;
	}
	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 16px;
	}
	.layout.with-detail {
		grid-template-columns: minmax(0, 1fr) minmax(0, 380px);
	}
	.side {
		align-self: start;
		position: sticky;
		top: calc(var(--header-h) + 12px);
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}
	th {
		text-align: left;
		font-weight: 550;
		color: var(--muted);
		font-size: 12px;
		padding: 6px 8px;
		border-bottom: 1px solid var(--line);
	}
	td {
		padding: 8px;
		border-bottom: 1px solid var(--line);
		vertical-align: top;
	}
	tr.selected td {
		background: var(--gold-soft);
	}
	td a {
		color: var(--gold-copy);
		font-variant-numeric: tabular-nums;
	}
	.progress {
		color: var(--muted);
		overflow-wrap: anywhere;
	}
	.movie a {
		font-variant-numeric: normal;
	}
	.none {
		color: var(--muted);
	}
	.when {
		white-space: nowrap;
		color: var(--muted);
	}
	.pager {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 12px;
		margin-top: 14px;
		font-size: 12.5px;
		color: var(--muted);
	}
	@media (max-width: 1000px) {
		.layout.with-detail {
			grid-template-columns: minmax(0, 1fr);
		}
		.side {
			position: static;
			order: -1;
		}
	}
	@media (max-width: 720px) {
		thead {
			display: none;
		}
		table,
		tbody {
			display: block;
		}
		tr {
			display: grid;
			grid-template-columns: auto auto 1fr;
			gap: 4px 10px;
			padding: 10px 4px;
			border-bottom: 1px solid var(--line);
		}
		td {
			padding: 0;
			border: 0;
		}
		.movie {
			grid-column: 1 / -1;
			order: 1;
		}
		.movie:has(.none) {
			display: none;
		}
		.progress {
			grid-column: 1 / -1;
			order: 2;
		}
		.when {
			grid-column: span 3;
			font-size: 12px;
			order: 3;
		}
		.when:last-child {
			display: none;
		}
	}
</style>
