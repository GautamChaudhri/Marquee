<script lang="ts">
	let {
		seasons,
		mode = 'subtitles',
		onCellClick
	}: {
		seasons: any[];
		mode?: 'subtitles' | 'letterbox';
		onCellClick?: (seasonNumber: number, episodeNumber: number) => void;
	} = $props();

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

	function handleCellClick(seasonNum: number, episodeId: number) {
		if (onCellClick) {
			onCellClick(seasonNum, episodeId);
		} else {
			const id = `episode-${seasonNum}-${episodeId}`;
			const el = document.getElementById(id);
			if (el) {
				el.scrollIntoView({ behavior: 'smooth', block: 'center' });
				el.classList.add('highlight-row');
				setTimeout(() => {
					el.classList.remove('highlight-row');
				}, 2000);
			}
		}
	}

	// Legend & Status mapping for Subtitles mode
	const SUBTITLE_STATUS_META: Record<string, { label: string; bg: string; border: string }> = {
		ok: {
			label: 'Fully Covered',
			bg: 'color-mix(in srgb, var(--good) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--good) 40%, var(--line))'
		},
		audio_gap: {
			label: 'Audio Gap',
			bg: 'color-mix(in srgb, var(--warn) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--warn) 40%, var(--line))'
		},
		subtitle_gap: {
			label: 'Subtitle Gap',
			bg: 'color-mix(in srgb, var(--info) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--info) 40%, var(--line))'
		},
		both_gap: {
			label: 'Both Gap',
			bg: 'color-mix(in srgb, var(--bad) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--bad) 40%, var(--line))'
		},
		unknown: {
			label: 'Unknown / Unscanned',
			bg: 'var(--ink3)',
			border: 'var(--line)'
		}
	};

	// Legend & Status mapping for Letterbox mode (C5)
	const LETTERBOX_STATUS_META: Record<string, { label: string; bg: string; border: string }> = {
		clear: {
			label: 'Clear',
			bg: 'color-mix(in srgb, var(--good) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--good) 40%, var(--line))'
		},
		sampled_clear: {
			label: 'Sampled Clear (Triage)',
			bg: 'color-mix(in srgb, var(--good) 8%, var(--ink3))', // desaturated/pale green
			border: 'color-mix(in srgb, var(--good) 20%, var(--line))'
		},
		candidate: {
			label: 'Letterboxed (Untreated)',
			bg: 'color-mix(in srgb, var(--warn) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--warn) 40%, var(--line))'
		},
		tagged: {
			label: 'Tagged',
			bg: 'color-mix(in srgb, var(--info) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--info) 40%, var(--line))'
		},
		reencoded: {
			label: 'Reencoded',
			bg: 'color-mix(in srgb, var(--gold) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--gold) 40%, var(--line))'
		},
		variable: {
			label: 'Variable AR',
			bg: 'color-mix(in srgb, var(--dovi) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--dovi) 40%, var(--line))'
		},
		error: {
			label: 'Error',
			bg: 'color-mix(in srgb, var(--bad) 20%, var(--ink3))',
			border: 'color-mix(in srgb, var(--bad) 40%, var(--line))'
		},
		ineligible: {
			label: 'Ineligible',
			bg: 'var(--ink2)',
			border: 'var(--line)'
		},
		unanalyzed: {
			label: 'Unanalyzed',
			bg: 'var(--ink3)',
			border: 'var(--line)'
		}
	};

	const METAS = $derived(mode === 'subtitles' ? SUBTITLE_STATUS_META : LETTERBOX_STATUS_META);

	function getEpisodeMeta(ep: any) {
		const key = ep.bucket || ep.status || 'unknown';
		return METAS[key] || METAS.unknown || METAS.unanalyzed;
	}

	function getTooltip(seasonNum: number, ep: any): string {
		if (mode === 'letterbox') {
			const epNum = ep.episode_number ?? ep.episode_id;
			const code = formatEpisodeCode(seasonNum, epNum);
			const titleStr = ep.title || 'Untitled';
			const bucketStr = ep.bucket || ep.status || 'unanalyzed';
			const arStr = ep.aspect_label || 'AR unknown';
			const confStr = ep.confidence ? `confidence: ${ep.confidence}` : 'no confidence';
			return `${code} · ${titleStr} · ${bucketStr} · ${arStr} · ${confStr}`;
		} else {
			const code = formatEpisodeCode(seasonNum, ep.episode_id);
			const titleStr = ep.title || 'Untitled';
			const meta = getEpisodeMeta(ep);
			return `${code} · ${titleStr} · ${meta.label} · Tier: ${ep.tier}`;
		}
	}
</script>

