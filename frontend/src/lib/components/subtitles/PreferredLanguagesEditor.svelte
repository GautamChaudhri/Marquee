<script lang="ts">
	import { putAudioSubsPreferences } from '$lib/api/subtitles';
	import { getSettings } from '$lib/api/system';
	import { CONFIGURATION_CONFLICT_MESSAGE, isConfigurationConflict } from '$lib/api/client';
	import { toast } from '$lib/toast';

	let {
		preferred,
		configurationVersion,
		onSave
	}: {
		preferred: { audio: string[]; subtitles: string[]; shared: string[] };
		configurationVersion: number;
		onSave: () => void;
	} = $props();

	let separatePreferred = $state(false);
	let preferredShared = $state('');
	let preferredAudio = $state('');
	let preferredSubtitles = $state('');
	let saving = $state(false);
	let observedVersion = $derived(configurationVersion);

	$effect(() => {
		preferredShared = (preferred.shared || []).join(', ');
		preferredAudio = (preferred.audio || []).join(', ');
		preferredSubtitles = (preferred.subtitles || []).join(', ');
		separatePreferred =
			(preferred.audio || []).length > 0 || (preferred.subtitles || []).length > 0;
	});

	function parseLanguages(value: string) {
		return value
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);
	}

	async function savePreferredLanguages() {
		saving = true;
		try {
			const prefs = {
				preferred_languages: parseLanguages(preferredShared),
				preferred_audio_languages: separatePreferred ? parseLanguages(preferredAudio) : [],
				preferred_subtitle_languages: separatePreferred ? parseLanguages(preferredSubtitles) : []
			};
			const result = await putAudioSubsPreferences(fetch, prefs, observedVersion);
			observedVersion = result.configuration_version;
			toast('Subtitle preferences saved successfully', 'good');
			onSave();
		} catch (e: any) {
			if (isConfigurationConflict(e)) {
				observedVersion = (await getSettings(fetch)).configuration_version;
				toast(CONFIGURATION_CONFLICT_MESSAGE, 'info');
				return;
			}
			toast(e.message || 'Failed to save preferred languages', 'bad');
		} finally {
			saving = false;
		}
	}
</script>

<div class="preferences-panel mq-rise">
	<div class="preferences-head">
		<div>
			<h3>Preferred Languages</h3>
			<p>Used for audio/subtitle gap status across the library.</p>
		</div>
		<label class="toggle-row">
			<input type="checkbox" bind:checked={separatePreferred} />
			<span>Separate audio and subtitles</span>
		</label>
	</div>
	<div class="preferences-grid" class:split={separatePreferred}>
		<label class="setting-field">
			<span>{separatePreferred ? 'Shared fallback' : 'Audio & subtitles'}</span>
			<input type="text" bind:value={preferredShared} placeholder="en, es, fr" autocomplete="off" />
		</label>
		{#if separatePreferred}
			<label class="setting-field">
				<span>Audio</span>
				<input type="text" bind:value={preferredAudio} placeholder="en, es" autocomplete="off" />
			</label>
			<label class="setting-field">
				<span>Subtitles</span>
				<input
					type="text"
					bind:value={preferredSubtitles}
					placeholder="en, fr"
					autocomplete="off"
				/>
			</label>
		{/if}
		<button class="btn-save-preferences" onclick={savePreferredLanguages} disabled={saving}>
			{saving ? 'Saving...' : 'Save'}
		</button>
	</div>
</div>

<style>
	.preferences-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
		margin-bottom: 16px;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.preferences-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
	}
	.preferences-head h3 {
		margin: 0;
		font-size: 15px;
	}
	.preferences-head p {
		margin: 4px 0 0;
		font-size: 12.5px;
		color: var(--muted);
	}
	.toggle-row {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		color: var(--muted);
		font-size: 12.5px;
		cursor: pointer;
		white-space: nowrap;
	}
	.toggle-row input[type='checkbox'] {
		appearance: none;
		width: 16px;
		height: 16px;
		border-radius: 999px;
		border: 1px solid var(--line2);
		background: var(--ink2);
		cursor: pointer;
		box-shadow: inset 0 0 0 4px var(--ink2);
		transition:
			border-color 0.15s,
			background-color 0.15s,
			box-shadow 0.15s;
	}
	.toggle-row input[type='checkbox']:checked {
		border-color: var(--gold);
		background: var(--gold);
		box-shadow:
			0 0 0 3px var(--gold-soft),
			0 0 14px rgba(255, 190, 73, 0.35);
	}
	.preferences-grid {
		display: grid;
		grid-template-columns: minmax(220px, 1fr) auto;
		gap: 12px;
		align-items: end;
	}
	.preferences-grid.split {
		grid-template-columns: repeat(3, minmax(160px, 1fr)) auto;
	}
	.setting-field {
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 0;
	}
	.setting-field span {
		font-size: 11px;
		text-transform: uppercase;
		font-weight: 700;
		color: var(--faint2);
	}
	.setting-field input {
		width: 100%;
		padding: 9px 10px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 13px;
		outline: none;
	}
	.setting-field input:focus {
		border-color: var(--gold);
		box-shadow: 0 0 0 2px var(--gold-soft);
	}
	.btn-save-preferences {
		font-size: 13px;
		font-weight: 650;
		padding: 9px 16px;
		border-radius: var(--radius-sm);
		border: 1px solid color-mix(in srgb, var(--gold) 60%, transparent);
		background: var(--gold);
		color: var(--ink);
		cursor: pointer;
	}
	.btn-save-preferences:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	@media (max-width: 800px) {
		.preferences-head {
			align-items: flex-start;
			flex-direction: column;
		}
		.preferences-grid,
		.preferences-grid.split {
			grid-template-columns: 1fr;
		}
	}
</style>
