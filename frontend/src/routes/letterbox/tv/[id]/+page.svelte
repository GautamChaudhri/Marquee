<script lang="ts">
	import { onMount } from 'svelte';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { page } from '$app/state';
	import { aspectRatio, bytesH, confidenceTone } from '$lib/display';
	import { toast } from '$lib/toast';
	import { subscribe } from '$lib/sse';
	import {
		getLetterboxTvDetail,
		getLetterboxTvEpisodeDetail,
		detectLetterboxTv,
		applyLetterboxTv,
		revertLetterboxTv,
		removeLetterboxTvEpisode,
		ignoreLetterboxTvEpisode,
		markNotLetterboxedTvEpisode,
		batchReencodeTv,
		listReencodeArtifacts,
		replaceOriginal,
		restoreOriginal,
		deleteArtifact,
		replaceReadyTvArtifacts,
		getMediaJob
	} from '$lib/api/letterbox';
	import { cancelJob, getJob, type JobSnapshot } from '$lib/api/jobs';
	import { trackJob } from '$lib/jobs';
	import { displayJobLabel } from '$lib/job-labels';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import EpisodeHeatmap from '$lib/components/subtitles/EpisodeHeatmap.svelte';
	import UniformityChip from '$lib/components/UniformityChip.svelte';
	import LetterboxFrame from '$lib/components/LetterboxFrame.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import ConfidencePopover from '$lib/components/letterbox/ConfidencePopover.svelte';
	import BatchReencodeModal from '$lib/components/letterbox/BatchReencodeModal.svelte';
	import ReencodePlanModal from '$lib/components/letterbox/ReencodePlanModal.svelte';
	import {
		LETTERBOX_TV_BUCKET_META,
		LETTERBOX_TV_CONTENT_META,
		LETTERBOX_TV_VERDICT_META
	} from '$lib/letterbox/status-meta';
	import type {
		LetterboxTvBucket,
		LetterboxTvEpisode,
		LetterboxTvSeason,
		LetterboxEpisodeDetail,
		JobSummary,
		ReencodeArtifact,
		BatchReencodeSettings
	} from '$lib/api/types';
	import { pairKey, pairColorMap, agreeCount } from '$lib/letterbox-samples';

	// Buckets whose detail view shows an editable before/after crop pair — an
	// undecided or applied-but-unconfirmed crop the user might still change.
	// Everything else (widescreen/sampled_widescreen/variable/reencoded/etc.) is settled,
	// so its detail view shows exactly one confirmation frame.
	const PAIR_PREVIEW_BUCKETS = new Set(['candidate', 'tagged']);
	// Buckets whose preview frame(s) are worth warming ahead of expand — settled
	// Widescreen buckets only need their single before frame.
	const PREFETCH_BUCKETS = new Set(['candidate', 'tagged', 'widescreen', 'sampled_widescreen']);

	const expandedEpisodeIds = new SvelteSet<number>();
	const episodeDetails = new SvelteMap<number, LetterboxEpisodeDetail>();
	const episodeDetailErrors = new SvelteMap<number, string>();
	const episodePreviewMinutes = new SvelteMap<number, number>();
	const expandedConfidenceEpisodes = new SvelteMap<number, boolean>();
	const loadingEpisodeIds = new SvelteSet<number>();
	const collapsedSeasons = new SvelteSet<number>();
	let collapseStateInitialized = $state(false);
	let prefetchGeneration = 0;

	async function toggleEpisodeExpand(episodeId: number) {
		if (expandedEpisodeIds.has(episodeId)) {
			expandedEpisodeIds.delete(episodeId);
			return;
		}
		expandedEpisodeIds.add(episodeId);
		if (episodeDetails.has(episodeId)) return;
		loadingEpisodeIds.add(episodeId);
		try {
			const d = await getLetterboxTvEpisodeDetail(fetch, seriesId, episodeId);
			episodeDetails.set(episodeId, d);
		} catch (e) {
			episodeDetailErrors.set(
				episodeId,
				e instanceof Error ? e.message : 'Failed to load episode preview'
			);
		} finally {
			loadingEpisodeIds.delete(episodeId);
		}
	}

	function toggleConfidenceExpand(episodeId: number) {
		expandedConfidenceEpisodes.set(
			episodeId,
			!(expandedConfidenceEpisodes.get(episodeId) ?? false)
		);
	}

	function setEpisodePreviewMinute(episodeId: number, minute: number) {
		episodePreviewMinutes.set(episodeId, minute);
	}

	function warmPreview(url?: string | null) {
		if (!url || typeof Image === 'undefined') return;
		const img = new Image();
		img.src = url;
	}

	let { data } = $props();

	let detail = $state(data.detail);
	let seriesId = $derived(Number(page.params.id));

	$effect(() => {
		if (!detail || collapseStateInitialized) return;
		for (const season of detail.seasons) {
			const actionable =
				(season.rollup.bucket_counts.candidate ?? 0) + (season.rollup.bucket_counts.tagged ?? 0);
			if (actionable === 0) {
				collapsedSeasons.add(season.season_number);
			}
		}
		collapseStateInitialized = true;
	});

	function episodePreviewUrl(
		episodeId: number,
		mode: 'before' | 'after',
		minute: number,
		exact = false
	) {
		return `/api/letterbox/tv/${seriesId}/episodes/${episodeId}/preview?mode=${mode}&minute=${minute}${exact ? '&exact=true' : ''}`;
	}

	function toggleSeasonCollapse(seasonNumber: number) {
		if (collapsedSeasons.has(seasonNumber)) {
			collapsedSeasons.delete(seasonNumber);
			return;
		}
		collapsedSeasons.add(seasonNumber);
	}

	async function focusEpisodeFromHeatmap(seasonNumber: number, episodeId: number) {
		collapsedSeasons.delete(seasonNumber);
		await toggleEpisodeExpand(episodeId);
		requestAnimationFrame(() => {
			const row = document.getElementById(`episode-${seasonNumber}-${episodeId}`);
			row?.scrollIntoView({ behavior: 'smooth', block: 'center' });
		});
	}

	// Background poll to refresh data when jobs are running
	let pollInterval: ReturnType<typeof setInterval>;
	let activeRuns = $state<
		Record<
			string,
			{
				progress: number;
				status: string;
				stage?: string;
				cancelling?: boolean;
				type?: string;
				label?: string | null;
				stop?: () => void;
			}
		>
	>({});

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

	async function runPrefetchPass() {
		if (!detail) return;
		const episodes = detail.seasons.flatMap((season: LetterboxTvSeason) =>
			season.episodes.filter((ep: LetterboxTvEpisode) => PREFETCH_BUCKETS.has(ep.bucket))
		);
		if (episodes.length === 0) return;

		const runId = ++prefetchGeneration;
		let nextIndex = 0;
		const workerCount = Math.min(4, episodes.length);

		const loadNext = async () => {
			while (runId === prefetchGeneration && nextIndex < episodes.length) {
				const episode = episodes[nextIndex++];
				if (!episode || episodeDetails.has(episode.episode_id)) continue;
				try {
					const detailRow = await getLetterboxTvEpisodeDetail(fetch, seriesId, episode.episode_id);
					if (runId !== prefetchGeneration) return;
					episodeDetails.set(episode.episode_id, detailRow);
					warmPreview(detailRow.preview_urls?.before);
					if (PAIR_PREVIEW_BUCKETS.has(episode.bucket)) {
						warmPreview(detailRow.preview_urls?.after);
					}
				} catch {
					if (runId !== prefetchGeneration) return;
				}
			}
		};

		await Promise.all(Array.from({ length: workerCount }, () => loadNext()));
	}

	onMount(() => {
		void runPrefetchPass();
		void loadReencodeArtifacts();
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
			prefetchGeneration += 1;
			clearInterval(pollInterval);
			for (const run of Object.values(activeRuns)) {
				run.stop?.();
			}
		};
	});

	function rehydrateJob(jobId: string) {
		if (activeRuns[jobId]) return;

		activeRuns[jobId] = { progress: 0, status: 'running' };
		getJob(fetch, jobId)
			.then((job) => {
				if (activeRuns[jobId]) {
					activeRuns[jobId].type = job.type;
					activeRuns[jobId].label = job.label;
				}
			})
			.catch(() => {
				// non-fatal — the progress bar just falls back to a humanized job type
			});
		const stop = trackJob<JobSnapshot>(
			fetch,
			jobId,
			{
				onProgress: (p) => {
					if (activeRuns[jobId]) {
						activeRuns[jobId].status = p.status;
						const progress = p.detail || {};
						if (typeof progress.percent === 'number' && Number.isFinite(progress.percent)) {
							activeRuns[jobId].progress = Math.max(0, Math.min(100, progress.percent));
							activeRuns[jobId].stage =
								typeof progress.stage === 'string' ? progress.stage : undefined;
						} else {
							const done = Number(progress.done || progress.children_completed || 0);
							const total = Number(progress.total || progress.children_total || 0);
							activeRuns[jobId].progress = total > 0 ? (done / total) * 100 : 0;
						}
					}
				},
				onDone: (job) => {
					delete activeRuns[jobId];
					toast(`${displayJobLabel(job)} finished!`, 'good');
					prefetchGeneration += 1;
					episodeDetails.clear();
					episodeDetailErrors.clear();
					void (async () => {
						await refreshDetail();
						await runPrefetchPass();
						await loadReencodeArtifacts();
					})();
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

	async function cancelActiveRun(jobId: string) {
		const run = activeRuns[jobId];
		if (!run || run.cancelling) return;
		try {
			await cancelJob(fetch, jobId);
			const current = activeRuns[jobId];
			if (current) {
				current.cancelling = true;
				current.status = 'cancelling';
			}
		} catch {
			toast('Could not cancel job', 'bad');
		}
	}

	// Filters for Episode Table
	let selectedVerdict = $state<string>('all');
	let searchQuery = $state<string>('');
	const filtersActive = $derived(selectedVerdict !== 'all' || searchQuery.trim().length > 0);
	const seasonViews = $derived.by(() => {
		if (!detail) return [];
		const query = searchQuery.trim().toLowerCase();
		return detail.seasons.map((season: LetterboxTvSeason) => {
			const filteredEpisodes = season.episodes.filter((ep: LetterboxTvEpisode) => {
				if (selectedVerdict !== 'all' && ep.bucket !== selectedVerdict) {
					return false;
				}
				if (!query) return true;
				const titleMatch = ep.title ? ep.title.toLowerCase().includes(query) : false;
				const epCode = `s${String(ep.season_number).padStart(2, '0')}e${String(ep.episode_number).padStart(2, '0')}`;
				return titleMatch || epCode.includes(query);
			});
			const hasMatches = filteredEpisodes.length > 0;
			return {
				season,
				filteredEpisodes,
				hasMatches,
				collapsed: filtersActive ? !hasMatches : collapsedSeasons.has(season.season_number)
			};
		});
	});

	// Actions (Season level)
	let exhaustive = $state<Record<number, boolean>>({});
	let forceScan = $state<Record<number, boolean>>({});
	let includeOpenMatte = $state<Record<number, boolean>>({});

	async function runSeasonDetect(seasonNumber: number) {
		try {
			const isExhaustive = Boolean(exhaustive[seasonNumber]);
			const isForce = Boolean(forceScan[seasonNumber]);
			const isIncludeOpenMatte = Boolean(includeOpenMatte[seasonNumber]);
			const ref = await detectLetterboxTv(fetch, seriesId, {
				season_number: seasonNumber,
				exhaustive: isExhaustive,
				force: isForce,
				include_open_matte: isIncludeOpenMatte || undefined
			});
			toast(`Started season ${seasonNumber} detect job...`, 'good');
			rehydrateJob(ref.job_id);
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Season scan failed to start', 'bad');
		}
	}

	// Confidence-filtered scope apply/revert — seasonNumber is null for show-wide scope.
	let scopeConfidence = $state<Record<string, string[]>>({});

	function scopeKey(seasonNumber: number | null): string {
		return seasonNumber == null ? 'show' : `season-${seasonNumber}`;
	}

	function confidenceFor(seasonNumber: number | null): string[] {
		return scopeConfidence[scopeKey(seasonNumber)] ?? ['high'];
	}

	function toggleConfidenceLevel(seasonNumber: number | null, level: string) {
		const key = scopeKey(seasonNumber);
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient lookup, discarded immediately
		const current = new Set(confidenceFor(seasonNumber).filter((l) => l !== 'all'));
		if (current.has(level)) current.delete(level);
		else current.add(level);
		scopeConfidence[key] = [...current];
	}

	function toggleConfidenceAll(seasonNumber: number | null) {
		const key = scopeKey(seasonNumber);
		scopeConfidence[key] = confidenceFor(seasonNumber).includes('all') ? ['high'] : ['all'];
	}

	function scopeEpisodes(seasonNumber: number | null): LetterboxTvEpisode[] {
		if (!detail) return [];
		return seasonNumber == null
			? detail.seasons.flatMap((s: LetterboxTvSeason) => s.episodes)
			: (detail.seasons.find((s: LetterboxTvSeason) => s.season_number === seasonNumber)
					?.episodes ?? []);
	}

	function candidateCount(seasonNumber: number | null): number {
		const levels = confidenceFor(seasonNumber);
		const matchesLevel = (ep: LetterboxTvEpisode) =>
			levels.includes('all') || (ep.confidence != null && levels.includes(ep.confidence));
		return scopeEpisodes(seasonNumber).filter(
			(ep: LetterboxTvEpisode) => ep.bucket === 'candidate' && matchesLevel(ep)
		).length;
	}

	async function runScopeApply(seasonNumber: number | null) {
		try {
			const levels = confidenceFor(seasonNumber);
			const res = await applyLetterboxTv(fetch, seriesId, {
				season_number: seasonNumber ?? undefined,
				confidence_levels: levels
			});
			const job = res as JobSummary;
			if (job?.job_id) {
				toast(
					seasonNumber == null
						? 'Started show-wide apply job...'
						: `Started season ${seasonNumber} apply job...`,
					'good'
				);
				rehydrateJob(job.job_id);
			} else {
				toast('Applied crop tags!', 'good');
			}
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to apply crop', 'bad');
		}
	}

	async function runScopeRevert(seasonNumber: number | null) {
		const targets = scopeEpisodes(seasonNumber).filter(
			(ep: LetterboxTvEpisode) =>
				ep.bucket !== 'unanalyzed' &&
				ep.bucket !== 'widescreen' &&
				ep.bucket !== 'sampled_widescreen'
		);

		if (targets.length === 0) {
			toast('No letterboxed episodes in this scope to revert.', 'info');
			return;
		}

		const scopeLabel = seasonNumber == null ? 'the entire show' : `Season ${seasonNumber}`;
		if (
			!confirm(
				`Are you sure you want to revert crop tags for all ${targets.length} episodes in ${scopeLabel}?`
			)
		) {
			return;
		}

		try {
			const res = await revertLetterboxTv(fetch, seriesId, {
				season_number: seasonNumber ?? undefined
			});
			const job = res as JobSummary;
			if (job?.job_id) {
				toast(
					seasonNumber == null
						? 'Started show-wide revert job...'
						: `Started season ${seasonNumber} revert job...`,
					'good'
				);
				rehydrateJob(job.job_id);
			} else {
				toast('Reverted crops successfully.', 'good');
			}
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

	async function runScanAnyway(episodeId: number) {
		try {
			const ref = await detectLetterboxTv(fetch, seriesId, {
				episode_id: episodeId,
				exhaustive: true,
				force: true,
				include_open_matte: true
			});
			toast('Started scan (include OM/PB)...', 'good');
			rehydrateJob(ref.job_id);
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Scan failed to start', 'bad');
		}
	}

	// Single-episode reencode: plan modal → confirm → track the media job.
	let reencodePlanEpisodeId = $state<number | null>(null);
	let reencodeTracking = $state<
		Record<number, { progress: number; stage: string | null; status: string }>
	>({});
	const reencodeUnsubs: Record<number, () => void> = {};
	const reencodePollTimers: Record<number, ReturnType<typeof setInterval>> = {};

	function openReencodePlan(episodeId: number) {
		reencodePlanEpisodeId = episodeId;
	}

	function closeReencodePlan() {
		reencodePlanEpisodeId = null;
	}

	function stopReencodeTracking(episodeId: number) {
		reencodeUnsubs[episodeId]?.();
		delete reencodeUnsubs[episodeId];
		if (reencodePollTimers[episodeId]) {
			clearInterval(reencodePollTimers[episodeId]);
			delete reencodePollTimers[episodeId];
		}
	}

	async function finishEpisodeReencode(episodeId: number, jobId: string) {
		stopReencodeTracking(episodeId);
		delete reencodeTracking[episodeId];
		try {
			const job = await getMediaJob(fetch, jobId);
			if (job.error) {
				toast(job.error.error ?? 'Re-encode failed (check job log)', 'bad');
			} else {
				toast('Re-encode complete — review the candidate below', 'good');
			}
		} catch {
			toast('Re-encode finished — refresh to see the result', 'info');
		}
		void loadReencodeArtifacts();
		void refreshDetail();
	}

	function startEpisodeReencodeTracking(episodeId: number, jobId: string) {
		if (reencodeTracking[episodeId]) return;
		reencodeTracking[episodeId] = { progress: 0, stage: null, status: 'queued' };

		async function pollOnce() {
			try {
				const job = await getMediaJob(fetch, jobId);
				if (!reencodeTracking[episodeId]) return;
				reencodeTracking[episodeId].status = job.status;
				if (job.stage) reencodeTracking[episodeId].stage = job.stage;
				if (job.progress_total > 0) {
					const pct = (job.progress_done / job.progress_total) * 100;
					if (pct > reencodeTracking[episodeId].progress) {
						reencodeTracking[episodeId].progress = pct;
					}
				}
				if (job.status !== 'queued' && job.status !== 'running') {
					await finishEpisodeReencode(episodeId, jobId);
				}
			} catch {
				// transient — keep polling
			}
		}

		reencodeUnsubs[episodeId] = subscribe(
			`/api/media-jobs/${jobId}/events`,
			['message', 'done'],
			(type, data) => {
				if (type === 'done') {
					void finishEpisodeReencode(episodeId, jobId);
					return;
				}
				if (type === 'error') return;
				if (!reencodeTracking[episodeId]) return;
				const ev = data as { stage?: string; progress?: { percent?: number } | null };
				if (ev.stage) reencodeTracking[episodeId].stage = ev.stage;
				if (ev.progress?.percent != null)
					reencodeTracking[episodeId].progress = ev.progress.percent;
			}
		);
		void pollOnce();
		reencodePollTimers[episodeId] = setInterval(() => void pollOnce(), 2000);
	}

	function handleReencodeConfirmed(episodeId: number, jobId: string) {
		closeReencodePlan();
		toast('Re-encode started...', 'good');
		startEpisodeReencodeTracking(episodeId, jobId);
	}

	// Season/show batch reencode (BatchReencodeModal, generalized in C7).
	let reencodeModalOpen = $state(false);
	let reencodeModalSeason = $state<number | null>(null);
	let reencodeModalBusy = $state(false);

	function openReencodeModal(seasonNumber: number | null) {
		reencodeModalSeason = seasonNumber;
		reencodeModalOpen = true;
	}

	async function startTvBatchReencode(payload: {
		confidenceLevels: string[];
		settings: BatchReencodeSettings;
	}) {
		if (reencodeModalBusy) return;
		reencodeModalBusy = true;
		try {
			const result = await batchReencodeTv(fetch, seriesId, {
				season_number: reencodeModalSeason ?? undefined,
				confidence_levels: payload.confidenceLevels,
				settings: payload.settings
			});
			if (result.count > 0) {
				toast(`Queued ${result.count} episode re-encodes`, 'good');
			} else {
				toast('No eligible episodes matched the selected confidence filter', 'info');
			}
			if (result.skipped.length > 0) {
				toast(
					`Skipped ${result.skipped.length} episode${result.skipped.length === 1 ? '' : 's'}`,
					'info'
				);
			}
			rehydrateJob(result.parent_job_id);
			reencodeModalOpen = false;
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not queue re-encodes', 'bad');
		} finally {
			reencodeModalBusy = false;
		}
	}

	// Reencode artifacts (review + replace/restore/delete + bulk replace-ready).
	let reencodeArtifacts = $state<ReencodeArtifact[]>([]);
	let reencodeArtifactsLoading = $state(false);
	let reencodeArtifactBusy = $state<Record<number, boolean>>({});
	let replaceAllReadyBusy = $state<Record<string, boolean>>({});

	function artifactForEpisode(episodeId: number): ReencodeArtifact | undefined {
		return reencodeArtifacts.find((a) => a.episode_id === episodeId);
	}

	async function loadReencodeArtifacts() {
		reencodeArtifactsLoading = true;
		try {
			const res = await listReencodeArtifacts(fetch, { series_id: seriesId });
			reencodeArtifacts = res.items;
		} catch {
			// keep the stale list — this is a non-fatal background refresh
		} finally {
			reencodeArtifactsLoading = false;
		}
	}

	async function doReplaceArtifact(artifact: ReencodeArtifact) {
		if (reencodeArtifactBusy[artifact.id]) return;
		reencodeArtifactBusy[artifact.id] = true;
		try {
			await replaceOriginal(fetch, artifact.id);
			toast('Original replaced — re-encode applied', 'good');
			await loadReencodeArtifacts();
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Replace failed', 'bad');
		} finally {
			delete reencodeArtifactBusy[artifact.id];
		}
	}

	async function doRestoreArtifact(artifact: ReencodeArtifact) {
		if (reencodeArtifactBusy[artifact.id]) return;
		reencodeArtifactBusy[artifact.id] = true;
		try {
			await restoreOriginal(fetch, artifact.id);
			toast('Original restored', 'good');
			await loadReencodeArtifacts();
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Restore failed', 'bad');
		} finally {
			delete reencodeArtifactBusy[artifact.id];
		}
	}

	async function doDeleteArtifact(artifact: ReencodeArtifact) {
		if (reencodeArtifactBusy[artifact.id]) return;
		if (!confirm('Discard this re-encode candidate?')) return;
		reencodeArtifactBusy[artifact.id] = true;
		try {
			await deleteArtifact(fetch, artifact.id);
			toast('Candidate discarded', 'good');
			await loadReencodeArtifacts();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Discard failed', 'bad');
		} finally {
			delete reencodeArtifactBusy[artifact.id];
		}
	}

	function readyArtifactCount(seasonNumber: number | null): number {
		const scopedEpisodeIds = new Set(
			scopeEpisodes(seasonNumber).map((ep: LetterboxTvEpisode) => ep.episode_id)
		);
		return reencodeArtifacts.filter(
			(a) =>
				a.status === 'candidate_ready' && a.episode_id != null && scopedEpisodeIds.has(a.episode_id)
		).length;
	}

	async function doReplaceAllReady(seasonNumber: number | null) {
		const key = scopeKey(seasonNumber);
		if (replaceAllReadyBusy[key]) return;
		const readyCount = readyArtifactCount(seasonNumber);
		if (readyCount === 0) {
			toast('No ready candidates in this scope.', 'info');
			return;
		}
		if (
			!confirm(
				`Replace ${readyCount} original file${readyCount === 1 ? '' : 's'} with their re-encoded candidates?`
			)
		) {
			return;
		}
		replaceAllReadyBusy[key] = true;
		try {
			const res = await replaceReadyTvArtifacts(fetch, seriesId, {
				season_number: seasonNumber ?? undefined
			});
			toast(`Replaced ${res.replaced} original file${res.replaced === 1 ? '' : 's'}`, 'good');
			if (res.failed.length > 0) {
				toast(`${res.failed.length} replace${res.failed.length === 1 ? '' : 's'} failed`, 'bad');
			}
			await loadReencodeArtifacts();
			void refreshDetail();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Replace failed', 'bad');
		} finally {
			delete replaceAllReadyBusy[key];
		}
	}

	const SEASON_BUCKET_ORDER: LetterboxTvBucket[] = [
		'candidate',
		'tagged',
		'widescreen',
		'sampled_widescreen',
		'reencoded',
		'variable',
		'open_matte',
		'pillarbox',
		'error',
		'ineligible',
		'unanalyzed'
	];
	// Buckets that show no expand chevron and no episode dropdown
	const NO_EXPAND_BUCKETS = new Set(['open_matte', 'pillarbox', 'unanalyzed', 'ineligible']);
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
		>
			{#snippet action()}
				<div class="show-header-actions">
					<ConfidencePopover
						label="Apply"
						levels={confidenceFor(null)}
						count={candidateCount(null)}
						onToggleLevel={(level) => toggleConfidenceLevel(null, level)}
						onToggleAll={() => toggleConfidenceAll(null)}
						onConfirm={() => runScopeApply(null)}
					/>
					<button class="btn btn-outline btn-sm" onclick={() => runScopeRevert(null)}>
						Revert
					</button>
					<button class="btn btn-outline btn-sm" onclick={() => openReencodeModal(null)}>
						Reencode
					</button>
					{#if readyArtifactCount(null) > 0}
						<button
							class="btn btn-outline btn-sm"
							disabled={replaceAllReadyBusy['show']}
							onclick={() => doReplaceAllReady(null)}
						>
							Replace all ready ({readyArtifactCount(null)})
						</button>
					{/if}
				</div>
			{/snippet}
		</SectionHeader>

		{#if Object.keys(activeRuns).length > 0}
			<div class="active-runs">
				{#each Object.entries(activeRuns) as [jobId, run] (jobId)}
					<div class="active-run">
						<div class="active-run-head">
							<span class="active-run-label">
								{displayJobLabel({ type: run.type ?? 'unknown', label: run.label })}
							</span>
							<span class="active-run-status mono"
								>{run.cancelling ? 'cancelling' : (run.stage ?? run.status)}</span
							>
							<button
								class="active-run-cancel"
								type="button"
								disabled={run.cancelling}
								title={run.cancelling ? 'Cancellation requested' : 'Cancel job'}
								onclick={() => cancelActiveRun(jobId)}
							>
								✕
							</button>
						</div>
						<ProgressBar value={run.progress} tone="gold" height={5} />
					</div>
				{/each}
			</div>
		{/if}

		<!-- Heatmap centerpiece -->
		<div class="section-container">
			<h3 class="section-title">Episode Compliance Heatmap</h3>
			<EpisodeHeatmap
				seasons={detail.seasons}
				mode="letterbox"
				onCellClick={focusEpisodeFromHeatmap}
			/>
		</div>

		<div class="section-container">
			<h3 class="section-title">Episodes</h3>
			<div class="episodes-table-card glass-panel">
				<div class="table-filters">
					<input
						type="search"
						placeholder="Search episode code or title..."
						class="search-input"
						bind:value={searchQuery}
					/>

					<div class="filter-group">
						<label for="filter-verdict">Verdict</label>
						<select id="filter-verdict" class="filter-select" bind:value={selectedVerdict}>
							<option value="all">All Verdicts</option>
							<option value="widescreen">Widescreen</option>
							<option value="sampled_widescreen">Sampled Widescreen</option>
							<option value="candidate">Letterboxed</option>
							<option value="tagged">Tagged</option>
							<option value="reencoded">Reencoded</option>
							<option value="variable">Variable</option>
							<option value="open_matte">Open Matte</option>
							<option value="pillarbox">Pillarbox</option>
							<option value="error">Error</option>
							<option value="unanalyzed">Unanalyzed</option>
						</select>
					</div>
				</div>

				{#if seasonViews.length === 0}
					<div class="empty-state">No episodes found.</div>
				{:else}
					<div class="season-panels">
						{#each seasonViews as seasonView (seasonView.season.season_number)}
							{@const season = seasonView.season}
							{@const state =
								season.rollup.verdict === 'ok'
									? null
									: LETTERBOX_TV_VERDICT_META[season.rollup.verdict]}
							<div class="season-panel glass-panel" class:specials={season.season_number === 0}>
								<div class="season-header">
									<div class="season-header-main">
										<button
											type="button"
											class="season-collapse"
											class:open={!seasonView.collapsed}
											aria-expanded={!seasonView.collapsed}
											title={seasonView.collapsed ? 'Expand season' : 'Collapse season'}
											onclick={() => toggleSeasonCollapse(season.season_number)}
										>
											▶
										</button>

										<div class="season-title-block">
											<div class="season-summary">
												<span class="season-number">
													{season.season_number === 0
														? 'Specials'
														: `Season ${season.season_number}`}
												</span>
												{#if state}
													<span class="verdict-chip" style={`--c: var(--${state.tone})`}>
														{state.label}
													</span>
												{/if}
												{#each season.rollup.content_types as contentType (contentType.type)}
													{@const content = LETTERBOX_TV_CONTENT_META[contentType.type]}
													<span
														class="verdict-chip"
														style={`--c: var(--${content.tone})`}
														title={`${content.label}: ${contentType.count}`}
													>
														{content.label}
													</span>
												{/each}
												<UniformityChip uniformity={season.rollup.uniformity} />
												<span class="ep-count font-mono">
													{season.rollup.episodes_total} episodes
												</span>
											</div>

											<div class="bucket-chip-row">
												{#each SEASON_BUCKET_ORDER as bucket (bucket)}
													{@const bucketCount = season.rollup.bucket_counts[bucket] ?? 0}
													{#if bucketCount > 0}
														{@const bucketMeta = LETTERBOX_TV_BUCKET_META[bucket]}
														<span class="bucket-chip" style={`--chip: var(--${bucketMeta.tone})`}>
															<span class="bucket-chip-label">{bucketMeta.label}</span>
															<span class="bucket-chip-count">{bucketCount}</span>
														</span>
													{/if}
												{/each}
											</div>
										</div>
									</div>

									<div class="season-header-actions">
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
											{#if (season.rollup.bucket_counts.open_matte ?? 0) + (season.rollup.bucket_counts.pillarbox ?? 0) > 0}
												<label class="checkbox-container">
													<input
														type="checkbox"
														bind:checked={includeOpenMatte[season.season_number]}
													/>
													<span class="checkmark"></span>
													Include OM/PB
												</label>
											{/if}
										</div>

										<button
											class="btn btn-primary btn-sm"
											onclick={() => runSeasonDetect(season.season_number)}
										>
											Detect
										</button>
										<ConfidencePopover
											label="Apply"
											levels={confidenceFor(season.season_number)}
											count={candidateCount(season.season_number)}
											onToggleLevel={(level) => toggleConfidenceLevel(season.season_number, level)}
											onToggleAll={() => toggleConfidenceAll(season.season_number)}
											onConfirm={() => runScopeApply(season.season_number)}
										/>
										<button
											class="btn btn-outline btn-sm"
											onclick={() => runScopeRevert(season.season_number)}
										>
											Revert
										</button>
										<button
											class="btn btn-outline btn-sm"
											onclick={() => openReencodeModal(season.season_number)}
										>
											Reencode
										</button>
										{#if readyArtifactCount(season.season_number) > 0}
											<button
												class="btn btn-outline btn-sm"
												disabled={replaceAllReadyBusy[scopeKey(season.season_number)]}
												onclick={() => doReplaceAllReady(season.season_number)}
											>
												Replace all ready ({readyArtifactCount(season.season_number)})
											</button>
										{/if}
									</div>
								</div>

								{#if filtersActive && !seasonView.hasMatches}
									<div class="season-empty-note">No matches for the current filters.</div>
								{:else if !seasonView.collapsed}
									<div class="season-table-shell">
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
													{#each seasonView.filteredEpisodes as ep (ep.episode_id)}
														{@const verd = LETTERBOX_TV_BUCKET_META[ep.bucket]}
														{@const expanded = expandedEpisodeIds.has(ep.episode_id)}
														<tr id={`episode-${ep.season_number}-${ep.episode_id}`}>
															<td class="expand-col">
																{#if !NO_EXPAND_BUCKETS.has(ep.bucket)}
																	<button
																		class="expand-toggle"
																		class:open={expanded}
																		title={expanded ? 'Hide frame preview' : 'Show frame preview'}
																		onclick={() => toggleEpisodeExpand(ep.episode_id)}
																	>
																		▶
																	</button>
																{/if}
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
															<td>
																{#if ep.confidence && ep.confidence !== 'none'}
																	<span
																		class="confidence-chip"
																		style={`--chip:${confidenceTone(ep.confidence)}`}
																	>
																		{ep.confidence}
																	</span>
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
																	{#if ep.bucket === 'open_matte' || ep.bucket === 'pillarbox'}
																		<span class="aspect-label-inline font-mono"
																			>{ep.aspect_label || '—'}</span
																		>
																		<button
																			class="btn btn-outline btn-xs"
																			title="Scan this episode anyway (force + include OM/PB)"
																			onclick={() => runScanAnyway(ep.episode_id)}
																		>
																			Scan anyway
																		</button>
																	{:else}
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

																		{#if reencodeTracking[ep.episode_id]}
																			<span
																				class="btn btn-outline btn-xs reencode-progress"
																				title="Re-encoding…"
																			>
																				Encoding {Math.round(
																					reencodeTracking[ep.episode_id].progress
																				)}%
																			</span>
																		{:else}
																			<button
																				class="btn btn-outline btn-xs"
																				title="Plan a permanent re-encode"
																				onclick={() => openReencodePlan(ep.episode_id)}
																			>
																				Reencode
																			</button>
																		{/if}
																	{/if}
																</div>
															</td>
														</tr>
														{#if expanded}
															<tr class="expand-row">
																<td colspan="8">
																	{#if loadingEpisodeIds.has(ep.episode_id)}
																		<div class="expand-note">Loading preview…</div>
																	{:else if episodeDetailErrors.has(ep.episode_id)}
																		<div class="expand-note">
																			{episodeDetailErrors.get(ep.episode_id)}
																		</div>
																	{:else}
																		{@const epDetail = episodeDetails.get(ep.episode_id)}
																		{#if !epDetail}
																			<div class="expand-note">No preview available.</div>
																		{:else}
																			{@const previewMinute =
																				episodePreviewMinutes.get(ep.episode_id) ??
																				epDetail.preview_minute ??
																				5}
																			{@const exactPreview = episodePreviewMinutes.has(
																				ep.episode_id
																			)}
																			{@const cropTop =
																				epDetail.recommended_crop_top ??
																				epDetail.applied_crop_top ??
																				0}
																			{@const cropBottom =
																				epDetail.recommended_crop_bottom ??
																				epDetail.applied_crop_bottom ??
																				0}
																			{@const afterHeight =
																				epDetail.source_height != null
																					? Math.max(
																							epDetail.source_height - cropTop - cropBottom,
																							0
																						)
																					: null}
																			{@const beforeUrl = exactPreview
																				? episodePreviewUrl(
																						ep.episode_id,
																						'before',
																						previewMinute,
																						true
																					)
																				: (epDetail.preview_urls?.before ??
																					episodePreviewUrl(
																						ep.episode_id,
																						'before',
																						previewMinute
																					))}
																			{@const afterUrl = exactPreview
																				? episodePreviewUrl(
																						ep.episode_id,
																						'after',
																						previewMinute,
																						true
																					)
																				: (epDetail.preview_urls?.after ??
																					episodePreviewUrl(ep.episode_id, 'after', previewMinute))}
																			{@const confidenceExpanded =
																				expandedConfidenceEpisodes.get(ep.episode_id) ?? false}
																			{@const epArtifact = artifactForEpisode(ep.episode_id)}
																			<div class="expand-content">
																				{#if PAIR_PREVIEW_BUCKETS.has(ep.bucket)}
																					<div class="expand-frames pair">
																						<LetterboxFrame
																							src={beforeUrl}
																							alt="before crop"
																							placeholder="No preview available"
																						/>
																						<LetterboxFrame
																							src={afterUrl}
																							alt="after crop"
																							tone="after"
																							placeholder="No preview available"
																						/>
																					</div>
																				{:else}
																					<div class="expand-frames single">
																						<LetterboxFrame
																							src={beforeUrl}
																							alt="episode frame"
																							placeholder="No preview available"
																						/>
																					</div>
																				{/if}

																				<div class="expand-meta">
																					<dl class="meta-grid">
																						{#if PAIR_PREVIEW_BUCKETS.has(ep.bucket)}
																							{#if epDetail.source_width && epDetail.source_height}
																								<dt>Before dims</dt>
																								<dd class="mono">
																									{epDetail.source_width}×{epDetail.source_height}
																								</dd>
																								<dt>Before AR</dt>
																								<dd class="mono">
																									{aspectRatio(
																										epDetail.source_width,
																										epDetail.source_height
																									)}
																								</dd>
																							{/if}
																							{#if epDetail.source_width && afterHeight}
																								<dt>After dims</dt>
																								<dd class="mono">
																									{epDetail.source_width}×{afterHeight}
																								</dd>
																								<dt>After AR</dt>
																								<dd class="mono">
																									{aspectRatio(epDetail.source_width, afterHeight)}
																								</dd>
																							{/if}
																							<dt>Crop T / B</dt>
																							<dd class="mono">{cropTop}px / {cropBottom}px</dd>
																						{:else}
																							{#if epDetail.source_width && epDetail.source_height}
																								<dt>Dimensions</dt>
																								<dd class="mono">
																									{epDetail.source_width}×{epDetail.source_height}
																								</dd>
																								<dt>Aspect ratio</dt>
																								<dd class="mono">
																									{aspectRatio(
																										epDetail.source_width,
																										epDetail.source_height
																									)}
																								</dd>
																							{/if}
																							{#if cropTop !== 0 || cropBottom !== 0}
																								<dt>Crop T / B</dt>
																								<dd class="mono">{cropTop}px / {cropBottom}px</dd>
																							{/if}
																						{/if}
																						<dt>Confidence</dt>
																						<dd>
																							{#if epDetail.confidence && epDetail.confidence !== 'none'}
																								<button
																									class="confidence-toggle"
																									type="button"
																									onclick={() =>
																										toggleConfidenceExpand(ep.episode_id)}
																								>
																									<span
																										class="confidence-chip"
																										style={`--chip:${confidenceTone(epDetail.confidence)}`}
																									>
																										{epDetail.confidence}
																									</span>
																									<span class="caret">
																										{confidenceExpanded ? '▲' : '▼'}
																									</span>
																								</button>
																							{:else}
																								<span class="meta-muted">—</span>
																							{/if}
																						</dd>
																					</dl>

																					<div class="meta-inline">
																						{#if epDetail.detect_method}
																							<span class="method-tag mono"
																								>{epDetail.detect_method}</span
																							>
																						{/if}
																						<span class="preview-chip mono">
																							Preview {previewMinute}m{#if exactPreview}
																								· exact{/if}
																						</span>
																					</div>
																					{#if epArtifact}
																						<div class="artifact-panel">
																							<div class="artifact-head">
																								<span
																									class="artifact-status"
																									style={`--c: var(--${epArtifact.status === 'replaced' ? 'good' : epArtifact.status === 'candidate_ready' || epArtifact.status === 'kept' ? 'info' : 'muted'})`}
																								>
																									{epArtifact.status}
																								</span>
																								<span class="mono artifact-sizes">
																									{bytesH(epArtifact.candidate_size_bytes)}
																									{#if epArtifact.original_size_bytes && epArtifact.candidate_size_bytes}
																										<span class="artifact-saved">
																											(was {bytesH(epArtifact.original_size_bytes)} ·
																											{Math.round(
																												(1 -
																													epArtifact.candidate_size_bytes /
																														epArtifact.original_size_bytes) *
																													100
																											)}% smaller)
																										</span>
																									{/if}
																								</span>
																							</div>
																							<div class="artifact-actions">
																								{#if epArtifact.status === 'candidate_ready' || epArtifact.status === 'kept'}
																									<button
																										class="btn btn-outline btn-xs"
																										disabled={reencodeArtifactBusy[epArtifact.id]}
																										onclick={() => doReplaceArtifact(epArtifact)}
																									>
																										Replace original
																									</button>
																									<button
																										class="btn btn-outline btn-xs btn-bad"
																										disabled={reencodeArtifactBusy[epArtifact.id]}
																										onclick={() => doDeleteArtifact(epArtifact)}
																									>
																										Delete
																									</button>
																								{:else if epArtifact.status === 'replaced'}
																									<button
																										class="btn btn-outline btn-xs"
																										disabled={reencodeArtifactBusy[epArtifact.id]}
																										onclick={() => doRestoreArtifact(epArtifact)}
																									>
																										Restore original
																									</button>
																								{/if}
																							</div>
																						</div>
																					{/if}
																					{#if epDetail.variable_ar_note}
																						<div class="detail-note">
																							{epDetail.variable_ar_note}
																						</div>
																					{/if}
																					{#if confidenceExpanded}
																						<div class="sample-gallery">
																							{#if epDetail.samples && epDetail.samples.length > 0}
																								{@const colorMap = pairColorMap(epDetail.samples)}
																								{@const { agreeCount: nAgree, totalOk } =
																									agreeCount(epDetail.samples)}
																								<div class="ce-summary">
																									{#if nAgree === totalOk && totalOk > 0}
																										All {totalOk} agree
																									{:else}
																										{nAgree}/{epDetail.samples.length} agree
																									{/if}
																									<span class="ce-hint"
																										>· click to preview that frame</span
																									>
																								</div>
																								{#each epDetail.samples as sample (sample.minute)}
																									{@const key = sample.ok ? pairKey(sample) : null}
																									{@const dotColor =
																										key != null
																											? (colorMap.get(key) ?? 'var(--faint)')
																											: 'var(--bad)'}
																									{#if sample.ok}
																										<button
																											class="sample-row"
																											class:active={previewMinute ===
																												sample.minute && exactPreview}
																											type="button"
																											onclick={() =>
																												setEpisodePreviewMinute(
																													ep.episode_id,
																													sample.minute
																												)}
																										>
																											<span class="mono sample-minute">
																												{sample.minute}m
																											</span>
																											<span class="mono sample-bars">
																												{sample.top_bar ?? '?'} / {sample.bottom_bar ??
																													'?'} px
																											</span>
																											<span class="sample-meta">
																												{sample.backend ?? 'cpu'} · {sample.elapsed_ms ??
																													'?'}ms
																											</span>
																											<span class="ce-dot" style="color:{dotColor}"
																												>●</span
																											>
																										</button>
																									{:else}
																										<div class="sample-row sample-row-error">
																											<span class="mono sample-minute">
																												{sample.minute}m
																											</span>
																											<span class="sample-error">
																												{sample.error ?? 'sample failed'}
																											</span>
																											<span class="ce-dot" style="color:var(--bad)"
																												>✕</span
																											>
																										</div>
																									{/if}
																								{/each}
																							{:else}
																								<div class="expand-note">
																									No sample data available.
																								</div>
																							{/if}
																						</div>
																					{/if}
																				</div>
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
									</div>
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>

		<div class="section-container">
			<h3 class="section-title">Reencode Artifacts</h3>
			<div class="episodes-table-card glass-panel">
				{#if reencodeArtifactsLoading && reencodeArtifacts.length === 0}
					<div class="empty-state">Loading artifacts…</div>
				{:else if reencodeArtifacts.length === 0}
					<div class="empty-state">No re-encode candidates yet.</div>
				{:else}
					<div class="table-scroll">
						<table class="episodes-table">
							<thead>
								<tr>
									<th>Episode</th>
									<th>Status</th>
									<th>Original</th>
									<th>Candidate</th>
									<th>Saved</th>
									<th class="actions-col">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each reencodeArtifacts as a (a.id)}
									<tr>
										<td class="font-mono">{a.episode_code ?? `#${a.episode_id}`}</td>
										<td>
											<span
												class="artifact-status"
												style={`--c: var(--${a.status === 'replaced' ? 'good' : a.status === 'candidate_ready' || a.status === 'kept' ? 'info' : 'muted'})`}
											>
												{a.status}
											</span>
										</td>
										<td class="font-mono">{bytesH(a.original_size_bytes)}</td>
										<td class="font-mono">{bytesH(a.candidate_size_bytes)}</td>
										<td class="font-mono">
											{#if a.original_size_bytes && a.candidate_size_bytes}
												{Math.round((1 - a.candidate_size_bytes / a.original_size_bytes) * 100)}%
											{:else}
												—
											{/if}
										</td>
										<td class="actions-col">
											<div class="action-buttons-group">
												{#if a.status === 'candidate_ready' || a.status === 'kept'}
													<button
														class="btn btn-outline btn-xs"
														disabled={reencodeArtifactBusy[a.id]}
														onclick={() => doReplaceArtifact(a)}
													>
														Replace original
													</button>
													<button
														class="btn btn-outline btn-xs btn-bad"
														disabled={reencodeArtifactBusy[a.id]}
														onclick={() => doDeleteArtifact(a)}
													>
														Delete
													</button>
												{:else if a.status === 'replaced'}
													<button
														class="btn btn-outline btn-xs"
														disabled={reencodeArtifactBusy[a.id]}
														onclick={() => doRestoreArtifact(a)}
													>
														Restore original
													</button>
												{/if}
											</div>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</div>
		</div>
	{/if}
</section>

<BatchReencodeModal
	open={reencodeModalOpen}
	items={scopeEpisodes(reencodeModalSeason).filter((ep) => ep.bucket === 'candidate')}
	getId={(ep: LetterboxTvEpisode) => ep.episode_id}
	subtitle={reencodeModalSeason == null
		? 'Queue a durable re-encode for the entire show.'
		: `Queue a durable re-encode for Season ${reencodeModalSeason}.`}
	busy={reencodeModalBusy}
	onStart={startTvBatchReencode}
	onClose={() => {
		if (!reencodeModalBusy) reencodeModalOpen = false;
	}}
/>

{#if reencodePlanEpisodeId != null}
	<ReencodePlanModal
		{seriesId}
		episodeId={reencodePlanEpisodeId}
		onClose={closeReencodePlan}
		onConfirmed={(jobId) => handleReencodeConfirmed(reencodePlanEpisodeId as number, jobId)}
	/>
{/if}

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
	.active-runs {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin-bottom: 4px;
	}
	.active-run {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: 10px;
		background: var(--ink3);
	}
	.active-run-head {
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 12px;
	}
	.active-run-label {
		font-weight: 600;
		color: var(--text);
	}
	.active-run-status {
		color: var(--muted);
		text-transform: capitalize;
	}
	.active-run-cancel {
		margin-left: auto;
		border: none;
		background: transparent;
		color: var(--muted);
		cursor: pointer;
		font-size: 12px;
		padding: 2px 6px;
		border-radius: 6px;
	}
	.active-run-cancel:hover {
		background: var(--ink2);
		color: var(--bad);
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

	.season-panels {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.season-panel {
		padding: 0;
		overflow: hidden;
	}
	.season-panel.specials {
		opacity: 0.75;
		border-left: 3px dashed var(--line);
	}
	.season-header {
		display: flex;
		justify-content: space-between;
		gap: 16px;
		padding: 16px 18px;
		border-bottom: 1px solid var(--line);
	}
	.season-header-main {
		display: flex;
		align-items: flex-start;
		gap: 12px;
		min-width: 0;
		flex: 1;
	}
	.season-title-block {
		display: flex;
		flex-direction: column;
		gap: 10px;
		min-width: 0;
	}
	.season-summary {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 12px;
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
	.bucket-chip-row {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.bucket-chip {
		--chip: var(--muted);
		display: inline-flex;
		align-items: center;
		gap: 8px;
		padding: 4px 10px;
		border-radius: 999px;
		border: 1px solid color-mix(in srgb, var(--chip) 28%, transparent);
		background: color-mix(in srgb, var(--chip) 10%, transparent);
		font-size: 11px;
		color: var(--text);
	}
	.bucket-chip-label {
		color: var(--muted);
	}
	.bucket-chip-count {
		font-weight: 700;
		color: var(--chip);
	}
	.season-header-actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 12px;
		justify-content: flex-end;
	}
	.show-header-actions {
		display: flex;
		align-items: center;
		gap: 8px;
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
	.season-table-shell {
		padding: 0 18px 18px;
	}
	.season-empty-note {
		padding: 0 18px 18px;
		font-size: 12px;
		color: var(--muted);
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
	.confidence-chip {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 2px 8px;
		border-radius: 999px;
		border: 1px solid color-mix(in srgb, var(--chip) 35%, transparent);
		background: color-mix(in srgb, var(--chip) 14%, transparent);
		color: var(--chip);
		font-size: 10px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.04em;
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
	.aspect-label-inline {
		font-size: 10px;
		color: var(--muted);
		align-self: center;
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
	.expand-content {
		display: grid;
		grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.9fr);
		gap: 16px;
		align-items: start;
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
	.expand-meta {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.meta-grid {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 8px 12px;
		margin: 0;
	}
	.meta-grid dt {
		font-size: 11px;
		font-weight: 700;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.meta-grid dd {
		margin: 0;
		color: var(--text);
	}
	.meta-muted {
		color: var(--muted);
	}
	.confidence-toggle {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		padding: 0;
		border: none;
		background: transparent;
		cursor: pointer;
	}
	.caret {
		color: var(--muted);
		font-size: 11px;
	}
	.meta-inline {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.artifact-panel {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: 10px;
		background: var(--ink3);
	}
	.artifact-head {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 10px;
		font-size: 12px;
	}
	.artifact-status {
		font-weight: 700;
		text-transform: uppercase;
		font-size: 10px;
		letter-spacing: 0.05em;
		color: var(--c);
	}
	.artifact-sizes {
		color: var(--text);
	}
	.artifact-saved {
		color: var(--muted);
		margin-left: 4px;
	}
	.artifact-actions {
		display: flex;
		gap: 8px;
	}
	.reencode-progress {
		cursor: default;
		color: var(--muted);
	}
	.method-tag,
	.preview-chip {
		display: inline-flex;
		align-items: center;
		padding: 4px 8px;
		border-radius: 999px;
		border: 1px solid var(--line);
		background: var(--ink3);
		font-size: 11px;
		color: var(--muted);
	}
	.detail-note {
		padding: 10px 12px;
		border-radius: var(--radius-sm);
		border: 1px solid color-mix(in srgb, var(--info) 25%, transparent);
		background: color-mix(in srgb, var(--info) 10%, transparent);
		color: var(--text);
		font-size: 12px;
	}
	.sample-gallery {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.sample-row {
		display: grid;
		grid-template-columns: auto auto 1fr;
		gap: 10px;
		align-items: center;
		padding: 9px 10px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--ink3);
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}
	.sample-row:hover {
		border-color: var(--line2);
		background: color-mix(in srgb, var(--ink3) 86%, white);
	}
	.sample-row.active {
		border-color: color-mix(in srgb, var(--gold) 55%, var(--line));
		background: color-mix(in srgb, var(--gold) 12%, var(--ink3));
	}
	.sample-row-error {
		cursor: default;
		grid-template-columns: auto 1fr;
	}
	.sample-minute,
	.sample-bars {
		font-size: 11px;
	}
	.sample-meta {
		color: var(--muted);
		font-size: 11px;
	}
	.sample-error {
		color: var(--bad);
		font-size: 11px;
	}
	.ce-summary {
		font-size: 11px;
		color: var(--muted);
		padding: 4px 2px;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.ce-hint {
		color: var(--faint2);
		font-size: 11px;
	}
	.ce-dot {
		font-size: 12px;
		line-height: 1;
		margin-left: auto;
	}

	.season-collapse {
		flex: 0 0 auto;
		background: transparent;
		border: none;
		color: var(--muted);
		cursor: pointer;
		font-size: 12px;
		padding: 4px;
		transition: transform 0.15s ease;
	}
	.season-collapse.open {
		transform: rotate(90deg);
		color: var(--text);
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
	.empty-state {
		padding: 32px;
		text-align: center;
		color: var(--muted);
	}

	@media (max-width: 980px) {
		.season-header {
			flex-direction: column;
			align-items: stretch;
		}
		.season-header-actions {
			justify-content: flex-start;
		}
		.expand-content {
			grid-template-columns: 1fr;
		}
	}
</style>
