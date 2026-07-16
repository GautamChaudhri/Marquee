<script lang="ts">
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import HdrBadge from '$lib/components/HdrBadge.svelte';
	import SegmentedBar from '$lib/components/SegmentedBar.svelte';
	import UniformityChip from '$lib/components/UniformityChip.svelte';
	import HdrHeatmap from '$lib/components/HdrHeatmap.svelte';
	import { toast } from '$lib/toast';
	import { analyzeSeriesDovi, getHdrTvDetail } from '$lib/api/hdr';
	import { SHOW_STATUS_META } from '$lib/hdr-display';
	import { toneVar } from '$lib/display';
	import type { ShowStatus, EpisodeHdrItem, SeasonRollup, ShowRollup } from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// svelte-ignore state_referenced_locally
	let detail = $state(data.detail);
	// svelte-ignore state_referenced_locally
	let error = $state(data.error);

	let activeSeasonFilter = $state<string>('');
	let activeStatusFilter = $state<string>('');

	let analyzing = $state(false);
	let initiatedJobIds = $state<string[]>([]);

	async function refresh() {
		if (!detail) return;
		try {
			detail = await getHdrTvDetail(fetch, detail.series.id);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to reload show details';
		}
	}

	async function runAnalyze(seasonNumber?: number | null) {
		if (analyzing || !detail) return;
		analyzing = true;
		try {
			const { job_id } = await analyzeSeriesDovi(fetch, detail.series.id, seasonNumber);
			initiatedJobIds = [...new Set([...initiatedJobIds, job_id])];
		} catch (e) {
			analyzing = false;
			toast(e instanceof Error ? e.message : 'Could not start Dolby Vision analysis', 'bad');
		}
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		analyzing = false;
		toast(
			`Dolby Vision analysis ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		await refresh();
	}

	const DOVI_P8_VARIANT: Record<number, string> = {
		1: 'P8.1',
		2: 'P8.2',
		4: 'P8.4'
	};

	function doviBadgeLabel(ep: EpisodeHdrItem): string | null {
		if (!ep.hdr_tags.includes('dovi') && !ep.hdr_tags.includes('dovi_no_fallback')) return null;
		if (!ep.dovi || ep.dovi.profile == null) return 'DoVi P?';
		const profile =
			ep.dovi.profile === 8
				? ((ep.dovi.bl_signal_compatibility_id != null
						? DOVI_P8_VARIANT[ep.dovi.bl_signal_compatibility_id]
						: null) ?? 'P8')
				: `P${ep.dovi.profile}`;
		const suffix = ep.dovi.profile === 7 && ep.dovi.el_type ? ` ${ep.dovi.el_type}` : '';
		return `DoVi ${profile}${suffix}`;
	}

	function doviBadgeTone(ep: EpisodeHdrItem): string {
		if (!ep.dovi || ep.dovi.profile == null) return 'var(--low)';
		return ep.hdr_tags.includes('dovi_no_fallback') ? 'var(--bad)' : 'var(--gold)';
	}

	function doviBadgeTitle(ep: EpisodeHdrItem): string | null {
		const label = doviBadgeLabel(ep);
		if (!label) return null;
		if (!ep.dovi || ep.dovi.profile == null) {
			return `${label}. Dolby Vision is present, but this episode has not been analyzed yet.`;
		}
		if (ep.dovi.profile === 5)
			return `${label}. Profile 5 can show green/purple tint on non-DV playback.`;
		if (ep.dovi.el_type === 'FEL')
			return `${label}. Full enhancement layer can trigger playback issues.`;
		if (ep.dovi.el_type === 'MEL')
			return `${label}. Minimal enhancement layer is generally safe to drop.`;
		return label;
	}

	const episodesList = $derived.by(() => {
		if (!detail) return [];
		const list: (EpisodeHdrItem & { season_number: number })[] = [];
		for (const season of detail.seasons) {
			if (activeSeasonFilter !== '' && String(season.season_number) !== activeSeasonFilter) {
				continue;
			}
			for (const ep of season.episodes) {
				if (activeStatusFilter !== '' && ep.preference_status !== activeStatusFilter) {
					continue;
				}
				list.push({
					...ep,
					season_number: season.season_number
				});
			}
		}
		return list;
	});

	function formatEpisodeCode(seasonNum: number, episodeNum: number): string {
		const s = String(seasonNum).padStart(2, '0');
		const e = String(episodeNum).padStart(2, '0');
		return `S${s}E${e}`;
	}

	function getUnionKinds(rollup: SeasonRollup | ShowRollup) {
		return rollup.uniformity === 'uniform' ? (rollup.uniform_tags ?? []) : rollup.union_tags;
	}

	function segmentsForRollup(rollup: SeasonRollup | ShowRollup) {
		return [
			{ key: 'exceeds', count: rollup.status_counts.exceeds_target ?? 0, tone: 'gold' as const },
			{ key: 'meets', count: rollup.status_counts.meets_target ?? 0, tone: 'good' as const },
			{ key: 'below', count: rollup.status_counts.below_target ?? 0, tone: 'warn' as const },
			{ key: 'unknown', count: rollup.episodes_unknown ?? 0, tone: 'low' as const },
			{ key: 'no_target', count: rollup.status_counts.no_hdr_target ?? 0, tone: 'muted' as const }
		];
	}

	const showStatuses = Object.keys(SHOW_STATUS_META) as ShowStatus[];

	function statusMeta(status: string) {
		const meta = SHOW_STATUS_META[status as ShowStatus];
		if (!meta) return { label: status, color: 'var(--muted)' };
		return {
			label: meta.label,
			color: toneVar(meta.tone)
		};
	}
</script>

{#if error || !detail}
	<section class="page error-page">
		<SectionHeader
			title="TV HDR Detail"
			subtitle="Detailed Television HDR rollup and heatmap view."
		/>
		<div class="error">{error ?? 'Failed to load show details.'}</div>
	</section>
{:else}
	<section class="page">
		<div class="header-nav">
			<a class="back-link" href="/hdr/tv">← Back to Shows</a>
		</div>
		<FeatureActivityPanel
			scopeKey={`feature:hdr:series:${detail.series.id}`}
			query={{ feature_area: 'hdr' }}
			jobIds={initiatedJobIds}
			heading="Series HDR activity"
			onSettled={handleJobSettled}
		/>

		<div class="hero">
			<div>
				<p class="eyebrow">{detail.series.year ?? '—'} · Series</p>
				<h1>{detail.series.title}</h1>
				<div class="hero-badges">
					<div class="rollup-badge" style={`--c: ${statusMeta(detail.rollup.status).color}`}>
						{statusMeta(detail.rollup.status).label}
					</div>
					<UniformityChip uniformity={detail.rollup.uniformity} />
					<HdrBadge kinds={getUnionKinds(detail.rollup)} />
				</div>
			</div>
			<div class="hero-side">
				<div class="profile-info">
					<span class="profile-label">Quality Profile</span>
					<strong>{detail.profile.name ?? '—'}</strong>
					{#if detail.profile.targets.length}
						<div class="profile-targets">
							<span class="label">Targets:</span>
							<HdrBadge kinds={detail.profile.targets} />
						</div>
					{/if}
				</div>
			</div>
		</div>

		<!-- Overview Rollup Card -->
		<div class="panel overview-panel">
			<h2>Compliance overview</h2>
			<div class="overview-stats">
				<div class="bar-container">
					<SegmentedBar
						segments={segmentsForRollup(detail.rollup)}
						total={detail.rollup.episodes_total}
						fractionText={`${detail.rollup.meeting_fraction.met}/${detail.rollup.meeting_fraction.of} episodes meet target`}
					/>
				</div>
				<div class="overview-actions">
					<button
						class="action-btn"
						onclick={() => runAnalyze(null)}
						disabled={analyzing || !detail.binaries.ffprobe}
					>
						{analyzing ? 'Analyzing…' : 'Analyze entire show'}
					</button>
					{#if !detail.binaries.ffprobe}
						<small class="warn-text">ffprobe missing — install to analyze Dolby Vision</small>
					{/if}
				</div>
			</div>
		</div>

		<!-- Heatmap Centerpiece -->
		<div class="section-block">
			<h2>Season × episode heatmap</h2>
			<HdrHeatmap seasons={detail.seasons} />
		</div>

		<!-- Season Rollup Grid -->
		<div class="section-block">
			<h2>Season rollups</h2>
			<div class="seasons-list">
				{#each detail.seasons as season (season.season_number)}
					{@const hasDovi =
						season.rollup.union_tags.includes('dovi') ||
						season.rollup.union_tags.includes('dovi_no_fallback')}
					<div class="season-card" class:specials={season.is_specials}>
						<div class="season-card-head">
							<div>
								<strong
									>{season.season_number === 0
										? 'Specials'
										: `Season ${season.season_number}`}</strong
								>
								{#if season.is_specials}
									<span
										class="specials-badge"
										title="Specials are excluded from the overall show rollup status."
										>Excluded from status</span
									>
								{/if}
							</div>
							<div class="season-card-badges">
								<UniformityChip uniformity={season.rollup.uniformity} />
								<HdrBadge kinds={getUnionKinds(season.rollup)} />
							</div>
						</div>

						<div class="season-card-stats">
							<div class="season-bar">
								<SegmentedBar
									segments={segmentsForRollup(season.rollup)}
									total={season.rollup.episodes_total}
									fractionText={`${season.rollup.status_counts.meets_target ?? 0} meet · ${season.rollup.status_counts.exceeds_target ?? 0} exceed · ${season.rollup.status_counts.below_target ?? 0} below`}
								/>
							</div>
							<div class="season-card-actions">
								{#if hasDovi}
									<button
										class="season-btn"
										onclick={() => runAnalyze(season.season_number)}
										disabled={analyzing || !detail.binaries.ffprobe}
									>
										Analyze season
									</button>
								{/if}
							</div>
						</div>
					</div>
				{/each}
			</div>
		</div>

		<!-- Episodes Table -->
		<div class="panel list-panel">
			<div class="table-header-bar">
				<div>
					<h2>Episodes</h2>
					<p>{episodesList.length} episodes match filters</p>
				</div>
				<div class="table-filters">
					<label>
						<span>Season</span>
						<select bind:value={activeSeasonFilter}>
							<option value="">All seasons</option>
							{#each detail.seasons as s (s.season_number)}
								<option value={String(s.season_number)}>
									{s.season_number === 0 ? 'Specials' : `Season ${s.season_number}`}
								</option>
							{/each}
						</select>
					</label>
					<label>
						<span>Status</span>
						<select bind:value={activeStatusFilter}>
							<option value="">All statuses</option>
							{#each showStatuses as status (status)}
								<option value={status}>
									{SHOW_STATUS_META[status].label}
								</option>
							{/each}
						</select>
					</label>
				</div>
			</div>

			<div class="rows">
				<div class="row head">
					<span>Code</span>
					<span>Title</span>
					<span>HDR profiles</span>
					<span>Status</span>
					<span>DoVi Analysis</span>
				</div>
				{#each episodesList as ep (ep.id)}
					<div class="row item" id={`episode-${ep.season_number}-${ep.episode_number}`}>
						<span class="ep-code">{formatEpisodeCode(ep.season_number, ep.episode_number)}</span>
						<span class="ep-title">
							<strong>{ep.title ?? `Episode ${ep.episode_number}`}</strong>
							<small>{ep.resolution ?? 'Unknown res'}</small>
						</span>
						<span class="tags-cell">
							<HdrBadge kinds={ep.hdr_tags} />
						</span>
						<span class="status-cell" style={`color: ${statusMeta(ep.preference_status).color}`}>
							{statusMeta(ep.preference_status).label}
						</span>
						<span class="dovi-cell">
							{#if doviBadgeLabel(ep)}
								<span
									class="dovi-badge"
									style={`--c:${doviBadgeTone(ep)}`}
									title={doviBadgeTitle(ep) ?? undefined}
								>
									{doviBadgeLabel(ep)}
								</span>
							{:else}
								<span class="muted">—</span>
							{/if}
						</span>
					</div>
				{/each}
				{#if episodesList.length === 0}
					<div class="empty-state">No episodes match the selected filters.</div>
				{/if}
			</div>
		</div>
	</section>
{/if}

<style>
	.page {
		display: flex;
		flex-direction: column;
		gap: 18px;
		padding: 22px 24px 32px;
	}
	.header-nav {
		margin-bottom: -6px;
	}
	.back-link {
		color: var(--gold);
		text-decoration: none;
		font-size: 13px;
		font-weight: 500;
	}
	.back-link:hover {
		text-decoration: underline;
	}
	.hero {
		display: flex;
		justify-content: space-between;
		gap: 18px;
		padding: 24px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background:
			radial-gradient(
				circle at top right,
				color-mix(in srgb, var(--dovi) 20%, transparent),
				transparent 35%
			),
			linear-gradient(160deg, var(--panel), var(--ink2));
	}
	.eyebrow {
		margin: 0 0 6px;
		font-size: 11px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--muted);
	}
	h1,
	h2,
	p {
		margin: 0;
	}
	h1 {
		font-size: 30px;
		letter-spacing: -0.02em;
	}
	.hero-badges {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		margin-top: 12px;
	}
	.rollup-badge {
		font-size: 11px;
		font-weight: 700;
		padding: 2px 6px;
		border-radius: 5px;
		background: color-mix(in srgb, var(--c) 14%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 30%, transparent);
		color: var(--c);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.hero-side {
		display: flex;
		align-items: center;
		flex-shrink: 0;
	}
	.profile-info {
		display: flex;
		flex-direction: column;
		gap: 4px;
		align-items: flex-end;
		text-align: right;
	}
	.profile-label {
		font-size: 10px;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.profile-info strong {
		font-size: 18px;
		font-weight: 600;
	}
	.profile-targets {
		display: flex;
		align-items: center;
		gap: 6px;
		margin-top: 4px;
	}
	.profile-targets .label {
		font-size: 10px;
		color: var(--muted);
	}
	.panel {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		padding: 18px;
	}
	.overview-panel {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.overview-panel h2 {
		font-size: 15px;
	}
	.overview-stats {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 24px;
	}
	.bar-container {
		flex: 1;
	}
	.overview-actions {
		display: flex;
		flex-direction: column;
		gap: 4px;
		align-items: flex-end;
	}
	.action-btn {
		padding: 10px 16px;
		border: 1px solid var(--gold);
		border-radius: var(--radius);
		background: color-mix(in srgb, var(--gold) 14%, transparent);
		color: var(--gold);
		font-weight: 600;
		cursor: pointer;
		white-space: nowrap;
	}
	.action-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.warn-text {
		font-size: 11px;
		color: var(--bad);
	}
	.section-block {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.section-block h2 {
		font-size: 15px;
		font-weight: 600;
		color: var(--text);
		border-left: 3px solid var(--gold);
		padding-left: 8px;
	}
	.seasons-list {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: 14px;
	}
	.season-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.season-card.specials {
		border-color: var(--line);
		opacity: 0.8;
	}
	.season-card-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		border-bottom: 1px solid var(--line);
		padding-bottom: 8px;
	}
	.season-card-head strong {
		font-size: 14px;
		display: block;
	}
	.specials-badge {
		font-size: 10px;
		color: var(--bad);
		background: color-mix(in srgb, var(--bad) 12%, transparent);
		padding: 1px 4px;
		border-radius: 3px;
		margin-top: 4px;
		display: inline-block;
	}
	.season-card-badges {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 4px;
	}
	.season-card-stats {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.season-bar {
		flex: 1;
	}
	.season-card-actions {
		display: flex;
		justify-content: flex-end;
	}
	.season-btn {
		padding: 6px 12px;
		font-size: 11px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--ink2);
		color: var(--text);
		cursor: pointer;
		font-weight: 500;
	}
	.season-btn:hover {
		background: var(--ink3);
	}
	.season-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.table-header-bar {
		display: flex;
		justify-content: space-between;
		align-items: flex-end;
		margin-bottom: 14px;
		border-bottom: 1px solid var(--line);
		padding-bottom: 10px;
	}
	.table-header-bar h2 {
		font-size: 15px;
	}
	.table-header-bar p {
		font-size: 12px;
		color: var(--muted);
		margin-top: 4px;
	}
	.table-filters {
		display: flex;
		gap: 12px;
	}
	.table-filters label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.table-filters label span {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
	}
	.table-filters select {
		height: 32px;
		min-width: 120px;
		padding: 0 8px;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--ink2);
		color: var(--text);
		outline: none;
		font-size: 12px;
	}
	.rows {
		display: flex;
		flex-direction: column;
	}
	.row {
		display: grid;
		grid-template-columns: 1fr 3fr 2fr 2.2fr 1.8fr;
		gap: 12px;
		padding: 10px 12px;
		align-items: center;
		border-bottom: 1px solid var(--line);
	}
	.row.head {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
		font-weight: 600;
		border-bottom: 2px solid var(--line);
	}
	.row.item {
		transition: background 0.12s ease;
	}
	.row.item:hover {
		background: var(--ink3);
	}
	:global(.row.highlight-row) {
		background: color-mix(in srgb, var(--gold) 20%, var(--ink3)) !important;
		outline: 1.5px solid var(--gold);
	}
	.ep-code {
		font-family: var(--font-mono);
		font-size: 12px;
		font-weight: 600;
		color: var(--muted);
	}
	.ep-title {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.ep-title strong {
		font-size: 13.5px;
		color: var(--text);
	}
	.ep-title small {
		font-size: 11px;
		color: var(--muted);
	}
	.status-cell {
		font-size: 12px;
		font-weight: 600;
	}
	.dovi-badge {
		font-family: var(--font-mono);
		font-size: 10px;
		font-weight: 700;
		padding: 2px 6px;
		border-radius: 4px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 15%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 30%, transparent);
		white-space: nowrap;
	}
	.empty-state {
		text-align: center;
		padding: 30px;
		color: var(--muted);
		font-size: 13px;
		border-bottom: 1px dashed var(--line);
	}
	.error {
		padding: 18px 20px;
		border-radius: var(--radius);
		border: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line));
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		color: var(--muted);
	}
</style>
