<script lang="ts">
	import { retryJob } from '$lib/api/jobs';
	import type { JobListItem } from '$lib/api/jobs';
	import { durationH } from '$lib/display';
	import { toast } from '$lib/toast';
	import StatusDot from './StatusDot.svelte';

	let { jobs, onRetried }: { jobs: JobListItem[]; onRetried?: () => void } = $props();

	function humanizeType(type: string): string {
		return type
			.split('_')
			.map((w) => w.charAt(0).toUpperCase() + w.slice(1))
			.join(' ');
	}

	function statusTone(status: string): 'good' | 'bad' | 'warn' | 'info' | 'muted' {
		if (status === 'succeeded') return 'good';
		if (status === 'failed' || status === 'dead_letter') return 'bad';
		if (status === 'cancelled' || status === 'interrupted') return 'warn';
		if (status === 'running' || status === 'claimed') return 'info';
		return 'muted';
	}

	function duration(job: JobListItem): string {
		if (!job.started_at) return '—';
		const end = job.finished_at ? new Date(job.finished_at) : new Date();
		const seconds = (end.getTime() - new Date(job.started_at).getTime()) / 1000;
		return durationH(seconds);
	}

	const RETRYABLE = new Set(['failed', 'interrupted', 'cancelled', 'dead_letter']);

	let retrying = $state<string | null>(null);

	async function handleRetry(jobId: string) {
		retrying = jobId;
		try {
			await retryJob(fetch, jobId);
			toast('Retry queued', 'good');
			onRetried?.();
		} catch (e) {
			toast(`Retry failed: ${e instanceof Error ? e.message : 'unknown error'}`, 'bad');
		} finally {
			retrying = null;
		}
	}
</script>

<div class="table-wrap">
	<table>
		<thead>
			<tr>
				<th>Type</th>
				<th>Subject</th>
				<th>Status</th>
				<th>Resources</th>
				<th>Started</th>
				<th>Duration</th>
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each jobs as job (job.job_id)}
				<tr>
					<td>{humanizeType(job.type)}</td>
					<td class="subject" title={job.subject?.title ?? job.subject?.id ?? ''}>
						{job.subject?.title ?? job.subject?.id ?? '—'}
					</td>
					<td>
						<div class="status-cell">
							<StatusDot tone={statusTone(job.status)} />
							<span>{job.status.replace(/_/g, ' ')}</span>
						</div>
					</td>
					<td class="mono">{Object.keys(job.resource_request ?? {}).join(', ') || '—'}</td>
					<td class="mono">{job.started_at ? new Date(job.started_at).toLocaleString() : '—'}</td>
					<td class="mono">{duration(job)}</td>
					<td class="actions">
						{#if RETRYABLE.has(job.status)}
							<button
								class="retry"
								disabled={retrying === job.job_id}
								onclick={() => handleRetry(job.job_id)}
							>
								{retrying === job.job_id ? 'Retrying…' : 'Retry'}
							</button>
						{/if}
						<a class="view" href={`/projection-room/jobs/${job.job_id}`}>Details</a>
					</td>
				</tr>
			{/each}
			{#if jobs.length === 0}
				<tr><td colspan="7" class="empty">No jobs match the current filters.</td></tr>
			{/if}
		</tbody>
	</table>
</div>

<style>
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
	}
	th {
		padding: 9px 12px;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
		background: var(--ink2);
		border-bottom: 1px solid var(--line);
		white-space: nowrap;
	}
	td {
		padding: 10px 12px;
		font-size: 13px;
		border-bottom: 1px solid var(--line2);
		color: var(--text);
		vertical-align: middle;
		white-space: nowrap;
	}
	.subject {
		max-width: 220px;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.status-cell {
		display: flex;
		align-items: center;
		gap: 6px;
		text-transform: capitalize;
	}
	.mono {
		font-family: var(--font-mono);
		color: var(--muted);
		font-size: 12px;
	}
	.actions {
		text-align: right;
		display: flex;
		align-items: center;
		gap: 10px;
		justify-content: flex-end;
	}
	.retry {
		padding: 3px 9px;
		border-radius: 6px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12px;
	}
	.retry:hover:not(:disabled) {
		border-color: var(--gold-deep);
		color: var(--gold);
	}
	.retry:disabled {
		opacity: 0.5;
	}
	.view {
		color: var(--gold);
		font-size: 12.5px;
	}
	.empty {
		text-align: center;
		color: var(--muted);
		padding: 28px;
		white-space: normal;
	}
</style>
