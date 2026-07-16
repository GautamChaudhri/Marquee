<script lang="ts">
	import { metricText } from '../presentation';
	import type { MetricCard } from '../types';

	let { metrics }: { metrics: MetricCard[] } = $props();
</script>

{#if metrics.length > 0}
	<section class="metrics" aria-label="Job metrics">
		{#each metrics as metric (metric.label)}
			<div class="metric">
				<span>{metric.label}</span>
				{#if metric.value.type === 'link'}
					<a href={metric.value.href}>{metricText(metric)}</a>
				{:else}
					<strong>{metricText(metric)}</strong>
				{/if}
				{#if metric.interpretation}<small>{metric.interpretation}</small>{/if}
			</div>
		{/each}
	</section>
{/if}

<style>
	.metrics {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
		gap: 8px;
	}
	.metric {
		display: grid;
		min-width: 0;
		gap: 3px;
		padding: 9px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--ink2);
	}
	.metric > span,
	.metric small {
		color: var(--muted);
		font-size: 11px;
	}
	.metric strong,
	.metric a {
		overflow-wrap: anywhere;
		font-size: 13px;
	}
	.metric a {
		color: var(--gold);
		text-decoration: underline;
		text-underline-offset: 2px;
	}
</style>
