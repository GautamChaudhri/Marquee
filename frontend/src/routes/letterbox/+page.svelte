<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { toast } from '$lib/toast';
	import { bytesH } from '$lib/display';
	import { analyzeAll, detectLetterboxTvLibrary, getLetterboxSummary } from '$lib/api/letterbox';
	import { listJobs, cancelJob, type JobSnapshot } from '$lib/api/jobs';
	import { trackJob } from '$lib/jobs';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import AspectRatioBar from '$lib/components/letterbox/AspectRatioBar.svelte';

	let { data } = $props();

	let summary = $state(data.summary);
	let error = $state(data.error);

	// Batch Detection State
	let movieDetecting = $state(false);
	let tvDetecting = $state(false);
	let tvExhaustive = $state(false);
	let activeTvJobId = $state<string | null>(null);
	let tvProgress = $state(0);
	let tvProgressDone = $state(0);
	let tvProgressTotal = $state(0);
	let tvJobStatus = $state<string | null>(null);
	let trackStop = $state<(() => void) | null>(null);

	// Poll summary data periodically (every 10s) to keep it fresh
	let pollInterval: ReturnType<typeof setInterval>;

	async function refreshSummary() {
		try {
			const res = await getLetterboxSummary(fetch);
			if (res) {
				summary = res;
				error = null;
			}
		} catch {
			// Fail silently during background poll unless we have no data
			if (!summary) {
				error = 'Could not fetch summary data.';
			}
		}
	}

	onMount(() => {
		pollInterval = setInterval(refreshSummary, 10000);

		// Check for active TV detection job on mount
		void (async () => {
			try {
				const { jobs } = await listJobs(fetch, {
					type: 'letterbox_detect_tv_batch',
					active: true,
					limit: 1
				});
				if (jobs.length > 0) {
					const activeTvJob = jobs[0].job_id;
					activeTvJobId = activeTvJob;
					rehydrateTvJob(activeTvJob);
				}
			} catch {
				// Ignore
			}
		})();

		return () => {
			clearInterval(pollInterval);
			trackStop?.();
		};
	});

	function rehydrateTvJob(jobId: string) {
		tvDetecting = true;
		tvJobStatus = 'running';

		trackStop?.();
		trackStop = trackJob<JobSnapshot>(
			fetch,
			jobId,
			{
				onProgress: (p) => {
					tvJobStatus = p.status;
					const progress = p.detail || {};
					tvProgressDone = Number(progress.children_completed || progress.done || 0);
					tvProgressTotal = Number(progress.children_total || progress.total || 0);
					tvProgress = tvProgressTotal > 0 ? (tvProgressDone / tvProgressTotal) * 100 : 0;
				},
				onDone: (job) => {
					tvDetecting = false;
					activeTvJobId = null;
					tvJobStatus = job.status;
					toast('TV Letterbox detection completed successfully!', 'good');
					void refreshSummary();
				},
				onError: (msg) => {
					toast(`TV detection job error: ${msg}`, 'bad');
					tvDetecting = false;
					activeTvJobId = null;
				}
			},
			{
				eventsUrl: `/api/jobs/${jobId}/events`
			}
		);
	}

	async function startMovieDetect() {
		if (movieDetecting) return;
		movieDetecting = true;
		try {
			const ref = await analyzeAll(fetch);
			toast('Started movie letterbox scan...', 'good');
			if (browser) {
				localStorage.setItem('letterbox:batch', ref.job_id);
			}
			void goto('/letterbox/movies');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to start movie scan', 'bad');
		} finally {
			movieDetecting = false;
		}
	}

	async function startTvDetect() {
		if (tvDetecting) return;
		tvDetecting = true;
		tvProgress = 0;
		tvProgressDone = 0;
		tvProgressTotal = 0;
		try {
			const ref = await detectLetterboxTvLibrary(fetch, { exhaustive: tvExhaustive });
			activeTvJobId = ref.job_id;
			toast(`Started TV letterbox scan (${tvExhaustive ? 'exhaustive' : 'triage'})...`, 'good');
			rehydrateTvJob(ref.job_id);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to start TV scan', 'bad');
			tvDetecting = false;
		}
	}

	async function stopTvDetect() {
		if (!activeTvJobId) return;
		try {
			await cancelJob(fetch, activeTvJobId);
			toast('TV letterbox scan cancellation requested', 'info');
			trackStop?.();
			tvDetecting = false;
			activeTvJobId = null;
			void refreshSummary();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to cancel TV scan', 'bad');
		}
	}
