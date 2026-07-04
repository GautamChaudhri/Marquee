<script lang="ts">
	import { toneVar, type Tone } from '$lib/display';

	let {
		segments,
		total,
		fractionText
	}: {
		segments: { key: string; count: number; tone: Tone }[];
		total: number;
		fractionText?: string;
	} = $props();

	function pct(count: number): string {
		if (!total || count <= 0) return '0%';
		return `${Math.max(3, (count / total) * 100)}%`;
	}
</script>

<div class="bar-wrap">
	<div class="bar">
		{#each segments.filter((s) => s.count > 0) as segment (segment.key)}
			<span
				class="seg"
				style={`width:${pct(segment.count)};--c:${toneVar(segment.tone)}`}
				title={`${segment.key}: ${segment.count}`}
			></span>
		{/each}
	</div>
	{#if fractionText}<small class="fraction">{fractionText}</small>{/if}
</div>

<style>
	.bar-wrap {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.bar {
		display: flex;
		height: 8px;
		border-radius: 999px;
		overflow: hidden;
		background: var(--line);
		width: 100%;
	}
	.seg {
		display: block;
		height: 100%;
		background: var(--c);
	}
	.fraction {
		color: var(--muted);
		font-size: 11px;
		white-space: nowrap;
	}
</style>
