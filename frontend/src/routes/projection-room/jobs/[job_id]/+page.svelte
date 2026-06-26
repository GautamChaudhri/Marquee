<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { cancelJob, getJobDetail, isTerminal } from '$lib/api/jobs';
	import type { JobDetail } from '$lib/api/jobs';
	import { trackJob, type JobProgressDetail } from '$lib/jobs';
	import { durationH } from '$lib/display';
	import { displayJobLabel, humanizeJobType } from '$lib/job-labels';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The URL's job_id is itself a durable reference — no localStorage needed
	// here the way the multi-job Live tab needs it; a hard refresh just
	// re-runs this loader with the same id.
	// svelte-ignore state_referenced_locally
	let job = $state<JobDetail | null>(data.job);
	let liveDetail = $state<JobProgressDetail>({});
	// svelte-ignore state_referenced_locally
	let liveStatus = $state(data.job?.status ?? '');
	let stop: (() => void) | null = null;

	function statusTone(status: string): 'good' | 'bad' | 'warn' | 'info' | 'muted' {
		if (status === 'succeeded') return 'good';
		if (status === 'failed' || status === 'dead_letter') return 'bad';
		if (status === 'cancelled' || status === 'interrupted') return 'warn';
		if (isTerminal(status)) return 'muted';
		return 'info';
	}

	async function refetch() {
		if (!job) return;
		try {
			job = await getJobDetail(fetch, job.job_id);
		} catch {
			/* keep showing the last known detail */
		}
	}

	async function handleCancel() {
		if (!job) return;
		await cancelJob(fetch, job.job_id);
		toast('Cancellation requested', 'info');
		void refetch();
	}

	onMount(() => {
		if (!job || isTerminal(job.status)) return;
		liveStatus = job.status;
		liveDetail = (job.progress ?? {}) as JobProgressDetail;
		stop = trackJob(
			fetch,
			job.job_id,
			{
				onProgress: ({ status, detail }) => {
					liveStatus = status;
					liveDetail = detail;
				},
				onDone: () => void refetch()
			},
			{ eventsUrl: job.events_url }
		);
	});

	onDestroy(() => stop?.());
</script>

