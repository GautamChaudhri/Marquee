<script lang="ts">
	/** Feature contribution bar (handoff §3). Each segment height encodes its
	 *  magnitude; with showLabels a color-keyed legend row names each feature. */
	let {
		segments = [],
		showLabels = false
	}: {
		segments?: { value: number; label?: string; color?: string; signed?: number }[];
		showLabels?: boolean;
	} = $props();

	const PALETTE = ['--gold', '--good', '--info', '--dovi', '--warn', '--low', '--cpu', '--gpu'];

	/** Abbreviated, human-readable names for the scorer's contribution keys. */
	const ABBREV: Record<string, string> = {
		knn_sim: 'kNN',
		aesthetic: 'Aes',
		title_colorfulness: 'Color',
		text_residual: 'Clean',
		resolution: 'Res',
		sharpness: 'Sharp',
		face_area: 'Face',
		provenance: 'Official',
		lang_match: 'Lang',
		dino_knn: 'DINO',
		taste_typicality: 'Typ',
		quality_artifacts: 'Qual',
		official_family: 'Family'
	};
	const abbr = (label?: string): string => (label ? (ABBREV[label] ?? label) : '');
</script>

<div class="scorebar">
	<div class="bar">
		{#each segments as seg, i (i)}
			<span
				class="seg"
				class:neg={seg.signed != null && seg.signed < 0}
				title={seg.label ? `${seg.label}: ${(seg.signed ?? seg.value).toFixed(2)}` : undefined}
				style="--c:var({seg.color ?? PALETTE[i % PALETTE.length]}); height:{Math.max(
					6,
					Math.min(100, seg.value * 100)
				)}%"
			></span>
		{/each}
	</div>
	{#if showLabels}
		<div class="legend">
			{#each segments as seg, i (i)}
				<span
					class="lab"
					title={seg.label}
					style="--c:var({seg.color ?? PALETTE[i % PALETTE.length]})"
				>
					{#if seg.signed != null && seg.signed < 0}<span class="sign">−</span>{/if}{abbr(
						seg.label
					)}
				</span>
			{/each}
		</div>
	{/if}
</div>

<style>
	.scorebar {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
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
	.seg.neg {
		opacity: 0.45;
	}
	.legend {
		display: flex;
		gap: 3px;
		padding: 0 2px;
	}
	.lab {
		flex: 1;
		min-width: 4px;
		font-size: 9px;
		line-height: 1.15;
		text-align: center;
		color: var(--muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		border-top: 2px solid var(--c);
		padding-top: 2px;
	}
	.sign {
		color: var(--low);
		font-weight: 700;
	}
</style>
