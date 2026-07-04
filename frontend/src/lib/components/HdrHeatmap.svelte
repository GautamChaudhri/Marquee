<script lang="ts">
	import type { HdrTvSeason } from '$lib/api/types';
	import { SHOW_STATUS_META } from '$lib/hdr-display';

	let { seasons }: { seasons: HdrTvSeason[] } = $props();

	// Sort seasons: regular seasons first (ascending), Specials (season_number === 0) last
	const sortedSeasons = $derived(
		[...seasons].sort((a, b) => {
			if (a.season_number === 0) return 1;
			if (b.season_number === 0) return -1;
			return a.season_number - b.season_number;
		})
	);

	function formatEpisodeCode(seasonNum: number, episodeNum: number): string {
		const s = String(seasonNum).padStart(2, '0');
		const e = String(episodeNum).padStart(2, '0');
		return `S${s}E${e}`;
	}

	function scrollToEpisode(seasonNum: number, episodeNum: number) {
		const id = `episode-${seasonNum}-${episodeNum}`;
		const el = document.getElementById(id);
		if (el) {
			el.scrollIntoView({ behavior: 'smooth', block: 'center' });
			el.classList.add('highlight-row');
			setTimeout(() => {
				el.classList.remove('highlight-row');
			}, 2000);
		}
	}

	const BUCKET_META = {
		sdr: { label: 'SDR', color: 'var(--muted)', border: 'var(--line)' },
		hdr: {
			label: 'HDR',
			color: 'var(--info)',
			border: 'color-mix(in srgb, var(--info) 40%, transparent)'
		},
		hdr10: {
			label: 'HDR10',
			color: 'var(--info)',
			border: 'color-mix(in srgb, var(--info) 60%, transparent)'
		},
		hdr10p: {
			label: 'HDR10+',
			color: 'var(--dovi)',
			border: 'color-mix(in srgb, var(--dovi) 50%, transparent)'
		},
		dovi: {
			label: 'DoVi',
			color: 'var(--gold)',
			border: 'color-mix(in srgb, var(--gold) 50%, transparent)'
		},
		dovi_no_fallback: {
			label: 'DoVi-',
			color: 'var(--bad)',
			border: 'color-mix(in srgb, var(--bad) 50%, transparent)'
		},
		unknown: { label: 'Unknown', color: 'var(--muted)', border: 'var(--line)' }
	};
</script>

<div class="heatmap-panel">
	<div class="legend">
		{#each Object.entries(BUCKET_META) as [key, meta] (key)}
			<div class="legend-item">
				<span class={`legend-color ${key}`}></span>
				<span class="legend-label">{meta.label}</span>
			</div>
		{/each}
	</div>

	<div class="seasons-grid">
		{#each sortedSeasons as season (season.season_number)}
			<div class="season-row" class:specials={season.season_number === 0}>
				<span class="season-label">
					{season.season_number === 0 ? 'Specials' : `Season ${season.season_number}`}
				</span>
				<div class="episodes-cells">
					{#each season.episodes as ep (ep.id)}
						{@const meta =
							BUCKET_META[ep.bucket as keyof typeof BUCKET_META] ?? BUCKET_META.unknown}
						<button
							type="button"
							class={`cell ${ep.bucket}`}
							style={`--border: ${meta.border}`}
							onclick={() => scrollToEpisode(season.season_number, ep.episode_number)}
							title={`${formatEpisodeCode(season.season_number, ep.episode_number)} · ${ep.title ?? 'Untitled'} · ${ep.hdr_tags.join(', ') || 'SDR'} · ${SHOW_STATUS_META[ep.preference_status]?.label ?? ep.preference_status}`}
						>
							<span class="ep-num">{ep.episode_number}</span>
						</button>
					{/each}
				</div>
			</div>
		{/each}
	</div>
</div>

<style>
	.heatmap-panel {
		display: flex;
		flex-direction: column;
		gap: 16px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 18px;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 14px;
		padding-bottom: 12px;
		border-bottom: 1px solid var(--line);
	}
	.legend-item {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.legend-color {
		width: 12px;
		height: 12px;
		border-radius: 3px;
		border: 1px solid var(--line);
	}
	.legend-label {
		font-size: 11px;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.seasons-grid {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.season-row {
		display: flex;
		align-items: flex-start;
		gap: 16px;
	}
	.season-row.specials {
		opacity: 0.65;
		margin-top: 6px;
		border-top: 1px dashed var(--line);
		padding-top: 12px;
	}
	.season-label {
		width: 80px;
		font-size: 12px;
		font-weight: 600;
		color: var(--muted);
		padding-top: 5px;
		flex-shrink: 0;
	}
	.episodes-cells {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		flex: 1;
	}
	.cell {
		width: 26px;
		height: 26px;
		border-radius: 4px;
		border: 1px solid var(--border);
		display: flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		font-size: 10px;
		font-family: var(--font-mono);
		font-weight: 600;
		color: var(--muted);
		transition: all 0.12s ease;
		background: var(--ink3);
	}
	.cell:hover {
		transform: scale(1.1);
		z-index: 10;
		filter: brightness(1.2);
		border-color: var(--text);
		color: var(--text);
	}

	/* Colors */
	.sdr {
		background: var(--ink3);
		color: var(--faint2);
	}
	.hdr {
		background: color-mix(in srgb, var(--info) 18%, var(--ink3));
		color: var(--info);
	}
	.hdr10 {
		background: color-mix(in srgb, var(--info) 26%, var(--ink3));
		color: var(--info);
	}
	.hdr10p {
		background: color-mix(in srgb, var(--dovi) 22%, var(--ink3));
		color: var(--dovi);
	}
	.dovi {
		background: color-mix(in srgb, var(--gold) 22%, var(--ink3));
		color: var(--gold);
	}
	.dovi_no_fallback {
		background: color-mix(in srgb, var(--bad) 22%, var(--ink3));
		color: var(--bad);
	}
	.unknown {
		background: repeating-linear-gradient(
			45deg,
			var(--ink3),
			var(--ink3) 4px,
			var(--line) 4px,
			var(--line) 8px
		);
		color: var(--faint2);
	}

	/* Legend Color Overrides */
	.legend-color.sdr {
		background: var(--ink3);
	}
	.legend-color.hdr {
		background: color-mix(in srgb, var(--info) 25%, var(--ink3));
		border-color: var(--info);
	}
	.legend-color.hdr10 {
		background: color-mix(in srgb, var(--info) 35%, var(--ink3));
		border-color: var(--info);
	}
	.legend-color.hdr10p {
		background: color-mix(in srgb, var(--dovi) 30%, var(--ink3));
		border-color: var(--dovi);
	}
	.legend-color.dovi {
		background: color-mix(in srgb, var(--gold) 30%, var(--ink3));
		border-color: var(--gold);
	}
	.legend-color.dovi_no_fallback {
		background: color-mix(in srgb, var(--bad) 30%, var(--ink3));
		border-color: var(--bad);
	}
	.legend-color.unknown {
		background: repeating-linear-gradient(
			45deg,
			var(--ink3),
			var(--ink3) 3px,
			var(--line) 3px,
			var(--line) 6px
		);
	}
</style>
