<script lang="ts">
	import { listPolicies, deletePolicy } from '$lib/api/subtitle-policies';
	import type { SubtitlePolicy } from '$lib/api/types';
	import StatusDot from '../StatusDot.svelte';
	import Icon from '../Icon.svelte';
	import { toast } from '$lib/toast';

	let {
		onEdit,
		onAudit,
		onApply
	}: {
		onEdit: (policy: SubtitlePolicy) => void;
		onAudit: (policy: SubtitlePolicy) => void;
		onApply: (policy: SubtitlePolicy) => void;
	} = $props();

	let policies = $state<SubtitlePolicy[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function loadPolicies() {
		loading = true;
		error = null;
		try {
			const res = await listPolicies(fetch);
			policies = res.policies || [];
		} catch (e: any) {
			error = e.message || 'Failed to load subtitle policies';
		} finally {
			loading = false;
		}
	}

	async function handleDelete(id: number) {
		if (!confirm('Are you sure you want to delete this policy?')) return;
		try {
			await deletePolicy(fetch, id);
			toast('Policy deleted successfully', 'good');
			loadPolicies();
		} catch (e: any) {
			toast(`Failed to delete: ${e.message}`, 'bad');
		}
	}

	$effect(() => {
		loadPolicies();
	});
</script>

<div class="policy-list-panel">
	<div class="header">
		<h4>Language Cleanup Policies</h4>
		<button class="btn primary btn-sm" onclick={() => onEdit({} as any)}> ➕ Create Policy </button>
	</div>

	{#if loading && policies.length === 0}
		<div class="loading-msg">Loading policies...</div>
	{:else if error}
		<div class="error-box">
			<p>⚠️ {error}</p>
			<button class="btn secondary btn-sm" onclick={loadPolicies}>Retry</button>
		</div>
	{:else if policies.length === 0}
		<div class="empty-box">
			<p>No subtitle policies found.</p>
			<p class="desc">
				Cleanup policies help keep your media directory clean by automatically removing subtitle
				tracks in unwanted languages or of unwanted codecs.
			</p>
			<button class="btn secondary btn-sm" onclick={() => onEdit({} as any)}
				>Create your first policy</button
			>
		</div>
	{:else}
		<div class="table-wrap">
			<table class="policies-table">
				<thead>
					<tr>
						<th>Name</th>
						<th>Mode</th>
						<th>Languages</th>
						<th>Target</th>
						<th>Auto Apply</th>
						<th>Status</th>
						<th class="actions-col">Actions</th>
					</tr>
				</thead>
				<tbody>
					{#each policies as p}
						<tr>
							<td class="policy-name">
								<strong>{p.name}</strong>
								<span class="rev">r{p.revision}</span>
							</td>
							<td>
								<span class="mode-badge" class:blocklist={p.mode === 'blocklist'}>
									{p.mode.toUpperCase()}
								</span>
							</td>
							<td>
								<div class="lang-pills">
									{#each p.languages as l}
										<span class="lang-pill">{l.toUpperCase()}</span>
									{/each}
								</div>
							</td>
							<td class="capitalize">{p.target_source}</td>
							<td>
								<span class="status-lbl" class:yes={p.auto_apply}>
									{p.auto_apply ? 'Yes' : 'No'}
								</span>
							</td>
							<td>
								<div class="status-cell">
									<StatusDot tone={p.enabled ? 'good' : 'bad'} />
									<span class="lbl">{p.enabled ? 'Active' : 'Disabled'}</span>
								</div>
							</td>
							<td class="actions-col">
								<button class="icon-btn" onclick={() => onAudit(p)} title="Audit / Dry Run">
									🔍 Audit
								</button>
								<button class="icon-btn" onclick={() => onApply(p)} title="Apply Policy">
									⚡ Apply
								</button>
								<button class="icon-btn" onclick={() => onEdit(p)} title="Edit"> ✏️ Edit </button>
								<button class="icon-btn delete" onclick={() => handleDelete(p.id)} title="Delete">
									🗑️
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<style>
	.policy-list-panel {
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
	}
	.header h4 {
		margin: 0;
		font-size: 14px;
		font-weight: 600;
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
	.empty-box .desc {
		font-size: 13px;
		max-width: 440px;
		margin: 8px auto 16px;
		line-height: 1.4;
	}
	.table-wrap {
		overflow-x: auto;
	}
	.policies-table {
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
	.policy-name {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.policy-name strong {
		font-weight: 600;
	}
	.policy-name .rev {
		font-size: 10px;
		color: var(--muted);
		background: var(--panel2);
		padding: 1px 4px;
		border-radius: 3px;
		font-family: var(--font-mono);
	}
	.mode-badge {
		background: rgba(86, 211, 100, 0.15);
		color: #56d364;
		font-size: 10px;
		font-weight: 700;
		padding: 2px 6px;
		border-radius: 4px;
		font-family: var(--font-mono);
	}
	.mode-badge.blocklist {
		background: rgba(239, 83, 80, 0.15);
		color: #ff7b72;
	}
	.lang-pills {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.lang-pill {
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 11px;
		padding: 1px 4px;
		border-radius: 3px;
		color: var(--text);
	}
	.capitalize {
		text-transform: capitalize;
	}
	.status-lbl {
		color: var(--muted);
	}
	.status-lbl.yes {
		color: var(--gold);
		font-weight: 600;
	}
	.status-cell {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.status-cell .lbl {
		font-size: 12.5px;
		color: var(--muted);
	}
	.actions-col {
		text-align: right;
		white-space: nowrap;
	}
	.icon-btn {
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
	.icon-btn:hover {
		background: var(--line);
	}
	.icon-btn.delete:hover {
		background: rgba(239, 83, 80, 0.15);
		border-color: var(--bad);
		color: var(--bad);
	}

	/* Buttons */
	.btn {
		font-size: 13px;
		font-weight: 600;
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		transition: background-color 0.15s;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--on-gold);
	}
	.btn.primary:hover {
		background: var(--gold-deep);
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
		padding: 5px 10px;
		font-size: 12px;
	}
</style>
