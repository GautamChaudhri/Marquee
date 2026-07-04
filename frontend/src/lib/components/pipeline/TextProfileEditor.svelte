<script lang="ts">
	import type { TextProfile, TextProfileScope, TextProfileSettings } from '$lib/api/text-profiles';

	let {
		profile,
		scope = 'movie',
		busy = false,
		onSave,
		onDelete
	}: {
		profile: TextProfile;
		scope?: TextProfileScope;
		busy?: boolean;
		onSave: (payload: { name: string; settings: TextProfileSettings }) => void;
		onDelete?: (() => void) | null;
	} = $props();

	let loadedId = $state<string | null>(null);
	let name = $state('');
	let allowTitle = $state(false);
	let allowDirector = $state(false);
	let allowStudio = $state(false);
	let allowRating = $state(false);
	let allowTagline = $state(false);
	let allowBilling = $state(false);
	let allowSeason = $state(false);
	let maxResidualBoxes = $state(0);
	let maxResidualAreaPercent = $state(0);
	let requireTitle = $state(false);

	function resetFromProfile(next: TextProfile) {
		loadedId = next.id;
		name = next.name;
		allowTitle = next.settings.allow_title;
		allowDirector = next.settings.allow_director;
		allowStudio = next.settings.allow_studio;
		allowRating = next.settings.allow_rating;
		allowTagline = next.settings.allow_tagline;
		allowBilling = next.settings.allow_billing;
		allowSeason = Boolean(next.settings.allow_season);
		maxResidualBoxes = next.settings.max_residual_boxes;
		maxResidualAreaPercent = Math.round(next.settings.max_residual_area_fraction * 100);
		requireTitle = next.settings.require_title;
	}

	$effect(() => {
		if (loadedId !== profile.id) resetFromProfile(profile);
	});

	const builtIn = $derived(profile.builtin);
	const trimmedName = $derived(name.trim());
	const draftSettings = $derived({
		mode: builtIn ? profile.settings.mode : 'custom',
		allow_title: allowTitle,
		allow_director: allowDirector,
		allow_studio: allowStudio,
		allow_rating: allowRating,
		allow_tagline: allowTagline,
		allow_billing: allowBilling,
		allow_season: allowSeason,
		max_residual_boxes: maxResidualBoxes,
		max_residual_area_fraction: maxResidualAreaPercent / 100,
		require_title: requireTitle
	});
	const dirty = $derived(
		trimmedName !== profile.name ||
			JSON.stringify(draftSettings) !== JSON.stringify(profile.settings)
	);

	function save() {
		if (busy || builtIn || !trimmedName) return;
		onSave({
			name: trimmedName,
			settings: {
				mode: 'custom',
				allow_title: allowTitle,
				allow_director: allowDirector,
				allow_studio: allowStudio,
				allow_rating: allowRating,
				allow_tagline: allowTagline,
				allow_billing: allowBilling,
				allow_season: allowSeason,
				max_residual_boxes: maxResidualBoxes,
				max_residual_area_fraction: maxResidualAreaPercent / 100,
				require_title: requireTitle
			}
		});
	}
</script>

