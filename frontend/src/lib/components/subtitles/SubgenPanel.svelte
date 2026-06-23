<script lang="ts">
	import { submitMovieGeneration, submitGeneration } from '$lib/api/subtitle-generators';
	import { listMovies } from '$lib/api/library';
	import type { MovieListItem } from '$lib/api/types';
	import ProgressBar from '../ProgressBar.svelte';
	import { subscribe } from '$lib/sse';
	import { toast } from '$lib/toast';

	let {
		movieId = null,
		mediaFileId = null,
		onComplete
	}: {
		movieId?: number | null;
		mediaFileId?: number | null;
		onComplete?: () => void;
	} = $props();

	// Whisper supported languages
	const LANGUAGES = [
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
		{ code: 'ar', label: 'Arabic' },
		{ code: 'ja', label: 'Japanese' },
		{ code: 'ko', label: 'Korean' },
		{ code: 'nl', label: 'Dutch' },
		{ code: 'pl', label: 'Polish' },
		{ code: 'sv', label: 'Swedish' },
		{ code: 'tr', label: 'Turkish' },
		{ code: 'uk', label: 'Ukrainian' },
		{ code: 'vi', label: 'Vietnamese' }
	];

	// Movie select state (for global tab mode)
	let movies = $state<MovieListItem[]>([]);
	let selectedMovieId = $state<number | null>(movieId);
	let loadingMovies = $state(false);

	// Form values
	let targetLang = $state('');
	let mode = $state<'transcribe' | 'translate'>('transcribe');
	let outputTarget = $state<'external' | 'embedded'>('external');

	// Job tracking state
	let generating = $state(false);
	let progressPercent = $state(0);
	let progressStage = $state('');
	let progressMessage = $state('');
	let activeJobId = $state<string | null>(null);

	// Fetch movies list if we are in global tab mode
	async function loadMoviesList() {
		if (movieId) return; // Locked to specific movie
		loadingMovies = true;
		try {
			const res = await listMovies(fetch, { page_size: 200 });
			// Filter movies that have a media_file_id
			movies = res.items.filter(m => m.media_file_id != null);
		} catch (e: any) {
			toast('Failed to load movies list', 'bad');
		} finally {
			loadingMovies = false;
		}
	}

	// Trigger AI generation
	async function handleGenerate() {
		const targetId = movieId || selectedMovieId;
		if (!targetId) {
			toast('Please select a movie first', 'info');
			return;
		}

		generating = true;
		progressPercent = 5;
		progressStage = 'starting';
		progressMessage = 'Submitting Whisper transcription request...';

		try {
			// Submit generation request
			const res = await submitMovieGeneration(fetch, targetId, {
				language_hint: targetLang || null,
				output: outputTarget
			});

			activeJobId = res.job_id;
			progressMessage = 'Job queued...';

			// Subscribe to SSE events
			const unsub = subscribe(`/api/media-jobs/${res.job_id}/events`, ['progress', 'complete', 'error'], (type, data: any) => {
				if (type === 'progress') {
					progressPercent = data.percent ?? progressPercent;
					progressStage = data.stage ?? progressStage;
					progressMessage = data.message ?? progressMessage;
				} else if (type === 'complete') {
					unsub();
					generating = false;
					activeJobId = null;
					if (data.status === 'completed' || data.status === 'succeeded') {
						toast('Subtitles generated successfully!', 'good');
						if (onComplete) onComplete();
					} else {
						toast(`Generation failed: ${data.error || 'Unknown error'}`, 'bad');
					}
				} else if (type === 'error') {
					unsub();
					generating = false;
					activeJobId = null;
					toast('Connection to generation progress lost', 'bad');
				}
			});

		} catch (e: any) {
			toast(e.message || 'Generation submission failed', 'bad');
			generating = false;
		}
	}

	$effect(() => {
		loadMoviesList();
	});
</script>

