<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { page } from '$app/state';
	import { getMovie } from '$lib/api/library';
	import { inspectMovie, getInventory, scanSubtitles, createPlan, extractTrack, previewTrack } from '$lib/api/subtitles';
	import { getGenerators, submitMovieGeneration } from '$lib/api/subtitle-generators';
	import { getSettings, putSettings } from '$lib/api/system';
	import { confirmJob, getMediaJob } from '$lib/api/media-jobs';
	import type { MediaJob } from '$lib/api/types';
	import { trackJob } from '$lib/jobs';
	import { toast } from '$lib/toast';
	import { bytesH } from '$lib/display';
	import TabBar from '$lib/components/TabBar.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';

	let { data } = $props();

	// Page State loaded from loader
	let movie = $state(data.movie);
	let inspect = $state(data.inspect);
	let settings = $state(data.settings);
	let error = $derived(data.error);

	// Tabs Configuration
	const tabs = [
		{ id: 'tracks', label: 'Tracks & Operations' },
		{ id: 'generation', label: 'AI Subtitle Generation' }
	];
	let activeTab = $state('tracks');

	// Inventory & Tracks State
	let inventory = $derived(inspect?.inventory || ({} as any));
	let tracks = $derived(inventory?.tracks || []);
	let audioStreams = $derived(inventory?.audio_streams || []);
	let capabilities = $derived(inventory?.capabilities || {});
	let coverage = $derived(inventory?.coverage || {});

	// Selection State
	let selectedTrackIds = $state<string[]>([]);
	let selectedAudioIndices = $state<number[]>([]);
	let deleteAfterExtract = $state(false);
	let deleteAfterEmbed = $state(false);

	// Batch Delete States
	let batchDeleteOpen = $state(false);
	let batchDeleteTarget = $state<'subtitles' | 'audio' | 'both'>('both');
	let batchDeleteMode = $state<'except' | 'only'>('except');
	let batchLanguages = $state<string[]>([]);

	// Language helper display
	const langNames = new Intl.DisplayNames(['en'], { type: 'language' });
	function getLanguageName(tag: string) {
		if (!tag || tag === 'und') return 'Undetermined';
		try {
			return langNames.of(tag) || tag;
		} catch {
			return tag;
		}
	}

	// Unique languages present in current subtitle tracks
	let uniqueLanguages = $derived.by(() => {
		const langs = new Set<string>();
		tracks.forEach((t: any) => {
			if (t.language_tag) {
				langs.add(t.language_tag.toLowerCase());
			}
		});
		return Array.from(langs).sort();
	});

	// Unique languages present in audio tracks
	let audioLanguages = $derived.by(() => {
		const langs = new Set<string>();
		audioStreams.forEach((stream: any) => {
			if (stream.language_tag) {
				langs.add(stream.language_tag.toLowerCase());
			}
		});
		return Array.from(langs).sort();
	});

	// Union or subset of unique languages for batch delete depending on target
	let uniqueLanguagesForBatch = $derived.by(() => {
		if (batchDeleteTarget === 'subtitles') {
			return uniqueLanguages;
		} else if (batchDeleteTarget === 'audio') {
			return audioLanguages;
		} else {
			const union = new Set([...uniqueLanguages, ...audioLanguages]);
			return Array.from(union).sort();
		}
	});

	// Missing preferred languages calculation
	let preferredLangs = $derived(settings?.subtitles?.preferred_languages || ['en']);
	let missingPreferredAudio = $derived.by(() => {
		const audioLangs = new Set((coverage?.audio_languages || []).map((l: string) => l.toLowerCase()));
		const preferred = preferredLangs.map((l: string) => l.toLowerCase());
		return preferred.filter((l: string) => !audioLangs.has(l));
	});
	let audioStatus = $derived(missingPreferredAudio.length > 0 ? 'gap' : 'ok');

	// Track Selection Details
	let selectedTracks = $derived(
		tracks.filter((t: any) => selectedTrackIds.includes(t.id))
	);
	let singleSelectedTrack = $derived(
		selectedTracks.length === 1 ? selectedTracks[0] : null
	);

	// Subgen Connection & Status
	let testing = $state(false);
	let testResult = $state<{ success: boolean; message: string } | null>(null);
	let subgenUrl = $state('');
	let subgenProfileName = $state('Subgen');
	let subgenModelLabel = $state('unknown');
	let subgenMode = $state('transcribe');
	let subgenLocalPathPrefix = $state('');
	let subgenRemotePathPrefix = $state('');
	let subgenCallbackToken = $state('');
	let subgenSettingsInitialized = $state(false);

	// Subgen Generation Form
	const WHISPER_LANGUAGES = [
		{ code: '', label: 'Auto-Detect' },
		{ code: 'en', label: 'English' },
		{ code: 'es', label: 'Spanish' },
		{ code: 'fr', label: 'French' },
		{ code: 'de', label: 'German' },
		{ code: 'ja', label: 'Japanese' },
		{ code: 'it', label: 'Italian' },
		{ code: 'pt', label: 'Portuguese' },
		{ code: 'ru', label: 'Russian' },
		{ code: 'zh', label: 'Chinese' },
		{ code: 'ko', label: 'Korean' }
	];
	let audioLangHint = $state('');
	let subgenOutputTarget = $state<'external' | 'embedded'>('external');

	// Job Running / SSE Progress Tracking
	let runningJobId = $state<string | null>(null);
	let progressPercent = $state(0);
	let progressStage = $state('');
	let progressMessage = $state('');
	let jobLog = $state<string[]>([]);
	let busy = $state(false);

	// Persist the active job id per-movie so the bar survives a refresh or
	// a navigate-away-and-back (the root layout remounts this component on
	// every route change). Mirrors routes/pipeline + routes/letterbox.
	const SUBTITLE_JOB_KEY = `marquee:subtitles:activeJob:${page.params.id}`;
	let stopTracking: (() => void) | null = null;

	function storeJobId(id: string | null) {
		if (!browser) return;
		if (id) localStorage.setItem(SUBTITLE_JOB_KEY, id);
		else localStorage.removeItem(SUBTITLE_JOB_KEY);
	}

	onDestroy(() => {
		stopTracking?.();
		stopTracking = null;
	});

	// Initialize override inputs from loaded settings
	$effect(() => {
		if (settings && !subgenSettingsInitialized) {
			const subgen = settings.integrations?.subgen || {};
			subgenUrl = subgen.url ?? '';
			subgenProfileName = subgen.profile_name ?? 'Subgen';
			subgenModelLabel = subgen.model_label ?? 'unknown';
			subgenMode = subgen.mode ?? 'transcribe';
			subgenLocalPathPrefix = subgen.local_path_prefix ?? '';
			subgenRemotePathPrefix = subgen.remote_path_prefix ?? '';
			subgenSettingsInitialized = true;
		}
	});

	// Helper to refresh page inventory
	async function refreshInventory() {
		if (!movie) return null;
		try {
			const res = await inspectMovie(fetch, movie.id);
			inspect = res;
			selectedTrackIds = [];
			selectedAudioIndices = [];
			return res;
		} catch (e: any) {
			toast(e.message || 'Failed to refresh tracks inventory', 'bad');
			return null;
		}
	}

	// Dynamic translation preview
	let testPath = $derived(movie?.media_file_path || '');
	let translatedPath = $derived.by(() => {
		if (subgenLocalPathPrefix && testPath.startsWith(subgenLocalPathPrefix)) {
			return testPath.replace(subgenLocalPathPrefix, subgenRemotePathPrefix || '');
		}
		return testPath || 'Empty';
	});

	// Trigger manual scan of file
	async function handleRescan() {
		if (!movie || !movie.media_file_id) return;
		busy = true;
		try {
			await scanSubtitles(fetch, movie.media_file_id);
			toast('Subtitles scanned successfully!', 'good');
			await refreshInventory();
		} catch (e: any) {
			toast(e.message || 'Scan failed', 'bad');
		} finally {
			busy = false;
		}
	}

	// Test Subgen Connection
	async function testConnection() {
		testing = true;
		testResult = null;
		try {
			const res = await getGenerators(fetch);
			const subgen = res.generators.find(g => g.type === 'subgen');
			if (subgen && subgen.online) {
				testResult = {
					success: true,
					message: `Connection successful! Subgen v${subgen.version || 'unknown'} online. Model: ${subgen.model || 'unknown'} (${subgen.device || 'CPU'})`
				};
				toast('Subgen connection test successful!', 'good');
			} else {
				testResult = {
					success: false,
					message: 'Connection failed: Subgen is offline or not configured'
				};
				toast('Subgen connection test failed', 'bad');
			}
		} catch (e: any) {
			testResult = {
				success: false,
				message: `Connection error: ${e.message || 'Unknown error'}`
			};
			toast('Subgen connection test failed', 'bad');
		} finally {
			testing = false;
		}
	}

	// Mutate/Save Subgen settings override before running jobs
	async function saveSettingsOverride() {
		try {
			const payload = {
				subtitles: {}, // Keep default subtitles settings
				subgen: {
					url: subgenUrl || null,
					profile_name: subgenProfileName,
					model_label: subgenModelLabel,
					mode: subgenMode,
					local_path_prefix: subgenLocalPathPrefix || null,
					remote_path_prefix: subgenRemotePathPrefix || null,
					callback_token: subgenCallbackToken || null
				}
			};
			const res = await putSettings(fetch, payload);
			settings = res.settings;
			return true;
		} catch (e: any) {
			toast(`Failed to update Subgen settings: ${e.message}`, 'bad');
			return false;
		}
	}

	// Terminal MediaJob statuses (mirrors the backend's `terminal` set in
	// media_jobs.py's SSE endpoint).
	const TERMINAL_STATUSES = ['succeeded', 'completed', 'failed', 'cancelled', 'interrupted'];

	/** Resume/attach the shared poll-plus-SSE tracker for a job whose initial
	 *  progress state has already been seeded by the caller. Always goes
	 *  through `trackJob()` so this bar gets the same "seed immediately" /
	 *  "either SSE or poll can finalize" guarantees as the letterbox and
	 *  poster-pipeline bars. */
	function attachTracking(jobId: string, onCompleteCallback?: (freshInspect: any) => void | Promise<void>) {
		storeJobId(jobId);
		stopTracking?.();
		stopTracking = trackJob<MediaJob>(
			fetch,
			jobId,
			{
				onProgress: ({ detail }) => {
					if (typeof detail?.stage === 'string') progressStage = detail.stage;
					if (typeof detail?.percent === 'number') progressPercent = detail.percent;
					if (typeof detail?.message === 'string') progressMessage = detail.message;
					jobLog = [...jobLog, `[${progressStage || 'info'}] ${progressMessage || ''}`];
				},
				onDone: async (job) => {
					stopTracking = null;
					runningJobId = null;
					busy = false;
					progressPercent = 100;
					storeJobId(null);
					if (job.status === 'succeeded' || job.status === 'completed') {
						toast('Subtitles operation completed successfully!', 'good');
						const freshInspect = await refreshInventory();
						if (onCompleteCallback) await onCompleteCallback(freshInspect);
					} else {
						toast(`Operation failed: ${(job.error as any)?.error || job.error || 'Unknown error'}`, 'bad');
						await refreshInventory();
					}
				}
			},
			{ eventsUrl: `/api/media-jobs/${jobId}/events`, fetchJob: getMediaJob }
		);
	}

	// Start tracking a freshly-created media job.
	function monitorJob(jobId: string, onCompleteCallback?: (freshInspect: any) => void | Promise<void>) {
		runningJobId = jobId;
		progressPercent = 0;
		progressStage = 'queued';
		progressMessage = 'Waiting in job queue...';
		jobLog = [];
		attachTracking(jobId, onCompleteCallback);
	}

	/** Re-attach to a job after page load: fetch its snapshot, then either
	 *  show the terminal result or resume tracking — never reset progress
	 *  to 0 if the job is already partway through. Mirrors
	 *  routes/pipeline + routes/letterbox `rehydrateBatch`. */
	async function rehydrateJob(jobId: string) {
		let job: MediaJob;
		try {
			job = await getMediaJob(fetch, jobId);
		} catch {
			storeJobId(null);
			return;
		}

		if (TERMINAL_STATUSES.includes(job.status)) {
			storeJobId(null);
			if (job.status === 'succeeded' || job.status === 'completed') {
				toast('Subtitles operation completed successfully!', 'good');
			} else {
				toast(`Operation failed: ${(job.error as any)?.error || job.error || 'Unknown error'}`, 'bad');
			}
			await refreshInventory();
			return;
		}

		runningJobId = jobId;
		progressStage = job.progress?.stage ?? job.status;
		progressPercent = job.progress?.percent ?? 0;
		progressMessage = job.progress?.message ?? 'Waiting in job queue...';
		jobLog = [];
		busy = true;
		attachTracking(jobId);
	}

	onMount(() => {
		// Priority 1: the page loader's inspect() call found an active job
		// for this exact media file.
		const active = data.inspect?.active_job?.job_id ?? null;
		if (active) {
			void rehydrateJob(active);
			return;
		}
		// Priority 2: localStorage still holds a job id from before refresh.
		if (browser) {
			const stored = localStorage.getItem(SUBTITLE_JOB_KEY);
			if (stored) void rehydrateJob(stored);
		}
	});

	// ── ACTION: Delete Selected Tracks ──
	// ── ACTION: Delete Selected Tracks (Subtitles and/or Audio) ──
	async function handleDeleteSelected() {
		if (!movie || !movie.media_file_id) return;
		if (selectedTrackIds.length === 0 && selectedAudioIndices.length === 0) return;
		busy = true;
		try {
			const plan = await createPlan(fetch, movie.media_file_id, {
				operation: 'track_remove',
				track_ids: selectedTrackIds,
				audio_stream_indices: selectedAudioIndices
			});
			await confirmJob(fetch, plan.job_id);
			monitorJob(plan.job_id);
		} catch (e: any) {
			toast(e.message || 'Delete operation failed', 'bad');
			busy = false;
		}
	}

	// ── ACTION: Extract Embedded to Sidecar ──
	async function handleExtractTrack() {
		if (!movie || !movie.media_file_id || !singleSelectedTrack) return;
		busy = true;
		const mediaFileId = movie.media_file_id;
		const trackId = singleSelectedTrack.id;
		const originalStreamIndex = singleSelectedTrack.stream_index;
		try {
			const res = await extractTrack(fetch, mediaFileId, trackId);
			monitorJob(res.job_id, async (freshInspect) => {
				// If cleanup checkbox is set, trigger removal of embedded track after extraction resolves
				if (deleteAfterExtract) {
					toast('Sidecar extracted. Remuxing to delete original embedded track...', 'info');
					busy = true;
					try {
						const freshTracks = freshInspect?.inventory?.tracks || [];
						const freshTrack = freshTracks.find((t: any) => t.source === 'embedded' && t.stream_index === originalStreamIndex);
						if (!freshTrack) {
							throw new Error('Could not find original embedded track in updated inventory');
						}
						const plan = await createPlan(fetch, mediaFileId, {
							operation: 'track_remove',
							track_ids: [freshTrack.id]
						});
						await confirmJob(fetch, plan.job_id);
						monitorJob(plan.job_id);
					} catch (e: any) {
						toast(`Cleanup remux failed: ${e.message}`, 'bad');
						busy = false;
					}
				}
			});
		} catch (e: any) {
			toast(e.message || 'Extraction failed', 'bad');
			busy = false;
		}
	}

	// ── ACTION: Embed External Sidecar into Video ──
	async function handleEmbedTrack() {
		if (!movie || !movie.media_file_id || !singleSelectedTrack) return;
		busy = true;
		const mediaFileId = movie.media_file_id;
		const trackId = singleSelectedTrack.id;
		const originalExternalPath = singleSelectedTrack.external_path;
		try {
			const plan = await createPlan(fetch, mediaFileId, {
				operation: 'subtitle_embed',
				track_ids: [trackId]
			});
			await confirmJob(fetch, plan.job_id);
			monitorJob(plan.job_id, async (freshInspect) => {
				// If cleanup checkbox is set, trigger deletion of external file after embedding resolves
				if (deleteAfterEmbed) {
					toast('Track embedded. Removing external sidecar file...', 'info');
					busy = true;
					try {
						const freshTracks = freshInspect?.inventory?.tracks || [];
						const freshTrack = freshTracks.find((t: any) => t.source === 'external' && t.external_path === originalExternalPath);
						if (!freshTrack) {
							throw new Error('Could not find original external track in updated inventory');
						}
						const removePlan = await createPlan(fetch, mediaFileId, {
							operation: 'track_remove',
							track_ids: [freshTrack.id]
						});
						await confirmJob(fetch, removePlan.job_id);
						monitorJob(removePlan.job_id);
					} catch (e: any) {
						toast(`Cleanup sidecar deletion failed: ${e.message}`, 'bad');
						busy = false;
					}
				}
			});
		} catch (e: any) {
			toast(e.message || 'Embedding failed', 'bad');
			busy = false;
		}
	}

	// ── ACTION: Run Batch Quick-Delete ──
	async function handleBatchDelete() {
		if (!movie || !movie.media_file_id) return;
		if (batchLanguages.length === 0) {
			toast('Select at least one language code', 'info');
			return;
		}

		// Compile target tracks and audio streams
		let targetTrackIds: string[] = [];
		let targetAudioIndices: number[] = [];

		if (batchDeleteTarget === 'subtitles' || batchDeleteTarget === 'both') {
			if (batchDeleteMode === 'only') {
				targetTrackIds = tracks
					.filter((t: any) => batchLanguages.includes(t.language_tag?.toLowerCase()))
					.map((t: any) => t.id);
			} else {
				targetTrackIds = tracks
					.filter((t: any) => !batchLanguages.includes(t.language_tag?.toLowerCase()))
					.map((t: any) => t.id);
			}
		}

		if (batchDeleteTarget === 'audio' || batchDeleteTarget === 'both') {
			if (batchDeleteMode === 'only') {
				targetAudioIndices = audioStreams
					.filter((s: any) => batchLanguages.includes(s.language_tag?.toLowerCase()))
					.map((s: any) => s.index);
			} else {
				targetAudioIndices = audioStreams
					.filter((s: any) => !batchLanguages.includes(s.language_tag?.toLowerCase()))
					.map((s: any) => s.index);
			}
		}

		if (targetTrackIds.length === 0 && targetAudioIndices.length === 0) {
			toast('No matching tracks found for this batch filter', 'info');
			batchDeleteOpen = false;
			return;
		}

		busy = true;
		batchDeleteOpen = false;
		try {
			const plan = await createPlan(fetch, movie.media_file_id, {
				operation: 'track_remove',
				track_ids: targetTrackIds,
				audio_stream_indices: targetAudioIndices
			});
			await confirmJob(fetch, plan.job_id);
			monitorJob(plan.job_id);
		} catch (e: any) {
			toast(e.message || 'Batch delete failed', 'bad');
			busy = false;
		}
	}

	// ── ACTION: Generate AI Subtitles ──
	async function handleGenerateAI() {
		if (!movie || !movie.media_file_id) return;
		busy = true;

		// 1. Commit Subgen override settings to the backend
		const saved = await saveSettingsOverride();
		if (!saved) {
			busy = false;
			return;
		}

		// 2. Submit subtitle generation request
		try {
			const res = await submitMovieGeneration(fetch, movie.id, {
				language_hint: audioLangHint || null,
				output: subgenOutputTarget
			});
			monitorJob(res.job_id);
		} catch (e: any) {
			toast(e.message || 'Generation failed', 'bad');
			busy = false;
		}
	}

	// Map language tags to colors
	const colorPalette = ['lang-1', 'lang-2', 'lang-3', 'lang-4', 'lang-5', 'lang-6', 'lang-7', 'lang-8'];
	function getLanguageClass(lang: string) {
		let hash = 0;
		for (let i = 0; i < lang.length; i++) {
			hash = lang.charCodeAt(i) + ((hash << 5) - hash);
		}
		const index = Math.abs(hash) % colorPalette.length;
		return colorPalette[index];
	}

	// Track selection checkbox helpers
	function toggleTrackSelect(id: string) {
		if (selectedTrackIds.includes(id)) {
			selectedTrackIds = selectedTrackIds.filter(tid => tid !== id);
		} else {
			selectedTrackIds = [...selectedTrackIds, id];
		}
	}
	function toggleAllTracks() {
		if (selectedTrackIds.length === tracks.length) {
			selectedTrackIds = [];
		} else {
			selectedTrackIds = tracks.map((t: any) => t.id);
		}
	}

	// Audio selection checkbox helpers
	function toggleAudioSelect(index: number) {
		if (selectedAudioIndices.includes(index)) {
			selectedAudioIndices = selectedAudioIndices.filter(i => i !== index);
		} else {
			selectedAudioIndices = [...selectedAudioIndices, index];
		}
	}
	function toggleAllAudio() {
		if (selectedAudioIndices.length === audioStreams.length) {
			selectedAudioIndices = [];
		} else {
			selectedAudioIndices = audioStreams.map((s: any) => s.index);
		}
	}

	let singleSelectedAudio = $derived(
		selectedAudioIndices.length === 1
			? audioStreams.find((s: any) => s.index === selectedAudioIndices[0])
			: null
	);

	// Language checkbox helpers for Batch Delete
	function toggleBatchLang(lang: string) {
		if (batchLanguages.includes(lang)) {
			batchLanguages = batchLanguages.filter(l => l !== lang);
		} else {
			batchLanguages = [...batchLanguages, lang];
		}
	}
