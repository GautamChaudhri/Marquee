<script lang="ts">
	import type { ContainedWorkSummary, WorkItemSummary } from '../types';

	let { summary }: { summary: WorkItemSummary | ContainedWorkSummary } = $props();

	type CountKey = 'succeeded' | 'review_required' | 'failed' | 'no_change' | 'cancelled';

	// Only settled outcomes appear here — this row is the verdict on a finished run, so
	// `pending` and `running` have no entry. Ordered best outcome first, which is also
	// how the counts read aloud: "seven succeeded, one needs attention".
	const OUTCOMES: Array<[CountKey, string]> = [
		['succeeded', 'succeeded'],
		['review_required', 'needs attention'],
		['failed', 'failed'],
		['no_change', 'no candidates'],
		['cancelled', 'cancelled']
	];

	const entries = $derived(
		OUTCOMES.map(([key, label]) => [key, summary.counts?.[key] ?? 0, label] as const).filter(
			([, count]) => count > 0
		)
	);
	const label = $derived('source' in summary ? 'Contained work outcomes' : 'Poster outcomes');
</script>

{#if entries.length > 0}
	<div class="outcomes" aria-label={label}>
		{#each entries as [key, count, label] (key)}
			<span data-status={key}><strong>{count}</strong> {label}</span>
		{/each}
	</div>
{/if}

<style>
	.outcomes {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	span {
		display: inline-flex;
		gap: 5px;
		align-items: baseline;
		padding: 4px 9px;
		border: 1px solid color-mix(in srgb, var(--pill-color) 40%, var(--line));
		border-radius: 999px;
		background: color-mix(in srgb, var(--pill-color) 8%, var(--panel2));
		color: var(--muted);
		font-size: 11px;
		/* Neutral by default so an outcome without a rule reads as unremarkable rather
		   than borrowing whichever colour happened to come last. */
		--pill-color: var(--muted);
	}
	strong {
		color: var(--pill-color);
		font-variant-numeric: tabular-nums;
	}
	/* Matches the roster's per-row colours in PosterProgress.svelte. */
	[data-status='succeeded'] {
		--pill-color: var(--good);
	}
	[data-status='review_required'] {
		--pill-color: var(--warn);
	}
	[data-status='failed'] {
		--pill-color: var(--bad);
	}
</style>