<section class="editor">
	<div class="editor-head">
		<div>
			<div class="eyebrow">Profile editor</div>
			<h3>{name || profile.name}</h3>
		</div>
		{#if builtIn}
			<span class="readonly">Built-in preset</span>
		{:else if dirty}
			<span class="unsaved">Unsaved changes</span>
		{/if}
	</div>

	<div class="field">
		<label for="tp-name">Name</label>
		<input
			id="tp-name"
			bind:value={name}
			disabled={builtIn || busy}
			maxlength="64"
			placeholder="Profile name"
		/>
	</div>

	<div class="grid">
		<label class="toggle" title="Allow the movie title text itself.">
			<input type="checkbox" bind:checked={allowTitle} disabled={builtIn || busy} />
			<span>Title</span>
		</label>
		<label class="toggle" title="Allow directed-by or film-by credits.">
			<input type="checkbox" bind:checked={allowDirector} disabled={builtIn || busy} />
			<span>Director</span>
		</label>
		<label class="toggle" title="Allow studio branding like Syncopy or Marvel Studios.">
			<input type="checkbox" bind:checked={allowStudio} disabled={builtIn || busy} />
			<span>Studio</span>
		</label>
		<label class="toggle" title="Allow MPAA or similar rating marks.">
			<input type="checkbox" bind:checked={allowRating} disabled={builtIn || busy} />
			<span>Rating</span>
		</label>
		<label class="toggle" title="Allow promotional taglines.">
			<input type="checkbox" bind:checked={allowTagline} disabled={builtIn || busy} />
			<span>Tagline</span>
		</label>
		<label class="toggle" title="Allow actor billing strips and credit bands.">
			<input type="checkbox" bind:checked={allowBilling} disabled={builtIn || busy} />
			<span>Billing</span>
		</label>
		{#if scope === 'season'}
			<label class="toggle" title="Allow season text such as Season 3 or Part 2.">
				<input type="checkbox" bind:checked={allowSeason} disabled={builtIn || busy} />
				<span>Season text</span>
			</label>
		{/if}
	</div>

	<div class="sliders">
		<div class="field">
			<label for="tp-boxes">Residual boxes</label>
			<div class="range-row">
				<input
					id="tp-boxes"
					type="range"
					min="0"
					max="20"
					step="1"
					value={maxResidualBoxes}
					disabled={builtIn || busy}
					oninput={(e) => (maxResidualBoxes = Number((e.currentTarget as HTMLInputElement).value))}
				/>
				<span class="range-val mono">{maxResidualBoxes}</span>
			</div>
		</div>
		<div class="field">
			<label for="tp-area">Residual area</label>
			<div class="range-row">
				<input
					id="tp-area"
					type="range"
					min="0"
					max="10"
					step="1"
					value={maxResidualAreaPercent}
					disabled={builtIn || busy}
					oninput={(e) =>
						(maxResidualAreaPercent = Number((e.currentTarget as HTMLInputElement).value))}
				/>
				<span class="range-val mono">{maxResidualAreaPercent}%</span>
			</div>
		</div>
	</div>

	<label class="require-row" title="Require at least one title match for the poster to pass OCR.">
		<input type="checkbox" bind:checked={requireTitle} disabled={builtIn || busy} />
		<span>Require title match</span>
	</label>

	<div class="actions">
		<button class="btn-save" onclick={save} disabled={builtIn || busy || !dirty || !trimmedName}>
			Save
		</button>
		<button class="btn-delete" onclick={() => onDelete?.()} disabled={builtIn || busy || !onDelete}>
			Delete
		</button>
	</div>
</section>

<style>
	.editor {
		margin-top: 14px;
		border-top: 1px solid var(--line);
		padding-top: 14px;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.editor-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 10px;
	}
	.eyebrow {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--faint);
		font-weight: 700;
	}
	h3 {
		margin: 4px 0 0;
		font-size: 15px;
	}
	.readonly,
	.unsaved {
		font-size: 11px;
	}
	.readonly {
		color: var(--faint);
	}
	.unsaved {
		color: var(--gold);
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.field label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.field input {
		border: 1px solid var(--line2);
		background: var(--ink2);
		color: var(--text);
		border-radius: 8px;
		padding: 8px 10px;
		font-size: 13px;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 10px;
	}
	@media (max-width: 760px) {
		.grid {
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
	@media (max-width: 760px) {
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
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
	}
	.btn-save,
	.btn-delete {
		border-radius: 8px;
		padding: 8px 14px;
		font-size: 13px;
	}
	.btn-save {
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-weight: 600;
	}
	.btn-delete {
		border: 1px solid color-mix(in srgb, var(--bad) 45%, transparent);
		background: color-mix(in srgb, var(--bad) 10%, var(--panel2));
		color: var(--bad);
	}
	.btn-save:disabled,
	.btn-delete:disabled,
	input:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.mono {
		font-variant-numeric: tabular-nums;
	}
</style>
