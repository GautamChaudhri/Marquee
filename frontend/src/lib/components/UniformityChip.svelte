<script lang="ts">
	import type { LetterboxTvUniformity, SeasonUniformity, ShowUniformity } from '$lib/api/types';

	type Uniformity = LetterboxTvUniformity | ShowUniformity | SeasonUniformity;

	let { uniformity }: { uniformity: Uniformity } = $props();

	const META: Record<Exclude<Uniformity, null>, { label: string; tone: string }> = {
		uniform: { label: 'Uniform', tone: 'var(--good)' },
		clean_mixed: { label: 'Clean Mix', tone: 'var(--info)' },
		dirty_mixed: { label: 'Dirty Mix', tone: 'var(--warn)' },
		uniform_by_season: { label: 'By season', tone: 'var(--info)' },
		mixed: { label: 'Mixed', tone: 'var(--warn)' }
	};
</script>

{#if uniformity}
	<span class="uniformity-chip" style={`--c:${META[uniformity].tone}`}>
		{META[uniformity].label}
	</span>
{/if}

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
