<script lang="ts">
	import type { TextProfileScope } from '$lib/api/text-profiles';
	import {
		categoriesFor,
		PRESET_OPTIONS,
		presetSettings,
		type PresetId,
		type ProfileDraft
	} from './text-profile-fields';

	let {
		draft,
		scope = 'movie',
		busy = false,
		showPresets = false
	}: {
		/** Mutated in place — the owning dialog holds it as `$state` and reads it on save. */
		draft: ProfileDraft;
		scope?: TextProfileScope;
		busy?: boolean;
		showPresets?: boolean;
	} = $props();

	let preset = $state<PresetId>('title_only');
	const categories = $derived(categoriesFor(scope));
	const areaPercent = $derived(Math.round(draft.settings.max_residual_area_fraction * 100));

	function applyPreset(next: PresetId) {
		preset = next;
		draft.settings = presetSettings(next, scope);
	}
</script>

<div class="editor">
	{#if showPresets}
		<div class="field">
			<span class="flabel">Start From</span>
			<div class="presets">
				{#each PRESET_OPTIONS as option (option.id)}
					<button
						type="button"
						class="preset"
						class:on={preset === option.id}
						disabled={busy}
						onclick={() => applyPreset(option.id)}
					>
						<strong>{option.label}</strong>
						<span>{option.description}</span>
					</button>
				{/each}
			</div>
		</div>
	{/if}

	<div class="field">
		<label class="flabel" for="tp-name">Name</label>
		<input
			id="tp-name"
			bind:value={draft.name}
			disabled={busy}
			maxlength="64"
			placeholder="Profile name"
		/>
	</div>

	<div class="field">
		<span class="flabel">Allowed Text</span>
		<div class="grid">
			{#each categories as category (category.key)}
				<label class="toggle" title={category.hint}>
					<input
						type="checkbox"
						checked={Boolean(draft.settings[category.key])}
						disabled={busy}
						onchange={(e) =>
							(draft.settings[category.key] = (e.currentTarget as HTMLInputElement).checked)}
					/>
					<span>{category.label}</span>
				</label>
			{/each}
		</div>
	</div>

	<div class="sliders">
		<div class="field">
			<label class="flabel" for="tp-boxes">Residual Boxes</label>
			<div class="range-row">
				<input
					id="tp-boxes"
					type="range"
					min="0"
					max="20"
					step="1"
					value={draft.settings.max_residual_boxes}
					disabled={busy}
					oninput={(e) =>
						(draft.settings.max_residual_boxes = Number(
							(e.currentTarget as HTMLInputElement).value
						))}
				/>
				<span class="range-val mono">{draft.settings.max_residual_boxes}</span>
			</div>
		</div>
		<div class="field">
			<label class="flabel" for="tp-area">Residual Area</label>
			<div class="range-row">
				<input
					id="tp-area"
					type="range"
					min="0"
					max="10"
					step="1"
					value={areaPercent}
					disabled={busy}
					oninput={(e) =>
						(draft.settings.max_residual_area_fraction =
							Number((e.currentTarget as HTMLInputElement).value) / 100)}
				/>
				<span class="range-val mono">{areaPercent}%</span>
			</div>
		</div>
	</div>

	<label class="require-row" title="Require at least one title match for the poster to pass OCR.">
		<input type="checkbox" bind:checked={draft.settings.require_title} disabled={busy} />
		<span>Require title match</span>
	</label>
</div>

<style>
	.editor {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.flabel {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.field > input {
		border: 1px solid var(--line2);
		background: var(--ink2);
		color: var(--text);
		border-radius: 8px;
		padding: 8px 10px;
		font-size: 13px;
	}
	.presets {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 8px;
	}
	.preset {
		display: flex;
		flex-direction: column;
		gap: 3px;
		text-align: left;
		padding: 9px 10px;
		border: 1px solid var(--line2);
		border-radius: 10px;
		background: var(--panel2);
		color: var(--text);
		cursor: pointer;
	}
	.preset.on {
		border-color: color-mix(in srgb, var(--gold) 40%, transparent);
		background: color-mix(in srgb, var(--gold) 9%, var(--panel2));
	}
	.preset strong {
		font-size: 12.5px;
	}
	.preset span {
		font-size: 11px;
		color: var(--muted);
		line-height: 1.4;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 8px;
	}
	@media (max-width: 620px) {
		.grid,
		.presets {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 9px 10px;
		border: 1px solid var(--line2);
		border-radius: 10px;
		background: var(--panel2);
		font-size: 12px;
		color: var(--text);
	}
	.toggle input,
	.require-row input {
		accent-color: var(--gold);
	}
	.sliders {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}
	@media (max-width: 620px) {
		.sliders {
			grid-template-columns: 1fr;
		}
	}
	.range-row {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.range-row input[type='range'] {
		flex: 1;
		accent-color: var(--gold);
	}
	.range-val {
		min-width: 42px;
		text-align: right;
		font-size: 12px;
		color: var(--muted);
	}
	.require-row {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12px;
		color: var(--text);
	}
	input:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.mono {
		font-variant-numeric: tabular-nums;
	}
</style>