</script>

<svelte:head>
	<title>Letterbox Dashboard | Marquee</title>
</svelte:head>

<section class="page letterbox-landing">
	<SectionHeader
		title="Letterbox Management"
		subtitle="Detect, tag, and reencode black bar borders to recover screen real estate."
	/>

	{#if error}
		<div class="error-banner">
			<span class="error-msg">{error}</span>
			<button class="btn btn-outline btn-sm" onclick={refreshSummary}>Retry</button>
		</div>
	{/if}

	{#if summary}
		<!-- Active TV Job Progress Overlay/Banner -->
		{#if tvDetecting}
			<div class="progress-card glass-panel animate-fade-in">
				<div class="progress-header">
					<div class="progress-title-group">
						<span class="spinner"></span>
						<div class="progress-details">
							<h3>TV Letterbox Detection Active</h3>
							<p class="progress-subtitle">
								Status: <strong class="capitalize">{tvJobStatus || 'running'}</strong> · {tvProgressDone}
								/ {tvProgressTotal} shows processed
							</p>
						</div>
					</div>
					<button class="btn btn-bad btn-sm" onclick={stopTvDetect}>Cancel Run</button>
				</div>
				<div class="progress-bar-container">
					<div class="progress-bar-fill" style={`width: ${tvProgress}%`}></div>
				</div>
			</div>
		{/if}

		<!-- workflow funnels C2 -->
		<div class="funnel-container">
			<!-- Movies Funnel -->
			<div class="funnel-row glass-panel">
				<div class="funnel-meta">
					<div class="funnel-title">
						<span class="icon movie-icon">🎬</span>
						<h2>Movies</h2>
					</div>
					<div class="meta-stats">
						<div class="stat-pill">
							<span class="val">{summary.movies.coverage.percent}%</span>
							<span class="lbl">Analyzed</span>
						</div>
						<div class="stat-pill">
							<span class="val">{summary.movies.coverage.total}</span>
							<span class="lbl">Total</span>
						</div>
					</div>
				</div>
				<div class="funnel-tiles">
					<a href="/letterbox/movies?tab=candidates" class="funnel-tile candidates">
						<span class="tile-count">{summary.movies.workflow_funnel.candidates}</span>
						<span class="tile-label">Candidates</span>
						<span class="tile-desc">Awaiting Probe</span>
					</a>
					<a href="/letterbox/movies?tab=staging" class="funnel-tile staging">
						<span class="tile-count">{summary.movies.workflow_funnel.staging}</span>
						<span class="tile-label">Staging</span>
						<span class="tile-desc">Detected Crops</span>
					</a>
					<a href="/letterbox/movies?tab=preview" class="funnel-tile preview">
						<span class="tile-count">{summary.movies.workflow_funnel.preview}</span>
						<span class="tile-label">Preview</span>
						<span class="tile-desc">Pending Confirm</span>
					</a>
					<a href="/letterbox/movies?tab=processed" class="funnel-tile processed">
						<span class="tile-count">{summary.movies.workflow_funnel.processed}</span>
						<span class="tile-label">Processed</span>
						<span class="tile-desc">Crop Tags Applied</span>
					</a>
				</div>
			</div>

			<!-- TV Funnel -->
			<div class="funnel-row glass-panel">
				<div class="funnel-meta">
					<div class="funnel-title">
						<span class="icon tv-icon">📺</span>
						<h2>Television</h2>
					</div>
					<div class="meta-stats">
						<div class="stat-pill">
							<span class="val">{summary.tv.coverage.percent}%</span>
							<span class="lbl">Analyzed</span>
						</div>
						<div class="stat-pill font-mono">
							<span class="val">{summary.tv.shows_total} / {summary.tv.episodes_total}</span>
							<span class="lbl">Shows / Eps</span>
						</div>
					</div>
				</div>
				<div class="funnel-tiles">
					<a href="/letterbox/tv?has_candidates=true" class="funnel-tile candidates">
						<span class="tile-count">{summary.tv.workflow_funnel.candidates}</span>
						<span class="tile-label">Candidates</span>
						<span class="tile-desc">Awaiting Probe</span>
					</a>
					<a href="/letterbox/tv?verdict=needs_action" class="funnel-tile staging">
						<span class="tile-count">{summary.tv.workflow_funnel.staging}</span>
						<span class="tile-label">Staging</span>
						<span class="tile-desc">Needs Action</span>
					</a>
					<a href="/letterbox/tv?verdict=treated" class="funnel-tile preview">
						<span class="tile-count">{summary.tv.workflow_funnel.preview}</span>
						<span class="tile-label">Preview</span>
						<span class="tile-desc">Treated / Active</span>
					</a>
					<a href="/letterbox/tv?verdict=clean" class="funnel-tile processed">
						<span class="tile-count">{summary.tv.workflow_funnel.processed}</span>
						<span class="tile-label">Processed</span>
						<span class="tile-desc">Cleaned / Clear</span>
					</a>
				</div>
			</div>
		</div>

		<!-- Dashboard Grid -->
		<div class="dashboard-grid">
			<!-- Left: Breakdowns (C3) -->
			<div class="grid-col left-col">
				<!-- Verdict Breakdown -->
				<div class="breakdown-card glass-panel">
					<h3>Verdict Distribution</h3>
					<p class="section-desc">Overall status breakdown of analyzed media files.</p>

					<div class="breakdown-split">
						<!-- Movies Breakdown -->
						<div class="library-breakdown">
							<h4>Movies</h4>
							<div class="breakdown-list">
								<div class="breakdown-row">
									<span class="color-dot good"></span>
									<span class="label">Clear</span>
									<span class="val">{summary.movies.verdict_breakdown.clear}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot warn"></span>
									<span class="label">Letterboxed (Untreated)</span>
									<span class="val">{summary.movies.verdict_breakdown.letterboxed_untreated}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot info"></span>
									<span class="label">Tagged</span>
									<span class="val">{summary.movies.verdict_breakdown.tagged}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot gold"></span>
									<span class="label">Reencoded</span>
									<span class="val">{summary.movies.verdict_breakdown.reencoded}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot purple"></span>
									<span class="label">Variable AR (Needs Review)</span>
									<span class="val">{summary.movies.verdict_breakdown.variable}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot low"></span>
									<span class="label">Ineligible</span>
									<span class="val">{summary.movies.verdict_breakdown.ineligible}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot bad"></span>
									<span class="label">Error</span>
									<span class="val">{summary.movies.verdict_breakdown.error}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot muted"></span>
									<span class="label">Unanalyzed</span>
									<span class="val">{summary.movies.verdict_breakdown.unanalyzed}</span>
								</div>
							</div>
						</div>

						<!-- TV Breakdown -->
						<div class="library-breakdown">
							<h4>Television</h4>
							<div class="breakdown-list">
								<div class="breakdown-row">
									<span class="color-dot good"></span>
									<span class="label">Widescreen</span>
									<span class="val">{summary.tv.verdict_breakdown.widescreen}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot good desaturated"></span>
									<span class="label">Sampled Widescreen</span>
									<span class="val">{summary.tv.verdict_breakdown.sampled_widescreen}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot warn"></span>
									<span class="label">Letterboxed (Untreated)</span>
									<span class="val">{summary.tv.verdict_breakdown.letterboxed_untreated}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot info"></span>
									<span class="label">Tagged</span>
									<span class="val">{summary.tv.verdict_breakdown.tagged}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot gold"></span>
									<span class="label">Reencoded</span>
									<span class="val">{summary.tv.verdict_breakdown.reencoded}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot purple"></span>
									<span class="label">Variable AR (Needs Review)</span>
									<span class="val">{summary.tv.verdict_breakdown.variable}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot teal"></span>
									<span class="label">Open Matte</span>
									<span class="val">{summary.tv.verdict_breakdown.open_matte}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot magenta"></span>
									<span class="label">Pillarbox</span>
									<span class="val">{summary.tv.verdict_breakdown.pillarbox}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot low"></span>
									<span class="label">Ineligible</span>
									<span class="val">{summary.tv.verdict_breakdown.ineligible}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot bad"></span>
									<span class="label">Error</span>
									<span class="val">{summary.tv.verdict_breakdown.error}</span>
								</div>
								<div class="breakdown-row">
									<span class="color-dot muted"></span>
									<span class="label">Unanalyzed</span>
									<span class="val">{summary.tv.verdict_breakdown.unanalyzed}</span>
								</div>
							</div>
						</div>
					</div>
				</div>

				<!-- Aspect Ratios -->
				<div class="breakdown-card glass-panel">
					<h3>Aspect Ratio Distribution</h3>
					<p class="section-desc">Share of detected crop aspects across libraries.</p>

					<div class="ar-distribution-group">
						<div class="ar-distribution">
							<h4>Movies</h4>
							<AspectRatioBar distribution={summary.movies.aspect_distribution} />
						</div>
						<div class="ar-distribution">
							<h4>Television</h4>
							<AspectRatioBar distribution={summary.tv.aspect_distribution} />
						</div>
					</div>
				</div>
			</div>

			<!-- Right: Info cards & batch controls (C3) -->
			<div class="grid-col right-col">
				<!-- Space Reclaimed Card -->
				{#if summary.movies.reencode}
					<div class="stat-card-expanded glass-panel">
						<div class="card-icon reclaimed-icon">💾</div>
						<div class="card-content">
							<h3>Disk Space Reclaimed</h3>
							<div class="primary-stat font-mono">
								{bytesH(summary.movies.reencode.space_reclaimed_bytes)}
							</div>
							<div class="secondary-stats">
								<div class="sub-stat">
									<strong>{summary.movies.reencode.awaiting_decision}</strong> artifacts awaiting review
								</div>
								<div class="sub-stat">
									<strong>{summary.movies.reencode.saved_originals_on_disk}</strong> original files preserved
									on disk
								</div>
							</div>
							<div class="card-actions">
								<a href="/letterbox/movies?tab=processed" class="btn btn-outline btn-sm">
									Review Re-encodes
								</a>
							</div>
						</div>
					</div>
				{/if}

				<!-- Variable AR Card -->
				<div class="stat-card-expanded glass-panel warn-border">
					<div class="card-icon variable-icon">⚠️</div>
					<div class="card-content">
						<h3>Variable Aspect Ratio Warnings</h3>
						<p class="card-desc">
							Some files contain scenes with mixed aspect ratios (e.g. alternating IMAX sequence
							expands to 16:9 full-screen). Cropping these tags is <strong>unsafe</strong> as it will
							cut off active image content.
						</p>
						<div class="secondary-stats">
							<div class="sub-stat">
								<strong>{summary.movies.verdict_breakdown.variable}</strong> movies flagged as variable
							</div>
							<div class="sub-stat">
								<strong>{summary.tv.verdict_breakdown.variable}</strong> TV episodes flagged as variable
							</div>
						</div>
						<div class="card-actions">
							<a href="/letterbox/movies?tab=staging" class="btn btn-outline btn-sm">
								Review Variable Movies
							</a>
						</div>
					</div>
				</div>

				<!-- Coverage and Batch Run Controls -->
				<div class="stat-card-expanded glass-panel">
					<div class="card-icon run-icon">⚡</div>
					<div class="card-content">
						<h3>Library Batch Actions</h3>
						<p class="card-desc">Scan files to identify letterbox crops.</p>

						<div class="batch-controls">
							<!-- Movie controls -->
							<div class="control-box">
								<div class="control-title-group">
									<h4>Movies Scan</h4>
									<span class="coverage-label">{summary.movies.coverage.percent}% covered</span>
								</div>
								<button
									class="btn btn-primary"
									onclick={startMovieDetect}
									disabled={movieDetecting}
								>
									{#if movieDetecting}
										<span class="spinner-sm"></span> Starting...
									{:else}
										Detect Movies
									{/if}
								</button>
							</div>

							<!-- TV controls -->
							<div class="control-box">
								<div class="control-title-group">
									<h4>Television Scan</h4>
									<span class="coverage-label">{summary.tv.coverage.percent}% covered</span>
								</div>

								<div class="exhaustive-toggle-container">
									<label class="toggle-switch">
										<input type="checkbox" bind:checked={tvExhaustive} disabled={tvDetecting} />
										<span class="slider"></span>
									</label>
									<div class="toggle-labels">
										<span class="toggle-title">Exhaustive Mode</span>
										<span
											class="help-tooltip"
											title="Exhaustive scans every downloaded episode. When off, a triage scan tests only 2-3 episodes per season; if they are clean, the remaining episodes are marked 'sampled_clear' to save resources."
										>
											(?)
										</span>
									</div>
								</div>

								<button class="btn btn-primary" onclick={startTvDetect} disabled={tvDetecting}>
									{#if tvDetecting}
										<span class="spinner-sm"></span> Scanning...
									{:else}
										Detect Television
									{/if}
								</button>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>

		<!-- Big Workspace Entrypoints -->
		<div class="entrypoints-footer">
			<a href="/letterbox/movies" class="entrypoint-btn glass-panel movies-entry">
				<div class="entry-inner">
					<span class="entry-icon">🎬</span>
					<div>
						<h3>Open Movies Workspace</h3>
						<p>View lists of candidates, review staged tags, and manage re-encodes.</p>
					</div>
				</div>
				<span class="arrow">→</span>
			</a>
			<a href="/letterbox/tv" class="entrypoint-btn glass-panel tv-entry">
				<div class="entry-inner">
					<span class="entry-icon">📺</span>
					<div>
						<h3>Open Television Workspace</h3>
						<p>Inspect show list summaries, view episode heatmaps, and run targeted scans.</p>
					</div>
				</div>
				<span class="arrow">→</span>
			</a>
		</div>
	{/if}
</section>

<style>
	.letterbox-landing {
		display: flex;
		flex-direction: column;
		gap: 24px;
		max-width: 1200px;
		margin: 0 auto;
		padding: 24px;
	}

	.error-banner {
		display: flex;
		justify-content: space-between;
		align-items: center;
		background: color-mix(in srgb, var(--bad) 15%, var(--ink3));
		border: 1px solid var(--bad-soft);
		border-radius: var(--radius);
		padding: 12px 18px;
		margin-bottom: 8px;
	}
	.error-msg {
		color: var(--bad);
		font-weight: 500;
	}

	/* Glass panel */
	.glass-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 20px;
		box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
		transition:
			transform 0.2s ease,
			box-shadow 0.2s ease;
	}

	/* Progress card */
	.progress-card {
		border-left: 4px solid var(--good);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.progress-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.progress-title-group {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.progress-details h3 {
		margin: 0;
		font-size: 15px;
		font-weight: 600;
		color: var(--text);
	}
	.progress-subtitle {
		margin: 2px 0 0 0;
		font-size: 12px;
		color: var(--muted);
	}
	.progress-bar-container {
		height: 6px;
		background: var(--ink3);
		border-radius: 3px;
		overflow: hidden;
		width: 100%;
	}
	.progress-bar-fill {
		height: 100%;
		background: var(--good);
		border-radius: 3px;
		transition: width 0.3s ease;
	}

	/* Spinner */
	.spinner {
		width: 20px;
		height: 20px;
		border: 2px solid var(--line);
		border-top: 2px solid var(--good);
		border-radius: 50%;
		animation: spin 1s linear infinite;
		display: inline-block;
	}
	.spinner-sm {
		width: 12px;
		height: 12px;
		border: 1.5px solid transparent;
		border-top: 1.5px solid currentColor;
		border-radius: 50%;
		animation: spin 1s linear infinite;
		display: inline-block;
		margin-right: 4px;
	}
	@keyframes spin {
		0% {
			transform: rotate(0deg);
		}
		100% {
			transform: rotate(360deg);
		}
	}

	/* Funnel grid */
	.funnel-container {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.funnel-row {
		display: grid;
		grid-template-columns: 240px 1fr;
		gap: 24px;
		align-items: center;
	}
	.funnel-title {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-bottom: 8px;
	}
	.funnel-title h2 {
		margin: 0;
		font-size: 18px;
		font-weight: 700;
	}
	.funnel-title .icon {
		font-size: 20px;
	}
	.meta-stats {
		display: flex;
		gap: 10px;
	}
	.stat-pill {
		display: flex;
		flex-direction: column;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 4px 10px;
		min-width: 60px;
		text-align: center;
	}
	.stat-pill .val {
		font-size: 13px;
		font-weight: 700;
		color: var(--text);
	}
	.stat-pill .lbl {
		font-size: 9px;
		text-transform: uppercase;
		color: var(--muted);
		margin-top: 1px;
	}
	.funnel-tiles {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 12px;
	}
	.funnel-tile {
		display: flex;
		flex-direction: column;
		padding: 14px;
		border-radius: var(--radius-sm);
		text-decoration: none;
		transition: all 0.2s ease;
		border: 1px solid var(--line);
		background: var(--ink2);
	}
	.funnel-tile:hover {
		transform: translateY(-2px);
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
		border-color: var(--border-color, var(--text));
	}
	.funnel-tile.candidates {
		--border-color: var(--warn);
	}
	.funnel-tile.staging {
		--border-color: var(--info);
	}
	.funnel-tile.preview {
		--border-color: var(--dovi);
	}
	.funnel-tile.processed {
		--border-color: var(--good);
	}

	.tile-count {
		font-size: 22px;
		font-weight: 800;
		color: var(--text);
		font-family: var(--font-mono);
	}
	.tile-label {
		font-size: 13px;
		font-weight: 600;
		color: var(--text);
		margin-top: 4px;
	}
	.tile-desc {
		font-size: 10px;
		color: var(--muted);
		margin-top: 2px;
	}

	/* Dashboard layout */
	.dashboard-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 24px;
	}
	.grid-col {
		display: flex;
		flex-direction: column;
		gap: 24px;
	}
	.breakdown-card h3 {
		margin: 0 0 4px 0;
		font-size: 16px;
		font-weight: 700;
	}
	.section-desc {
		font-size: 12px;
		color: var(--muted);
		margin: 0 0 16px 0;
	}
	.breakdown-split {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 24px;
	}
	.library-breakdown h4 {
		margin: 0 0 12px 0;
		font-size: 13px;
		font-weight: 650;
		color: var(--muted);
		border-bottom: 1px dashed var(--line);
		padding-bottom: 6px;
	}
	.breakdown-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.breakdown-row {
		display: flex;
		align-items: center;
		font-size: 12px;
	}
	.color-dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		margin-right: 8px;
		flex-shrink: 0;
		border: 1px solid rgba(255, 255, 255, 0.1);
	}
	.color-dot.good {
		background: var(--good);
	}
	.color-dot.good.desaturated {
		background: color-mix(in srgb, var(--good) 40%, var(--ink3));
	}
	.color-dot.warn {
		background: var(--warn);
	}
	.color-dot.info {
		background: var(--info);
	}
	.color-dot.gold {
		background: var(--gold);
	}
	.color-dot.purple {
		background: var(--dovi);
	}
	.color-dot.teal {
		background: var(--teal);
	}
	.color-dot.magenta {
		background: var(--magenta);
	}
	.color-dot.low {
		background: var(--low);
	}
	.color-dot.bad {
		background: var(--bad);
	}
	.color-dot.muted {
		background: var(--faint);
	}

	.breakdown-row .label {
		color: var(--muted);
		flex: 1;
	}
	.breakdown-row .val {
		font-weight: 700;
		color: var(--text);
		font-family: var(--font-mono);
	}

	.ar-distribution-group {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.ar-distribution h4 {
		margin: 0 0 8px 0;
		font-size: 13px;
		font-weight: 650;
		color: var(--muted);
	}

	/* Expanded stat cards */
	.stat-card-expanded {
		display: flex;
		gap: 18px;
		align-items: flex-start;
	}
	.stat-card-expanded.warn-border {
		border-left: 4px solid var(--warn);
	}
	.card-icon {
		font-size: 28px;
		padding: 10px;
		background: var(--ink2);
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		flex-shrink: 0;
	}
	.card-content {
		flex: 1;
	}
	.card-content h3 {
		margin: 0 0 6px 0;
		font-size: 15px;
		font-weight: 700;
		color: var(--text);
	}
	.card-desc {
		font-size: 12px;
		color: var(--muted);
		line-height: 1.5;
		margin: 0 0 12px 0;
	}
	.primary-stat {
		font-size: 26px;
		font-weight: 800;
		color: var(--text);
		margin-bottom: 8px;
	}
	.secondary-stats {
		display: flex;
		flex-direction: column;
		gap: 4px;
		margin-bottom: 12px;
	}
	.sub-stat {
		font-size: 12px;
		color: var(--muted);
	}
	.sub-stat strong {
		color: var(--text);
	}
	.card-actions {
		display: flex;
		gap: 8px;
	}

	/* Batch controls styling */
	.batch-controls {
		display: flex;
		flex-direction: column;
		gap: 16px;
		margin-top: 12px;
	}
	.control-box {
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 12px;
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 16px;
	}
	.control-title-group h4 {
		margin: 0;
		font-size: 13px;
		font-weight: 650;
	}
	.coverage-label {
		font-size: 10px;
		color: var(--muted);
		text-transform: uppercase;
		display: block;
		margin-top: 2px;
	}

	.exhaustive-toggle-container {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.toggle-labels {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.toggle-title {
		font-size: 11px;
		font-weight: 600;
		color: var(--muted);
	}
	.help-tooltip {
		font-size: 11px;
		color: var(--info);
		cursor: help;
		font-weight: bold;
	}

	/* Toggle Switch */
	.toggle-switch {
		position: relative;
		display: inline-block;
		width: 28px;
		height: 16px;
		flex-shrink: 0;
	}
	.toggle-switch input {
		opacity: 0;
		width: 0;
		height: 0;
	}
	.slider {
		position: absolute;
		cursor: pointer;
		top: 0;
		left: 0;
		right: 0;
		bottom: 0;
		background-color: var(--ink3);
		transition: 0.2s;
		border-radius: 16px;
		border: 1px solid var(--line);
	}
	.slider:before {
		position: absolute;
		content: '';
		height: 10px;
		width: 10px;
		left: 2px;
		bottom: 2px;
		background-color: var(--text);
		transition: 0.2s;
		border-radius: 50%;
	}
	input:checked + .slider {
		background-color: var(--good);
		border-color: var(--good-soft, var(--good));
	}
	input:checked + .slider:before {
		transform: translateX(12px);
		background-color: var(--on-gold, #241a04);
	}

	/* Buttons styling */
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		font-size: 12px;
		font-weight: 600;
		cursor: pointer;
		transition: all 0.15s ease;
		border: 1px solid transparent;
		background: var(--ink3);
		color: var(--text);
		text-decoration: none;
	}
	.btn:hover {
		filter: brightness(1.1);
	}
	.btn-primary {
		background: var(--gold);
		color: var(--on-gold);
		border-color: var(--gold-deep);
	}
	.btn-primary:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.btn-outline {
		background: transparent;
		border-color: var(--line2);
	}
	.btn-outline:hover {
		background: var(--ink2);
	}
	.btn-bad {
		background: var(--bad);
		color: white;
		border-color: color-mix(in srgb, var(--bad) 80%, black);
	}
	.btn-sm {
		padding: 4px 8px;
		font-size: 11px;
	}

	/* Entry points footer */
	.entrypoints-footer {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 20px;
		margin-top: 8px;
	}
	.entrypoint-btn {
		display: flex;
		align-items: center;
		justify-content: space-between;
		text-decoration: none;
		cursor: pointer;
		padding: 24px;
	}
	.entrypoint-btn:hover {
		transform: translateY(-2px);
		box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25);
		border-color: var(--accent-color);
	}
	.entrypoint-btn.movies-entry {
		--accent-color: var(--good);
	}
	.entrypoint-btn.tv-entry {
		--accent-color: var(--info);
	}

	.entry-inner {
		display: flex;
		gap: 16px;
		align-items: center;
		text-align: left;
	}
	.entry-icon {
		font-size: 32px;
	}
	.entry-inner h3 {
		margin: 0 0 4px 0;
		font-size: 16px;
		font-weight: 700;
		color: var(--text);
	}
	.entry-inner p {
		margin: 0;
		font-size: 12px;
		color: var(--muted);
		line-height: 1.4;
	}
	.arrow {
		font-size: 20px;
		color: var(--muted);
		transition: transform 0.2s ease;
	}
	.entrypoint-btn:hover .arrow {
		transform: translateX(4px);
		color: var(--text);
	}

	.capitalize {
		text-transform: capitalize;
	}
</style>
