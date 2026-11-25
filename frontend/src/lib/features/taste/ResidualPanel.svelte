<script lang="ts">
	import type { Residual } from '#lib/api/types.ts';
	import { residualLines } from './evaluation';

	/** The residual: in use or not and why, held-out accuracy before and after, what it leans
	 *  on, the last training, and what is still missing, each said once. No button turns it on
	 *  or off: SCORER=weighted does, and the state line says so. */
	let { residual }: { residual: Residual } = $props();

	const lines = $derived(residualLines(residual));
</script>

<div class="residual" data-testid="residual">
	<p class="line strong" data-testid="residual-state">{lines.state}</p>
	{#each lines.details as line (line)}<p class="line">{line}</p>{/each}
	{#if lines.features.length > 0}
		<p class="line">It leans on:</p>
		<ul>
			{#each lines.features as feature (feature.name)}
				<li>
					<code>{feature.name}</code>
					<span class:up={feature.weight > 0} class:down={feature.weight < 0}>
						{feature.weight > 0 ? 'more is better' : 'less is better'}
						({feature.weight.toFixed(2)})
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.residual {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.line {
		margin: 0;
		font-size: 13px;
		color: var(--muted);
	}
	.line.strong {
		color: var(--text);
	}
	ul {
		margin: 0;
		padding-left: 18px;
		font-size: 13px;
		color: var(--muted);
	}
	code {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--text);
	}
	.up {
		color: var(--good);
	}
	.down {
		color: var(--low);
	}
</style>
