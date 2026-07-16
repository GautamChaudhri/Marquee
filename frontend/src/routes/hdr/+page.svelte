<script lang="ts">
	import { goto } from '$app/navigation';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import { analyzeDoviBatch, putRadarrOverlayPreferences } from '$lib/api/radarr-overlay';
	import { analyzeTvDovi, getHdrSummary, putSonarrPreferences } from '$lib/api/hdr';
	import PreferenceTargetsEditor from '$lib/components/PreferenceTargetsEditor.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import SegmentedBar from '$lib/components/SegmentedBar.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	import { toast } from '$lib/toast';
	import {
		HDR_DISTRIBUTION_ORDER,
		HDR_DIST_TONE,
		HDR_TAG_LABEL,
		MOVIE_STATUS_META,
		SHOW_STATUS_META,
		UNIFORMITY_META
	} from '$lib/hdr-display';
	import type {
		HdrPreferenceChoice,
		RadarrOverlayStatus,
		ShowStatus,
		ShowUniformity
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// svelte-ignore state_referenced_locally
	let summary = $state(data.summary);
	// svelte-ignore state_referenced_locally
	let error = $state(data.error);

	const showStatuses = Object.keys(SHOW_STATUS_META) as ShowStatus[];
	const uniformities = Object.keys(UNIFORMITY_META) as ShowUniformity[];

	async function refresh() {
		try {
			summary = await getHdrSummary(fetch);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load HDR summary';
		}
	}

	function movieDistHref(key: string): string {
		return `/hdr/movies?hdr_tags=${encodeURIComponent(key)}`;
	}
	function movieStatusHref(status: RadarrOverlayStatus): string {
		return `/hdr/movies?preference_status=${status}`;
	}
	function tvDistHref(key: string): string {
		return `/hdr/tv?hdr_tags=${encodeURIComponent(key)}`;
	}
	function tvStatusHref(status: ShowStatus): string {
		return `/hdr/tv?preference_status=${status}`;
	}
	function tvUniformityHref(uniformity: ShowUniformity): string {
		return `/hdr/tv?uniformity=${uniformity}`;
	}

	// ── DoVi analysis (movies + TV) ────────────────────────────────────────
	let analyzingMovies = $state(false);
	let analyzingTv = $state(false);
	let movieJobId = $state<string | null>(null);
	let tvJobId = $state<string | null>(null);
	let initiatedJobIds = $state<string[]>([]);

	async function analyzeAllMovies() {
		if (analyzingMovies) return;
		analyzingMovies = true;
		try {
			const { job_id } = await analyzeDoviBatch(fetch);
			movieJobId = job_id;
			initiatedJobIds = [...new Set([...initiatedJobIds, job_id])];
		} catch (e) {
			analyzingMovies = false;
			toast(e instanceof Error ? e.message : 'Could not start Dolby Vision analysis', 'bad');
		}
	}

	async function analyzeAllShows() {
		if (analyzingTv) return;
		analyzingTv = true;
		try {
			const { job_id } = await analyzeTvDovi(fetch);
			tvJobId = job_id;
			initiatedJobIds = [...new Set([...initiatedJobIds, job_id])];
		} catch (e) {
			analyzingTv = false;
			toast(e instanceof Error ? e.message : 'Could not start Dolby Vision analysis', 'bad');
		}
	}

	async function saveMoviePreferences(
		profiles: {
			profile_id: number;
			meet_target: HdrPreferenceChoice | null;
			exceed_target: HdrPreferenceChoice | null;
			excluded_targets: HdrPreferenceChoice[];
		}[]
	) {
		await putRadarrOverlayPreferences(fetch, profiles);
		await refresh();
	}

	async function saveTvPreferences(
		profiles: {
			profile_id: number;
			meet_target: HdrPreferenceChoice | null;
			exceed_target: HdrPreferenceChoice | null;
			excluded_targets: HdrPreferenceChoice[];
		}[]
	) {
		await putSonarrPreferences(fetch, profiles);
		await refresh();
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		if (snapshot.job_id === movieJobId) analyzingMovies = false;
		if (snapshot.job_id === tvJobId) analyzingTv = false;
		toast(
			`Dolby Vision analysis ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		await refresh();
	}
</script>

<SectionHeader
	title="HDR Management"
	subtitle="HDR truth, Dolby Vision coverage, and per-profile preference compliance — movies and TV."
/>

{#if error || !summary}
	<div class="error">{error ?? 'Failed to load HDR summary.'}</div>
{:else}
	<FeatureActivityPanel
		scopeKey="feature:hdr:overview"
		query={{ feature_area: 'hdr' }}
		jobIds={initiatedJobIds}
		heading="HDR activity"
		onSettled={handleJobSettled}
	/>
	<div class="section">
		<div class="actions">
			<button class="action" onclick={() => goto('/hdr/movies')}>
				<span>Movie HDR list</span>
				<b>{summary.movies.total}</b>
			</button>
			<button class="action" onclick={() => goto('/hdr/tv')}>
				<span>TV HDR list</span>
				<b>{summary.tv.shows_total}</b>
			</button>
		</div>
	</div>

	<div class="section">
		<h2>Movies</h2>
		<div class="stats">
			<StatCard label="Total" value={summary.movies.total} tone="info" />
			{#each HDR_DISTRIBUTION_ORDER as key (key)}
				<a class="tile-link" href={movieDistHref(key)}>
					<StatCard
						label={HDR_TAG_LABEL[key]}
						value={summary.movies.distribution[key]}
						tone={HDR_DIST_TONE[key]}
					/>
				</a>
			{/each}
		</div>
		<div class="stats status-row">
			{#each ['below_target', 'meets_target', 'exceeds_target'] as const as status (status)}
				<a class="tile-link" href={movieStatusHref(status)}>
					<StatCard
						label={MOVIE_STATUS_META[status].label}
						value={summary.movies.status_counts[status] ?? 0}
						tone={MOVIE_STATUS_META[status].tone}
					/>
				</a>
			{/each}
		</div>
	</div>

	<div class="section">
		<h2>Television</h2>
		<div class="stats">
			<StatCard label="Shows" value={summary.tv.shows_total} tone="info" />
			{#each HDR_DISTRIBUTION_ORDER as key (key)}
				<a class="tile-link" href={tvDistHref(key)}>
					<StatCard
						label={HDR_TAG_LABEL[key]}
						value={summary.tv.episode_distribution[key]}
						tone={HDR_DIST_TONE[key]}
					/>
				</a>
			{/each}
		</div>
		<div class="stats status-row">
			{#each showStatuses as status (status)}
				<a class="tile-link" href={tvStatusHref(status)}>
					<StatCard
						label={SHOW_STATUS_META[status].label}
						value={summary.tv.show_status_counts[status] ?? 0}
						tone={SHOW_STATUS_META[status].tone}
					/>
				</a>
			{/each}
		</div>
		<div class="stats uniformity-row">
			{#each uniformities as uniformity (uniformity)}
				<a class="tile-link" href={tvUniformityHref(uniformity)}>
					<StatCard
						label={UNIFORMITY_META[uniformity].label}
						value={summary.tv.uniformity_counts[uniformity] ?? 0}
						tone="info"
					/>
				</a>
			{/each}
		</div>
	</div>

	<div class="section">
		<h2>Preference targets</h2>
		<div class="preference-grid">
			<PreferenceTargetsEditor
				preferences={summary.profile_preferences.radarr}
				label="Movies"
				emptyMessage="No Radarr profiles with HDR targets — check your Radarr custom formats."
				onSave={saveMoviePreferences}
			/>
			<PreferenceTargetsEditor
				preferences={summary.profile_preferences.sonarr}
				label="Television"
				emptyMessage="No Sonarr profiles with HDR targets — check your Sonarr custom formats."
				onSave={saveTvPreferences}
			/>
		</div>
	</div>

	<div class="section">
		<h2>Dolby Vision analysis</h2>
		<div class="dovi-grid">
			<div class="dovi-card">
				<div class="dovi-head">
					<strong>Movies</strong>
					<span class="coverage"
						>{summary.movies.dovi_analysis.analyzed} / {summary.movies.dovi_analysis.total_dovi} analyzed</span
					>
				</div>
				<button class="analyze-btn" onclick={analyzeAllMovies} disabled={analyzingMovies}>
					{analyzingMovies ? 'Analyzing…' : 'Analyze all movies'}
				</button>
			</div>
			<div class="dovi-card">
				<div class="dovi-head">
					<strong>Shows</strong>
					<span class="coverage"
						>{summary.tv.dovi_analysis.analyzed} / {summary.tv.dovi_analysis.total_dovi} analyzed</span
					>
				</div>
				<button class="analyze-btn" onclick={analyzeAllShows} disabled={analyzingTv}>
					{analyzingTv ? 'Analyzing…' : 'Analyze all shows'}
				</button>
			</div>
		</div>
	</div>

	<div class="section">
		<h2>Insights</h2>
		<div class="insights-grid">
			<div class="insight-card">
				<h3>Worst offenders</h3>
				{#if summary.worst_offenders.length}
					<div class="offenders">
						{#each summary.worst_offenders as show (show.series_id)}
							<a class="offender" href={`/hdr/tv/${show.series_id}`}>
								<div class="offender-head">
									<strong>{show.title}</strong>
									<span class="muted">{show.year ?? ''}</span>
								</div>
								<SegmentedBar
									segments={[
										{ key: 'met', count: show.meeting_fraction.met, tone: 'good' },
										{ key: 'below', count: show.below_count, tone: 'warn' },
										{ key: 'unknown', count: show.unknown_count, tone: 'low' }
									]}
									total={show.episodes_total}
									fractionText={`${show.below_count} below · ${show.unknown_count} unknown`}
								/>
							</a>
						{/each}
					</div>
				{:else}
					<p class="muted">No shows currently below their HDR target.</p>
				{/if}
			</div>

			<div class="insight-card">
				<h3>DoVi without fallback</h3>
				<div class="insight-row">
					<a class="insight-stat" href="/hdr/movies?dovi_no_fallback=true">
						<strong>{summary.insights.dovi_no_fallback.movies}</strong>
						<span>movies</span>
					</a>
					<a class="insight-stat" href="/hdr/tv?dovi_no_fallback=true">
						<strong>{summary.insights.dovi_no_fallback.episodes}</strong>
						<span>episodes ({summary.insights.dovi_no_fallback.shows_affected} shows)</span>
					</a>
				</div>
			</div>

			{#if summary.insights.no_hdr_target.movies > 0 || summary.insights.no_hdr_target.shows > 0}
				<div class="insight-card warn">
					<h3>No HDR target configured</h3>
					<div class="insight-row">
						<a class="insight-stat" href="/hdr/movies?preference_status=no_hdr_target">
							<strong>{summary.insights.no_hdr_target.movies}</strong>
							<span>movies</span>
						</a>
						<a class="insight-stat" href="/hdr/tv?preference_status=no_hdr_target">
							<strong>{summary.insights.no_hdr_target.shows}</strong>
							<span>shows</span>
						</a>
					</div>
				</div>
			{/if}

			<div class="insight-card">
				<h3>4K but SDR</h3>
				<div class="insight-row">
					<a class="insight-stat" href="/hdr/movies?hdr_tags=sdr">
						<strong>{summary.insights.four_k_sdr.movies}</strong>
						<span>movies</span>
					</a>
					<a class="insight-stat" href="/hdr/tv">
						<strong>{summary.insights.four_k_sdr.episodes}</strong>
						<span>episodes ({summary.insights.four_k_sdr.shows_affected} shows)</span>
					</a>
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	h2 {
		font-size: 16px;
		margin: 0 0 12px;
	}
	h3 {
		font-size: 13px;
		margin: 0 0 10px;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.section {
		margin-bottom: 22px;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
	}
	.action {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		min-width: 220px;
		padding: 14px 16px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		color: inherit;
		cursor: pointer;
		text-align: left;
	}
	.action span {
		color: var(--muted);
	}
	.action b {
		font-size: 14px;
	}
	.action:hover {
		border-color: var(--gold);
		background: color-mix(in srgb, var(--gold) 8%, var(--panel));
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(7, minmax(0, 1fr));
		gap: 10px;
	}
	.status-row,
	.uniformity-row {
		margin-top: 10px;
		grid-template-columns: repeat(6, minmax(0, 1fr));
	}
	.tile-link {
		display: block;
		color: inherit;
		text-decoration: none;
	}
	.preference-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 14px;
	}
	.dovi-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 14px;
	}
	.dovi-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.dovi-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	.coverage {
		font-family: var(--font-mono);
		color: var(--muted);
		font-size: 13px;
	}
	.analyze-btn {
		padding: 10px 16px;
		border: 1px solid var(--gold);
		border-radius: var(--radius);
		background: color-mix(in srgb, var(--gold) 14%, transparent);
		color: var(--gold);
		font-weight: 600;
		cursor: pointer;
		align-self: flex-start;
	}
	.analyze-btn:disabled {
		opacity: 0.6;
		cursor: progress;
	}
	.insights-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 14px;
	}
	.insight-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		padding: 16px;
	}
	.insight-card.warn {
		border-color: color-mix(in srgb, var(--warn) 40%, var(--line));
		background: color-mix(in srgb, var(--warn) 6%, var(--panel));
	}
	.offenders {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.offender {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 8px 10px;
		border-radius: var(--radius-sm);
		text-decoration: none;
		color: inherit;
	}
	.offender:hover {
		background: var(--panel2);
	}
	.offender-head {
		display: flex;
		justify-content: space-between;
		gap: 8px;
		font-size: 13px;
	}
	.insight-row {
		display: flex;
		gap: 12px;
	}
	.insight-stat {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--ink2);
		color: inherit;
		text-decoration: none;
	}
	.insight-stat:hover {
		background: var(--panel2);
	}
	.insight-stat strong {
		font-size: 20px;
		font-family: var(--font-mono);
	}
	.insight-stat span {
		font-size: 11px;
		color: var(--muted);
	}
	.muted {
		color: var(--muted);
	}
	.error {
		padding: 18px 20px;
		border-radius: var(--radius);
		border: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line));
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		color: var(--muted);
	}
	@media (max-width: 1100px) {
		.stats {
			grid-template-columns: repeat(4, minmax(0, 1fr));
		}
		.status-row,
		.uniformity-row {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
		.preference-grid,
		.dovi-grid,
		.insights-grid {
			grid-template-columns: 1fr;
		}
	}
	@media (max-width: 640px) {
		.stats,
		.status-row,
		.uniformity-row {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
