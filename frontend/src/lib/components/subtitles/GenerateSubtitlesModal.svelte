<script lang="ts">
	import { generateTv } from '$lib/api/subtitles';
	import { submitGeneration } from '$lib/api/subtitle-generators';
	import type { AudioStreamInfo } from '$lib/api/types';
	import { toast } from '$lib/toast';

	let {
		seriesId,
		scope, // 'series' | 'season' | 'episode'
		seasonNumber = null,
		episodeId = null,
		mediaFileId = null,
		audioLanguages = [],
		audioStreams = [],
		generator = null,
		onClose,
		onSuccess
	}: {
		seriesId: number;
		scope: 'series' | 'season' | 'episode';
		seasonNumber?: number | null;
		episodeId?: number | null;
		mediaFileId?: number | null;
		audioLanguages?: string[];
		audioStreams?: AudioStreamInfo[];
		generator?: any;
		onClose: () => void;
		onSuccess: (jobId: string, isBatch: boolean) => void;
	} = $props();

	let languageHint = $state(audioLanguages[0] || 'en');
	let output = $state<'external' | 'embedded'>('external');
	let task = $state<'transcribe' | 'translate'>('transcribe');
	let streamIndex = $state<number | null>(null);
	let advancedOpen = $state(false);
	let generating = $state(false);

	const translateAvailable = $derived(generator?.capabilities?.translate ?? false);

	async function handleSubmit() {
		generating = true;
		try {
			if (scope === 'episode' && mediaFileId) {
				const req = {
					language_hint: languageHint,
					output,
					task,
					stream_index: streamIndex ?? undefined
				};
				const res = await submitGeneration(fetch, mediaFileId, req as any);
				toast('Subtitle generation job started', 'good');
				onSuccess(res.job_id, false);
			} else {
				const req = {
					season_number: seasonNumber ?? undefined,
					language_hint: languageHint,
					output,
					task,
					stream_index: streamIndex ?? undefined
				};
				const res = await generateTv(fetch, seriesId, req as any);
				toast('TV Subtitle batch generation enqueued', 'good');
				onSuccess(res.job_id, true);
			}
		} catch (e: any) {
			toast(e.message || 'Failed to start subtitle generation', 'bad');
		} finally {
			generating = false;
		}
	}
</script>

