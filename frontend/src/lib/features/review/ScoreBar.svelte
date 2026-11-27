<script lang="ts">
	import { barLabel, segments, type ScorePart } from './score';

	/** A score as one short stacked bar: a colour per family of parts, as long as the score.
	 *  A run whose archive is gone has no parts; the empty track keeps the row's height. */
	let {
		parts,
		large = false
	}: { parts: readonly ScorePart[] | null | undefined; large?: boolean } = $props();

	const bar = $derived(segments(parts));
	const label = $derived(
		bar.length > 0 ? `Score parts: ${barLabel(bar)}` : 'This run kept no score breakdown'
	);
</script>

<div
	class="bar"
	class:large
	class:empty={bar.length === 0}
	role="img"
	aria-label={label}
	title={bar.length === 0 ? label : undefined}
	data-testid="score-bar"
>
	{#each bar as segment (segment.family)}
		<span
			class="seg {segment.family}"
			style:width="{(segment.share * 100).toFixed(2)}%"
			title="{segment.label} {segment.amount.toFixed(2)}"
		></span>
	{/each}
</div>

<style>
	.bar {
		display: flex;
		gap: 1px;
		height: 5px;
		border-radius: 3px;
		background: var(--panel2);
		overflow: hidden;
	}
	.bar.large {
		height: 9px;
		border-radius: 5px;
	}
	.bar.empty {
		background: repeating-linear-gradient(90deg, var(--panel2) 0 6px, transparent 6px 10px);
	}
	.seg {
		flex: none;
		height: 100%;
	}
	.taste {
		background: var(--gold);
	}
	.look {
		background: var(--info);
	}
	.content {
		background: var(--teal);
	}
	.source {
		background: var(--purple);
	}
	.residual {
		background: var(--magenta);
	}
	.other {
		background: var(--faint);
	}
</style>
