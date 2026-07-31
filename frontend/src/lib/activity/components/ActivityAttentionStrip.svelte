<script lang="ts">
	import type { ActivityAttentionResponse } from '../types';

	let { summary }: { summary: ActivityAttentionResponse | null } = $props();
	const items = $derived(
		summary
			? [
					{ label: 'Running', value: summary.running, tone: 'running' },
					{ label: 'In Queue', value: summary.waiting_held, tone: 'queue' },
					{ label: 'Retrying', value: summary.retrying, tone: 'retrying' },
					{
						label: 'Needs attention',
						value: summary.needs_attention,
						tone: 'attention'
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
	.item[data-tone='running'] {
		border-left-color: var(--info);
		background: color-mix(in srgb, var(--info) 7%, var(--panel));
	}
	.item[data-tone='running'] span {
		color: var(--info);
	}
	.item[data-tone='queue'] {
		border-left-color: var(--queue);
		background: color-mix(in srgb, var(--queue) 12%, var(--panel));
	}
	.item[data-tone='queue'] span {
		color: var(--queue);
	}
	.item[data-tone='retrying'] {
		border-left-color: var(--low);
		background: color-mix(in srgb, var(--low) 7%, var(--panel));
	}
	.item[data-tone='retrying'] span {
		color: var(--low);
	}
	.item[data-tone='attention'] {
		border-left-color: var(--warn);
		background: color-mix(in srgb, var(--warn) 7%, var(--panel));
	}
	.item[data-tone='attention'] span {
		color: var(--warn);
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
