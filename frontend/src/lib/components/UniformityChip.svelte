<script lang="ts">
	import type { ShowUniformity, SeasonUniformity } from '$lib/api/types';

	let { uniformity }: { uniformity: ShowUniformity | SeasonUniformity } = $props();

	const META: Record<ShowUniformity | SeasonUniformity, { label: string; tone: string }> = {
		uniform: { label: 'Uniform', tone: 'var(--good)' },
		uniform_by_season: { label: 'By season', tone: 'var(--info)' },
		mixed: { label: 'Mixed', tone: 'var(--warn)' }
	};
</script>

<span class="uniformity-chip" style={`--c:${META[uniformity].tone}`}>
	{META[uniformity].label}
</span>

<style>
	.uniformity-chip {
		font-size: 11px;
		font-weight: 600;
		padding: 2px 6px;
		border-radius: 5px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 12%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 25%, transparent);
		white-space: nowrap;
		display: inline-flex;
		align-items: center;
	}
</style>
