<script lang="ts">
	import { listMediaJobs, cancelJob, restoreJob, deleteBackup } from '$lib/api/media-jobs';
	import type { MediaJob } from '$lib/api/types';
	import ProgressBar from '../ProgressBar.svelte';
	import StatusDot from '../StatusDot.svelte';
	import { toast } from '$lib/toast';

	let jobs = $state<MediaJob[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let filterStatus = $state<string>('all');
	let timerId = $state<any>(null);

	// Load jobs list
	async function loadJobs() {
		error = null;
		try {
			const params: any = {};
			if (filterStatus !== 'all') params.status = filterStatus;
			const res = await listMediaJobs(fetch, params);
			jobs = res.jobs || [];
		} catch (e: any) {
			error = e.message || 'Failed to fetch media jobs list';
		} finally {
			loading = false;
		}
	}

	// Action triggers
	async function handleCancel(id: string) {
		try {
			await cancelJob(fetch, id);
			toast('Cancellation requested', 'info');
			loadJobs();
		} catch (e: any) {
			toast(`Cancel failed: ${e.message}`, 'bad');
		}
	}

	async function handleRestore(id: string) {
		if (!confirm('Are you sure you want to restore the pre-mutation backup for this file? This will undo the changes.')) return;
		try {
			await restoreJob(fetch, id);
			toast('Backup restored successfully', 'good');
			loadJobs();
		} catch (e: any) {
			toast(`Restore failed: ${e.message}`, 'bad');
		}
	}

	async function handleDeleteBackup(id: string) {
		if (!confirm('Are you sure you want to permanently delete the backup file? This cannot be undone.')) return;
		try {
			await deleteBackup(fetch, id);
			toast('Backup deleted successfully', 'good');
			loadJobs();
		} catch (e: any) {
			toast(`Delete failed: ${e.message}`, 'bad');
		}
	}

	// Watch filter changes
	$effect(() => {
		loadJobs();
	});

	// Polling for active jobs
	$effect(() => {
		// Set up polling interval every 4 seconds
		timerId = setInterval(() => {
			loadJobs();
		}, 4000);

		return () => {
			if (timerId) clearInterval(timerId);
		};
	});
</script>

<div class="jobs-list-panel">
	<div class="header">
		<h4>Subtitle Mutation Jobs History</h4>
		<div class="controls">
			<div class="filter-group">
				<button class="filter-btn" class:active={filterStatus === 'all'} onclick={() => filterStatus = 'all'}>All</button>
				<button class="filter-btn" class:active={filterStatus === 'running'} onclick={() => filterStatus = 'running'}>Running</button>
				<button class="filter-btn" class:active={filterStatus === 'completed'} onclick={() => filterStatus = 'completed'}>Completed</button>
				<button class="filter-btn" class:active={filterStatus === 'failed'} onclick={() => filterStatus = 'failed'}>Failed</button>
			</div>
			<button class="btn secondary btn-sm" onclick={loadJobs} disabled={loading}>
				🔄 Refresh
			</button>
		</div>
	</div>

	{#if loading && jobs.length === 0}
		<div class="loading-msg">Loading jobs...</div>
	{:else if error}
		<div class="error-box">⚠️ {error}</div>
	{:else if jobs.length === 0}
		<div class="empty-box">No subtitle mutation jobs found matching current filters.</div>
	{:else}
		<div class="table-wrap">
			<table class="jobs-table">
				<thead>
					<tr>
						<th>Job ID</th>
						<th>Operation</th>
						<th>File ID</th>
						<th>Status</th>
						<th>Progress</th>
						<th>Created At</th>
						<th class="actions-col">Actions</th>
					</tr>
				</thead>
				<tbody>
					{#each jobs as job}
						<tr>
							<td class="job-id-cell">
								<code title={job.job_id}>{job.job_id.substring(0, 8)}...</code>
							</td>
							<td>
								<span class="op-badge">{job.operation.replace('subtitle_', '').toUpperCase()}</span>
							</td>
							<td class="mono">{job.media_file_id || '—'}</td>
							<td>
								<div class="status-cell">
									<StatusDot tone={
										job.status === 'completed' || job.status === 'succeeded' ? 'good' :
										job.status === 'failed' ? 'bad' :
										job.status === 'planned' ? 'info' : 'warn'
									} />
									<span class="status-lbl">{job.status}</span>
								</div>
							</td>
							<td>
								<div class="progress-cell">
									{#if job.status === 'running'}
										{@const pct = job.progress?.percent ?? 0}
										<div class="progress-wrapper">
											<span class="pct">{pct}%</span>
											<ProgressBar value={pct} />
										</div>
										<span class="stage" title={job.progress?.message}>{job.progress?.stage || 'Mutating'}</span>
									{:else if job.status === 'completed' || job.status === 'succeeded'}
										<span class="progress-done">Done</span>
									{:else if job.status === 'failed'}
										<span class="progress-err" title={job.error}>Error: {job.error || 'Job failed'}</span>
									{:else}
										<span class="progress-pending">Queued</span>
									{/if}
								</div>
							</td>
							<td class="date-col">
								{new Date(job.created_at).toLocaleString()}
							</td>
							<td class="actions-col">
								{#if job.status === 'running' || job.status === 'queued'}
									<button class="action-btn cancel" onclick={() => handleCancel(job.job_id)}>
										Cancel
									</button>
								{/if}
								{#if job.backup_id}
									<button class="action-btn restore" onclick={() => handleRestore(job.job_id)}>
										Restore
									</button>
									<button class="action-btn delete-bk" onclick={() => handleDeleteBackup(job.job_id)}>
										Delete Backup
									</button>
								{/if}
								{#if !job.backup_id && job.status !== 'running' && job.status !== 'queued'}
									<span class="muted">—</span>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<style>
	.jobs-list-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
	}
	.header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 16px;
		border-bottom: 1px solid var(--line);
		padding-bottom: 12px;
		flex-wrap: wrap;
		gap: 12px;
	}
	.header h4 {
		margin: 0;
		font-size: 14px;
		font-weight: 600;
	}
	.controls {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.filter-group {
		display: flex;
		gap: 4px;
		background: var(--panel2);
		padding: 3px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
	}
	.filter-btn {
		background: transparent;
		border: none;
		padding: 4px 10px;
		color: var(--muted);
		font-size: 12px;
		font-weight: 500;
		border-radius: calc(var(--radius-sm) - 2px);
		cursor: pointer;
	}
	.filter-btn.active {
		background: var(--panel);
		color: var(--gold);
	}
	.loading-msg {
		padding: 32px;
		text-align: center;
		color: var(--muted);
	}
	.error-box {
		padding: 24px;
		text-align: center;
		color: var(--bad);
	}
	.empty-box {
		text-align: center;
		padding: 32px;
		color: var(--muted);
	}
	.table-wrap {
		overflow-x: auto;
	}
	.jobs-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
	}
	th {
		padding: 10px 14px;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
		background: var(--ink2);
		border-bottom: 1px solid var(--line);
	}
	td {
		padding: 12px 14px;
		font-size: 13px;
		border-bottom: 1px solid var(--line2);
		color: var(--text);
		vertical-align: middle;
	}
	tr:last-child td {
		border-bottom: none;
	}
	.job-id-cell code {
		font-family: var(--font-mono);
		font-size: 12.5px;
		color: var(--gold);
	}
	.op-badge {
		background: var(--panel2);
		border: 1px solid var(--line);
		font-family: var(--font-mono);
		font-size: 10px;
		font-weight: 700;
		padding: 2px 6px;
		border-radius: 4px;
	}
	.status-cell {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.status-lbl {
		font-size: 12.5px;
		text-transform: capitalize;
	}
	.progress-cell {
		display: flex;
		flex-direction: column;
		gap: 4px;
		max-width: 200px;
	}
	.progress-wrapper {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.progress-wrapper .pct {
		font-family: var(--font-mono);
		font-size: 11.5px;
		color: var(--gold);
		font-weight: 600;
		min-width: 32px;
	}
	.stage {
		font-size: 11px;
		font-family: var(--font-mono);
		color: var(--muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.progress-done {
		color: var(--good);
		font-weight: 550;
	}
	.progress-err {
		color: var(--bad);
		font-size: 12px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 180px;
	}
	.progress-pending {
		color: var(--info);
	}
	.mono {
		font-family: var(--font-mono);
		color: var(--muted);
	}
	.date-col {
		font-size: 12px;
		color: var(--muted);
	}
	.actions-col {
		text-align: right;
		white-space: nowrap;
	}
	.action-btn {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
		padding: 4px 8px;
		border-radius: 4px;
		font-size: 11.5px;
		cursor: pointer;
		margin-left: 4px;
		transition: background-color 0.15s;
	}
	.action-btn:hover {
		background: var(--line);
	}
	.action-btn.cancel:hover {
		background: rgba(239, 83, 80, 0.15);
		border-color: var(--bad);
		color: var(--bad);
	}
	.action-btn.restore {
		border-color: var(--gold-deep);
		color: var(--gold);
	}
	.action-btn.restore:hover {
		background: rgba(255, 194, 75, 0.1);
	}
	.action-btn.delete-bk:hover {
		background: rgba(239, 83, 80, 0.1);
		border-color: var(--bad);
		color: var(--bad);
	}
	.muted {
		color: var(--faint);
	}

	.btn {
		font-size: 13px;
		font-weight: 600;
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
		transition: background-color 0.15s;
	}
	.btn.secondary {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.btn.secondary:hover {
		background: var(--panel);
	}
	.btn-sm {
		padding: 4px 8px;
		font-size: 11px;
	}
</style>
