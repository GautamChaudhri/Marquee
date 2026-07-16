<script lang="ts">
	import type { ActivityAttentionResponse } from '../types';

	let { summary }: { summary: ActivityAttentionResponse | null } = $props();
	const items = $derived(
		summary
			? [
					{ label: 'Running', value: summary.running, tone: 'normal' },
					{ label: 'Waiting / held', value: summary.waiting_held, tone: 'normal' },
					{ label: 'Retrying', value: summary.retrying, tone: 'warning' },
					{
						label: 'Needs attention',
						value: summary.needs_attention,
						tone: summary.highest_severity
					}
				]
			: []
	);
</script>

<section class="strip" aria-label="Activity attention summary" aria-live="polite">
	{#if summary}
		{#each items as item (item.label)}
			<div class="item" data-tone={item.tone}>
				<span>{item.label}</span>
				<strong>{item.value}</strong>
			</div>
		{/each}
	{:else}
		<p>Attention summary is temporarily unavailable.</p>
	{/if}
</section>

<style>
	.strip {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 8px;
		margin-top: 16px;
	}
	.item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-left: 3px solid var(--muted);
		border-radius: var(--radius-sm);
		background: var(--panel);
		color: var(--muted);
		font-size: 12px;
	}
	.item[data-tone='warning'] {
		border-left-color: var(--warn);
	}
	.item[data-tone='error'] {
		border-left-color: var(--bad);
	}
	.item strong {
		color: var(--text);
		font: 700 16px var(--font-mono);
	}
	.strip p {
		grid-column: 1 / -1;
		margin: 0;
		color: var(--muted);
		font-size: 12px;
	}
	@media (max-width: 720px) {
		.strip {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
