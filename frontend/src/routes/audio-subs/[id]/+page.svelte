<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { page } from '$app/state';
	import {
		inspectMovie,
		scanSubtitles,
		createPlan,
		extractTrack,
		updateMovieSubtitlePreferences
	} from '$lib/api/subtitles';
	import { getGenerators, submitMovieGeneration } from '$lib/api/subtitle-generators';
	import { getSettings, putSettings } from '$lib/api/system';
	import { confirmJob, getMediaJob } from '$lib/api/media-jobs';
	import type { MediaJob, TrackEdit } from '$lib/api/types';
	import { trackJob } from '$lib/jobs';
	import { toast } from '$lib/toast';
	import { bytesH } from '$lib/display';
	import TabBar from '$lib/components/TabBar.svelte';
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

	// Staged-edit draft state: local working copies of the tables that
	// flag/reorder edits mutate directly, only sent to the backend on Save.
	let audioDraft = $state<any[]>([]);
	let subtitleDraft = $state<any[]>([]);
	let audioDirty = $state(false);
	let subtitleDirty = $state(false);

	$effect(() => {
		if (!audioDirty) audioDraft = audioStreams.map((s: any) => ({ ...s }));
	});
	$effect(() => {
		if (!subtitleDirty) subtitleDraft = tracks.map((t: any) => ({ ...t }));
	});

	// Movie preferred-language override state
	let moviePreferencesInitialized = $state(false);
	let movieOverrideEnabled = $state(false);
	let movieSeparatePreferred = $state(false);
	let moviePreferredShared = $state('en');
	let moviePreferredAudio = $state('en');
	let moviePreferredSubtitles = $state('en');
	let savingMoviePreferences = $state(false);

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
	let effectiveAudioPreferredLangs = $derived(
		coverage?.preferred_audio_languages ||
			settings?.subtitles?.effective_preferred_audio_languages ||
			preferredLangs
	);
	let effectiveSubtitlePreferredLangs = $derived(
		coverage?.preferred_subtitle_languages ||
			settings?.subtitles?.effective_preferred_subtitle_languages ||
			preferredLangs
	);
	let missingPreferredAudio = $derived.by(() => {
		if (coverage?.missing_preferred_audio_languages) {
			return coverage.missing_preferred_audio_languages;
		}
		const audioLangs = new Set((coverage?.audio_languages || []).map((l: string) => l.toLowerCase()));
		const preferred = effectiveAudioPreferredLangs.map((l: string) => l.toLowerCase());
		return preferred.filter((l: string) => !audioLangs.has(l));
	});
	let missingPreferredSubtitles = $derived(coverage?.missing_preferred_languages || []);
	let audioStatus = $derived(coverage?.audio_status || (missingPreferredAudio.length > 0 ? 'gap' : 'ok'));
	let subtitleStatus = $derived(coverage?.subtitle_status || (missingPreferredSubtitles.length > 0 ? 'gap' : 'ok'));

	// Track Selection Details (derived from the draft, so action buttons
	// reflect staged-but-unsaved edits)
	let selectedTracks = $derived(
		subtitleDraft.filter((t: any) => selectedTrackIds.includes(t.id))
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
			audioDirty = false;
			subtitleDirty = false;
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
	function attachTracking(
		jobId: string,
		onCompleteCallback?: (freshInspect: any) => void | Promise<void>,
		onSettled?: (job: MediaJob) => void
	) {
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
					onSettled?.(job);
				}
			},
			{ eventsUrl: `/api/media-jobs/${jobId}/events`, fetchJob: getMediaJob }
		);
	}

	// Start tracking a freshly-created media job.
	function monitorJob(
		jobId: string,
		onCompleteCallback?: (freshInspect: any) => void | Promise<void>,
		onSettled?: (job: MediaJob) => void
	) {
		runningJobId = jobId;
		progressPercent = 0;
		progressStage = 'queued';
		progressMessage = 'Waiting in job queue...';
		jobLog = [];
		attachTracking(jobId, onCompleteCallback, onSettled);
	}

	// Run a job and resolve once it reaches a terminal state, regardless of
	// success/failure — used to chain sequential plan+confirm calls (Save).
	function runJobAndWait(jobId: string): Promise<MediaJob> {
		return new Promise((resolve) => monitorJob(jobId, undefined, resolve));
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
		if (selectedTrackIds.length === subtitleDraft.length) {
			selectedTrackIds = [];
		} else {
			selectedTrackIds = subtitleDraft.map((t: any) => t.id);
		}
	}
	function selectOnlyTrack(id: string) {
		if (selectedTrackIds.length === 1 && selectedTrackIds[0] === id) {
			selectedTrackIds = [];
		} else {
			selectedTrackIds = [id];
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
		if (selectedAudioIndices.length === audioDraft.length) {
			selectedAudioIndices = [];
		} else {
			selectedAudioIndices = audioDraft.map((s: any) => s.index);
		}
	}
	function selectOnlyAudio(index: number) {
		if (selectedAudioIndices.length === 1 && selectedAudioIndices[0] === index) {
			selectedAudioIndices = [];
		} else {
			selectedAudioIndices = [index];
		}
	}

	let singleSelectedAudio = $derived(
		selectedAudioIndices.length === 1
			? audioDraft.find((s: any) => s.index === selectedAudioIndices[0])
			: null
	);

	$effect(() => {
		if (!inspect || moviePreferencesInitialized) return;
		const prefs = inspect.preferred_languages || coverage?.preferences || movie?.preferred_languages;
		const shared = prefs?.shared || settings?.subtitles?.preferred_languages || ['en'];
		const audio = prefs?.audio || settings?.subtitles?.effective_preferred_audio_languages || shared;
		const subtitles = prefs?.subtitles || settings?.subtitles?.effective_preferred_subtitle_languages || shared;
		movieOverrideEnabled = Boolean(prefs?.override);
		moviePreferredShared = shared.join(', ');
		moviePreferredAudio = audio.join(', ');
		moviePreferredSubtitles = subtitles.join(', ');
		movieSeparatePreferred = audio.join(',') !== subtitles.join(',');
		moviePreferencesInitialized = true;
	});

	function parseLanguages(value: string) {
		return value
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);
	}

	async function saveMoviePreferences() {
		if (!movie) return;
		savingMoviePreferences = true;
		try {
			await updateMovieSubtitlePreferences(fetch, movie.id, {
				preferred_audio_languages: movieSeparatePreferred
					? parseLanguages(moviePreferredAudio)
					: parseLanguages(moviePreferredShared),
				preferred_subtitle_languages: movieSeparatePreferred
					? parseLanguages(moviePreferredSubtitles)
					: parseLanguages(moviePreferredShared)
			});
			movieOverrideEnabled = true;
			moviePreferencesInitialized = false;
			await refreshInventory();
			toast('Movie preferred languages saved', 'good');
		} catch (e: any) {
			toast(e.message || 'Failed to save movie preferred languages', 'bad');
		} finally {
			savingMoviePreferences = false;
		}
	}

	async function resetMoviePreferences() {
		if (!movie) return;
		savingMoviePreferences = true;
		try {
			await updateMovieSubtitlePreferences(fetch, movie.id, { use_global: true });
			movieOverrideEnabled = false;
			moviePreferencesInitialized = false;
			await refreshInventory();
			toast('Movie preferences reset to library defaults', 'good');
		} catch (e: any) {
			toast(e.message || 'Failed to reset movie preferred languages', 'bad');
		} finally {
			savingMoviePreferences = false;
		}
	}

	function audioDefault(stream: any) {
		return Boolean(stream?.is_default || stream?.disposition?.default || stream?.disposition?.default_flag);
	}
	function audioForced(stream: any) {
		return Boolean(stream?.is_forced || stream?.disposition?.forced || stream?.disposition?.forced_flag);
	}
	function audioSdh(stream: any) {
		return Boolean(stream?.is_sdh || stream?.disposition?.hearing_impaired);
	}
	function audioCommentary(stream: any) {
		return Boolean(
			stream?.is_commentary ||
				stream?.disposition?.comment ||
				stream?.disposition?.commentary ||
				stream?.disposition?.original
		);
	}
	function audioChannelLabel(stream: any) {
		return stream?.channel_label || (stream?.channels ? `${stream.channels}ch` : '—');
	}
	function audioFormatLabel(stream: any) {
		return stream?.format_label || stream?.profile || stream?.codec_long_name || '—';
	}
	function subtitleCodecLabel(track: any) {
		return track?.codec_label || (track?.codec || '—').replaceAll('_', ' ').toUpperCase();
	}
	function subtitleKindLabel(track: any) {
		return track?.kind_label || track?.kind || '—';
	}

	function audioEdit(stream: any, changes: Partial<TrackEdit>): TrackEdit {
		return {
			stream_type: 'audio',
			audio_stream_index: stream.index,
			is_default: audioDefault(stream),
			is_forced: audioForced(stream),
			is_sdh: audioSdh(stream),
			is_commentary: audioCommentary(stream),
			...changes
		};
	}

	function subtitleEdit(track: any, changes: Partial<TrackEdit>): TrackEdit {
		return {
			track_id: track.id,
			is_default: track.is_default,
			is_forced: track.is_forced,
			is_sdh: track.is_sdh,
			is_commentary: track.is_commentary,
			...changes
		};
	}

	// Draft mutators: stage flag/reorder edits locally; nothing hits the
	// backend until saveAudioChanges()/saveSubtitleChanges() runs.
	function draftSetAudioDefault(index: number) {
		audioDraft = audioDraft.map((s) => ({ ...s, is_default: s.index === index }));
		audioDirty = true;
	}

	function draftToggleAudioFlag(index: number, field: 'is_forced' | 'is_sdh' | 'is_commentary') {
		audioDraft = audioDraft.map((s) => {
			if (s.index !== index) return s;
			const current =
				field === 'is_forced' ? audioForced(s) : field === 'is_sdh' ? audioSdh(s) : audioCommentary(s);
			return { ...s, [field]: !current };
		});
		audioDirty = true;
	}

	function draftReorderAudio(direction: 'first' | 'up' | 'down' | 'last') {
		if (!singleSelectedAudio) return;
		const order = [...audioDraft];
		const current = order.findIndex((s) => s.index === singleSelectedAudio.index);
		if (current < 0) return;
		const [moved] = order.splice(current, 1);
		let nextIndex = current;
		if (direction === 'first') nextIndex = 0;
		if (direction === 'up') nextIndex = Math.max(0, current - 1);
		if (direction === 'down') nextIndex = Math.min(order.length, current + 1);
		if (direction === 'last') nextIndex = order.length;
		order.splice(nextIndex, 0, moved);
		audioDraft = order;
		audioDirty = true;
	}

	function draftSetSubtitleDefault(id: string) {
		subtitleDraft = subtitleDraft.map((t) =>
			t.source === 'embedded' ? { ...t, is_default: t.id === id } : t
		);
		subtitleDirty = true;
	}

	function draftToggleSubtitleFlag(id: string, field: 'is_forced' | 'is_sdh' | 'is_commentary') {
		subtitleDraft = subtitleDraft.map((t) => (t.id === id ? { ...t, [field]: !t[field] } : t));
		subtitleDirty = true;
	}

	function discardAudioChanges() {
		audioDirty = false;
	}

	function discardSubtitleChanges() {
		subtitleDirty = false;
	}

	async function saveAudioChanges() {
		if (!movie || !movie.media_file_id || busy) return;
		const metadataEdits = audioDraft
			.filter((draft) => {
				const original = audioStreams.find((s: any) => s.index === draft.index);
				return (
					original &&
					(audioDefault(draft) !== audioDefault(original) ||
						audioForced(draft) !== audioForced(original) ||
						audioSdh(draft) !== audioSdh(original) ||
						audioCommentary(draft) !== audioCommentary(original))
				);
			})
			.map((draft) =>
				audioEdit(draft, {
					is_default: audioDefault(draft),
					is_forced: audioForced(draft),
					is_sdh: audioSdh(draft),
					is_commentary: audioCommentary(draft)
				})
			);
		const draftOrder = audioDraft.map((s: any) => s.index);
		const orderChanged = audioStreams.map((s: any) => s.index).join(',') !== draftOrder.join(',');

		if (metadataEdits.length === 0 && !orderChanged) {
			toast('No changes to save', 'info');
			return;
		}

		busy = true;
		try {
			if (metadataEdits.length > 0) {
				const plan = await createPlan(fetch, movie.media_file_id, {
					operation: 'subtitle_metadata',
					track_ids: [],
					edits: metadataEdits
				});
				await confirmJob(fetch, plan.job_id);
				const job = await runJobAndWait(plan.job_id);
				if (job.status !== 'succeeded' && job.status !== 'completed') return;
			}
			if (orderChanged) {
				const plan = await createPlan(fetch, movie.media_file_id, {
					operation: 'audio_reorder',
					track_ids: [],
					audio_stream_order: draftOrder
				});
				await confirmJob(fetch, plan.job_id);
				await runJobAndWait(plan.job_id);
			}
			audioDirty = false;
		} catch (e: any) {
			toast(e.message || 'Save failed', 'bad');
		} finally {
			busy = false;
		}
	}

	async function saveSubtitleChanges() {
		if (!movie || !movie.media_file_id || busy) return;
		const metadataEdits = subtitleDraft
			.filter((draft) => draft.source === 'embedded')
			.filter((draft) => {
				const original = tracks.find((t: any) => t.id === draft.id);
				return (
					original &&
					(draft.is_default !== original.is_default ||
						draft.is_forced !== original.is_forced ||
						draft.is_sdh !== original.is_sdh ||
						draft.is_commentary !== original.is_commentary)
				);
			})
			.map((draft) =>
				subtitleEdit(draft, {
					is_default: draft.is_default,
					is_forced: draft.is_forced,
					is_sdh: draft.is_sdh,
					is_commentary: draft.is_commentary
				})
			);

		if (metadataEdits.length === 0) {
			toast('No changes to save', 'info');
			return;
		}

		busy = true;
		try {
			const plan = await createPlan(fetch, movie.media_file_id, {
				operation: 'subtitle_metadata',
				track_ids: [],
				edits: metadataEdits
			});
			await confirmJob(fetch, plan.job_id);
			await runJobAndWait(plan.job_id);
			subtitleDirty = false;
		} catch (e: any) {
			toast(e.message || 'Save failed', 'bad');
		} finally {
			busy = false;
		}
	}

	async function deleteAudioSelected() {
		if (!movie || !movie.media_file_id || selectedAudioIndices.length === 0) return;
		busy = true;
		try {
			const plan = await createPlan(fetch, movie.media_file_id, {
				operation: 'track_remove',
				track_ids: [],
				audio_stream_indices: selectedAudioIndices
			});
			await confirmJob(fetch, plan.job_id);
			monitorJob(plan.job_id);
		} catch (e: any) {
			toast(e.message || 'Audio delete failed', 'bad');
			busy = false;
		}
	}

	async function deleteSubtitleSelected() {
		if (!movie || !movie.media_file_id || selectedTrackIds.length === 0) return;
		busy = true;
		try {
			const plan = await createPlan(fetch, movie.media_file_id, {
				operation: 'track_remove',
				track_ids: selectedTrackIds,
				audio_stream_indices: []
			});
			await confirmJob(fetch, plan.job_id);
			monitorJob(plan.job_id);
		} catch (e: any) {
			toast(e.message || 'Subtitle delete failed', 'bad');
			busy = false;
		}
	}

	// Language checkbox helpers for Batch Delete
	function toggleBatchLang(lang: string) {
		if (batchLanguages.includes(lang)) {
			batchLanguages = batchLanguages.filter(l => l !== lang);
		} else {
			batchLanguages = [...batchLanguages, lang];
		}
	}

	function toggleRowFromKeyboard(event: KeyboardEvent, toggle: () => void) {
		if (event.key !== 'Enter' && event.key !== ' ') return;
		event.preventDefault();
		toggle();
	}
