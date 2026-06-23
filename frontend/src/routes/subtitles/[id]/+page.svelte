<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { getMovie } from '$lib/api/library';
	import { inspectMovie, getInventory, scanSubtitles, createPlan, extractTrack, previewTrack } from '$lib/api/subtitles';
	import { getGenerators, submitMovieGeneration } from '$lib/api/subtitle-generators';
	import { getSettings, putSettings } from '$lib/api/system';
	import { confirmJob, getMediaJob } from '$lib/api/media-jobs';
	import { subscribe } from '$lib/sse';
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
		{ id: 'subtitles', label: 'Subtitles & Operations' },
		{ id: 'generation', label: 'AI Subtitle Generation' },
		{ id: 'audio', label: 'Audio Tracks' }
	];
	let activeTab = $state('subtitles');

	// Inventory & Tracks State
	let inventory = $derived(inspect?.inventory || ({} as any));
	let tracks = $derived(inventory?.tracks || []);
	let audioStreams = $derived(inventory?.audio_streams || []);
	let capabilities = $derived(inventory?.capabilities || {});
	let coverage = $derived(inventory?.coverage || {});

	// Selection State
	let selectedTrackIds = $state<string[]>([]);
	let deleteAfterExtract = $state(false);
	let deleteAfterEmbed = $state(false);

	// Batch Delete States
	let batchDeleteOpen = $state(false);
	let batchDeleteMode = $state<'except' | 'only'>('except');
	let batchLanguages = $state<string[]>([]);

	// Unique languages present in current tracks
	let uniqueLanguages = $derived.by(() => {
		const langs = new Set<string>();
		tracks.forEach((t: any) => {
			if (t.language_tag) {
				langs.add(t.language_tag.toLowerCase());
			}
		});
		return Array.from(langs).sort();
	});

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
	let pollInterval: ReturnType<typeof setInterval> | null = null;

	onDestroy(() => {
		if (pollInterval) {
			clearInterval(pollInterval);
			pollInterval = null;
		}
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
		if (!movie) return;
		try {
			inspect = await inspectMovie(fetch, movie.id);
			selectedTrackIds = [];
		} catch (e: any) {
			toast(e.message || 'Failed to refresh tracks inventory', 'bad');
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

	// Monitor running media job via SSE
	function monitorJob(jobId: string, onCompleteCallback?: () => void) {
		runningJobId = jobId;
		progressPercent = 0;
		progressStage = 'queued';
		progressMessage = 'Waiting in job queue...';
		jobLog = [];

		if (pollInterval) {
			clearInterval(pollInterval);
			pollInterval = null;
		}

		let unsub: () => void;
		unsub = subscribe(`/api/media-jobs/${jobId}/events`, ['message', 'done'], async (type, data: any) => {
			if (type === 'message') {
				progressPercent = data.percent ?? progressPercent;
				progressStage = data.stage ?? progressStage;
				progressMessage = data.message ?? progressMessage;
				jobLog = [...jobLog, `[${data.stage || 'info'}] ${data.message || ''}`];
			} else if (type === 'done') {
				unsub();
				if (pollInterval) {
					clearInterval(pollInterval);
					pollInterval = null;
				}
				runningJobId = null;
				busy = false;
				progressPercent = 100;
				try {
					const job = await getMediaJob(fetch, jobId);
					if (job.status === 'succeeded' || job.status === 'completed') {
						toast('Subtitles operation completed successfully!', 'good');
						await refreshInventory();
						if (onCompleteCallback) onCompleteCallback();
					} else {
						toast(`Operation failed: ${(job.error as any)?.error || job.error || 'Unknown error'}`, 'bad');
					}
				} catch (e: any) {
					toast(`Operation completed. Failed to verify status: ${e.message}`, 'info');
					await refreshInventory();
					if (onCompleteCallback) onCompleteCallback();
				}
			} else if (type === 'error') {
				unsub();
				if (pollInterval) {
					clearInterval(pollInterval);
					pollInterval = null;
				}
				runningJobId = null;
				busy = false;
				toast('Lost connection to task server', 'bad');
			}
		});

		// Fallback polling loop to ensure list updates and progress clears even if SSE drops
		pollInterval = setInterval(async () => {
			try {
				const job = await getMediaJob(fetch, jobId);
				if (job.status !== 'queued' && job.status !== 'running') {
					if (pollInterval) {
						clearInterval(pollInterval);
						pollInterval = null;
					}
					if (runningJobId === jobId) {
						unsub();
						runningJobId = null;
						busy = false;
						progressPercent = 100;
						if (job.status === 'succeeded' || job.status === 'completed') {
							toast('Subtitles operation completed successfully!', 'good');
							await refreshInventory();
							if (onCompleteCallback) onCompleteCallback();
						} else {
							toast(`Operation failed: ${(job.error as any)?.error || job.error || 'Unknown error'}`, 'bad');
							await refreshInventory();
						}
					}
				}
			} catch (e) {
				// Keep polling on transient errors
			}
		}, 1500);
	}

	// ── ACTION: Delete Selected Tracks ──
	async function handleDeleteSelected() {
		if (!movie || !movie.media_file_id || selectedTrackIds.length === 0) return;
		busy = true;
		try {
			const plan = await createPlan(fetch, movie.media_file_id, {
				operation: 'subtitle_remove',
				track_ids: selectedTrackIds
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
		try {
			const res = await extractTrack(fetch, mediaFileId, trackId);
			monitorJob(res.job_id, async () => {
				// If cleanup checkbox is set, trigger removal of embedded track after extraction resolves
				if (deleteAfterExtract) {
					toast('Sidecar extracted. Remuxing to delete original embedded track...', 'info');
					busy = true;
					try {
						const plan = await createPlan(fetch, mediaFileId, {
							operation: 'subtitle_remove',
							track_ids: [trackId]
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
		try {
			const plan = await createPlan(fetch, mediaFileId, {
				operation: 'subtitle_embed',
				track_ids: [trackId]
			});
			await confirmJob(fetch, plan.job_id);
			monitorJob(plan.job_id, async () => {
				// If cleanup checkbox is set, trigger deletion of external file after embedding resolves
				if (deleteAfterEmbed) {
					toast('Track embedded. Removing external sidecar file...', 'info');
					busy = true;
					try {
						const removePlan = await createPlan(fetch, mediaFileId, {
							operation: 'subtitle_remove',
							track_ids: [trackId]
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

		// Compile target track IDs
		let targetTrackIds: string[] = [];
		if (batchDeleteMode === 'only') {
			// Delete only selected
			targetTrackIds = tracks
				.filter((t: any) => batchLanguages.includes(t.language_tag?.toLowerCase()))
				.map((t: any) => t.id);
		} else {
			// Delete everything EXCEPT selected
			targetTrackIds = tracks
				.filter((t: any) => !batchLanguages.includes(t.language_tag?.toLowerCase()))
				.map((t: any) => t.id);
		}

		if (targetTrackIds.length === 0) {
			toast('No matching tracks found for this batch filter', 'info');
			batchDeleteOpen = false;
			return;
		}

		busy = true;
		batchDeleteOpen = false;
		try {
			const plan = await createPlan(fetch, movie.media_file_id, {
				operation: 'subtitle_remove',
				track_ids: targetTrackIds
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
			{#if activeTab === 'subtitles'}
				<!-- ── SUBTITLES TAB ── -->
				<div class="subtitles-tab-grid">
					
					<!-- Left Main: Tracks list and controls -->
					<div class="left-panel">
						<div class="panel-section header-row">
							<h4>Subtitle Tracks ({tracks.length})</h4>
							<div class="actions">
								<button class="btn secondary btn-sm" onclick={handleRescan} disabled={busy}>
									🔄 Re-Scan File
								</button>
							</div>
						</div>

						<!-- Tracks Table -->
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

						<!-- Redesigned Operations Command Bar -->
						<div class="commands-bar mq-rise">
							<h5>Subtitle Track Actions</h5>
							{#if selectedTrackIds.length === 0}
								<p class="help-text">Select one or more tracks in the list to reveal modification actions.</p>
							{:else}
								<div class="commands-controls">
									<!-- Contextual Single Track Actions -->
									{#if singleSelectedTrack}
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
											🗑️ Delete Selected ({selectedTrackIds.length})
										</button>
										<span class="help-text">Permanently remuxes the video file or unlinks the sidecar file.</span>
									</div>
								</div>
							{/if}
						</div>
					</div>

					<!-- Right Panel: Coverage & Batch Delete Drawer -->
					<div class="right-panel">
						<!-- Coverage Box -->
						<div class="panel-section coverage-box">
							<h4>Subtitle Coverage Status</h4>
							<div class="coverage-row">
								<span class="lbl">Coverage Status:</span>
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

						<!-- Batch / Quick Delete Panel -->
						<div class="panel-section batch-delete-panel">
							<button class="btn-batch-toggle" onclick={() => batchDeleteOpen = !batchDeleteOpen}>
								<span>⚡ Batch / Quick Delete</span>
								<span class="arrow">{batchDeleteOpen ? '▲' : '▼'}</span>
							</button>

							{#if batchDeleteOpen}
								<div class="batch-delete-body mq-rise">
									<p class="help">Quickly wipe multiple subtitle tracks based on specific languages.</p>
									
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

									<div class="languages-checklist">
										<span class="label">Choose Languages:</span>
										{#if uniqueLanguages.length === 0}
											<div class="muted font-sm">No track languages detected.</div>
										{:else}
											<div class="checklist-grid">
												{#each uniqueLanguages as lang}
													<label class="chk-item">
														<input type="checkbox" checked={batchLanguages.includes(lang)} onchange={() => toggleBatchLang(lang)} />
														<span>{lang.toUpperCase()}</span>
													</label>
												{/each}
											</div>
										{/if}
									</div>

									<button class="btn danger btn-sm mt-10 w-full" onclick={handleBatchDelete} disabled={busy || uniqueLanguages.length === 0 || batchLanguages.length === 0}>
										🔥 Execute Batch Delete
									</button>
								</div>
							{/if}
						</div>

						<!-- Audio Stream Layout details -->
						<div class="panel-section audio-summary-box">
							<h4>Audio Streams Reference</h4>
							<table class="simple-table">
								<thead>
									<tr>
										<th>Stream</th>
										<th>Lang</th>
										<th>Codec</th>
										<th>Ch</th>
									</tr>
								</thead>
								<tbody>
									{#if audioStreams.length === 0}
										<tr>
											<td colspan="4" class="muted center">No audio details.</td>
										</tr>
									{:else}
										{#each audioStreams as stream}
											<tr>
												<td class="font-mono">#{stream.index}</td>
												<td class="uppercase font-sm font-bold">{stream.language || 'und'}</td>
												<td class="font-mono font-sm">{stream.codec || '—'}</td>
												<td>{stream.channels || '—'}ch</td>
											</tr>
										{/each}
									{/if}
								</tbody>
							</table>
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

			{:else if activeTab === 'audio'}
				<!-- ── AUDIO TAB PLACEHOLDER ── -->
				<div class="audio-tab-placeholder mq-rise">
					<div class="placeholder-icon">🎛️</div>
					<h3>Audio Track Manipulation</h3>
					<p class="subtitle">Coming Soon — Future Feature Expansion</p>
					<p class="description">
						In the future, this tab will house the advanced audio controls for this movie. 
						It will mirror the subtitle track manipulation functionality, allowing you to:
					</p>
					<ul class="features-list">
						<li><strong>Wipe Audio Streams</strong> directly from the Matroska/MP4 container.</li>
						<li><strong>Extract Audio</strong> channels to high-quality external files (.ac3, .dts, .flac).</li>
						<li><strong>Embed External Audio</strong> tracks (such as commentary tracks or localized language dubs) into your video file.</li>
						<li><strong>Edit Audio Metadata</strong> including default, forced flags, and language tags.</li>
						<li><strong>Layout Multi-Channel Controls</strong> for standardizing layouts (Stereo, 5.1, 7.1, Dolby Atmos).</li>
					</ul>
					<div class="mock-table-view">
						<div class="mock-header">Mock Audio Layout Preview</div>
						<table class="simple-table mock-table">
							<thead>
								<tr>
									<th>Stream</th>
									<th>Codec</th>
									<th>Channels</th>
									<th>Language</th>
									<th>Flags</th>
									<th>Actions</th>
								</tr>
							</thead>
							<tbody>
								<tr class="muted">
									<td>#0 (Audio)</td>
									<td>truehd</td>
									<td>8ch (Atmos)</td>
									<td>English (en)</td>
									<td><span class="flag-pill default">DEFAULT</span></td>
									<td><button class="btn secondary btn-xs" disabled>Extract</button></td>
								</tr>
								<tr class="muted">
									<td>#1 (Audio)</td>
									<td>ac3</td>
									<td>6ch (5.1)</td>
									<td>Spanish (es)</td>
									<td>—</td>
									<td><button class="btn secondary btn-xs" disabled>Extract</button></td>
								</tr>
							</tbody>
						</table>
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

	/* Audio Tab Placeholder */
	.audio-tab-placeholder {
		background: var(--panel);
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		padding: 48px 32px;
		text-align: center;
		max-width: 680px;
		margin: 20px auto;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 14px;
	}
	.placeholder-icon {
		font-size: 40px;
		margin-bottom: 8px;
	}
	.audio-tab-placeholder h3 {
		margin: 0;
		font-size: 18px;
		font-weight: 700;
	}
	.audio-tab-placeholder .subtitle {
		margin: 0;
		color: var(--gold);
		font-size: 13px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.audio-tab-placeholder .description {
		font-size: 13.5px;
		color: var(--muted);
		line-height: 1.5;
		margin-top: 6px;
	}
	.features-list {
		text-align: left;
		font-size: 12.5px;
		color: var(--muted);
		line-height: 1.6;
		margin: 10px 0;
		padding-left: 20px;
		display: flex;
		flex-direction: column;
		gap: 8px;
		max-width: 480px;
	}
	.mock-table-view {
		width: 100%;
		max-width: 600px;
		margin-top: 24px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: 6px;
		overflow: hidden;
	}
	.mock-header {
		background: var(--ink2);
		padding: 8px 12px;
		font-size: 10.5px;
		text-transform: uppercase;
		font-weight: 700;
		color: var(--faint);
		letter-spacing: 0.05em;
		text-align: left;
		border-bottom: 1px solid var(--line);
	}
	.mock-table {
		opacity: 0.5;
	}
	.btn-xs {
		padding: 3px 6px;
		font-size: 10.5px;
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
