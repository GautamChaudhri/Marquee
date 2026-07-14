<script lang="ts">
	import { CONFIGURATION_CONFLICT_MESSAGE, isConfigurationConflict } from '$lib/api/client';
	import { putSubgenSettings } from '$lib/api/subtitle-generators';
	import { getSettings } from '$lib/api/system';
	import type { RuntimeSettings, SubgenSettings } from '$lib/api/types';
	import { toast } from '$lib/toast';

	let {
		settings,
		configurationVersion,
		onClose,
		onReload
	}: {
		settings: NonNullable<RuntimeSettings['integrations']['subgen']>;
		configurationVersion: number;
		onClose: () => void;
		onReload: (settings: RuntimeSettings) => void;
	} = $props();

	let deployment = $state<'disabled' | 'external' | 'embedded'>('disabled');
	let url = $state('');
	let profileName = $state('Subgen');
	let modelLabel = $state('unknown');
	let mode = $state<'transcribe' | 'translate'>('transcribe');
	let localPathPrefix = $state('');
	let remotePathPrefix = $state('');
	let saving = $state(false);
	let initialized = $state(false);

	$effect(() => {
		if (initialized) return;
		deployment = settings.deployment ?? 'disabled';
		url = settings.url ?? '';
		profileName = settings.profile_name ?? 'Subgen';
		modelLabel = settings.model_label ?? 'unknown';
		mode = settings.mode === 'translate' ? 'translate' : 'transcribe';
		localPathPrefix = settings.local_path_prefix ?? '';
		remotePathPrefix = settings.remote_path_prefix ?? '';
		initialized = true;
	});

	async function saveSettings() {
		const payload: SubgenSettings = {
			deployment,
			url: url || null,
			profile_name: profileName || null,
			model_label: modelLabel || null,
			mode,
			local_path_prefix: localPathPrefix || null,
			remote_path_prefix: remotePathPrefix || null
		};

		saving = true;
		try {
			await putSubgenSettings(fetch, payload, configurationVersion);
			const fresh = await getSettings(fetch);
			onReload(fresh);
			toast('Subgen settings updated successfully', 'good');
			onClose();
		} catch (error) {
			if (isConfigurationConflict(error)) {
				onReload(await getSettings(fetch));
				toast(CONFIGURATION_CONFLICT_MESSAGE, 'info');
				return;
			}
			toast(error instanceof Error ? error.message : 'Failed to update settings', 'bad');
		} finally {
			saving = false;
		}
	}
</script>

<div class="modal-overlay">
	<div class="modal-card mq-rise">
		<div class="modal-header">
			<h4>Configure Subgen Subtitle Generator</h4>
			<button class="close-btn" onclick={onClose} aria-label="Close">&times;</button>
		</div>

		<div class="modal-body">
			<p class="ownership-note">
				Connection settings apply to new work immediately. Model, device, compute, and process
				concurrency are environment-owned and require a restart.
			</p>

			<div class="form-group">
				<label for="subgen-deployment">Deployment</label>
				<select id="subgen-deployment" bind:value={deployment}>
					<option value="disabled">Disabled</option>
					<option value="external">External</option>
					<option value="embedded">Embedded</option>
				</select>
			</div>

			<div class="settings-grid">
				<label class="form-group">
					<span>Subgen URL</span>
					<input type="text" bind:value={url} placeholder="http://localhost:8000" />
				</label>
				<label class="form-group">
					<span>Operation mode</span>
					<select bind:value={mode}>
						<option value="transcribe">Transcribe</option>
						<option value="translate">Translate</option>
					</select>
				</label>
				<label class="form-group">
					<span>Profile name</span>
					<input type="text" bind:value={profileName} />
				</label>
				<label class="form-group">
					<span>Model label</span>
					<input type="text" bind:value={modelLabel} />
				</label>
				<label class="form-group">
					<span>Local path prefix</span>
					<input type="text" bind:value={localPathPrefix} />
				</label>
				<label class="form-group">
					<span>Remote path prefix</span>
					<input type="text" bind:value={remotePathPrefix} />
				</label>
			</div>

			<div class="restart-grid">
				<span>Whisper model</span><strong>{settings.whisper_model ?? 'environment default'}</strong>
				<span>Device</span><strong>{settings.transcribe_device ?? 'environment default'}</strong>
				<span>Compute type</span><strong>{settings.compute_type ?? 'environment default'}</strong>
				<span>Concurrency</span><strong
					>{settings.concurrent_transcriptions ?? 'environment default'}</strong
				>
			</div>

			<div class="secret-state">
				Callback token:
				<strong>
					{settings.callback_token_configured
						? 'configured by environment'
						: 'not configured (environment only)'}
				</strong>
			</div>
		</div>

		<div class="modal-footer">
			<button class="btn secondary" onclick={onClose}>Cancel</button>
			<button class="btn primary" onclick={saveSettings} disabled={saving}>
				{saving ? 'Saving…' : 'Save'}
			</button>
		</div>
	</div>
</div>

<style>
	.modal-overlay {
		position: fixed;
		inset: 0;
		z-index: 1000;
		display: flex;
		align-items: center;
		justify-content: center;
		background: rgba(0, 0, 0, 0.7);
		backdrop-filter: blur(4px);
	}
	.modal-card {
		width: min(640px, calc(100vw - 32px));
		max-height: 90vh;
		overflow: auto;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.modal-header,
	.modal-footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 16px;
		border-bottom: 1px solid var(--line);
	}
	.modal-header h4 {
		margin: 0;
	}
	.modal-footer {
		justify-content: flex-end;
		border-top: 1px solid var(--line);
		border-bottom: 0;
		background: var(--panel2);
	}
	.close-btn {
		border: 0;
		background: transparent;
		color: var(--muted);
		font-size: 24px;
		cursor: pointer;
	}
	.modal-body {
		display: flex;
		flex-direction: column;
		gap: 18px;
		padding: 20px;
	}
	.ownership-note,
	.secret-state {
		margin: 0;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--muted);
		font-size: 12px;
		line-height: 1.5;
	}
	.settings-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 14px;
	}
	.form-group {
		display: flex;
		flex-direction: column;
		gap: 6px;
		color: var(--muted);
		font-size: 12px;
	}
	.form-group input,
	.form-group select {
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--text);
	}
	.restart-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px 16px;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--muted);
		font-size: 12px;
	}
	.restart-grid strong {
		color: var(--text);
		text-align: right;
	}
	.btn {
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		font-size: 13px;
		font-weight: 600;
		cursor: pointer;
	}
	.btn.primary {
		border: 1px solid var(--gold);
		background: var(--gold);
		color: var(--ink);
	}
	.btn.secondary {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.btn:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	@media (max-width: 640px) {
		.settings-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