<div class="subgen-panel">
	{#if generating}
		<div class="generation-progress mq-rise">
			<h5>Generating Subtitles via AI</h5>
			<div class="job-id">Job ID: <code>{activeJobId}</code></div>
			<div class="stage">{progressStage.toUpperCase()}</div>
			<ProgressBar value={progressPercent} />
			<p class="progress-message">{progressMessage}</p>
		</div>
	{:else}
		<form onsubmit={(e) => { e.preventDefault(); handleGenerate(); }}>
			<!-- Movie selector (shown in global tab mode) -->
			{#if !movieId}
				<div class="form-group">
					<label for="movie-select">Select Movie</label>
					{#if loadingMovies}
						<select id="movie-select" disabled>
							<option>Loading movies...</option>
						</select>
					{:else}
						<select id="movie-select" bind:value={selectedMovieId}>
							<option value={null}>-- Choose a movie --</option>
							{#each movies as m}
								<option value={m.id}>{m.title} ({m.year})</option>
							{/each}
						</select>
					{/if}
				</div>
			{/if}

			<!-- Target Language -->
			<div class="form-group">
				<label for="lang-select">Audio Language Hint</label>
				<select id="lang-select" bind:value={targetLang}>
					{#each LANGUAGES as lang}
						<option value={lang.code}>{lang.label}</option>
					{/each}
				</select>
				<small class="help">Providing the spoken language avoids Whisper auto-detection errors.</small>
			</div>

			<!-- Output Type -->
			<div class="form-group radio-group">
				<label>Output Target</label>
				<div class="radio-options">
					<label class="radio-option">
						<input type="radio" value="external" bind:group={outputTarget} />
						<div class="opt-desc">
							<strong>External Sidecar (.srt)</strong>
							<span>Saves as Plex-compatible sidecar file next to the video. (Recommended)</span>
						</div>
					</label>
					<label class="radio-option">
						<input type="radio" value="embedded" bind:group={outputTarget} />
						<div class="opt-desc">
							<strong>Embed in Container</strong>
							<span>Remuxes the container to embed the generated track. Preservation remux.</span>
						</div>
					</label>
				</div>
			</div>

			<!-- Trigger button -->
			<div class="form-foot">
				<button class="btn primary" type="submit" disabled={!movieId && !selectedMovieId}>
					🚀 Generate Subtitles
				</button>
			</div>
		</form>
	{/if}
</div>

<style>
	.subgen-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 18px;
		color: var(--text);
	}
	.form-group {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin-bottom: 16px;
	}
	.form-group label {
		font-size: 12px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--faint2);
	}
	.form-group select {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 10px;
		font-size: 13.5px;
		color: var(--text);
		outline: none;
	}
	.form-group select:focus {
		border-color: var(--gold);
	}
	.help {
		color: var(--muted);
		font-size: 11px;
	}

	.radio-group label {
		margin-bottom: 4px;
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
		transition: border-color 0.15s;
	}
	.radio-option:hover {
		border-color: var(--line2);
	}
	.radio-option input {
		margin-top: 3px;
		accent-color: var(--gold);
	}
	.opt-desc {
		display: flex;
		flex-direction: column;
		gap: 2px;
		font-size: 13px;
	}
	.opt-desc strong {
		color: var(--text);
	}
	.opt-desc span {
		color: var(--muted);
		font-size: 11.5px;
	}

	.form-foot {
		display: flex;
		justify-content: flex-end;
		border-top: 1px solid var(--line);
		padding-top: 14px;
		margin-top: 20px;
	}

	/* Progress view */
	.generation-progress {
		text-align: center;
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.generation-progress h5 {
		margin: 0;
		font-size: 15px;
		color: var(--text);
	}
	.generation-progress .job-id {
		font-size: 12px;
		color: var(--muted);
	}
	.generation-progress .stage {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--gold);
		font-weight: 700;
	}
	.progress-message {
		font-size: 13px;
		color: var(--muted);
	}

	/* Buttons */
	.btn {
		font-size: 13px;
		font-weight: 600;
		padding: 10px 20px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
		transition: background-color 0.15s;
	}
	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--on-gold);
	}
	.btn.primary:hover:not(:disabled) {
		background: var(--gold-deep);
	}
</style>
