<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { cancelJob, getJobDetail, isTerminal } from '$lib/api/jobs';
	import type { JobChildDetail, JobDetail, JobContext } from '$lib/api/jobs';
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
	let cancelling = $state(false);
	let stop: (() => void) | null = null;

	function statusTone(status: string): 'good' | 'bad' | 'warn' | 'info' | 'muted' {
		if (status === 'succeeded') return 'good';
		if (status === 'failed' || status === 'dead_letter') return 'bad';
		if (status === 'cancelled' || status === 'interrupted' || status === 'cancelling')
			return 'warn';
		if (isTerminal(status)) return 'muted';
		return 'info';
	}

	function prettyJson(value: unknown): string {
		return JSON.stringify(value, null, 2);
	}

	function hasContent(value: unknown): value is Record<string, unknown> {
		return (
			!!value &&
			typeof value === 'object' &&
			Object.keys(value as Record<string, unknown>).length > 0
		);
	}

	function effectiveRequest(detail: JobDetail | JobChildDetail): JobContext | null {
		if (hasContent(detail.request)) return detail.request;
		const payloadKeys = Object.keys(detail.payload ?? {}).filter((key) => key !== 'media_job_id');
		if (payloadKeys.length === 0) return null;
		return detail.payload;
	}

	function displaySubject(detail: JobDetail | JobChildDetail): string {
		return detail.subject?.title ?? displayJobLabel(detail);
	}

	async function refetch() {
		if (!job) return;
		try {
			job = await getJobDetail(fetch, job.job_id);
			liveStatus = job.cancel_requested && !isTerminal(job.status) ? 'cancelling' : job.status;
		} catch {
			/* keep showing the last known detail */
		}
	}

	async function handleCancel() {
		if (!job || cancelling || job.cancel_requested) return;
		try {
			await cancelJob(fetch, job.job_id);
			cancelling = true;
			liveStatus = 'cancelling';
			job = { ...job, cancel_requested: true, status: 'cancelling' };
			toast('Cancellation requested', 'info');
			void refetch();
		} catch {
			toast('Could not cancel job', 'bad');
		}
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
					liveStatus = liveStatus === 'cancelling' && status === 'running' ? 'cancelling' : status;
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
	<SectionHeader title={displaySubject(job)} subtitle={`${job.type} · ${job.job_id}`} />
	<a class="back" href="/projection-room?tab=history">← Back to Projection Room</a>

	<div class="head-row">
		<div class="status-block">
			<StatusDot tone={statusTone(liveStatus)} size={10} />
			<span class="status-label">{humanizeJobType(liveStatus)}</span>
		</div>
		{#if !isTerminal(liveStatus)}
			<button class="cancel" disabled={cancelling || job.cancel_requested} onclick={handleCancel}>
				{cancelling || job.cancel_requested ? 'Cancelling…' : 'Cancel'}
			</button>
		{/if}
	</div>

	{#if !isTerminal(liveStatus)}
		<RunProgress detail={liveDetail} status={liveStatus} title="Live progress" />
	{/if}

	<div class="meta-grid">
		<div><span class="k">Priority</span><span class="v mono">{job.priority}</span></div>
		<div>
			<span class="k">Attempts</span><span class="v mono"
				>{job.attempt_count}/{job.max_attempts}</span
			>
		</div>
		<div>
			<span class="k">Resources</span>
			<span class="v mono">{Object.keys(job.resource_request ?? {}).join(', ') || '—'}</span>
		</div>
		<div>
			<span class="k">Created</span><span class="v mono"
				>{job.created_at ? new Date(job.created_at).toLocaleString() : '—'}</span
			>
		</div>
		<div>
			<span class="k">Started</span><span class="v mono"
				>{job.started_at ? new Date(job.started_at).toLocaleString() : '—'}</span
			>
		</div>
		<div>
			<span class="k">Finished</span><span class="v mono"
				>{job.finished_at ? new Date(job.finished_at).toLocaleString() : '—'}</span
			>
		</div>
		{#if job.started_at}
			{@const end = job.finished_at ? new Date(job.finished_at) : new Date()}
			<div>
				<span class="k">Duration</span>
				<span class="v mono"
					>{durationH((end.getTime() - new Date(job.started_at).getTime()) / 1000)}</span
				>
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
					<tr
						><th>#</th><th>Status</th><th>Worker</th><th>Started</th><th>Finished</th><th
							>Metrics</th
						><th>Error</th></tr
					>
				</thead>
				<tbody>
					{#each job.attempts as a (a.number)}
						<tr>
							<td class="mono">{a.number}</td>
							<td>{humanizeJobType(a.status)}</td>
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
							<td class="mono"
								>{r.released_at ? new Date(r.released_at).toLocaleString() : 'held'}</td
							>
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
						<span class="event-time mono"
							>{e.created_at ? new Date(e.created_at).toLocaleTimeString() : '—'}</span
						>
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
			<pre>{prettyJson(job.result)}</pre>
		</section>
	{/if}

	{#if job.error}
		<section>
			<h3>Error</h3>
			<pre class="err-block">{prettyJson(job.error)}</pre>
		</section>
	{/if}

	{#if effectiveRequest(job)}
		<section>
			<h3>Request</h3>
			<pre>{prettyJson(effectiveRequest(job))}</pre>
		</section>
	{/if}

	{#if job.plan}
		<section>
			<h3>Plan</h3>
			<pre>{prettyJson(job.plan)}</pre>
		</section>
	{/if}

	{#if job.children.length > 0}
		<section>
			<h3>Child jobs ({job.children.length})</h3>
			<div class="children">
				{#each job.children as child (child.job_id)}
					<details class="child-card">
						<summary>
							<div class="child-head">
								<div class="child-title">
									<span>{displaySubject(child)}</span>
									<span class="child-sub">{displayJobLabel(child)} · {child.job_id}</span>
								</div>
								<div class="child-state">
									<StatusDot tone={statusTone(child.status)} size={9} />
									<span>{humanizeJobType(child.status)}</span>
								</div>
							</div>
						</summary>
						<div class="child-meta">
							<div><span class="k">Stage</span><span class="v">{child.stage ?? '—'}</span></div>
							<div>
								<span class="k">Created</span><span class="v mono"
									>{child.created_at ? new Date(child.created_at).toLocaleString() : '—'}</span
								>
							</div>
							<div>
								<span class="k">Started</span><span class="v mono"
									>{child.started_at ? new Date(child.started_at).toLocaleString() : '—'}</span
								>
							</div>
							<div>
								<span class="k">Finished</span><span class="v mono"
									>{child.finished_at ? new Date(child.finished_at).toLocaleString() : '—'}</span
								>
							</div>
						</div>
						{#if effectiveRequest(child)}
							<h4>Request</h4>
							<pre>{prettyJson(effectiveRequest(child))}</pre>
						{/if}
						{#if child.plan}
							<h4>Plan</h4>
							<pre>{prettyJson(child.plan)}</pre>
						{/if}
						{#if child.result}
							<h4>Result</h4>
							<pre>{prettyJson(child.result)}</pre>
						{/if}
						{#if child.error}
							<h4>Error</h4>
							<pre class="err-block">{prettyJson(child.error)}</pre>
						{/if}
					</details>
				{/each}
			</div>
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
	.children {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.child-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 0;
		overflow: hidden;
	}
	.child-card summary {
		list-style: none;
		cursor: pointer;
		padding: 14px;
	}
	.child-card summary::-webkit-details-marker {
		display: none;
	}
	.child-head {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		align-items: center;
		flex-wrap: wrap;
	}
	.child-title {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.child-sub {
		color: var(--muted);
		font-size: 12px;
		font-family: var(--font-mono);
	}
	.child-state {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12.5px;
		font-weight: 600;
	}
	.child-meta {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
		gap: 10px;
		padding: 0 14px 14px;
	}
	.child-card h4 {
		margin: 0 14px 8px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
	}
	.child-card pre {
		margin: 0 14px 14px;
	}
</style>
