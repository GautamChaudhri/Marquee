<script lang="ts">
	import { onMount } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import { page } from '$app/state';
	import { toast } from '$lib/toast';
	import {
		getLetterboxTvDetail,
		getLetterboxTvEpisodeDetail,
		detectLetterboxTv,
		applyLetterboxTv,
		removeLetterboxTvEpisode,
		ignoreLetterboxTvEpisode,
		markNotLetterboxedTvEpisode
	} from '$lib/api/letterbox';
	import { type JobSnapshot } from '$lib/api/jobs';
	import { trackJob } from '$lib/jobs';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import EpisodeHeatmap from '$lib/components/subtitles/EpisodeHeatmap.svelte';
	import UniformityChip from '$lib/components/UniformityChip.svelte';
	import LetterboxFrame from '$lib/components/LetterboxFrame.svelte';
	import type {
		ShowUniformity,
		LetterboxTvEpisode,
		LetterboxTvSeason,
		LetterboxEpisodeDetail
	} from '$lib/api/types';

	// Buckets whose detail view shows an editable before/after crop pair — an
	// undecided or applied-but-unconfirmed crop the user might still change.
	// Everything else (clear/sampled_clear/variable/reencoded/etc.) is settled,
	// so its detail view shows exactly one confirmation frame.
	const PAIR_PREVIEW_BUCKETS = new Set(['candidate', 'tagged']);

	let expandedEpisodeId = $state<number | null>(null);
	const episodeDetails = new SvelteMap<number, LetterboxEpisodeDetail>();
	const episodeDetailErrors = new SvelteMap<number, string>();
	let loadingEpisodeId = $state<number | null>(null);

	async function toggleEpisodeExpand(episodeId: number) {
		if (expandedEpisodeId === episodeId) {
			expandedEpisodeId = null;
			return;
		}
		expandedEpisodeId = episodeId;
		if (episodeDetails.has(episodeId)) return;
		loadingEpisodeId = episodeId;
		try {
			const d = await getLetterboxTvEpisodeDetail(fetch, seriesId, episodeId);
			episodeDetails.set(episodeId, d);
		} catch (e) {
			episodeDetailErrors.set(
				episodeId,
				e instanceof Error ? e.message : 'Failed to load episode preview'
			);
		} finally {
			loadingEpisodeId = null;
		}
	}

	let { data } = $props();

	let detail = $state(data.detail);
	let seriesId = $derived(Number(page.params.id));

	// Background poll to refresh data when jobs are running
	let pollInterval: ReturnType<typeof setInterval>;
	let activeRuns = $state<Record<string, { progress: number; status: string; stop?: () => void }>>(
		{}
	);

	async function refreshDetail() {
		try {
			const res = await getLetterboxTvDetail(fetch, seriesId);
			if (res) {
				detail = res;
			}
		} catch {
			// Fail silently in background
		}
	}

	onMount(() => {
		pollInterval = setInterval(() => {
			// Check if we need to poll (if there are active runs or active_job_ids in detail)
			const hasActiveJobs =
				detail?.active_job_ids?.length > 0 || Object.keys(activeRuns).length > 0;
			if (hasActiveJobs) {
				void refreshDetail();
			}
		}, 4000);

		// Rehydrate any active jobs listed in detail
		if (detail?.active_job_ids) {
			for (const jobId of detail.active_job_ids) {
				rehydrateJob(jobId);
			}
		}

		return () => {
			clearInterval(pollInterval);
			for (const run of Object.values(activeRuns)) {
				run.stop?.();
			}
		};
	});

	function rehydrateJob(jobId: string) {
		if (activeRuns[jobId]) return;

		activeRuns[jobId] = { progress: 0, status: 'running' };
		const stop = trackJob<JobSnapshot>(
			fetch,
			jobId,
			{
				onProgress: (p) => {
					if (activeRuns[jobId]) {
						activeRuns[jobId].status = p.status;
						const progress = p.detail || {};
						const done = Number(progress.done || progress.children_completed || 0);
						const total = Number(progress.total || progress.children_total || 0);
						activeRuns[jobId].progress = total > 0 ? (done / total) * 100 : 0;
					}
				},
				onDone: () => {
					delete activeRuns[jobId];
					toast('Scan job finished!', 'good');
					void refreshDetail();
				},
				onError: () => {
					delete activeRuns[jobId];
					void refreshDetail();
				}
			},
			{
				eventsUrl: `/api/jobs/${jobId}/events`
			}
		);
		activeRuns[jobId].stop = stop;
	}

	// All episodes flattened
	const allEpisodes = $derived(
		detail?.seasons?.flatMap((s: LetterboxTvSeason) =>
			s.episodes.map((ep: LetterboxTvEpisode) => ({
				...ep,
				season_number: s.season_number // ensure season_number is set
			}))
		) || []
	);

	// Filters for Episode Table
	let selectedSeason = $state<string>('all');
	let selectedVerdict = $state<string>('all');
	let searchQuery = $state<string>('');

	// Pagination
	let currentPage = $state(1);
	let pageSize = $state(10);

	// Filtering logic
	const filteredEpisodes = $derived.by(() => {
		return allEpisodes.filter((ep: LetterboxTvEpisode) => {
			if (selectedSeason !== 'all' && ep.season_number !== Number(selectedSeason)) {
				return false;
			}
			if (selectedVerdict !== 'all' && ep.bucket !== selectedVerdict) {
				return false;
			}
			if (searchQuery) {
				const query = searchQuery.toLowerCase();
				const titleMatch = ep.title ? ep.title.toLowerCase().includes(query) : false;
				const epCode = `s${String(ep.season_number).padStart(2, '0')}e${String(ep.episode_number).padStart(2, '0')}`;
				if (!titleMatch && !epCode.includes(query)) {
					return false;
				}
			}
			return true;
		});
	});

	const paginatedEpisodes = $derived.by(() => {
		const start = (currentPage - 1) * pageSize;
		return filteredEpisodes.slice(start, start + pageSize);
	});

	const totalPages = $derived(Math.ceil(filteredEpisodes.length / pageSize));

	$effect(() => {
		// Reset page when filters change
		if (selectedSeason || selectedVerdict || searchQuery) {
			currentPage = 1;
		}
	});

	// Actions (Season level)
	let exhaustive = $state<Record<number, boolean>>({});
	let forceScan = $state<Record<number, boolean>>({});

	async function runSeasonDetect(seasonNumber: number) {
		try {
			const isExhaustive = Boolean(exhaustive[seasonNumber]);
			const isForce = Boolean(forceScan[seasonNumber]);
			const ref = await detectLetterboxTv(fetch, seriesId, {
				season_number: seasonNumber,
				exhaustive: isExhaustive,
				force: isForce
			});
			toast(`Started season ${seasonNumber} detect job...`, 'good');
			rehydrateJob(ref.job_id);
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Season scan failed to start', 'bad');
		}
	}

	async function runSeasonApply(seasonNumber: number) {
		try {
			await applyLetterboxTv(fetch, seriesId, { season_number: seasonNumber });
			toast(`Applied crop tags to season ${seasonNumber}!`, 'good');
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to apply season crop', 'bad');
		}
	}

	async function runSeasonRevert(seasonNumber: number) {
		const seasonEpisodes =
			detail?.seasons?.find((s: LetterboxTvSeason) => s.season_number === seasonNumber)?.episodes ||
			[];
		const targets = seasonEpisodes.filter(
			(ep: LetterboxTvEpisode) =>
				ep.bucket !== 'unanalyzed' && ep.bucket !== 'clear' && ep.bucket !== 'sampled_clear'
		);

		if (targets.length === 0) {
			toast('No letterboxed episodes in this season to revert.', 'info');
			return;
		}

		if (
			!confirm(
				`Are you sure you want to revert crop tags for all ${targets.length} episodes in Season ${seasonNumber}?`
			)
		) {
			return;
		}

		try {
			toast(`Reverting ${targets.length} episodes...`, 'info');
			await Promise.all(
				targets.map((ep: LetterboxTvEpisode) =>
					removeLetterboxTvEpisode(fetch, seriesId, ep.episode_id)
				)
			);
			toast(`Reverted season ${seasonNumber} crops successfully.`, 'good');
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Revert failed', 'bad');
		}
	}

	// Actions (Episode level)
	async function runEpisodeDetect(episodeId: number) {
		try {
			const ref = await detectLetterboxTv(fetch, seriesId, {
				episode_id: episodeId,
				exhaustive: true, // episode detect is always exhaustive
				force: true
			});
			toast('Started episode scan...', 'good');
			rehydrateJob(ref.job_id);
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Episode scan failed to start', 'bad');
		}
	}

	async function runEpisodeApply(episodeId: number) {
		try {
			await applyLetterboxTv(fetch, seriesId, { episode_id: episodeId });
			toast('Applied crop tag to episode!', 'good');
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to apply episode crop', 'bad');
		}
	}

	async function runEpisodeClear(episodeId: number) {
		try {
			await markNotLetterboxedTvEpisode(fetch, seriesId, episodeId);
			toast('Episode marked as not letterboxed.', 'good');
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to mark not letterboxed', 'bad');
		}
	}

	async function runEpisodeIgnore(episodeId: number) {
		try {
			await ignoreLetterboxTvEpisode(fetch, seriesId, episodeId);
			toast('Episode marked skipped / ignore.', 'good');
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to ignore episode', 'bad');
		}
	}

	async function runEpisodeRevert(episodeId: number) {
		try {
			await removeLetterboxTvEpisode(fetch, seriesId, episodeId);
			toast('Removed crop tag from episode.', 'good');
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to remove crop tag', 'bad');
		}
	}

	function triggerEpisodeReencode() {
		// TV permanent reencoding is not supported by the backend yet
		toast(
			'Episode permanent re-encoding is currently not supported by the backend (only available for Movies).',
			'info'
		);
	}

	// Verdict metadata helpers
	const VERDICT_META: Record<
		string,
		{ label: string; tone: 'good' | 'info' | 'warn' | 'dovi' | 'muted' | 'bad' | 'gold' }
	> = {
		clear: { label: 'Clear', tone: 'good' },
		sampled_clear: { label: 'Sampled Clear', tone: 'good' },
		candidate: { label: 'Candidate', tone: 'warn' },
		tagged: { label: 'Tagged', tone: 'info' },
		reencoded: { label: 'Reencoded', tone: 'gold' },
		variable: { label: 'Variable', tone: 'dovi' },
		error: { label: 'Error', tone: 'bad' },
		ineligible: { label: 'Ineligible', tone: 'muted' },
		unanalyzed: { label: 'Unanalyzed', tone: 'muted' }
	};
