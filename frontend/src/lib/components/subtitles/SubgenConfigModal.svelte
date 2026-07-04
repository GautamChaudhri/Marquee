<script lang="ts">
	import { getSubgenHardware, putSubgenSettings } from '$lib/api/subtitle-generators';
	import type { SubgenHardwareResponse, SubgenSettings } from '$lib/api/types';
	import { toast } from '$lib/toast';

	let {
		settings,
		onClose,
		onSave
	}: {
		settings: any;
		onClose: () => void;
		onSave: (settings: any) => void;
	} = $props();

	let hwInfo = $state<SubgenHardwareResponse | null>(null);
	let loading = $state(true);

	// Form fields
	let deployment = $state<'disabled' | 'external' | 'embedded'>('disabled');
	let url = $state('');
	let mode = $state<'transcribe' | 'translate'>('transcribe');
	let whisperModel = $state('large-v3-turbo');
	let transcribeDevice = $state<'auto' | 'cpu' | 'cuda'>('auto');
	let gpuIndex = $state<number>(0);
	let computeType = $state('float16');
	let concurrentTranscriptions = $state(1);
	let whisperThreads = $state(4);
	let modelPath = $state('');

	$effect(() => {
		deployment = settings.SUBGEN_DEPLOYMENT || 'disabled';
		url = settings.SUBGEN_URL || '';
		mode = settings.SUBGEN_MODE || 'transcribe';
		whisperModel = settings.SUBGEN_WHISPER_MODEL || 'large-v3-turbo';
		transcribeDevice = settings.SUBGEN_TRANSCRIBE_DEVICE || 'auto';
		gpuIndex = settings.SUBGEN_GPU_INDEX ?? 0;
		computeType = settings.SUBGEN_COMPUTE_TYPE || 'float16';
		concurrentTranscriptions = settings.SUBGEN_CONCURRENT_TRANSCRIPTIONS ?? 1;
		whisperThreads = settings.SUBGEN_WHISPER_THREADS ?? 4;
		modelPath = settings.SUBGEN_MODEL_PATH || '';
	});

	async function loadHardware() {
		try {
			hwInfo = await getSubgenHardware(fetch);
		} catch (e: any) {
			toast(e.message || 'Failed to load hardware specs', 'bad');
		} finally {
			loading = false;
		}
	}

	function applyRecommendation() {
		if (hwInfo?.recommendation) {
			whisperModel = hwInfo.recommendation.model;
			transcribeDevice = hwInfo.recommendation.device;
			gpuIndex = hwInfo.recommendation.gpu_index ?? 0;
			computeType = hwInfo.recommendation.compute_type;
		}
	}

	async function saveSettings() {
		const payload: SubgenSettings = {
			deployment,
			url,
			mode,
			whisper_model: whisperModel,
			transcribe_device: transcribeDevice,
			gpu_index: transcribeDevice === 'cuda' ? gpuIndex : null,
			compute_type: computeType,
			concurrent_transcriptions: concurrentTranscriptions,
			whisper_threads: whisperThreads,
			model_path: whisperModel === 'custom' ? modelPath : null
		};

		try {
			const res = await putSubgenSettings(fetch, payload);
			toast('Subgen settings updated successfully', 'good');
			onSave(res.settings);
		} catch (e: any) {
			toast(e.message || 'Failed to update settings', 'bad');
		}
	}

	$effect(() => {
		loadHardware();
	});
</script>

