<script lang="ts">
	import type { HdrKind } from '$lib/api/types';
	let { kind }: { kind: HdrKind | null } = $props();

	const META: Record<HdrKind, { label: string; v: string }> = {
		dovi: { label: 'DoVi', v: '--dovi' },
		hdr10p: { label: 'HDR10+', v: '--warn' },
		hdr10: { label: 'HDR10', v: '--info' },
		sdr: { label: 'SDR', v: '--faint' }
	};
	const m = $derived(kind ? META[kind] : null);
</script>

{#if m}
	<span class="hdr" style="--c:var({m.v})">{m.label}</span>
{/if}

<style>
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