{#if !job}
	<SectionHeader title="Job not found" subtitle={data.error ?? 'This job no longer exists.'} />
	<a class="back" href="/projection-room?tab=history">← Back to Projection Room</a>
{:else}
	<SectionHeader title={job.subject?.title ?? displayJobLabel(job)} subtitle={`${job.type} · ${job.job_id}`} />
	<a class="back" href="/projection-room?tab=history">← Back to Projection Room</a>

	<div class="head-row">
		<div class="status-block">
			<StatusDot tone={statusTone(liveStatus)} size={10} />
			<span class="status-label">{humanizeJobType(liveStatus)}</span>
		</div>
		{#if !isTerminal(liveStatus) && !job.cancel_requested}
			<button class="cancel" onclick={handleCancel}>Cancel</button>
		{/if}
	</div>

	{#if !isTerminal(liveStatus)}
		<RunProgress detail={liveDetail} status={liveStatus} title="Live progress" />
	{/if}

	<div class="meta-grid">
		<div><span class="k">Priority</span><span class="v mono">{job.priority}</span></div>
		<div><span class="k">Attempts</span><span class="v mono">{job.attempt_count}/{job.max_attempts}</span></div>
		<div>
			<span class="k">Resources</span>
			<span class="v mono">{Object.keys(job.resource_request ?? {}).join(', ') || '—'}</span>
		</div>
		<div><span class="k">Created</span><span class="v mono">{job.created_at ? new Date(job.created_at).toLocaleString() : '—'}</span></div>
		<div><span class="k">Started</span><span class="v mono">{job.started_at ? new Date(job.started_at).toLocaleString() : '—'}</span></div>
		<div><span class="k">Finished</span><span class="v mono">{job.finished_at ? new Date(job.finished_at).toLocaleString() : '—'}</span></div>
		{#if job.started_at}
			{@const end = job.finished_at ? new Date(job.finished_at) : new Date()}
			<div>
				<span class="k">Duration</span>
				<span class="v mono">{durationH((end.getTime() - new Date(job.started_at).getTime()) / 1000)}</span>
			</div>
		{/if}
		{#if job.parent_id}
			<div>
				<span class="k">Parent</span>
				<a class="v mono link" href={`/projection-room/jobs/${job.parent_id}`}>{job.parent_id}</a>
			</div>
		{/if}
	</div>

	<section>
		<h3>Attempts ({job.attempts.length})</h3>
		{#if job.attempts.length === 0}
			<p class="empty">No attempts recorded yet.</p>
		{:else}
			<table>
				<thead>
					<tr><th>#</th><th>Status</th><th>Worker</th><th>Started</th><th>Finished</th><th>Metrics</th><th>Error</th></tr>
				</thead>
				<tbody>
					{#each job.attempts as a (a.number)}
						<tr>
							<td class="mono">{a.number}</td>
							<td>{humanize(a.status)}</td>
							<td class="mono">{a.worker_id}</td>
							<td class="mono">{a.started_at ? new Date(a.started_at).toLocaleString() : '—'}</td>
							<td class="mono">{a.finished_at ? new Date(a.finished_at).toLocaleString() : '—'}</td>
							<td class="mono">{a.metrics ? JSON.stringify(a.metrics) : '—'}</td>
							<td class="err">{a.error ? JSON.stringify(a.error) : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>

	<section>
		<h3>Resource reservations ({job.resources.length})</h3>
		{#if job.resources.length === 0}
			<p class="empty">No resources reserved.</p>
		{:else}
			<table>
				<thead>
					<tr><th>Key</th><th>Units</th><th>Stage</th><th>Acquired</th><th>Released</th></tr>
				</thead>
				<tbody>
					{#each job.resources as r, i (i)}
						<tr>
							<td class="mono">{r.key}</td>
							<td class="mono">{r.units}</td>
							<td>{r.stage ?? '—'}</td>
							<td class="mono">{r.acquired_at ? new Date(r.acquired_at).toLocaleString() : '—'}</td>
							<td class="mono">{r.released_at ? new Date(r.released_at).toLocaleString() : 'held'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>

	<section>
		<h3>Event log ({job.events.length})</h3>
		{#if job.events.length === 0}
			<p class="empty">No events recorded.</p>
		{:else}
			<div class="timeline">
				{#each job.events as e (e.id)}
					<div class="event">
						<span class="event-time mono">{e.created_at ? new Date(e.created_at).toLocaleTimeString() : '—'}</span>
						<span class="event-state">{e.state}</span>
						{#if e.stage}<span class="event-stage">{e.stage}</span>{/if}
						{#if e.message}<span class="event-msg">{e.message}</span>{/if}
					</div>
				{/each}
			</div>
		{/if}
	</section>

	{#if job.result}
		<section>
			<h3>Result</h3>
			<pre>{JSON.stringify(job.result, null, 2)}</pre>
		</section>
	{/if}

	{#if job.error}
		<section>
			<h3>Error</h3>
			<pre class="err-block">{JSON.stringify(job.error, null, 2)}</pre>
		</section>
	{/if}
{/if}

<style>
	.back {
		display: inline-block;
		color: var(--muted);
		font-size: 12.5px;
		margin: 4px 0 16px;
	}
	.back:hover {
		color: var(--gold);
	}
	.head-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 16px;
	}
	.status-block {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.status-label {
		font-size: 14px;
		font-weight: 600;
	}
	.cancel {
		padding: 6px 14px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 13px;
	}
	.cancel:hover {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 40%, transparent);
	}
	.meta-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
		gap: 12px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px;
		margin: 16px 0 28px;
	}
	.meta-grid > div {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.k {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
	}
	.v {
		font-size: 13px;
		color: var(--text);
	}
	.link {
		color: var(--gold);
	}
	section {
		margin-bottom: 28px;
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
	}
	table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	th {
		padding: 8px 12px;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
		background: var(--ink2);
		border-bottom: 1px solid var(--line);
	}
	td {
		padding: 9px 12px;
		font-size: 12.5px;
		border-bottom: 1px solid var(--line2);
		color: var(--text);
	}
	tr:last-child td {
		border-bottom: none;
	}
	.err {
		color: var(--bad);
		font-size: 11.5px;
		font-family: var(--font-mono);
	}
	.mono {
		font-family: var(--font-mono);
	}
	.timeline {
		display: flex;
		flex-direction: column;
		gap: 2px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 10px 14px;
		max-height: 420px;
		overflow-y: auto;
	}
	.event {
		display: flex;
		gap: 10px;
		align-items: baseline;
		padding: 4px 0;
		border-bottom: 1px solid var(--line2);
		font-size: 12.5px;
		flex-wrap: wrap;
	}
	.event:last-child {
		border-bottom: none;
	}
	.event-time {
		color: var(--faint);
		font-size: 11px;
		flex: none;
	}
	.event-state {
		color: var(--gold);
		font-weight: 600;
		flex: none;
	}
	.event-stage {
		color: var(--info);
		flex: none;
	}
	.event-msg {
		color: var(--muted);
	}
	pre {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px;
		font-size: 12px;
		font-family: var(--font-mono);
		color: var(--text);
		overflow-x: auto;
		white-space: pre-wrap;
		word-break: break-word;
	}
	pre.err-block {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 30%, transparent);
	}
</style>
