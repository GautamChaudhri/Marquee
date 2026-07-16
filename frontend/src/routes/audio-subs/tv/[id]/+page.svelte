<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import SegmentedBar from '$lib/components/SegmentedBar.svelte';
	import UniformityChip from '$lib/components/UniformityChip.svelte';
	import CoverageChips from '$lib/components/subtitles/CoverageChips.svelte';
	import EpisodeHeatmap from '$lib/components/subtitles/EpisodeHeatmap.svelte';
	import GenerateSubtitlesModal from '$lib/components/subtitles/GenerateSubtitlesModal.svelte';
	import { getAudioSubsTvDetail, putSeriesPreferences, deepScanSeries } from '$lib/api/subtitles';
	import { toast } from '$lib/toast';

	let { data } = $props();

	let detail = $state(untrack(() => data.detail));
	let error = $state(untrack(() => data.error));

	// Overrides state
	let audioOverride = $state('');
	let subtitleOverride = $state('');
	let savingPreferences = $state(false);

	$effect(() => {
		if (detail) {
			audioOverride = (detail.preferred_audio_languages || []).join(', ');
			subtitleOverride = (detail.preferred_subtitle_languages || []).join(', ');
		}
	});

	async function refreshDetail() {
		if (!detail) return;
		try {
			detail = await getAudioSubsTvDetail(fetch, detail.series.id);
		} catch (e: any) {
			error = e.message || 'Failed to refresh TV show details';
		}
	}

	async function saveSeriesOverrides() {
		if (!detail) return;
		savingPreferences = true;
		try {
			const parseLangs = (val: string) =>
				val
					.split(',')
					.map((s) => s.trim())
					.filter(Boolean);

			const prefs = {
				preferred_audio_languages: parseLangs(audioOverride),
				preferred_subtitle_languages: parseLangs(subtitleOverride)
			};

			await putSeriesPreferences(fetch, detail.series.id, prefs);
			toast('Series-level overrides saved', 'good');
			await refreshDetail();
		} catch (e: any) {
			toast(e.message || 'Failed to save series overrides', 'bad');
		} finally {
			savingPreferences = false;
		}
	}

	// Generate modal state
	let generateModalOpen = $state(false);
	let generateScope = $state<'series' | 'season' | 'episode'>('series');
	let generateSeasonNumber = $state<number | null>(null);
	let generateEpisodeId = $state<number | null>(null);
	let generateMediaFileId = $state<number | null>(null);
	let generateAudioLanguages = $state<string[]>([]);
	let generateAudioStreams = $state<any[]>([]);

	// Job tracking state
	let jobBusy = $state(false);
	let initiatedJobIds = $state<string[]>([]);

	function openGenerateModal(scope: 'series' | 'season' | 'episode', opts: any = {}) {
		generateScope = scope;
		generateSeasonNumber = opts.seasonNumber ?? null;
		generateEpisodeId = opts.episodeId ?? null;
		generateMediaFileId = opts.mediaFileId ?? null;
		generateAudioLanguages = opts.audioLanguages ?? [];
		generateAudioStreams = opts.audioStreams ?? [];
		generateModalOpen = true;
	}

	function handleGenerateSuccess(jobId: string) {
		generateModalOpen = false;
		jobBusy = true;
		initiatedJobIds = [...new Set([...initiatedJobIds, jobId])];
	}

	async function runDeepScan(seasonNumber?: number | null) {
		if (!detail) return;
		jobBusy = true;
		try {
			const res = await deepScanSeries(fetch, detail.series.id, seasonNumber);
			initiatedJobIds = [...new Set([...initiatedJobIds, res.job_id])];
			toast('Series deep scan enqueued', 'good');
		} catch (e: any) {
			jobBusy = false;
			toast(e.message || 'Failed to trigger scan', 'bad');
		}
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		jobBusy = false;
		toast(
			`${snapshot.label} ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		await refreshDetail();
	}

	// Filters for Episode Table
	let selectedSeason = $state<string>('all');
	let selectedStatus = $state<string>('all');

	const filteredSeasons = $derived.by(() => {
		if (!detail) return [];
		return detail.seasons
			.filter((s) => {
				if (selectedSeason !== 'all' && s.season_number.toString() !== selectedSeason) {
					return false;
				}
				return true;
			})
			.map((s) => {
				return {
					...s,
					episodes: s.episodes.filter((e) => {
						if (selectedStatus !== 'all' && e.status !== selectedStatus) {
							return false;
						}
						return true;
					})
				};
			});
	});
</script>

<svelte:head>
	<title>{detail?.series.title || 'Show Details'} — Audio & Subs</title>
</svelte:head>

<div class="page-container">
	<div class="top-nav">
		<a href="/audio-subs/tv" class="back-link">← Back to Television Library</a>
	</div>

	{#if error}
		<div class="error-banner">⚠ {error}</div>
	{/if}

	{#if detail}
		{@const r = detail.rollup}
		{@const segments = [
			{ key: 'OK', count: r.status_counts?.ok || 0, tone: 'good' as const },
			{ key: 'Audio Gap', count: r.status_counts?.audio_gap || 0, tone: 'warn' as const },
			{ key: 'Subtitle Gap', count: r.status_counts?.subtitle_gap || 0, tone: 'info' as const },
			{ key: 'Both Gap', count: r.status_counts?.both_gap || 0, tone: 'bad' as const },
			{ key: 'Unknown', count: r.status_counts?.unknown || 0, tone: 'muted' as const }
		]}
		<!-- Header Section -->
		<div class="show-header mq-rise">
			<div class="header-main">
				<div>
					<h2>{detail.series.title} <span class="year">({detail.series.year})</span></h2>
					<div class="rollup-stats">
						<CoverageChips status={detail.rollup.status} />
						<UniformityChip uniformity={detail.rollup.uniformity} />
						<span class="fraction"
							>{detail.rollup.episodes_counted}/{detail.rollup.episodes_total} Covered</span
						>
					</div>
					<div class="missing-chips">
						{#each detail.rollup.missing_languages as lang}
							<span class="missing-chip">− {lang}</span>
						{/each}
					</div>
				</div>

				<div class="header-actions">
					<button class="btn secondary" onclick={() => runDeepScan(null)} disabled={jobBusy}>
						🔍 Deep Scan All
					</button>
					<button
						class="btn primary"
						onclick={() =>
							detail &&
							openGenerateModal('series', { audioLanguages: detail.rollup.missing_languages })}
						disabled={jobBusy}
					>
						⚡ Generate All Subtitles
					</button>
				</div>
			</div>

			<!-- Rollup Segmented Bar -->
			<div class="bar-wrap">
				<SegmentedBar {segments} total={r.episodes_total} />
			</div>

			<!-- Series Override Editor -->
			<div class="overrides-panel">
				<h5>Show Preferred Languages Override</h5>
				<div class="fields-row">
					<label class="override-field">
						<span>Preferred Audio</span>
						<input type="text" bind:value={audioOverride} placeholder="en, es" autocomplete="off" />
					</label>
					<label class="override-field">
						<span>Preferred Subtitles</span>
						<input
							type="text"
							bind:value={subtitleOverride}
							placeholder="en, fr"
							autocomplete="off"
						/>
					</label>
					<button
						class="btn-save-override"
						onclick={saveSeriesOverrides}
						disabled={savingPreferences}
					>
						{savingPreferences ? 'Saving...' : 'Save Overrides'}
					</button>
				</div>
			</div>
		</div>

		<FeatureActivityPanel
			scopeKey={`feature:audio-subtitles:series:${detail.series.id}`}
			query={{ feature_area: 'audio_subtitles' }}
			jobIds={initiatedJobIds}
			heading="Series audio and subtitle activity"
			onSettled={handleJobSettled}
		/>

		<!-- Heatmap Centerpiece -->
		<div class="section-title">
			<h3>Season × Episode Heatmap</h3>
		</div>
		<EpisodeHeatmap seasons={detail.seasons} />

		<!-- Episodes details and rows list -->
		<div class="table-section-header">
			<h3>Episodes Details</h3>

			<div class="filters-row">
				<label class="tbl-filter">
					<span>Season</span>
					<select bind:value={selectedSeason}>
						<option value="all">All Seasons</option>
						{#each detail.seasons as s}
							<option value={s.season_number.toString()}>
								Season {s.season_number === 0 ? 'Specials' : s.season_number}
							</option>
						{/each}
					</select>
				</label>

				<label class="tbl-filter">
					<span>Status</span>
					<select bind:value={selectedStatus}>
						<option value="all">All Statuses</option>
						<option value="ok">OK</option>
						<option value="audio_gap">Audio Gap</option>
						<option value="subtitle_gap">Subtitle Gap</option>
						<option value="both_gap">Both Gap</option>
						<option value="unknown">Unknown</option>
					</select>
				</label>
			</div>
		</div>

		{#each filteredSeasons as season (season.season_number)}
			<div class="season-block mq-rise">
				<div class="season-block-header">
					<h4>
						{season.season_number === 0 ? 'Specials' : `Season ${season.season_number}`}
					</h4>
					<div class="season-block-metrics">
						<span
							>Dubbing: {Math.round(
								(season.rollup.dub_coverage.ok / (season.rollup.dub_coverage.of || 1)) * 100
							)}%</span
						>
						<CoverageChips status={season.rollup.status} />
						<div class="actions-group">
							<button
								class="btn secondary btn-xs"
								onclick={() => runDeepScan(season.season_number)}
								disabled={jobBusy}
							>
								🔍 Scan Season
							</button>
							<button
								class="btn primary btn-xs"
								onclick={() =>
									openGenerateModal('season', {
										seasonNumber: season.season_number,
										audioLanguages: season.rollup.missing_languages
									})}
								disabled={jobBusy}
							>
								⚡ Generate Subtitles
							</button>
						</div>
					</div>
				</div>

				<div class="table-wrap">
					<table class="episode-table">
						<thead>
							<tr>
								<th>Code</th>
								<th>Title</th>
								<th>Audio Track Languages</th>
								<th>Subtitle Languages</th>
								<th>Tier</th>
								<th>Status</th>
								<th class="actions-col"></th>
							</tr>
						</thead>
						<tbody>
							{#each season.episodes as ep (ep.episode_id)}
								<tr id={`episode-${season.season_number}-${ep.episode_id}`}>
									<td class="code">{ep.code.toUpperCase()}</td>
									<td class="title">{ep.title}</td>
									<td>
										<div class="languages-list">
											{#each ep.audio_languages as lang}
												<span class="lang-tag audio">{lang.toUpperCase()}</span>
											{:else}
												<span class="empty-lbl">None</span>
											{/each}
										</div>
									</td>
									<td>
										<div class="languages-list">
											{#each ep.subtitle_languages as lang}
												<span class="lang-tag sub">{lang.toUpperCase()}</span>
											{:else}
												<span class="empty-lbl">None</span>
											{/each}
											{#each ep.forced_languages as lang}
												<span class="badge forced">{lang.toUpperCase()} Forced</span>
											{/each}
											{#each ep.sdh_languages as lang}
												<span class="badge sdh">{lang.toUpperCase()} SDH</span>
											{/each}
										</div>
									</td>
									<td>
										<span class={`tier-badge ${ep.tier}`}>
											{ep.tier === 'probed' ? 'Tier 2 (Probed)' : 'Tier 1 (Synced)'}
										</span>
									</td>
									<td>
										<CoverageChips status={ep.status} />
									</td>
									<td class="actions-col">
										<button
											class="btn-generate-action"
											onclick={() =>
												openGenerateModal('episode', {
													seasonNumber: season.season_number,
													episodeId: ep.episode_id,
													mediaFileId: ep.media_file_id,
													audioLanguages: ep.audio_languages
												})}
											disabled={!ep.media_file_id || jobBusy}
											title="Generate Subtitles"
										>
											⚡
										</button>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		{/each}
		{#if generateModalOpen}
			<GenerateSubtitlesModal
				seriesId={detail.series.id}
				scope={generateScope}
				seasonNumber={generateSeasonNumber}
				episodeId={generateEpisodeId}
				mediaFileId={generateMediaFileId}
				audioLanguages={generateAudioLanguages}
				generator={data.summary?.generator[0] || null}
				onClose={() => (generateModalOpen = false)}
				onSuccess={handleGenerateSuccess}
			/>
		{/if}
	{/if}
</div>

<style>
	.page-container {
		display: flex;
		flex-direction: column;
		gap: 20px;
		padding-bottom: 32px;
	}
	.top-nav {
		margin-bottom: 4px;
	}
	.back-link {
		color: var(--muted);
		font-size: 13px;
		text-decoration: none;
		display: inline-flex;
		align-items: center;
		transition: color 0.15s;
	}
	.back-link:hover {
		color: var(--gold);
	}
	.show-header {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.header-main {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 16px;
		flex-wrap: wrap;
	}
	.header-main h2 {
		margin: 0;
		font-size: 20px;
		font-weight: 700;
	}
	.header-main .year {
		color: var(--muted);
		font-weight: 550;
	}
	.rollup-stats {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-top: 8px;
	}
	.rollup-stats .fraction {
		font-size: 12.5px;
		color: var(--muted);
		font-weight: 550;
	}
	.missing-chips {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		margin-top: 8px;
	}
	.missing-chip {
		font-size: 11px;
		color: var(--warn);
		background: color-mix(in srgb, var(--warn) 10%, transparent);
		border: 1px solid color-mix(in srgb, var(--warn) 20%, transparent);
		padding: 2px 6px;
		border-radius: 4px;
	}
	.header-actions {
		display: flex;
		gap: 8px;
	}
	.bar-wrap {
		width: 100%;
		border-bottom: 1px solid var(--line);
		padding-bottom: 16px;
	}
	.overrides-panel {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.overrides-panel h5 {
		margin: 0;
		font-size: 12px;
		text-transform: uppercase;
		font-weight: 700;
		color: var(--faint2);
	}
	.fields-row {
		display: flex;
		gap: 16px;
		align-items: end;
		flex-wrap: wrap;
	}
	.override-field {
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 180px;
	}
	.override-field span {
		font-size: 11px;
		color: var(--muted);
	}
	.override-field input {
		padding: 8px 10px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 13px;
		outline: none;
	}
	.override-field input:focus {
		border-color: var(--gold);
	}
	.btn-save-override {
		padding: 8px 16px;
		background: var(--gold);
		color: var(--ink);
		border: none;
		border-radius: var(--radius-sm);
		font-size: 13px;
		font-weight: 650;
		cursor: pointer;
	}
	.btn-save-override:disabled {
		opacity: 0.6;
	}
	.progress-banner {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.section-title h3 {
		margin: 0;
		font-size: 14px;
		text-transform: uppercase;
		font-weight: 750;
		color: var(--faint2);
		letter-spacing: 0.05em;
	}
	.table-section-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 16px;
		flex-wrap: wrap;
		border-bottom: 1px solid var(--line);
		padding-bottom: 10px;
	}
	.table-section-header h3 {
		margin: 0;
		font-size: 14px;
		text-transform: uppercase;
		font-weight: 750;
		color: var(--faint2);
		letter-spacing: 0.05em;
	}
	.filters-row {
		display: flex;
		gap: 12px;
	}
	.tbl-filter {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.tbl-filter span {
		font-size: 12px;
		color: var(--muted);
	}
	.tbl-filter select {
		padding: 6px 10px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 12.5px;
		outline: none;
	}
	.season-block {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		overflow: hidden;
		display: flex;
		flex-direction: column;
	}
	.season-block-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 12px 16px;
		background: var(--ink2);
		border-bottom: 1px solid var(--line);
		flex-wrap: wrap;
		gap: 12px;
	}
	.season-block-header h4 {
		margin: 0;
		font-size: 14px;
		font-weight: 650;
	}
	.season-block-metrics {
		display: flex;
		align-items: center;
		gap: 12px;
		font-size: 12px;
		color: var(--muted);
	}
	.actions-group {
		display: flex;
		gap: 6px;
	}
	.table-wrap {
		overflow-x: auto;
	}
	.episode-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
	}
	th {
		padding: 10px 16px;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
		border-bottom: 1px solid var(--line);
		background: var(--ink3);
	}
	td {
		padding: 12px 16px;
		font-size: 12.5px;
		border-bottom: 1px solid var(--line2);
		color: var(--text);
		vertical-align: middle;
	}
	tr:last-child td {
		border-bottom: none;
	}
	.code {
		font-family: var(--font-mono);
		font-weight: 600;
		color: var(--muted);
	}
	.title {
		font-weight: 550;
	}
	.languages-list {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		align-items: center;
	}
	.lang-tag {
		font-size: 11px;
		font-weight: 600;
		padding: 1px 4px;
		border-radius: 3px;
	}
	.lang-tag.audio {
		background: var(--ink2);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.lang-tag.sub {
		background: var(--line);
		color: var(--text);
	}
	.badge {
		font-size: 10px;
		font-weight: 700;
		padding: 1px 4px;
		border-radius: 3px;
		letter-spacing: 0.02em;
	}
	.badge.forced {
		background: color-mix(in srgb, var(--info) 15%, transparent);
		color: var(--info);
	}
	.badge.sdh {
		background: color-mix(in srgb, var(--gold) 15%, transparent);
		color: var(--gold);
	}
	.tier-badge {
		font-size: 10.5px;
		color: var(--muted);
	}
	.tier-badge.probed {
		color: var(--gold);
		font-weight: 550;
	}
	.empty-lbl {
		color: var(--faint2);
		font-style: italic;
	}
	.actions-col {
		text-align: right;
		width: 50px;
	}
	.btn-generate-action {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--gold);
		cursor: pointer;
		width: 26px;
		height: 26px;
		border-radius: 4px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		transition: background-color 0.15s;
		outline: none;
	}
	.btn-generate-action:hover:not(:disabled) {
		background: var(--line);
		border-color: var(--gold);
	}
	.btn-generate-action:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn {
		font-weight: 600;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--ink);
	}
	.btn.secondary {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.btn.primary:disabled,
	.btn.secondary:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.btn-xs {
		font-size: 11px;
		padding: 4px 8px;
	}
	.btn-sm {
		font-size: 12.5px;
		padding: 6px 12px;
	}
	.error-banner {
		padding: 14px;
		background: rgba(239, 83, 80, 0.1);
		border: 1px solid var(--bad);
		color: var(--bad);
		border-radius: var(--radius-sm);
		font-size: 13.5px;
	}
</style>