</script>

<svelte:head>
	<title>{detail?.series?.title ? `${detail.series.title} - Letterbox` : 'TV Show Details'}</title>
</svelte:head>

<section class="page letterbox-tv-detail">
	<div class="breadcrumbs-row">
		<a href="/letterbox" class="breadcrumb-link">Letterbox</a>
		<span class="sep">/</span>
		<a href="/letterbox/tv" class="breadcrumb-link">TV Shows</a>
		<span class="sep">/</span>
		<span class="curr">{detail?.series?.title || 'Detail'}</span>
	</div>

	{#if detail}
		<SectionHeader
			title={detail.series.title}
			subtitle={detail.series.year ? `Released in ${detail.series.year}` : 'Television Series'}
		/>

		<!-- Heatmap centerpiece -->
		<div class="section-container">
			<h3 class="section-title">Episode Compliance Heatmap</h3>
			<EpisodeHeatmap seasons={detail.seasons} mode="letterbox" />
		</div>

		<!-- Season summary list -->
		<div class="section-container">
			<h3 class="section-title">Season Overview & Actions</h3>
			<div class="seasons-list">
				{#each detail.seasons as season (season.season_number)}
					{@const verdict = VERDICT_META[season.rollup.verdict] || VERDICT_META.unanalyzed}
					<div class="season-item glass-panel" class:specials={season.season_number === 0}>
						<div class="season-meta">
							<span class="season-number">
								{#if season.season_number === 0}
									Specials
								{:else}
									Season {season.season_number}
								{/if}
							</span>
							<span class="verdict-chip" style={`--c: var(--${verdict.tone})`}>
								{verdict.label}
							</span>
							<UniformityChip uniformity={season.rollup.uniformity as ShowUniformity} />
							<span class="ep-count font-mono">{season.rollup.episodes_total} episodes</span>
						</div>

						<div class="season-actions">
							<!-- Controls -->
							<div class="scan-options">
								<label class="checkbox-container">
									<input type="checkbox" bind:checked={exhaustive[season.season_number]} />
									<span class="checkmark"></span>
									Exhaustive
								</label>
								<label class="checkbox-container">
									<input type="checkbox" bind:checked={forceScan[season.season_number]} />
									<span class="checkmark"></span>
									Force
								</label>
							</div>

							<button
								class="btn btn-primary btn-sm"
								onclick={() => runSeasonDetect(season.season_number)}
							>
								Detect
							</button>
							<button
								class="btn btn-outline btn-sm"
								onclick={() => runSeasonApply(season.season_number)}
							>
								Apply
							</button>
							<button
								class="btn btn-outline btn-sm"
								onclick={() => runSeasonRevert(season.season_number)}
							>
								Revert
							</button>
						</div>
					</div>
				{/each}
			</div>
		</div>

		<!-- Episode list table -->
		<div class="section-container">
			<h3 class="section-title">Episodes Table</h3>
			<div class="episodes-table-card glass-panel">
				<!-- Filters row -->
				<div class="table-filters">
					<input
						type="search"
						placeholder="Search episode code or title..."
						class="search-input"
						bind:value={searchQuery}
					/>

					<div class="filter-group">
						<label for="filter-season">Season</label>
						<select id="filter-season" class="filter-select" bind:value={selectedSeason}>
							<option value="all">All Seasons</option>
							{#each detail.seasons as s (s.season_number)}
								<option value={String(s.season_number)}>
									{s.season_number === 0 ? 'Specials' : `Season ${s.season_number}`}
								</option>
							{/each}
						</select>
					</div>

					<div class="filter-group">
						<label for="filter-verdict">Verdict</label>
						<select id="filter-verdict" class="filter-select" bind:value={selectedVerdict}>
							<option value="all">All Verdicts</option>
							<option value="clear">Clear</option>
							<option value="sampled_clear">Sampled Clear</option>
							<option value="candidate">Candidate</option>
							<option value="tagged">Tagged</option>
							<option value="reencoded">Reencoded</option>
							<option value="variable">Variable</option>
							<option value="error">Error</option>
							<option value="unanalyzed">Unanalyzed</option>
						</select>
					</div>
				</div>

				<!-- Table list -->
				{#if filteredEpisodes.length === 0}
					<div class="empty-state">No matching episodes found.</div>
				{:else}
					<div class="table-scroll">
						<table class="episodes-table">
							<thead>
								<tr>
									<th class="expand-col"></th>
									<th>Episode</th>
									<th>Title</th>
									<th>Verdict</th>
									<th>Aspect Ratio</th>
									<th>Conf</th>
									<th>Provenance</th>
									<th class="actions-col">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each paginatedEpisodes as ep (ep.episode_id)}
									{@const verd = VERDICT_META[ep.bucket] || VERDICT_META.unanalyzed}
									{@const expanded = expandedEpisodeId === ep.episode_id}
									<tr id={`episode-${ep.season_number}-${ep.episode_id}`}>
										<td class="expand-col">
											<button
												class="expand-toggle"
												class:open={expanded}
												title={expanded ? 'Hide frame preview' : 'Show frame preview'}
												onclick={() => toggleEpisodeExpand(ep.episode_id)}
											>
												▶
											</button>
										</td>
										<td class="font-mono">
											S{String(ep.season_number).padStart(2, '0')}E{String(
												ep.episode_number
											).padStart(2, '0')}
										</td>
										<td class="title-td font-semibold">
											{ep.title || 'Untitled'}
										</td>
										<td>
											<span class="verdict-chip" style={`--c: var(--${verd.tone})`}>
												{verd.label}
											</span>
										</td>
										<td class="font-mono">{ep.aspect_label || '—'}</td>
										<td class="font-mono">
											{#if ep.confidence !== null && ep.confidence !== undefined}
												{Math.round(parseFloat(ep.confidence) * 100)}%
											{:else}
												—
											{/if}
										</td>
										<td>
											{#if ep.resolved_by}
												<span class="prov-badge">{ep.resolved_by}</span>
											{:else}
												<span class="prov-badge unspec">None</span>
											{/if}
										</td>
										<td class="actions-col">
											<div class="action-buttons-group">
												<button
													class="btn btn-outline btn-xs"
													title="Detect letterbox borders"
													onclick={() => runEpisodeDetect(ep.episode_id)}
												>
													Detect
												</button>

												{#if ep.bucket === 'candidate'}
													<button
														class="btn btn-primary btn-xs"
														title="Apply detected crop tag"
														onclick={() => runEpisodeApply(ep.episode_id)}
													>
														Apply
													</button>
													<button
														class="btn btn-outline btn-xs btn-good"
														title="Approve / Clear crop"
														onclick={() => runEpisodeClear(ep.episode_id)}
													>
														Clear
													</button>
													<button
														class="btn btn-outline btn-xs btn-bad"
														title="Ignore recommendation"
														onclick={() => runEpisodeIgnore(ep.episode_id)}
													>
														Ignore
													</button>
												{:else if ep.bucket === 'tagged'}
													<button
														class="btn btn-outline btn-xs btn-bad"
														title="Remove applied crop tag"
														onclick={() => runEpisodeRevert(ep.episode_id)}
													>
														Revert
													</button>
												{/if}

												<button
													class="btn btn-outline btn-xs"
													title="Re-encode permanently (Not supported)"
													onclick={triggerEpisodeReencode}
												>
													Reencode
												</button>
											</div>
										</td>
									</tr>
									{#if expanded}
										<tr class="expand-row">
											<td colspan="8">
												{#if loadingEpisodeId === ep.episode_id}
													<div class="expand-note">Loading preview…</div>
												{:else if episodeDetailErrors.has(ep.episode_id)}
													<div class="expand-note">{episodeDetailErrors.get(ep.episode_id)}</div>
												{:else}
													{@const epDetail = episodeDetails.get(ep.episode_id)}
													{#if !epDetail}
														<div class="expand-note">No preview available.</div>
													{:else if PAIR_PREVIEW_BUCKETS.has(ep.bucket)}
														<div class="expand-frames pair">
															<LetterboxFrame
																src={epDetail.preview_urls?.before ?? null}
																alt="before crop"
																placeholder="No preview available"
															/>
															<LetterboxFrame
																src={epDetail.preview_urls?.after ?? null}
																alt="after crop"
																tone="after"
																placeholder="No preview available"
															/>
														</div>
													{:else}
														<div class="expand-frames single">
															<LetterboxFrame
																src={epDetail.preview_urls?.before ?? null}
																alt="episode frame"
																placeholder="No preview available"
															/>
														</div>
													{/if}
												{/if}
											</td>
										</tr>
									{/if}
								{/each}
							</tbody>
						</table>
					</div>

					<!-- Pagination row -->
					<div class="pagination-row">
						<div class="page-size-selector">
							<label for="page-size">Show</label>
							<select id="page-size" class="filter-select select-sm" bind:value={pageSize}>
								<option value={10}>10</option>
								<option value={25}>25</option>
								<option value={50}>50</option>
							</select>
							<span>episodes</span>
						</div>

						<div class="page-nav">
							<button
								class="btn btn-outline btn-sm"
								disabled={currentPage === 1}
								onclick={() => currentPage--}
							>
								Prev
							</button>
							<span class="page-info">
								Page <strong>{currentPage}</strong> of <strong>{totalPages}</strong>
								({filteredEpisodes.length} total)
							</span>
							<button
								class="btn btn-outline btn-sm"
								disabled={currentPage === totalPages}
								onclick={() => currentPage++}
							>
								Next
							</button>
						</div>
					</div>
				{/if}
			</div>
		</div>
	{/if}
</section>

<style>
	.letterbox-tv-detail {
		display: flex;
		flex-direction: column;
		gap: 24px;
		max-width: 1200px;
		margin: 0 auto;
		padding: 24px;
	}

	.breadcrumbs-row {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12px;
		color: var(--muted);
	}
	.breadcrumb-link {
		color: var(--muted);
		text-decoration: none;
		transition: color 0.15s ease;
	}
	.breadcrumb-link:hover {
		color: var(--text);
	}
	.sep {
		color: var(--faint);
	}
	.curr {
		color: var(--text);
		font-weight: 600;
	}

	.section-container {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.section-title {
		margin: 0;
		font-size: 15px;
		font-weight: 700;
		color: var(--text);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.glass-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
		box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
	}

	/* Seasons List */
	.seasons-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.season-item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		padding: 12px 18px;
	}
	.season-item.specials {
		opacity: 0.75;
		border-left: 3px dashed var(--line);
	}
	.season-meta {
		display: flex;
		align-items: center;
		gap: 16px;
	}
	.season-number {
		font-size: 14px;
		font-weight: 750;
		min-width: 80px;
	}
	.ep-count {
		color: var(--muted);
		font-size: 12px;
	}

	.season-actions {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.scan-options {
		display: flex;
		align-items: center;
		gap: 12px;
		background: var(--ink3);
		padding: 4px 10px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
	}

	/* Custom Checkbox */
	.checkbox-container {
		display: inline-flex;
		align-items: center;
		position: relative;
		cursor: pointer;
		font-size: 11px;
		font-weight: 600;
		color: var(--muted);
		user-select: none;
		gap: 6px;
	}
	.checkbox-container input {
		position: absolute;
		opacity: 0;
		cursor: pointer;
		height: 0;
		width: 0;
	}
	.checkmark {
		height: 12px;
		width: 12px;
		background-color: var(--ink2);
		border: 1px solid var(--line2);
		border-radius: 2px;
		display: inline-block;
		transition: all 0.1s ease;
	}
	.checkbox-container input:checked ~ .checkmark {
		background-color: var(--gold);
		border-color: var(--gold);
	}
	.checkmark:after {
		content: '';
		position: absolute;
		display: none;
	}
	.checkbox-container input:checked ~ .checkmark:after {
		display: block;
	}

	/* Episodes Table */
	.episodes-table-card {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.table-filters {
		display: flex;
		align-items: center;
		gap: 16px;
		flex-wrap: wrap;
	}
	.search-input {
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 6px 12px;
		font-size: 12px;
		color: var(--text);
		flex: 1;
		min-width: 200px;
	}
	.filter-group {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.filter-group label {
		font-size: 11px;
		font-weight: 600;
		color: var(--muted);
	}
	.filter-select {
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 6px 10px;
		font-size: 12px;
		color: var(--text);
		cursor: pointer;
	}

	.table-scroll {
		overflow-x: auto;
	}
	.episodes-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
		font-size: 13px;
	}
	.episodes-table th {
		background: var(--ink2);
		padding: 10px 14px;
		font-weight: 700;
		color: var(--muted);
		border-bottom: 1px solid var(--line);
	}
	.episodes-table td {
		padding: 12px 14px;
		border-bottom: 1px solid var(--line);
		vertical-align: middle;
	}
	.episodes-table tr:hover {
		background: var(--ink2);
	}
	.title-td {
		font-size: 13px;
		color: var(--text);
	}
	.prov-badge {
		font-size: 10px;
		font-weight: 600;
		padding: 1px 6px;
		border-radius: 4px;
		background: var(--ink3);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.prov-badge.unspec {
		color: var(--faint2);
		border-style: dashed;
	}

	.actions-col {
		text-align: right;
		width: 280px;
	}
	.action-buttons-group {
		display: flex;
		gap: 6px;
		justify-content: flex-end;
	}

	.expand-col {
		width: 28px;
	}
	.expand-toggle {
		background: transparent;
		border: none;
		color: var(--muted);
		cursor: pointer;
		font-size: 10px;
		padding: 4px;
		transition: transform 0.15s ease;
	}
	.expand-toggle.open {
		transform: rotate(90deg);
		color: var(--text);
	}
	.expand-row td {
		background: var(--ink2);
		padding: 16px;
	}
	.expand-note {
		font-size: 12px;
		color: var(--muted);
	}
	.expand-frames {
		display: grid;
		gap: 12px;
	}
	.expand-frames.pair {
		grid-template-columns: 1fr 1fr;
	}
	.expand-frames.single {
		max-width: 360px;
	}

	.verdict-chip {
		display: inline-flex;
		align-items: center;
		font-size: 11px;
		font-weight: 600;
		padding: 2px 8px;
		border-radius: 999px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 12%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 25%, transparent);
		white-space: nowrap;
	}

	/* Pagination */
	.pagination-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		border-top: 1px solid var(--line);
		padding-top: 14px;
		margin-top: 4px;
	}
	.page-size-selector {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 12px;
		color: var(--muted);
	}
	.page-nav {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.page-info {
		font-size: 12px;
		color: var(--muted);
	}
	.page-info strong {
		color: var(--text);
	}

	.select-sm {
		padding: 3px 6px;
		font-size: 11px;
	}

	/* Buttons */
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
	.btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.btn-primary {
		background: var(--gold);
		color: #241a04;
		border-color: var(--gold-deep);
	}
	.btn-outline {
		background: transparent;
		border-color: var(--line2);
	}
	.btn-outline:hover {
		background: var(--ink2);
	}
	.btn-good {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 40%, var(--line));
	}
	.btn-good:hover {
		background: color-mix(in srgb, var(--good) 10%, transparent);
	}
	.btn-bad {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 40%, var(--line));
	}
	.btn-bad:hover {
		background: color-mix(in srgb, var(--bad) 10%, transparent);
	}
	.btn-sm {
		padding: 4px 8px;
		font-size: 11px;
	}
	.btn-xs {
		padding: 2px 6px;
		font-size: 10px;
		border-radius: 3px;
	}

	.font-semibold {
		font-weight: 600;
	}
	.capitalize {
		text-transform: capitalize;
	}
	.empty-state {
		padding: 32px;
		text-align: center;
		color: var(--muted);
	}
</style>
