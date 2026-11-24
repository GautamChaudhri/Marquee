<script lang="ts">
	import type { Deployment } from '$lib/api/types';
	import { deploymentSummary, restoreTarget } from '$lib/features/runs/runs';
	import { formatWhen } from '$lib/format';
	import Button from '$lib/ui/Button.svelte';

	/** A movie's deployments, newest first, each with the button that puts back what was
	 *  there before it. */
	let {
		deployments,
		busyId = null,
		disabled = false,
		onRestore
	}: {
		deployments: Deployment[];
		busyId?: number | null;
		disabled?: boolean;
		onRestore: (deployment: Deployment) => void;
	} = $props();
</script>

<ol class="history" data-testid="poster-history">
	{#each deployments as deployment (deployment.id)}
		<li>
			<div class="what">
				<span class="summary">{deploymentSummary(deployment)}</span>
				<span class="meta">
					Deployment {deployment.id} · {formatWhen(deployment.created_at)} · before it:
					{restoreTarget(deployment)}
				</span>
			</div>
			<Button
				variant="secondary"
				busy={busyId === deployment.id}
				disabled={disabled && busyId !== deployment.id}
				onclick={() => onRestore(deployment)}
			>
				Put back what was there before
			</Button>
		</li>
	{/each}
</ol>

<style>
	.history {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	li {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 8px 14px;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
	}
	.what {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
	}
	.summary {
		font-size: 13px;
		overflow-wrap: anywhere;
	}
	.meta {
		font-size: 12px;
		color: var(--muted);
	}
</style>