</script>

<svelte:head>
	<title>{movie?.title || 'Audio & Subs'} — Marquee</title>
</svelte:head>

<div class="movie-subtitles-page">
	<!-- Top Bar Navigation Back -->
	<div class="top-nav">
		<a href="/audio-subs" class="back-link">← Back to Tracks Library</a>
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
						<!-- Audio Tracks Section -->
						<div class="tracks-list-header">
							<h5>Audio Tracks ({audioDraft.length})</h5>
						</div>
						<div class="inline-actions audio-action-panel">
							<div class="inline-actions-head">
								<span>Audio Actions</span>
								<strong>{selectedAudioIndices.length} selected</strong>
							</div>
							<div class="inline-actions-body">
								<div class="action-group">
									<button class="chip-action" class:active={singleSelectedAudio && audioDefault(singleSelectedAudio)} onclick={() => singleSelectedAudio && draftSetAudioDefault(singleSelectedAudio.index)} disabled={busy || !singleSelectedAudio}>
										Set Default
									</button>
									<button class="chip-action" class:active={singleSelectedAudio && audioForced(singleSelectedAudio)} onclick={() => singleSelectedAudio && draftToggleAudioFlag(singleSelectedAudio.index, 'is_forced')} disabled={busy || !singleSelectedAudio}>
										Forced
									</button>
									<button class="chip-action" class:active={singleSelectedAudio && audioSdh(singleSelectedAudio)} onclick={() => singleSelectedAudio && draftToggleAudioFlag(singleSelectedAudio.index, 'is_sdh')} disabled={busy || !singleSelectedAudio}>
										HI
									</button>
									<button class="chip-action" class:active={singleSelectedAudio && audioCommentary(singleSelectedAudio)} onclick={() => singleSelectedAudio && draftToggleAudioFlag(singleSelectedAudio.index, 'is_commentary')} disabled={busy || !singleSelectedAudio}>
										Comment
									</button>
									<div class="icon-btn-group">
										<button class="chip-action" aria-label="Move to first" title="Move to first" onclick={() => draftReorderAudio('first')} disabled={busy || !singleSelectedAudio}>⏮</button>
										<button class="chip-action" aria-label="Move up" title="Move up" onclick={() => draftReorderAudio('up')} disabled={busy || !singleSelectedAudio}>▲</button>
										<button class="chip-action" aria-label="Move down" title="Move down" onclick={() => draftReorderAudio('down')} disabled={busy || !singleSelectedAudio}>▼</button>
										<button class="chip-action" aria-label="Move to last" title="Move to last" onclick={() => draftReorderAudio('last')} disabled={busy || !singleSelectedAudio}>⏭</button>
									</div>
								</div>
								<div class="action-group save-group">
									<button class="chip-action danger" onclick={deleteAudioSelected} disabled={busy || selectedAudioIndices.length === 0}>
										Delete Audio ({selectedAudioIndices.length})
									</button>
									<button class="chip-action" onclick={discardAudioChanges} disabled={busy || !audioDirty}>
										Discard
									</button>
									<button class="chip-action primary" onclick={saveAudioChanges} disabled={busy || !audioDirty}>
										Save Changes
									</button>
								</div>
							</div>
						</div>
						<div class="tracks-table-wrap">
							<table class="tracks-table">
								<thead>
									<tr>
										<th class="chk-col">
											<input class="select-circle" type="checkbox" checked={audioDraft.length > 0 && selectedAudioIndices.length === audioDraft.length} onchange={toggleAllAudio} />
										</th>
										<th>Language</th>
										<th>Source</th>
										<th>Codec</th>
										<th>Format</th>
										<th>Channels</th>
										<th>Flags</th>
										<th>Stream</th>
									</tr>
								</thead>
								<tbody>
									{#if audioDraft.length === 0}
										<tr>
											<td colspan="8" class="empty-table">No audio tracks found. Re-scan the media file.</td>
										</tr>
									{:else}
										{#each audioDraft as stream (stream.index)}
											<tr
												class:selected={selectedAudioIndices.includes(stream.index)}
												onclick={() => selectOnlyAudio(stream.index)}
												onkeydown={(event) => toggleRowFromKeyboard(event, () => selectOnlyAudio(stream.index))}
												role="button"
												tabindex="0"
											>
												<td class="chk-col">
													<input class="select-circle" type="checkbox" checked={selectedAudioIndices.includes(stream.index)} onclick={(event) => event.stopPropagation()} onchange={() => toggleAudioSelect(stream.index)} />
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
												<td>{audioFormatLabel(stream)}</td>
												<td>
													<span>{audioChannelLabel(stream)}</span>
												</td>
												<td>
													<div class="flags-row">
														{#if audioDefault(stream)}<span class="flag-pill default">DEFAULT</span>{/if}
														{#if audioForced(stream)}<span class="flag-pill forced">FORCED</span>{/if}
														{#if audioSdh(stream)}<span class="flag-pill sdh">HI</span>{/if}
														{#if audioCommentary(stream)}<span class="flag-pill commentary">COMMENT</span>{/if}
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
							<h5>Subtitle Tracks ({subtitleDraft.length})</h5>
						</div>
						<div class="inline-actions subtitle-action-panel">
							<div class="inline-actions-head">
								<span>Subtitle Actions</span>
								<strong>{selectedTrackIds.length} selected</strong>
							</div>
							<div class="inline-actions-body">
								<div class="action-group">
									<button class="chip-action" class:active={singleSelectedTrack?.is_default} onclick={() => singleSelectedTrack && draftSetSubtitleDefault(singleSelectedTrack.id)} disabled={busy || singleSelectedTrack?.source !== 'embedded'}>
										Set Default
									</button>
									<button class="chip-action" class:active={singleSelectedTrack?.is_forced} onclick={() => singleSelectedTrack && draftToggleSubtitleFlag(singleSelectedTrack.id, 'is_forced')} disabled={busy || singleSelectedTrack?.source !== 'embedded'}>
										Forced
									</button>
									<button class="chip-action" class:active={singleSelectedTrack?.is_sdh} onclick={() => singleSelectedTrack && draftToggleSubtitleFlag(singleSelectedTrack.id, 'is_sdh')} disabled={busy || singleSelectedTrack?.source !== 'embedded'}>
										SDH
									</button>
									<button class="chip-action" class:active={singleSelectedTrack?.is_commentary} onclick={() => singleSelectedTrack && draftToggleSubtitleFlag(singleSelectedTrack.id, 'is_commentary')} disabled={busy || singleSelectedTrack?.source !== 'embedded'}>
										Comment
									</button>
									<button class="chip-action" onclick={handleExtractTrack} disabled={busy || singleSelectedTrack?.source !== 'embedded' || selectedAudioIndices.length > 0}>
										📂 Extract to Sidecar
									</button>
									<button class="chip-action" onclick={handleEmbedTrack} disabled={busy || singleSelectedTrack?.source !== 'external' || selectedAudioIndices.length > 0}>
										📥 Embed into Container
									</button>
									<label class="checkbox-row">
										<input class="select-circle" type="checkbox" bind:checked={deleteAfterExtract} />
										<span>Delete after extract</span>
									</label>
									<label class="checkbox-row">
										<input class="select-circle" type="checkbox" bind:checked={deleteAfterEmbed} />
										<span>Delete after embed</span>
									</label>
								</div>
								<div class="action-group save-group">
									<button class="chip-action danger" onclick={deleteSubtitleSelected} disabled={busy || selectedTrackIds.length === 0}>
										Delete Subtitles ({selectedTrackIds.length})
									</button>
									<button class="chip-action" onclick={discardSubtitleChanges} disabled={busy || !subtitleDirty}>
										Discard
									</button>
									<button class="chip-action primary" onclick={saveSubtitleChanges} disabled={busy || !subtitleDirty}>
										Save Changes
									</button>
								</div>
							</div>
						</div>
						<div class="tracks-table-wrap">
							<table class="tracks-table">
								<thead>
									<tr>
										<th class="chk-col">
											<input class="select-circle" type="checkbox" checked={subtitleDraft.length > 0 && selectedTrackIds.length === subtitleDraft.length} onchange={toggleAllTracks} />
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
									{#if subtitleDraft.length === 0}
										<tr>
											<td colspan="7" class="empty-table">No subtitle tracks found. Re-scan the media file or run AI generation.</td>
										</tr>
									{:else}
										{#each subtitleDraft as track (track.id)}
											<tr
												class:selected={selectedTrackIds.includes(track.id)}
												onclick={() => selectOnlyTrack(track.id)}
												onkeydown={(event) => toggleRowFromKeyboard(event, () => selectOnlyTrack(track.id))}
												role="button"
												tabindex="0"
											>
												<td class="chk-col">
													<input class="select-circle" type="checkbox" checked={selectedTrackIds.includes(track.id)} onclick={(event) => event.stopPropagation()} onchange={() => toggleTrackSelect(track.id)} />
												</td>
												<td>
													<span class="lang-tag font-mono uppercase">{track.language_tag || 'und'}</span>
													<span class="lang-name">{getLanguageName(track.language_tag)}</span>
												</td>
												<td>
													<span class="source-badge" class:external={track.source === 'external'}>
														{track.source}
													</span>
												</td>
												<td class="font-mono">{subtitleCodecLabel(track)}</td>
												<td>
													<span class="kind-badge" class:bitmap={track.kind === 'bitmap'}>
														{subtitleKindLabel(track)}
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

					</div>

					<!-- Right Panel: Coverage & Batch Delete Drawer -->
					<div class="right-panel">
						<button class="rescan-side-button" onclick={handleRescan} disabled={busy}>
							🔄 Re-Scan File
						</button>

						<div class="panel-section movie-preferences-box">
							<div class="movie-preferences-head">
								<h4>Preferred Languages</h4>
								<span class="inherit-badge" class:override={movieOverrideEnabled}>
									{movieOverrideEnabled ? 'Movie Override' : 'Library Default'}
								</span>
							</div>
							<label class="checkbox-row compact-row">
								<input class="select-circle" type="checkbox" bind:checked={movieSeparatePreferred} />
								<span>Separate audio and subtitles</span>
							</label>
							<label class="pref-field">
								<span>{movieSeparatePreferred ? 'Shared fallback' : 'Audio & subtitles'}</span>
								<input type="text" bind:value={moviePreferredShared} placeholder="en, es, fr" autocomplete="off" />
							</label>
							{#if movieSeparatePreferred}
								<label class="pref-field">
									<span>Audio</span>
									<input type="text" bind:value={moviePreferredAudio} placeholder="en, es" autocomplete="off" />
								</label>
								<label class="pref-field">
									<span>Subtitles</span>
									<input type="text" bind:value={moviePreferredSubtitles} placeholder="en, fr" autocomplete="off" />
								</label>
							{/if}
							<div class="preferences-actions">
								<button class="btn secondary btn-sm" onclick={resetMoviePreferences} disabled={savingMoviePreferences || !movieOverrideEnabled}>
									Reset
								</button>
								<button class="btn primary btn-sm" onclick={saveMoviePreferences} disabled={savingMoviePreferences}>
									{savingMoviePreferences ? 'Saving...' : 'Save'}
								</button>
							</div>
						</div>

						<!-- Coverage Box -->
						<div class="panel-section coverage-box">
							<h4>Language Coverage Status</h4>
							
							<!-- Audio Coverage Part -->
							<div class="coverage-section">
								<div class="section-subtitle">Audio</div>
								<div class="coverage-row">
									<span class="lbl">Status:</span>
									<span class="val status-lbl" class:ok={audioStatus === 'ok'} class:gap={audioStatus === 'gap'}>
										{audioStatus === 'ok' ? 'Full Coverage' : 'Gaps Present'}
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
										<span class="label">Preferred:</span>
										<div class="tags">
											{#each effectiveAudioPreferredLangs as lang}
												<span class="lang-pill {getLanguageClass(lang)}">{lang.toUpperCase()}</span>
											{/each}
										</div>
									</div>
									{#if missingPreferredAudio.length > 0}
									<div class="detail-row">
										<span class="label">Missing Preferred:</span>
										<div class="tags">
											{#each missingPreferredAudio as lang}
												<span class="lang-pill missing">{lang.toUpperCase()}</span>
											{/each}
										</div>
									</div>
									{/if}
								</div>
							</div>

							<!-- Divider line -->
							<hr class="coverage-divider" />

							<!-- Subtitles Coverage Part -->
							<div class="coverage-section">
								<div class="section-subtitle">Subtitles</div>
								<div class="coverage-row">
									<span class="lbl">Status:</span>
									<span class="val status-lbl" class:ok={subtitleStatus === 'ok'} class:gap={subtitleStatus === 'gap'}>
										{subtitleStatus === 'ok' ? 'Full Coverage' : subtitleStatus === 'gap' ? 'Gaps Present' : 'Unscanned'}
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
											<span class="label">Preferred:</span>
											<div class="tags">
												{#each effectiveSubtitlePreferredLangs as lang}
													<span class="lang-pill {getLanguageClass(lang)}">{lang.toUpperCase()}</span>
												{/each}
											</div>
										</div>
										{#if missingPreferredSubtitles.length > 0}
										<div class="detail-row">
											<span class="label">Missing Preferred:</span>
											<div class="tags">
												{#each missingPreferredSubtitles as lang}
													<span class="lang-pill missing">{lang.toUpperCase()}</span>
												{/each}
											</div>
										</div>
										{/if}
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
														<input class="select-circle" type="checkbox" checked={batchLanguages.includes(lang)} onchange={() => toggleBatchLang(lang)} />
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
	.inline-actions {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: color-mix(in srgb, var(--gold) 3%, var(--panel));
		padding: 12px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.audio-action-panel {
		border-color: color-mix(in srgb, #79c0ff 28%, var(--line));
	}
	.subtitle-action-panel {
		border-color: color-mix(in srgb, var(--gold) 30%, var(--line));
	}
	.inline-actions-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		font-size: 11px;
		text-transform: uppercase;
		font-weight: 750;
		color: var(--faint2);
	}
	.inline-actions-head strong {
		color: var(--gold);
	}
	.inline-actions-body,
	.action-group {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.chip-action {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		border-radius: 999px;
		padding: 6px 10px;
		font-size: 12px;
		font-weight: 650;
		cursor: pointer;
	}
	.chip-action:hover:not(:disabled),
	.chip-action.active {
		border-color: var(--gold);
		color: var(--gold);
		box-shadow: 0 0 0 2px var(--gold-soft);
	}
	.chip-action.danger {
		border-color: color-mix(in srgb, var(--bad) 40%, var(--line));
		color: var(--bad);
	}
	.chip-action.primary {
		background: var(--gold);
		border-color: var(--gold-deep);
		color: var(--on-gold);
	}
	.chip-action.primary:hover:not(:disabled) {
		background: var(--gold-deep);
		box-shadow: none;
	}
	.chip-action:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.icon-btn-group {
		display: inline-flex;
		border: 1px solid var(--line2);
		border-radius: 999px;
		overflow: hidden;
	}
	.icon-btn-group .chip-action {
		border: none;
		border-radius: 0;
		padding: 6px 9px;
	}
	.icon-btn-group .chip-action:not(:last-child) {
		border-right: 1px solid var(--line2);
	}
	.save-group {
		margin-left: auto;
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
	.tracks-table tbody tr[role='button'] {
		cursor: pointer;
	}
	.tracks-table tbody tr[role='button']:focus-visible td {
		outline: 1px solid var(--gold);
		outline-offset: -1px;
	}
	.chk-col {
		width: 40px;
		text-align: center;
	}
	.select-circle {
		appearance: none;
		width: 15px;
		height: 15px;
		border-radius: 999px;
		border: 1px solid var(--line2);
		background: var(--ink2);
		cursor: pointer;
		box-shadow: inset 0 0 0 4px var(--ink2);
		transition: background-color 0.15s, border-color 0.15s, box-shadow 0.15s;
		vertical-align: middle;
	}
	.select-circle:checked {
		border-color: var(--gold);
		background: var(--gold);
		box-shadow: 0 0 0 3px var(--gold-soft), 0 0 14px rgba(255, 190, 73, 0.42);
	}
	.select-circle:focus-visible {
		outline: 2px solid color-mix(in srgb, var(--gold) 50%, transparent);
		outline-offset: 2px;
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
	.checkbox-row.compact-row {
		font-size: 12.5px;
	}
	.checkbox-row input:not(.select-circle) {
		accent-color: var(--gold);
	}
	/* Right Panel components */
	.right-panel {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.rescan-side-button {
		width: 100%;
		border: 1px solid var(--line);
		background: var(--panel);
		color: var(--text);
		border-radius: var(--radius);
		padding: 12px 14px;
		font-size: 13px;
		font-weight: 700;
		cursor: pointer;
	}
	.rescan-side-button:hover:not(:disabled) {
		border-color: var(--gold);
		color: var(--gold);
		box-shadow: 0 0 0 2px var(--gold-soft);
	}
	.rescan-side-button:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.movie-preferences-box {
		gap: 12px;
	}
	.movie-preferences-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
	}
	.inherit-badge {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		border-radius: 999px;
		padding: 3px 8px;
		font-size: 10.5px;
		font-weight: 700;
	}
	.inherit-badge.override {
		border-color: color-mix(in srgb, var(--gold) 40%, transparent);
		background: var(--gold-soft);
		color: var(--gold);
	}
	.pref-field {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.pref-field span {
		color: var(--faint2);
		font-size: 10.5px;
		font-weight: 750;
		text-transform: uppercase;
	}
	.pref-field input {
		width: 100%;
		border: 1px solid var(--line);
		background: var(--panel2);
		color: var(--text);
		border-radius: var(--radius-sm);
		padding: 8px 10px;
		font-size: 12.5px;
		outline: none;
	}
	.pref-field input:focus {
		border-color: var(--gold);
		box-shadow: 0 0 0 2px var(--gold-soft);
	}
	.preferences-actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
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
	.chk-item input:not(.select-circle) {
		accent-color: var(--gold);
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
