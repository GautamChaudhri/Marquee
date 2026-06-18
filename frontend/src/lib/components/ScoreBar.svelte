<script lang="ts">
	/** 8-segment feature bar (handoff §3). Each segment height encodes its value. */
	let { segments = [] }: { segments?: { value: number; label?: string; color?: string }[] } =
		$props();

	const PALETTE = ['--gold', '--good', '--info', '--dovi', '--warn', '--low', '--cpu', '--gpu'];
</script>

<div class="bar">
	{#each segments as seg, i (i)}
		<span
			class="seg"
			title={seg.label ? `${seg.label}: ${seg.value.toFixed(2)}` : undefined}
			style="--c:var({seg.color ?? PALETTE[i % PALETTE.length]}); height:{Math.max(
				6,
				Math.min(100, seg.value * 100)
			)}%"
		></span>
	{/each}
</div>

<style>
	.bar {
		display: flex;
		align-items: flex-end;
		gap: 3px;
		height: 34px;
		padding: 2px;
	}
	.seg {
		flex: 1;
		min-width: 4px;
		background: var(--c);
		border-radius: 3px;
		opacity: 0.9;
	}
</style>
