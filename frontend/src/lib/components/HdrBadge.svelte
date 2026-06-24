<script lang="ts">
	import type { HdrKind } from '$lib/api/types';
	let {
		kind = null,
		kinds = []
	}: { kind?: HdrKind | null; kinds?: HdrKind[] } = $props();

	const META: Record<HdrKind, { label: string; v: string }> = {
		hdr: { label: 'HDR', v: '--good' },
		dovi: { label: 'DoVi', v: '--dovi' },
		dovi_no_fallback: { label: 'DoVi-', v: '--low' },
		hdr10p: { label: 'HDR10+', v: '--warn' },
		hdr10: { label: 'HDR10', v: '--info' },
		sdr: { label: 'SDR', v: '--faint' }
	};
	const active = $derived(
		kinds.length ? kinds.filter((value) => value in META) : kind ? [kind] : []
	);
</script>

{#if active.length}
	<span class="row">
		{#each active as value (value)}
			{@const m = META[value]}
			<span class="hdr" style="--c:var({m.v})">{m.label}</span>
		{/each}
	</span>
{/if}

<style>
	.row {
		display: inline-flex;
		flex-wrap: wrap;
		gap: 4px;
		align-items: center;
	}
	.hdr {
		font-family: var(--font-mono);
		font-size: 10px;
		font-weight: 600;
		letter-spacing: 0.02em;
		padding: 2px 6px;
		border-radius: 5px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 14%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 30%, transparent);
		white-space: nowrap;
	}
</style>