</script>

<svelte:head>
	<title>{movie?.title || 'Subtitles Details'} — Marquee</title>
</svelte:head>

<div class="movie-subtitles-page">
	<!-- Top Bar Navigation Back -->
	<div class="top-nav">
		<a href="/subtitles" class="back-link">← Back to Subtitles Library</a>
	</div>

	{#if error}
		<div class="error-banner">⚠ {error}</div>
	{:else if movie}
		<!-- Header Panel -->
		<div class="movie-header mq-rise">
			<div class="poster-col">
				<div class="thumb-wrap">
					<PosterThumb
						title={movie.title}
						posterStatus={movie.poster_status}
						posterUrl={movie.poster_url}
						hdr={movie.hdr}
					/>
				</div>
			</div>
			<div class="info-col">
				<div class="title-row">
					<h2>{movie.title} <span class="year">({movie.year})</span></h2>
					<span class="container-badge">{movie.container || 'mkv'}</span>
				</div>
				<div class="meta-details">
					<div class="detail-item">
						<span class="lbl">File Path:</span>
						<span class="val font-mono truncate" title={movie.media_file_path}>{movie.media_file_path}</span>
					</div>
					{#if inventory.duration_seconds}
					<div class="detail-item">
						<span class="lbl">Duration:</span>
						<span class="val">{Math.floor(inventory.duration_seconds / 60)} minutes</span>
					</div>
					{/if}
				</div>
			</div>
		</div>

		<!-- Main Tab Layout -->
		<div class="tabs-wrap">
			<TabBar {tabs} active={activeTab} onSelect={(id) => activeTab = id} />
		</div>

		<!-- Progress Overlay when job runs -->
		{#if runningJobId}
			<div class="job-progress-banner mq-rise">
				<div class="banner-head">
					<span class="title">🏃 Active Subtitles Task</span>
					<span class="status-badge font-mono">{progressStage.toUpperCase()}</span>
				</div>
				<ProgressBar value={progressPercent} />
				<p class="banner-message">{progressMessage}</p>
				{#if jobLog.length > 0}
					<details class="logs-fold">
						<summary>Show execution logs</summary>
						<pre class="logs-pre">{jobLog.join('\n')}</pre>
					</details>
				{/if}
			</div>
		{/if}

		<!-- Tab Content -->
		<div class="tab-content-wrapper">
			{#if activeTab === 'tracks'}
				<!-- ── TRACKS TAB ── -->
				<div class="subtitles-tab-grid">
					
					<!-- Left Main: Tracks list and controls -->
					<div class="left-panel">
						<div class="panel-section header-row">
							<h4>Media Tracks & Operations</h4>
							<div class="actions">
								<button class="btn secondary btn-sm" onclick={handleRescan} disabled={busy}>
									🔄 Re-Scan File
								</button>
							</div>
						</div>

						<!-- Audio Tracks Section -->
						<div class="tracks-list-header">
							<h5>Audio Tracks ({audioStreams.length})</h5>
						</div>
						<div class="tracks-table-wrap">
							<table class="tracks-table">
								<thead>
									<tr>
										<th class="chk-col">
											<input type="checkbox" checked={audioStreams.length > 0 && selectedAudioIndices.length === audioStreams.length} onchange={toggleAllAudio} />
										</th>
										<th>Language</th>
										<th>Source</th>
										<th>Codec</th>
										<th>Channels</th>
										<th>Flags</th>
										<th>Stream</th>
									</tr>
								</thead>
								<tbody>
									{#if audioStreams.length === 0}
										<tr>
											<td colspan="7" class="empty-table">No audio tracks found. Re-scan the media file.</td>
										</tr>
									{:else}
										{#each audioStreams as stream (stream.index)}
											<tr class:selected={selectedAudioIndices.includes(stream.index)}>
												<td class="chk-col">
													<input type="checkbox" checked={selectedAudioIndices.includes(stream.index)} onchange={() => toggleAudioSelect(stream.index)} />
												</td>
												<td>
													<span class="lang-tag font-mono uppercase">{stream.language_tag || 'und'}</span>
													<span class="lang-name">{getLanguageName(stream.language_tag)}</span>
												</td>
												<td>
													<span class="source-badge">
														embedded
													</span>
												</td>
												<td class="font-mono">{stream.codec || '—'}</td>
												<td>
													<span>{stream.channels ? `${stream.channels}ch` : '—'}</span>
												</td>
												<td>
													<div class="flags-row">
														{#if stream.disposition?.default || stream.disposition?.default_flag}<span class="flag-pill default">DEFAULT</span>{/if}
														{#if stream.disposition?.forced || stream.disposition?.forced_flag}<span class="flag-pill forced">FORCED</span>{/if}
														{#if stream.disposition?.hearing_impaired}<span class="flag-pill sdh">SDH</span>{/if}
														{#if stream.disposition?.comment || stream.disposition?.commentary || stream.disposition?.original}<span class="flag-pill commentary">COMMENT</span>{/if}
													</div>
												</td>
												<td class="font-mono font-sm">#{stream.index}</td>
											</tr>
										{/each}
									{/if}
								</tbody>
							</table>
						</div>

						<!-- Subtitle Tracks Section -->
						<div class="tracks-list-header mt-10">
							<h5>Subtitle Tracks ({tracks.length})</h5>
						</div>
						<div class="tracks-table-wrap">
							<table class="tracks-table">
								<thead>
									<tr>
										<th class="chk-col">
											<input type="checkbox" checked={tracks.length > 0 && selectedTrackIds.length === tracks.length} onchange={toggleAllTracks} />
										</th>
										<th>Language</th>
										<th>Source</th>
										<th>Codec</th>
										<th>Kind</th>
										<th>Flags</th>
										<th>Size</th>
									</tr>
								</thead>
								<tbody>
									{#if tracks.length === 0}
										<tr>
											<td colspan="7" class="empty-table">No subtitle tracks found. Re-scan the media file or run AI generation.</td>
										</tr>
									{:else}
										{#each tracks as track (track.id)}
											<tr class:selected={selectedTrackIds.includes(track.id)}>
												<td class="chk-col">
													<input type="checkbox" checked={selectedTrackIds.includes(track.id)} onchange={() => toggleTrackSelect(track.id)} />
												</td>
												<td>
													<span class="lang-tag font-mono uppercase">{track.language_tag || 'und'}</span>
													<span class="lang-name">{track.language_raw || 'Undetermined'}</span>
												</td>
												<td>
													<span class="source-badge" class:external={track.source === 'external'}>
														{track.source}
													</span>
												</td>
												<td class="font-mono">{track.codec || '—'}</td>
												<td>
													<span class="kind-badge" class:bitmap={track.kind === 'bitmap'}>
														{track.kind}
													</span>
												</td>
												<td>
													<div class="flags-row">
														{#if track.is_forced}<span class="flag-pill forced">FORCED</span>{/if}
														{#if track.is_sdh}<span class="flag-pill sdh">SDH</span>{/if}
														{#if track.is_commentary}<span class="flag-pill commentary">COMMENT</span>{/if}
														{#if track.is_default}<span class="flag-pill default">DEFAULT</span>{/if}
														{#if track.is_generated}<span class="flag-pill generated">AI</span>{/if}
													</div>
												</td>
												<td class="font-mono font-sm">{track.size_bytes ? bytesH(track.size_bytes) : '—'}</td>
											</tr>
										{/each}
									{/if}
								</tbody>
							</table>
						</div>

						<!-- Unified Operations Command Bar -->
						<div class="commands-bar mq-rise">
							<h5>Track Actions</h5>
							{#if selectedTrackIds.length === 0 && selectedAudioIndices.length === 0}
								<p class="help-text">Select one or more tracks in the lists to reveal modification actions.</p>
							{:else}
								<div class="commands-controls">
									<!-- Contextual Single Subtitle Track Actions -->
									{#if singleSelectedTrack && selectedAudioIndices.length === 0}
										{#if singleSelectedTrack.source === 'embedded'}
											<div class="action-card">
												<button class="btn primary" onclick={handleExtractTrack} disabled={busy}>
													📂 Extract to Sidecar File
												</button>
												<label class="checkbox-row">
													<input type="checkbox" bind:checked={deleteAfterExtract} />
													<span>Delete embedded track from video container after extraction completes</span>
												</label>
											</div>
										{:else if singleSelectedTrack.source === 'external'}
											<div class="action-card">
												<button class="btn primary" onclick={handleEmbedTrack} disabled={busy}>
													📥 Embed into Video Container
												</button>
												<label class="checkbox-row">
													<input type="checkbox" bind:checked={deleteAfterEmbed} />
													<span>Delete external sidecar file (.srt) after embedding completes</span>
												</label>
											</div>
										{/if}
									{/if}

									<!-- Delete Selected (always available when > 0 items checked) -->
									<div class="action-card delete-card">
										<button class="btn danger" onclick={handleDeleteSelected} disabled={busy}>
											🗑️ Delete Selected ({selectedTrackIds.length + selectedAudioIndices.length})
										</button>
										<span class="help-text">Permanently remuxes the video container file or unlinks the sidecar subtitle files.</span>
									</div>
								</div>
							{/if}
						</div>
					</div>

					<!-- Right Panel: Coverage & Batch Delete Drawer -->
					<div class="right-panel">
						<!-- Coverage Box -->
						<div class="panel-section coverage-box">
							<h4>Language Coverage Status</h4>
							
							<!-- Audio Coverage Part -->
							<div class="coverage-section">
								<div class="section-subtitle">Audio</div>
								<div class="coverage-row">
									<span class="lbl">Status:</span>
									<span class="val status-lbl" class:ok={audioStatus === 'ok'} class:gap={audioStatus === 'gap'}>
										{audioStatus === 'ok' ? '✅ OK' : '⚠️ Gaps Present'}
									</span>
								</div>
								
								<div class="coverage-details">
									<div class="detail-row">
										<span class="label">Languages Present:</span>
										<div class="tags">
											{#if coverage?.audio_languages && coverage.audio_languages.length > 0}
												{#each coverage.audio_languages as lang}
													<span class="lang-pill {getLanguageClass(lang)}">{lang.toUpperCase()}</span>
												{/each}
											{:else}
												<span class="muted">—</span>
											{/if}
										</div>
									</div>
									<div class="detail-row">
										<span class="label">Missing Preferred:</span>
										<div class="tags">
											{#if missingPreferredAudio.length > 0}
												{#each missingPreferredAudio as lang}
													<span class="lang-pill missing">{lang.toUpperCase()}</span>
												{/each}
											{:else}
												<span class="muted font-sm font-good">None (Full Coverage)</span>
											{/if}
										</div>
									</div>
								</div>
							</div>

							<!-- Divider line -->
							<hr class="coverage-divider" />

							<!-- Subtitles Coverage Part -->
							<div class="coverage-section">
								<div class="section-subtitle">Subtitles</div>
								<div class="coverage-row">
									<span class="lbl">Status:</span>
									<span class="val status-lbl" class:ok={movie.subtitle_status === 'ok'} class:gap={movie.subtitle_status === 'gap'}>
										{movie.subtitle_status === 'ok' ? '✅ OK' : movie.subtitle_status === 'gap' ? '⚠️ Gaps Present' : '❌ Unscanned'}
									</span>
								</div>

								{#if coverage}
									<div class="coverage-details">
										<div class="detail-row">
											<span class="label">Full Dialogue Languages:</span>
											<div class="tags">
												{#if coverage.full_dialogue_languages && coverage.full_dialogue_languages.length > 0}
													{#each coverage.full_dialogue_languages as lang}
														<span class="lang-pill {getLanguageClass(lang)}">{lang.toUpperCase()}</span>
													{/each}
												{:else}
													<span class="muted">—</span>
												{/if}
											</div>
										</div>
										<div class="detail-row">
											<span class="label">Forced Dialogue:</span>
											<div class="tags">
												{#if coverage.forced_only_languages && coverage.forced_only_languages.length > 0}
													{#each coverage.forced_only_languages as lang}
														<span class="lang-pill forced">{lang.toUpperCase()}</span>
													{/each}
												{:else}
													<span class="muted">—</span>
												{/if}
											</div>
										</div>
										<div class="detail-row">
											<span class="label">Missing Preferred:</span>
											<div class="tags">
												{#if coverage.missing_preferred_languages && coverage.missing_preferred_languages.length > 0}
													{#each coverage.missing_preferred_languages as lang}
														<span class="lang-pill missing">{lang.toUpperCase()}</span>
													{/each}
												{:else}
													<span class="muted font-sm font-good">None (Full Coverage)</span>
												{/if}
											</div>
										</div>
									</div>
								{/if}
							</div>
						</div>

						<!-- Batch / Quick Delete Panel -->
						<div class="panel-section batch-delete-panel">
							<button class="btn-batch-toggle" onclick={() => batchDeleteOpen = !batchDeleteOpen}>
								<span>⚡ Batch / Quick Delete</span>
								<span class="arrow">{batchDeleteOpen ? '▲' : '▼'}</span>
							</button>

							{#if batchDeleteOpen}
								<div class="batch-delete-body mq-rise">
									<p class="help">Quickly wipe multiple tracks based on specific languages.</p>
									
									<div class="batch-step">
										<span class="step-label">1. Target Tracks:</span>
										<div class="option-group">
											<label class="radio-label">
												<input type="radio" value="both" bind:group={batchDeleteTarget} />
												<span>Audio & Subtitles</span>
											</label>
											<label class="radio-label">
												<input type="radio" value="subtitles" bind:group={batchDeleteTarget} />
												<span>Subtitles Only</span>
											</label>
											<label class="radio-label">
												<input type="radio" value="audio" bind:group={batchDeleteTarget} />
												<span>Audio Only</span>
											</label>
										</div>
									</div>

									<div class="batch-step">
										<span class="step-label">2. Deletion Mode:</span>
										<div class="option-group">
											<label class="radio-label">
												<input type="radio" value="except" bind:group={batchDeleteMode} />
												<span>Delete everything <strong>except</strong> selected</span>
											</label>
											<label class="radio-label">
												<input type="radio" value="only" bind:group={batchDeleteMode} />
												<span>Delete <strong>only</strong> selected</span>
											</label>
										</div>
									</div>

									<div class="languages-checklist">
										<span class="step-label">3. Choose Languages:</span>
										{#if uniqueLanguagesForBatch.length === 0}
											<div class="muted font-sm">No track languages found.</div>
										{:else}
											<div class="checklist-grid">
												{#each uniqueLanguagesForBatch as lang}
													<label class="chk-item">
														<input type="checkbox" checked={batchLanguages.includes(lang)} onchange={() => toggleBatchLang(lang)} />
														<span>{lang.toUpperCase()}</span>
													</label>
												{/each}
											</div>
										{/if}
									</div>

									<button class="btn danger btn-sm mt-10 w-full" onclick={handleBatchDelete} disabled={busy || uniqueLanguagesForBatch.length === 0 || batchLanguages.length === 0}>
										🔥 Execute Batch Delete
									</button>
								</div>
							{/if}
						</div>
					</div>
				</div>

			{:else if activeTab === 'generation'}
				<!-- ── AI SUBTITLE GENERATION TAB ── -->
				<div class="generation-tab-grid">
					
					<!-- Left: Subgen status & overrides settings -->
					<div class="settings-col">
						<div class="panel-section">
							<h3>AI Subgen Connection Override</h3>
							<div class="setting-row">
								<label for="subgen-url" class="lbl">Subgen URL:</label>
								<input type="text" id="subgen-url" class="str-input wide" bind:value={subgenUrl} placeholder="e.g. http://localhost:9000" />
							</div>
							<div class="setting-row">
								<label for="profile-name" class="lbl">Profile Name:</label>
								<input type="text" id="profile-name" class="str-input" bind:value={subgenProfileName} />
							</div>
							<div class="setting-row">
								<label for="model-label" class="lbl">Model Label:</label>
								<input type="text" id="model-label" class="str-input" bind:value={subgenModelLabel} />
							</div>
							<div class="setting-row">
								<label for="subgen-mode" class="lbl">Translation Mode:</label>
								<select id="subgen-mode" class="enum-select" bind:value={subgenMode}>
									<option value="transcribe">Transcribe</option>
									<option value="translate">Translate</option>
								</select>
							</div>
							<div class="setting-row">
								<label for="l-prefix" class="lbl">Local Path Prefix:</label>
								<input type="text" id="l-prefix" class="str-input" bind:value={subgenLocalPathPrefix} placeholder="e.g. /mnt/Movies" />
							</div>
							<div class="setting-row">
								<label for="r-prefix" class="lbl">Remote Path Prefix:</label>
								<input type="text" id="r-prefix" class="str-input" bind:value={subgenRemotePathPrefix} placeholder="e.g. /movies" />
							</div>
							<div class="setting-row">
								<label for="callback-token" class="lbl">Callback Token:</label>
								<input type="password" id="callback-token" class="str-input" bind:value={subgenCallbackToken} placeholder={settings?.integrations?.subgen?.callback_token_configured ? "••••••••" : "Not set"} />
							</div>

							<div class="preview-output mt-10">
								<span class="lbl">Mapped Subgen Path:</span>
								<code class="val">{translatedPath}</code>
							</div>

							<div class="test-conn-area">
								<button class="btn secondary btn-sm" onclick={testConnection} disabled={testing}>
									{testing ? 'Testing...' : 'Test Connection'}
								</button>
								{#if testResult}
									<div class="test-result" class:success={testResult.success}>
										{testResult.message}
									</div>
								{/if}
							</div>
						</div>
					</div>

					<!-- Right: Generate Form -->
					<div class="form-col">
						<div class="panel-section">
							<h3>Generate Subtitles via Whisper</h3>
							
							<div class="form-group">
								<label for="lang-select">Audio Language Hint</label>
								<select id="lang-select" class="enum-select full-width" bind:value={audioLangHint}>
									{#each WHISPER_LANGUAGES as lang}
										<option value={lang.code}>{lang.label}</option>
									{/each}
								</select>
								<span class="help">Providing the spoken language prevents Whisper auto-detection errors.</span>
							</div>

							<div class="form-group">
								<label>Output Target</label>
								<div class="radio-options">
									<label class="radio-option">
										<input type="radio" value="external" bind:group={subgenOutputTarget} />
										<div class="opt-desc">
											<strong>External Sidecar (.srt)</strong>
											<span>Saves as Plex-compatible sidecar file next to the video. (Recommended)</span>
										</div>
									</label>
									<label class="radio-option">
										<input type="radio" value="embedded" bind:group={subgenOutputTarget} />
										<div class="opt-desc">
											<strong>Embed in Video Container</strong>
											<span>Remuxes the container to embed the generated track. Preservation remux.</span>
										</div>
									</label>
								</div>
							</div>

							<div class="form-foot">
								<button class="btn primary" onclick={handleGenerateAI} disabled={busy}>
									🚀 Generate Subtitles for Movie
								</button>
							</div>
						</div>
					</div>
				</div>
			{/if}
		</div>
	{/if}
</div>

<style>
	.movie-subtitles-page {
		display: flex;
		flex-direction: column;
		gap: 20px;
		max-width: 1200px;
		margin: 0 auto;
		padding: 16px 24px 64px;
		color: var(--text);
	}
	.top-nav {
		margin-bottom: 4px;
	}
	.back-link {
		color: var(--gold);
		font-size: 13.5px;
		font-weight: 550;
		text-decoration: none;
	}
	.back-link:hover {
		color: var(--gold-deep);
		text-decoration: underline;
	}
	.error-banner {
		padding: 14px;
		background: rgba(239, 83, 80, 0.1);
		border: 1px solid var(--bad);
		color: var(--bad);
		border-radius: var(--radius);
		font-size: 13.5px;
	}

	/* Movie Header */
	.movie-header {
		display: flex;
		gap: 24px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 20px;
	}
	.poster-col {
		flex-shrink: 0;
	}
	.thumb-wrap {
		width: 72px;
		aspect-ratio: 2/3;
		border-radius: 6px;
		overflow: hidden;
		border: 1px solid var(--line2);
	}
	.info-col {
		display: flex;
		flex-direction: column;
		justify-content: center;
		gap: 12px;
		min-width: 0;
	}
	.title-row {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.title-row h2 {
		margin: 0;
		font-size: 20px;
		font-weight: 700;
	}
	.title-row h2 .year {
		color: var(--muted);
		font-weight: 400;
	}
	.container-badge {
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--muted);
		text-transform: uppercase;
		background: var(--panel2);
		padding: 2px 6px;
		border-radius: 4px;
		border: 1px solid var(--line2);
	}
	.meta-details {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.detail-item {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
	}
	.detail-item .lbl {
		color: var(--muted);
		width: 70px;
		flex-shrink: 0;
	}
	.detail-item .val {
		color: var(--text);
		font-weight: 550;
	}

	/* Tabs */
	.tabs-wrap {
		border-bottom: 1px solid var(--line);
		padding-bottom: 4px;
	}
	.tab-content-wrapper {
		margin-top: 8px;
	}

	/* Job Progress Banner */
	.job-progress-banner {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.banner-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.banner-head .title {
		font-size: 14px;
		font-weight: 600;
	}
	.status-badge {
		background: var(--gold-soft);
		color: var(--gold);
		font-size: 11px;
		font-weight: 700;
		padding: 2px 6px;
		border-radius: 4px;
	}
	.banner-message {
		font-size: 13px;
		color: var(--muted);
		margin: 0;
	}
	.logs-fold {
		font-size: 12px;
		color: var(--muted);
		cursor: pointer;
	}
	.logs-pre {
		margin-top: 8px;
		background: var(--panel2);
		padding: 8px 12px;
		border-radius: 4px;
		border: 1px solid var(--line);
		max-height: 120px;
		overflow-y: auto;
		color: var(--text);
		font-family: var(--font-mono);
		font-size: 11px;
		line-height: 1.4;
		text-align: left;
	}

	/* Subtitles Tab Layout */
	.subtitles-tab-grid {
		display: grid;
		grid-template-columns: 1fr 340px;
		gap: 20px;
		align-items: flex-start;
	}
	@media (max-width: 900px) {
		.subtitles-tab-grid {
			grid-template-columns: 1fr;
		}
	}
	.left-panel {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.panel-section {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 20px;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.panel-section h4 {
		margin: 0;
		font-size: 15px;
		font-weight: 650;
	}
	.header-row {
		flex-direction: row;
		justify-content: space-between;
		align-items: center;
		padding: 14px 20px;
	}

	/* Table design */
	.tracks-table-wrap {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--panel);
	}
	.tracks-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
		font-size: 13px;
	}
	.tracks-table th, .tracks-table td {
		padding: 10px 14px;
		border-bottom: 1px solid var(--line);
	}
	.tracks-table th {
		background: var(--ink2);
		text-transform: uppercase;
		font-size: 10.5px;
		font-weight: 700;
		color: var(--faint2);
		letter-spacing: 0.05em;
	}
	.tracks-table tr:last-child td {
		border-bottom: none;
	}
	.tracks-table tr:hover td {
		background: rgba(255,255,255,0.015);
	}
	.tracks-table tr.selected td {
		background: color-mix(in srgb, var(--gold) 5%, transparent);
	}
	.chk-col {
		width: 40px;
		text-align: center;
	}
	.chk-col input {
		width: 14px;
		height: 14px;
		accent-color: var(--gold);
		cursor: pointer;
	}
	.lang-tag {
		font-weight: 700;
		color: var(--gold);
	}
	.lang-pill {
		font-size: 10px;
		font-weight: 700;
		padding: 2px 5px;
		border-radius: 4px;
		color: var(--ink);
	}
	.lang-pill.missing {
		background: transparent;
		border: 1px dashed var(--bad);
		color: var(--bad);
	}
	.lang-pill.forced {
		background: transparent;
		border: 1px dashed var(--warn);
		color: var(--warn);
	}
	/* 8-color accent palette — matches SubtitleMovieRow */
	:global(.lang-1) { background: #ff7b72; color: #fff; }
	:global(.lang-2) { background: #79c0ff; color: #0d1117; }
	:global(.lang-3) { background: #7ee787; color: #0d1117; }
	:global(.lang-4) { background: #d2a8ff; color: #0d1117; }
	:global(.lang-5) { background: #ffca28; color: #0d1117; }
	:global(.lang-6) { background: #ffa657; color: #0d1117; }
	:global(.lang-7) { background: #56d364; color: #0d1117; }
	:global(.lang-8) { background: #ec407a; color: #fff; }
	.lang-name {
		color: var(--muted);
		font-size: 12px;
		margin-left: 6px;
	}
	.source-badge {
		font-size: 10.5px;
		font-weight: 655;
		text-transform: uppercase;
		padding: 1px 6px;
		border-radius: 4px;
		background: rgba(121, 192, 255, 0.12);
		color: #79c0ff;
		border: 1px solid rgba(121, 192, 255, 0.2);
	}
	.source-badge.external {
		background: rgba(255, 202, 40, 0.12);
		color: #ffca28;
		border-color: rgba(255, 202, 40, 0.2);
	}
	.kind-badge {
		font-size: 11px;
		color: var(--muted);
	}
	.kind-badge.bitmap {
		color: var(--warn);
		font-weight: 550;
	}
	.flags-row {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.flag-pill {
		font-size: 9px;
		font-weight: 750;
		padding: 1px 4px;
		border-radius: 3px;
		background: var(--panel2);
		color: var(--muted);
		border: 1px solid var(--line2);
	}
	.flag-pill.forced { background: rgba(255, 123, 114, 0.15); color: #ff7b72; border-color: rgba(255, 123, 114, 0.25); }
	.flag-pill.sdh { background: rgba(126, 231, 135, 0.15); color: #7ee787; border-color: rgba(126, 231, 135, 0.25); }
	.flag-pill.commentary { background: rgba(210, 168, 255, 0.15); color: #d2a8ff; border-color: rgba(210, 168, 255, 0.25); }
	.flag-pill.default { background: rgba(255, 202, 40, 0.15); color: #ffca28; border-color: rgba(255, 202, 40, 0.25); }
	.flag-pill.generated { background: rgba(86, 211, 100, 0.15); color: #56d364; border-color: rgba(86, 211, 100, 0.25); }
	.flag-pill.missing { background: rgba(239, 83, 80, 0.15); color: var(--bad); border-color: rgba(239, 83, 80, 0.25); }
	.empty-table {
		text-align: center;
		padding: 32px;
		color: var(--muted);
	}

	/* Command action bar styling */
	.commands-bar {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 20px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.commands-bar h5 {
		margin: 0;
		font-size: 13.5px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--faint2);
	}
	.commands-controls {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.action-card {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.checkbox-row {
		display: flex;
		align-items: center;
		gap: 8px;
		cursor: pointer;
		font-size: 12px;
		color: var(--muted);
	}
	.checkbox-row input {
		width: 14px;
		height: 14px;
		accent-color: var(--gold);
	}
	.delete-card {
		border-color: color-mix(in srgb, var(--bad) 30%, var(--line));
		background: color-mix(in srgb, var(--bad) 3%, var(--panel2));
		flex-direction: row;
		align-items: center;
		justify-content: space-between;
	}
	.delete-card .help-text {
		margin: 0;
	}

	/* Right Panel components */
	.right-panel {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.coverage-box {
		gap: 10px;
	}
	.coverage-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 13px;
	}
	.coverage-row .val {
		font-weight: 600;
	}
	.status-lbl.ok { color: var(--good); }
	.status-lbl.gap { color: var(--warn); }
	.coverage-details {
		border-top: 1px solid var(--line);
		padding-top: 10px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		font-size: 12.5px;
	}
	.coverage-details .detail-row {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.coverage-details .detail-row .label {
		color: var(--muted);
	}
	.tags {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
	}

	/* Batch Delete Panel */
	.batch-delete-panel {
		padding: 0;
		overflow: hidden;
	}
	.btn-batch-toggle {
		width: 100%;
		padding: 14px 20px;
		background: transparent;
		border: none;
		display: flex;
		justify-content: space-between;
		align-items: center;
		color: var(--text);
		font-size: 13px;
		font-weight: 600;
		cursor: pointer;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.btn-batch-toggle:hover {
		background: var(--panel2);
	}
	.btn-batch-toggle .arrow {
		color: var(--muted);
		font-size: 10px;
	}
	.batch-delete-body {
		padding: 0 20px 20px;
		display: flex;
		flex-direction: column;
		gap: 14px;
		border-top: 1px dashed var(--line);
		padding-top: 14px;
	}
	.batch-delete-body .help {
		font-size: 12px;
		color: var(--muted);
		margin: 0;
		line-height: 1.4;
	}
	.option-group {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.radio-label {
		display: flex;
		align-items: center;
		gap: 8px;
		cursor: pointer;
		font-size: 12.5px;
	}
	.radio-label input {
		accent-color: var(--gold);
	}
	.languages-checklist {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.languages-checklist .label {
		font-size: 11.5px;
		font-weight: 600;
		color: var(--muted);
	}
	.checklist-grid {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 6px;
		background: var(--panel2);
		border: 1px solid var(--line);
		padding: 8px 12px;
		border-radius: 6px;
		max-height: 110px;
		overflow-y: auto;
	}
	.chk-item {
		display: flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
		font-size: 12px;
	}
	.chk-item input {
		accent-color: var(--gold);
	}

	/* Simple Table */
	.simple-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 12px;
	}
	.simple-table th, .simple-table td {
		padding: 6px 8px;
		border-bottom: 1px solid var(--line);
		text-align: left;
	}
	.simple-table th {
		background: var(--panel2);
		color: var(--muted);
		font-size: 10px;
		text-transform: uppercase;
		font-weight: 700;
	}
	.simple-table tr:last-child td {
		border-bottom: none;
	}

	/* AI Generation Tab Grid */
	.generation-tab-grid {
		display: grid;
		grid-template-columns: 360px 1fr;
		gap: 20px;
		align-items: flex-start;
	}
	@media (max-width: 900px) {
		.generation-tab-grid {
			grid-template-columns: 1fr;
		}
	}
	.settings-col, .form-col {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.settings-col h3, .form-col h3 {
		margin: 0;
		font-size: 15px;
		font-weight: 650;
		border-bottom: 1px solid var(--line);
		padding-bottom: 10px;
		margin-bottom: 6px;
	}
	.setting-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 12.5px;
		min-height: 32px;
	}
	.setting-row .lbl {
		color: var(--muted);
	}
	.str-input {
		padding: 5px 10px;
		border-radius: 6px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12px;
		width: 130px;
		outline: none;
	}
	.str-input:focus { border-color: var(--gold); }
	.str-input.wide { width: 200px; }
	.enum-select {
		padding: 5px 24px 5px 10px;
		border-radius: 6px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12px;
		min-width: 130px;
		appearance: none;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238a909f'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 8px center;
		outline: none;
	}
	.enum-select.full-width {
		width: 100%;
	}
	.preview-output {
		background: var(--panel2);
		border: 1px solid var(--line);
		padding: 8px 10px;
		border-radius: 6px;
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 12px;
	}
	.preview-output .lbl { color: var(--muted); }
	.preview-output .val { color: var(--gold); font-family: var(--font-mono); }
	
	.test-conn-area {
		margin-top: 6px;
		border-top: 1px dashed var(--line);
		padding-top: 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.test-result {
		font-size: 11px;
		color: var(--bad);
		background: rgba(239, 83, 80, 0.05);
		border: 1px solid rgba(239, 83, 80, 0.2);
		padding: 6px;
		border-radius: 4px;
		line-height: 1.4;
	}
	.test-result.success {
		color: var(--good);
		background: rgba(86, 211, 100, 0.05);
		border-color: rgba(86, 211, 100, 0.2);
	}

	/* Form details */
	.form-group {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.form-group label {
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--faint2);
	}
	.form-group .help {
		font-size: 11px;
		color: var(--muted);
	}
	.radio-options {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.radio-option {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 12px;
		cursor: pointer;
	}
	.radio-option input {
		margin-top: 3px;
		accent-color: var(--gold);
	}
	.opt-desc {
		display: flex;
		flex-direction: column;
		gap: 2px;
		font-size: 12.5px;
	}
	.opt-desc strong { color: var(--text); }
	.opt-desc span { color: var(--muted); font-size: 11px; }
	.form-foot {
		display: flex;
		justify-content: flex-end;
		border-top: 1px solid var(--line);
		padding-top: 14px;
		margin-top: 6px;
	}

	.section-subtitle {
		font-size: 13.5px;
		font-weight: 650;
		color: var(--gold);
		margin-bottom: 8px;
	}
	.coverage-section {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.coverage-divider {
		border: 0;
		border-top: 1px dashed var(--line);
		margin: 12px 0;
	}
	.batch-step {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.step-label {
		font-size: 11.5px;
		font-weight: 600;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.tracks-list-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-top: 16px;
		margin-bottom: 8px;
	}
	.tracks-list-header h5 {
		margin: 0;
		font-size: 14.5px;
		font-weight: 650;
		color: var(--text);
	}

	/* Base buttons */
	.btn {
		font-size: 12.5px;
		font-weight: 600;
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: 1px solid transparent;
		transition: background-color 0.15s, border-color 0.15s;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		outline: none;
	}
	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--on-gold);
		border-color: var(--gold-deep);
	}
	.btn.primary:hover:not(:disabled) {
		background: var(--gold-deep);
	}
	.btn.secondary {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.btn.secondary:hover:not(:disabled) {
		background: var(--panel);
	}
	.btn.danger {
		background: color-mix(in srgb, var(--bad) 15%, var(--panel2));
		border-color: color-mix(in srgb, var(--bad) 30%, transparent);
		color: var(--bad);
	}
	.btn.danger:hover:not(:disabled) {
		background: color-mix(in srgb, var(--bad) 25%, var(--panel2));
	}
	.btn-sm {
		padding: 6px 12px;
		font-size: 12px;
	}
	.w-full {
		width: 100%;
		justify-content: center;
	}
	.mt-10 { margin-top: 10px; }
	.center { text-align: center; }
	.uppercase { text-transform: uppercase; }
	.truncate {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.font-sm { font-size: 11.5px; }
	.font-good { color: var(--good); }
	.font-mono { font-family: var(--font-mono); }
</style>