<div class="modal-overlay">
	<div class="modal-card mq-rise">
		<div class="modal-header">
			<h4>Generate AI Subtitles</h4>
			<button class="close-btn" onclick={onClose}>&times;</button>
		</div>

		<div class="modal-body">
			<div class="scope-info">
				<strong>Target Scope:</strong>
				{#if scope === 'series'}
					<span>Entire Show (All Seasons)</span>
				{:else if scope === 'season'}
					<span>Season {seasonNumber}</span>
				{:else}
					<span>Episode {episodeId}</span>
				{/if}
			</div>

			<!-- Language selector -->
			<div class="form-group">
				<label for="source-lang">Audio Source Language</label>
				<select id="source-lang" bind:value={languageHint}>
					{#each audioLanguages as lang}
						<option value={lang}>{lang.toUpperCase()}</option>
					{:else}
						<option value="en">English (default)</option>
						<option value="es">Spanish</option>
						<option value="fr">French</option>
					{/each}
				</select>
			</div>

			<!-- Output type -->
			<div class="form-section">
				<span class="section-title">Output Destination</span>
				<div class="toggle-group">
					<button
						class="toggle-btn"
						class:active={output === 'external'}
						onclick={() => (output = 'external')}
					>
						External SRT File
					</button>
					<button
						class="toggle-btn"
						class:active={output === 'embedded'}
						onclick={() => (output = 'embedded')}
					>
						Embed in MKV/MP4
					</button>
				</div>
			</div>

			<!-- Task: transcribe vs translate -->
			<div class="form-section">
				<span class="section-title">AI Action</span>
				<div class="toggle-group">
					<button
						class="toggle-btn"
						class:active={task === 'transcribe'}
						onclick={() => (task = 'transcribe')}
					>
						Transcribe
					</button>
					<button
						class="toggle-btn"
						class:active={task === 'translate'}
						disabled={!translateAvailable}
						onclick={() => (task = 'translate')}
					>
						Translate to English
					</button>
				</div>
				{#if !translateAvailable}
					<small class="unavailable-hint">
						⚠️ English translation is not supported by the currently loaded Whisper model.
					</small>
				{/if}
			</div>

			<!-- Advanced disclosure -->
			<details class="advanced-disclosure" bind:open={advancedOpen}>
				<summary>Advanced Options</summary>
				<div class="disclosure-content">
					{#if scope === 'episode' && audioStreams && audioStreams.length > 0}
						<div class="form-group">
							<label for="audio-track">Exact Audio Track Picker</label>
							<select id="audio-track" bind:value={streamIndex}>
								<option value={null}>Auto-select best track</option>
								{#each audioStreams as stream}
									<option value={stream.index}>
										Track {stream.index}: {stream.language_tag.toUpperCase()} ({stream.channels}ch) {stream.title
											? `— ${stream.title}`
											: ''}
									</option>
								{/each}
							</select>
						</div>
					{:else}
						<p class="no-tracks-hint">
							Exact audio track picker is only available at the episode level for probed files.
						</p>
					{/if}
				</div>
			</details>
		</div>

		<div class="modal-footer">
			<button class="btn secondary" onclick={onClose} disabled={generating}>Cancel</button>
			<button class="btn primary" onclick={handleSubmit} disabled={generating}>
				{generating ? 'Submitting...' : 'Generate Subtitles'}
			</button>
		</div>
	</div>
</div>

<style>
	.modal-overlay {
		position: fixed;
		top: 0;
		left: 0;
		right: 0;
		bottom: 0;
		background: rgba(0, 0, 0, 0.7);
		backdrop-filter: blur(4px);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 1100;
	}
	.modal-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		width: 100%;
		max-width: 480px;
		display: flex;
		flex-direction: column;
	}
	.modal-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 16px;
		border-bottom: 1px solid var(--line);
	}
	.modal-header h4 {
		margin: 0;
		font-size: 15px;
		font-weight: 650;
	}
	.close-btn {
		background: transparent;
		border: none;
		font-size: 22px;
		cursor: pointer;
		color: var(--muted);
	}
	.modal-body {
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.scope-info {
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 10px 12px;
		font-size: 13px;
		display: flex;
		justify-content: space-between;
	}
	.form-section {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.section-title {
		font-size: 10px;
		text-transform: uppercase;
		font-weight: 700;
		color: var(--faint2);
	}
	.toggle-group {
		display: flex;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: var(--ink2);
	}
	.toggle-btn {
		flex: 1;
		border: none;
		background: transparent;
		padding: 8px;
		font-size: 12.5px;
		font-weight: 600;
		color: var(--muted);
		cursor: pointer;
		transition:
			background-color 0.15s,
			color 0.15s;
	}
	.toggle-btn.active {
		background: var(--gold);
		color: var(--ink);
	}
	.toggle-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.unavailable-hint {
		font-size: 11px;
		color: var(--warn);
	}
	.form-group {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.form-group label {
		font-size: 11.5px;
		color: var(--muted);
	}
	.form-group select {
		padding: 8px 10px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 13px;
		outline: none;
	}
	.advanced-disclosure {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 10px;
	}
	.advanced-disclosure summary {
		font-size: 12px;
		font-weight: 600;
		cursor: pointer;
		outline: none;
		color: var(--muted);
	}
	.disclosure-content {
		margin-top: 10px;
		padding-top: 10px;
		border-top: 1px dashed var(--line);
	}
	.no-tracks-hint {
		margin: 0;
		font-size: 11.5px;
		color: var(--muted);
		font-style: italic;
	}
	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		padding: 12px 16px;
		border-top: 1px solid var(--line);
		background: var(--panel2);
		border-bottom-left-radius: var(--radius);
		border-bottom-right-radius: var(--radius);
	}
	.btn {
		font-size: 12.5px;
		font-weight: 600;
		padding: 8px 14px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--ink);
	}
	.btn.primary:disabled {
		opacity: 0.6;
	}
	.btn.secondary {
		background: transparent;
		border: 1px solid var(--line);
		color: var(--text);
	}
</style>
