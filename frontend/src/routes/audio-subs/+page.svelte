<script lang="ts">
	import { untrack } from 'svelte';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import PreferredLanguagesEditor from '$lib/components/subtitles/PreferredLanguagesEditor.svelte';
	import SubgenCard from '$lib/components/subtitles/SubgenCard.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	import { getAudioSubsSummary, deepScan } from '$lib/api/subtitles';
	import { toast } from '$lib/toast';
	import type { RuntimeSettings } from '$lib/api/types';

	let { data } = $props();

	let summary = $state(untrack(() => data.summary));
	let runtimeSettings = $state<RuntimeSettings | null>(untrack(() => data.settings));
	let error = $state(untrack(() => data.error));

	function acceptSettings(settings: RuntimeSettings) {
		runtimeSettings = settings;
	}

	let scanning = $state(false);
	let initiatedJobIds = $state<string[]>([]);

	async function refreshSummary() {
		try {
			summary = await getAudioSubsSummary(fetch);
		} catch (e: any) {
			error = e.message || 'Failed to refresh dashboard data';
		}
	}

	async function triggerScan(scope: 'movies' | 'tv' | 'all') {
		scanning = true;
		try {
			const res = await deepScan(fetch, scope);
			initiatedJobIds = [...new Set([...initiatedJobIds, res.job_id])];
			toast(`Scan enqueued: ${scope}`, 'good');
		} catch (e: any) {
			scanning = false;
			toast(e.message || 'Failed to enqueue deep scan', 'bad');
		}
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		scanning = false;
		toast(
			`Subtitle scan ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		await refreshSummary();
	}
</script>

<svelte:head>
	<title>Audio & Subtitles — Marquee</title>
</svelte:head>

<div class="page-container">
	<SectionHeader
		title="Audio & Subtitles Dashboard"
		subtitle="Overview of library localization coverage, languages preferences, and automatic speech recognition (ASR) generators."
	/>
	<FeatureActivityPanel
		scopeKey="feature:audio-subtitles:overview"
		query={{ feature_area: 'audio_subtitles' }}
		jobIds={initiatedJobIds}
		heading="Audio and subtitle activity"
		onSettled={handleJobSettled}
	/>

	{#if error}
		<div class="error-banner">
			<p>⚠ {error}</p>
		</div>
	{/if}

	{#if summary}
		<!-- Quick Navigation Library Jump Buttons -->
		<div class="jump-row">
			<a href="/audio-subs/movies" class="jump-btn"> 🎥 Movie Library </a>
			<a href="/audio-subs/tv" class="jump-btn"> 📺 Television Library </a>
		</div>

		<!-- Metrics Section -->
		<div class="metrics-section">
			<div class="category-header">
				<h4>Movies Localization Metrics</h4>
				<span class="count">Total: {summary.movies.total}</span>
			</div>
			<div class="metrics-grid">
				<a href="/audio-subs/movies?status=audio_ok" class="metric-card ok">
					<span class="label">Audio OK</span>
					<span class="value">{summary.movies.audio_ok}</span>
				</a>
				<a href="/audio-subs/movies?status=audio_gap" class="metric-card gap">
					<span class="label">Audio Gaps</span>
					<span class="value">{summary.movies.audio_gap}</span>
				</a>
				<a href="/audio-subs/movies?status=subtitle_ok" class="metric-card ok">
					<span class="label">Subtitle OK</span>
					<span class="value">{summary.movies.subtitle_ok}</span>
				</a>
				<a href="/audio-subs/movies?status=subtitle_gap" class="metric-card gap">
					<span class="label">Subtitle Gaps</span>
					<span class="value">{summary.movies.subtitle_gap}</span>
				</a>
				<a href="/audio-subs/movies?status=both_gap" class="metric-card both-gap">
					<span class="label">Both Missing</span>
					<span class="value">{summary.movies.both_gap}</span>
				</a>
				<a href="/audio-subs/movies?status=unknown" class="metric-card unknown">
					<span class="label">Unscanned</span>
					<span class="value">{summary.movies.unknown}</span>
				</a>
			</div>
		</div>

		<div class="metrics-section">
			<div class="category-header">
				<h4>Television Episode Localization Metrics</h4>
			</div>
			<div class="metrics-grid">
				<a href="/audio-subs/tv?status=ok" class="metric-card ok">
					<span class="label">Audio OK</span>
					<span class="value">{summary.tv.audio_ok}</span>
				</a>
				<a href="/audio-subs/tv?status=audio_gap" class="metric-card gap">
					<span class="label">Audio Gaps</span>
					<span class="value">{summary.tv.audio_gap}</span>
				</a>
				<a href="/audio-subs/tv?status=subtitle_ok" class="metric-card ok">
					<span class="label">Subtitle OK</span>
					<span class="value">{summary.tv.subtitle_ok}</span>
				</a>
				<a href="/audio-subs/tv?status=subtitle_gap" class="metric-card gap">
					<span class="label">Subtitle Gaps</span>
					<span class="value">{summary.tv.subtitle_gap}</span>
				</a>
				<a href="/audio-subs/tv?status=both_gap" class="metric-card both-gap">
					<span class="label">Both Missing</span>
					<span class="value">{summary.tv.both_gap}</span>
				</a>
				<a href="/audio-subs/tv?status=unknown" class="metric-card unknown">
					<span class="label">Unscanned</span>
					<span class="value">{summary.tv.unknown}</span>
				</a>
			</div>
		</div>

		<!-- Mid Section: Preferences & Generator Configuration -->
		<div class="dashboard-grid">
			<div class="left-col">
				<PreferredLanguagesEditor
					preferred={summary.preferred}
					configurationVersion={runtimeSettings?.configuration_version ?? 0}
					onSave={refreshSummary}
				/>
				<SubgenCard
					generator={summary.generator[0] || null}
					settings={runtimeSettings?.integrations?.subgen || {}}
					configurationVersion={runtimeSettings?.configuration_version ?? 0}
					onSettingsReload={acceptSettings}
					onRefresh={refreshSummary}
				/>
			</div>

			<div class="right-col">
				<!-- Insights Card -->
				<div class="insights-card mq-rise">
					<h4>Localization Insights</h4>
					<div class="insight-row">
						<span class="label">Unlabeled Tracks</span>
						<span class="value"
							>{summary.movies.unknown_language_tracks} tracks with no language tag</span
						>
					</div>
					<div class="insight-row">
						<span class="label">AI Generated Subtitles</span>
						<span class="value">{summary.movies.generated_tracks} active Whisper subtitles</span>
					</div>
					<div class="insight-row">
						<span class="label">Forced Audio Tracks</span>
						<span class="value">{summary.movies.forced_coverage} movies covered</span>
					</div>
					<div class="insight-row">
						<span class="label">SDH Coverage</span>
						<span class="value">{summary.movies.sdh_coverage} movies covered</span>
					</div>
				</div>

				<!-- Dub-Coverage Spotlight -->
				{#if summary.tv.dub_coverage_highlights && summary.tv.dub_coverage_highlights.length > 0}
					<div class="spotlight-card mq-rise">
						<h4>Dubbing Coverage Spotlight</h4>
						<p class="subtitle">Shows missing preferred audio languages</p>
						<div class="spotlight-list">
							{#each summary.tv.dub_coverage_highlights as show}
								<a href={`/audio-subs/tv/${show.series_id}`} class="spotlight-item">
									<div class="info">
										<span class="title">{show.title}</span>
										<span class="langs">Missing: {show.missing_audio_languages.join(', ')}</span>
									</div>
									<span class="pct"
										>{Math.round((show.coverage.ok / (show.coverage.of || 1)) * 100)}%</span
									>
								</a>
							{/each}
						</div>
					</div>
				{/if}

				<!-- Policy Strip -->
				<div class="policy-card mq-rise">
					<div class="header">
						<div>
							<h4>Cleanup Policies</h4>
							<p>{summary.policies.active_count} active policies configured</p>
						</div>
						<a href="/audio-subs/policies" class="btn-manage">Manage Policies</a>
					</div>
				</div>

				<!-- Deep Scan Panel -->
				<div class="scan-card mq-rise">
					<h4>Deep Subtitle Scan</h4>
					<p class="desc">
						Forces a raw media container inspect to sync audio channel layouts and subtitles codec
						details.
					</p>

					<div class="scan-meta">
						<div class="meta-item">
							<span class="lbl">Pending files</span>
							<span class="val">{summary.deep_scan.pending_file_count}</span>
						</div>
						{#if summary.deep_scan.last_run_at}
							<div class="meta-item">
								<span class="lbl">Last scan</span>
								<span class="val">{new Date(summary.deep_scan.last_run_at).toLocaleString()}</span>
							</div>
						{/if}
					</div>

					<div class="actions">
						<button class="btn secondary" onclick={() => triggerScan('movies')} disabled={scanning}>
							Scan Movies
						</button>
						<button class="btn secondary" onclick={() => triggerScan('tv')} disabled={scanning}>
							Scan TV
						</button>
						<button class="btn primary" onclick={() => triggerScan('all')} disabled={scanning}>
							Scan All
						</button>
					</div>
				</div>
			</div>
		</div>
	{:else}
		<div class="loading">Loading dashboard...</div>
	{/if}
</div>

<style>
	.page-container {
		display: flex;
		flex-direction: column;
		gap: 20px;
		padding-bottom: 32px;
	}
	.jump-row {
		display: flex;
		gap: 12px;
	}
	.jump-btn {
		flex: 1;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 18px;
		text-align: center;
		color: var(--text);
		text-decoration: none;
		font-weight: 650;
		font-size: 14px;
		transition:
			background-color 0.15s,
			border-color 0.15s,
			color 0.15s;
	}
	.jump-btn:hover {
		background: var(--line);
		border-color: var(--gold);
		color: var(--gold);
	}
	.metrics-section {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.category-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.category-header h4 {
		margin: 0;
		font-size: 13.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
	}
	.category-header .count {
		font-size: 12px;
		color: var(--muted);
	}
	.metrics-grid {
		display: grid;
		grid-template-columns: repeat(6, 1fr);
		gap: 12px;
	}
	@media (max-width: 900px) {
		.metrics-grid {
			grid-template-columns: repeat(3, 1fr);
		}
	}
	@media (max-width: 600px) {
		.metrics-grid {
			grid-template-columns: repeat(2, 1fr);
		}
	}
	.metric-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 12px;
		display: flex;
		flex-direction: column;
		gap: 4px;
		text-decoration: none;
		color: var(--text);
		transition:
			transform 0.1s,
			border-color 0.15s;
	}
	.metric-card:hover {
		transform: translateY(-2px);
		border-color: var(--border-color, var(--line));
	}
	.metric-card .label {
		font-size: 11px;
		color: var(--muted);
	}
	.metric-card .value {
		font-size: 22px;
		font-weight: 700;
	}
	.metric-card.ok {
		--border-color: var(--good);
		color: var(--good);
	}
	.metric-card.gap {
		--border-color: var(--warn);
		color: var(--warn);
	}
	.metric-card.both-gap {
		--border-color: var(--bad);
		color: var(--bad);
	}
	.metric-card.unknown {
		--border-color: var(--muted);
		color: var(--muted);
	}
	.dashboard-grid {
		display: grid;
		grid-template-columns: 1.2fr 1fr;
		gap: 20px;
		align-items: flex-start;
	}
	@media (max-width: 960px) {
		.dashboard-grid {
			grid-template-columns: 1fr;
		}
	}
	.left-col,
	.right-col {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.insights-card,
	.spotlight-card,
	.policy-card,
	.scan-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.insights-card h4,
	.spotlight-card h4,
	.scan-card h4,
	.policy-card h4 {
		margin: 0;
		font-size: 14px;
		font-weight: 650;
	}
	.insight-row {
		display: flex;
		justify-content: space-between;
		font-size: 13px;
		border-bottom: 1px solid var(--line2);
		padding-bottom: 8px;
	}
	.insight-row:last-child {
		border-bottom: none;
		padding-bottom: 0;
	}
	.insight-row .label {
		color: var(--muted);
	}
	.insight-row .value {
		font-weight: 550;
	}
	.spotlight-card .subtitle {
		margin: -8px 0 4px;
		font-size: 12px;
		color: var(--muted);
	}
	.spotlight-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.spotlight-item {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 8px 12px;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		text-decoration: none;
		color: var(--text);
		transition: border-color 0.15s;
	}
	.spotlight-item:hover {
		border-color: var(--gold);
	}
	.spotlight-item .info {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.spotlight-item .title {
		font-size: 13px;
		font-weight: 600;
	}
	.spotlight-item .langs {
		font-size: 11px;
		color: var(--warn);
	}
	.spotlight-item .pct {
		font-size: 14px;
		font-weight: 700;
		color: var(--muted);
	}
	.policy-card .header {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.policy-card p {
		margin: 4px 0 0;
		font-size: 12px;
		color: var(--muted);
	}
	.btn-manage {
		font-size: 12px;
		font-weight: 650;
		padding: 6px 12px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		text-decoration: none;
	}
	.btn-manage:hover {
		background: var(--line);
	}
	.scan-card .desc {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
		line-height: 1.4;
	}
	.scan-meta {
		display: flex;
		gap: 24px;
		background: var(--ink2);
		padding: 8px 12px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
	}
	.meta-item {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.meta-item .lbl {
		font-size: 10px;
		text-transform: uppercase;
		font-weight: 700;
		color: var(--faint2);
	}
	.meta-item .val {
		font-size: 13px;
		font-weight: 600;
	}
	.scan-card .actions {
		display: flex;
		gap: 8px;
		justify-content: flex-end;
	}
	.scan-card .btn {
		font-size: 12px;
		font-weight: 600;
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
	}
	.scan-card .btn.primary {
		background: var(--gold);
		color: var(--ink);
	}
	.scan-card .btn.secondary {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.loading {
		text-align: center;
		padding: 48px;
		color: var(--muted);
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