<div class="heatmap-panel">
	<!-- Legend Section -->
	<div class="legend">
		<div class="legend-group">
			<span class="legend-group-title">Status</span>
			{#each Object.entries(METAS) as [key, meta] (key)}
				<div class="legend-item">
					<span
						class="legend-color"
						class:hatched={key === 'unknown' || key === 'unanalyzed'}
						style={`background: ${meta.bg}; border-color: ${meta.border}`}
					></span>
					<span class="legend-label">{meta.label}</span>
				</div>
			{/each}
		</div>

		{#if mode === 'subtitles'}
			<div class="legend-group">
				<span class="legend-group-title">Accuracy Tier</span>
				<div class="legend-item">
					<span class="legend-color-dot"></span>
					<span class="legend-label">Tier-2 Probed (Accurate)</span>
				</div>
				<div class="legend-item">
					<span class="legend-color-no-dot"></span>
					<span class="legend-label">Tier-1 Synced (Basic)</span>
				</div>
			</div>
		{:else}
			<div class="legend-group">
				<span class="legend-group-title">Legend Hint</span>
				<div class="legend-item">
					<span class="legend-label-hint">
						* Sampled Clear: Season triage said clear, not individually scanned.
					</span>
				</div>
			</div>
		{/if}
	</div>

	<!-- Heatmap Rows -->
	<div class="seasons-grid">
		{#each sortedSeasons as season (season.season_number)}
			<div class="season-row" class:specials={season.season_number === 0}>
				<span class="season-label">
					{#if season.season_number === 0}
						<span class="specials-label-text">Specials</span>
						<span class="specials-hint">Excluded</span>
					{:else}
						Season {season.season_number}
					{/if}
				</span>

				<div class="episodes-cells">
					{#each season.episodes as ep (ep.episode_id)}
						{@const meta = getEpisodeMeta(ep)}
						<button
							type="button"
							class="cell"
							class:hatched={ep.bucket === 'unanalyzed' ||
								ep.status === 'unknown' ||
								ep.status === 'unanalyzed' ||
								(!ep.status && !ep.bucket)}
							style={`background: ${meta.bg}; border-color: ${meta.border}`}
							onclick={() => handleCellClick(season.season_number, ep.episode_id)}
							title={getTooltip(season.season_number, ep)}
						>
							<span class="ep-num">{ep.episode_number ?? ep.episode_id}</span>
							{#if mode === 'subtitles' && ep.tier === 'probed'}
								<span class="probed-dot"></span>
							{/if}
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
		gap: 18px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 18px;
	}
	.legend {
		display: flex;
		flex-direction: column;
		gap: 12px;
		padding-bottom: 14px;
		border-bottom: 1px solid var(--line);
	}
	.legend-group {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.legend-group-title {
		font-size: 10px;
		text-transform: uppercase;
		font-weight: 750;
		color: var(--faint2);
		letter-spacing: 0.05em;
		margin-right: 4px;
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
	.legend-color.hatched {
		background: repeating-linear-gradient(
			45deg,
			var(--ink3),
			var(--ink3) 2px,
			var(--line) 2px,
			var(--line) 4px
		) !important;
	}
	.legend-color-dot {
		width: 12px;
		height: 12px;
		border-radius: 3px;
		border: 1px solid var(--line);
		background: var(--ink3);
		position: relative;
	}
	.legend-color-dot::after {
		content: '';
		position: absolute;
		bottom: 2px;
		right: 2px;
		width: 4px;
		height: 4px;
		border-radius: 50%;
		background: var(--gold);
	}
	.legend-color-no-dot {
		width: 12px;
		height: 12px;
		border-radius: 3px;
		border: 1px solid var(--line);
		background: var(--ink3);
	}
	.legend-label {
		font-size: 11px;
		color: var(--muted);
	}
	.legend-label-hint {
		font-size: 11px;
		color: var(--faint2);
		font-style: italic;
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
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.specials-label-text {
		font-weight: 700;
	}
	.specials-hint {
		font-size: 9px;
		color: var(--low);
		text-transform: uppercase;
		letter-spacing: 0.05em;
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
		border: 1px solid var(--line);
		display: flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		font-size: 10px;
		font-family: var(--font-mono);
		font-weight: 600;
		color: var(--text);
		transition: all 0.12s ease;
		position: relative;
		background: transparent;
	}
	.cell:hover {
		transform: scale(1.1);
		z-index: 10;
		filter: brightness(1.2);
		border-color: var(--text) !important;
	}
	.cell.hatched {
		background: repeating-linear-gradient(
			45deg,
			var(--ink3),
			var(--ink3) 4px,
			var(--line) 4px,
			var(--line) 8px
		) !important;
		color: var(--muted);
	}
	.probed-dot {
		position: absolute;
		bottom: 2px;
		right: 2px;
		width: 4px;
		height: 4px;
		border-radius: 50%;
		background: var(--gold);
	}
</style>