<div class="modal-overlay">
	<div class="modal-card mq-rise">
		<div class="modal-header">
			<h4>Configure Subgen Subtitle Generator</h4>
			<button class="close-btn" onclick={onClose}>&times;</button>
		</div>

		<div class="modal-body">
			<!-- Deployment Mode -->
			<div class="form-section">
				<span class="section-title">Deployment Mode</span>
				<div class="toggle-group">
					<button
						class="toggle-btn"
						class:active={deployment === 'disabled'}
						onclick={() => (deployment = 'disabled')}
					>
						Disabled
					</button>
					<button
						class="toggle-btn"
						class:active={deployment === 'external'}
						onclick={() => (deployment = 'external')}
					>
						External
					</button>
					<button
						class="toggle-btn"
						class:active={deployment === 'embedded'}
						onclick={() => (deployment = 'embedded')}
					>
						Embedded (Self-hosted GPU/CPU)
					</button>
				</div>
			</div>

			{#if deployment === 'external'}
				<div class="form-group">
					<label for="subgen-url">Subgen URL</label>
					<input type="text" id="subgen-url" bind:value={url} placeholder="http://localhost:8000" />
				</div>
			{/if}

			{#if deployment === 'embedded'}
				{#if loading}
					<div class="loading-hw">Loading system capabilities...</div>
				{:else if hwInfo}
					<!-- Recommendations Spotlight -->
					{#if hwInfo.recommendation}
						<div class="recommendation-spotlight">
							<div class="header">
								<span class="star">⭐</span>
								<span class="title">Recommended Settings</span>
								<button class="btn-apply-rec" onclick={applyRecommendation}>
									Apply Recommendation
								</button>
							</div>
							<p class="reason">{hwInfo.recommendation.reason}</p>
							<div class="details">
								<span>Model: {hwInfo.recommendation.model}</span>
								<span>Device: {hwInfo.recommendation.device.toUpperCase()}</span>
								<span>Compute: {hwInfo.recommendation.compute_type}</span>
							</div>
						</div>
					{/if}

					<!-- Settings Grid -->
					<div class="settings-grid">
						<!-- Mode -->
						<div class="form-group">
							<label for="subgen-mode">Operation Mode</label>
							<select id="subgen-mode" bind:value={mode}>
								<option value="transcribe">Transcribe (Original Language)</option>
								<option value="translate">Translate to English</option>
							</select>
						</div>

						<!-- Whisper Model -->
						<div class="form-group">
							<label for="subgen-model">Whisper Model</label>
							<select id="subgen-model" bind:value={whisperModel}>
								{#each hwInfo.catalog as spec}
									{@const status = hwInfo.models[spec.id]}
									{@const isDisabled =
										status?.verdict === 'too_big' ||
										(mode === 'translate' && spec.can_translate === false)}
									{@const label =
										spec.id === 'custom'
											? 'Custom Model (Specify below)'
											: `${spec.id} (${spec.params} params)`}
									<option value={spec.id} disabled={isDisabled}>
										{label}
										{#if status?.verdict === 'too_big'}
											— too large for this system
										{:else}
											{spec.notes ? ` — ${spec.notes}` : ''}
										{/if}
										{#if mode === 'translate' && spec.can_translate === false}
											— doesn't support translation
										{/if}
									</option>
								{/each}
							</select>
						</div>

						{#if whisperModel === 'custom'}
							<div class="form-group full-width">
								<label for="subgen-model-path"
									>Custom Model Path (HuggingFace repo or local folder)</label
								>
								<input
									type="text"
									id="subgen-model-path"
									bind:value={modelPath}
									placeholder="systran/faster-whisper-large-v3"
								/>
							</div>
						{/if}

						<!-- Device -->
						<div class="form-group">
							<label for="subgen-device">Hardware Device</label>
							<select id="subgen-device" bind:value={transcribeDevice}>
								<option value="auto">Auto-detect</option>
								<option value="cpu">CPU Only</option>
								{#if hwInfo.hardware.gpus.length > 0}
									<option value="cuda">NVIDIA GPU (CUDA)</option>
								{/if}
							</select>
						</div>

						<!-- GPU Selection -->
						{#if transcribeDevice === 'cuda' && hwInfo.hardware.gpus.length > 0}
							<div class="form-group">
								<label for="subgen-gpu">Select GPU</label>
								<select id="subgen-gpu" bind:value={gpuIndex}>
									{#each hwInfo.hardware.gpus as gpu}
										<option value={gpu.index}>
											GPU {gpu.index}: {gpu.name} ({Math.round(
												gpu.vram_total / 1024 / 1024 / 1024
											)}GB VRAM)
										</option>
									{/each}
								</select>
							</div>
						{/if}

						<!-- Compute Type -->
						<div class="form-group">
							<label for="subgen-compute">Compute Type</label>
							<select id="subgen-compute" bind:value={computeType}>
								<option value="float16">float16 (Optimal for GPU)</option>
								<option value="int8">int8 (Optimal for CPU / low VRAM)</option>
								<option value="float32">float32 (Highest precision)</option>
							</select>
						</div>

						<!-- Concurrency -->
						<div class="form-group">
							<label for="subgen-concurrency">Concurrency limit</label>
							<input
								type="number"
								id="subgen-concurrency"
								bind:value={concurrentTranscriptions}
								min="1"
								max="16"
							/>
						</div>

						<!-- CPU Threads -->
						{#if transcribeDevice === 'cpu' || transcribeDevice === 'auto'}
							<div class="form-group">
								<label for="subgen-threads">CPU Threads</label>
								<input
									type="number"
									id="subgen-threads"
									bind:value={whisperThreads}
									min="1"
									max={hwInfo.hardware.cpu_count}
								/>
							</div>
						{/if}
					</div>
				{/if}
			{/if}
		</div>

		<div class="modal-footer">
			<button class="btn secondary" onclick={onClose}>Cancel</button>
			<button class="btn primary" onclick={saveSettings}>Save Config</button>
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
		z-index: 1000;
	}
	.modal-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		width: 100%;
		max-width: 640px;
		max-height: 90vh;
		display: flex;
		flex-direction: column;
		overflow: hidden;
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
		font-size: 16px;
		font-weight: 600;
	}
	.close-btn {
		background: transparent;
		border: none;
		font-size: 24px;
		cursor: pointer;
		color: var(--muted);
	}
	.close-btn:hover {
		color: var(--text);
	}
	.modal-body {
		padding: 20px;
		overflow-y: auto;
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.form-section {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.section-title {
		font-size: 11px;
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
		padding: 10px;
		font-size: 13px;
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
	.form-group {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.form-group.full-width {
		grid-column: 1 / -1;
	}
	.form-group label {
		font-size: 12px;
		color: var(--muted);
	}
	.form-group input,
	.form-group select {
		padding: 8px 12px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 13px;
		outline: none;
	}
	.form-group input:focus,
	.form-group select:focus {
		border-color: var(--gold);
	}
	.settings-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 16px;
	}
	.recommendation-spotlight {
		background: color-mix(in srgb, var(--gold) 8%, var(--ink));
		border: 1px solid color-mix(in srgb, var(--gold) 25%, transparent);
		border-radius: var(--radius-sm);
		padding: 12px 14px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.recommendation-spotlight .header {
		display: flex;
		align-items: center;
		gap: 8px;
		font-weight: 650;
	}
	.recommendation-spotlight .header .title {
		font-size: 13.5px;
		color: var(--gold);
	}
	.btn-apply-rec {
		margin-left: auto;
		background: var(--gold);
		border: none;
		color: var(--ink);
		font-size: 11.5px;
		font-weight: 700;
		padding: 4px 8px;
		border-radius: 4px;
		cursor: pointer;
	}
	.recommendation-spotlight .reason {
		font-size: 12px;
		color: var(--text);
		margin: 0;
	}
	.recommendation-spotlight .details {
		display: flex;
		gap: 16px;
		font-size: 11px;
		font-family: var(--font-mono);
		color: var(--muted);
	}
	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		padding: 16px;
		border-top: 1px solid var(--line);
		background: var(--panel2);
	}
	.btn {
		font-size: 13px;
		font-weight: 600;
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--ink);
	}
	.btn.secondary {
		background: transparent;
		border: 1px solid var(--line);
		color: var(--text);
	}
	.loading-hw {
		padding: 24px;
		text-align: center;
		color: var(--muted);
	}
</style>
