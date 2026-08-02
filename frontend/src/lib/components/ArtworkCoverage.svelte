<script lang="ts">
	import { toneVar } from '$lib/display';
	import type { ArtworkCoverage } from '$lib/tv-artwork-coverage';

	let {
		coverage,
		mode = 'inline',
		decorative = false
	}: {
		coverage: ArtworkCoverage;
		mode?: 'pin' | 'inline' | 'dot';
		decorative?: boolean;
	} = $props();
</script>

<span
	class="coverage"
	class:pin={mode === 'pin'}
	data-state={coverage.state}
	style={`--coverage-color:${toneVar(coverage.tone)}`}
	role={decorative ? undefined : 'img'}
	aria-label={decorative ? undefined : coverage.accessibleLabel}
	aria-hidden={decorative ? 'true' : undefined}
	title={coverage.accessibleLabel}
>
	<span class="marker"></span>
	{#if mode === 'inline'}<span class="label">{coverage.label}</span>{/if}
</span>

<style>
	.coverage {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
		color: var(--muted);
		font-size: 12.5px;
		font-weight: 500;
		line-height: 1.25;
	}
	.marker {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--coverage-color);
		flex: none;
		box-shadow: 0 0 0 3px color-mix(in srgb, var(--coverage-color) 16%, transparent);
	}
	.coverage.pin {
		position: absolute;
		top: 8px;
		left: 8px;
		z-index: 2;
	}
	.label {
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
