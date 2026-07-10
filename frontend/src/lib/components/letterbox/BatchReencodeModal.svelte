<script lang="ts" generics="T extends { confidence: string | null }">
	import type { BatchReencodeSettings } from '$lib/api/types';
	import {
		ENCODER_LABELS,
		KNOWN_ENCODERS,
		PROFILE_META,
		PROFILE_SETTINGS,
		encoderFamilyKey,
		presetOptionsForEncoder,
		type QualityProfile
	} from '$lib/letterbox/encodeSettings';

	let {
		open,
		items,
		getId,
		subtitle = 'Queue a durable re-encode for the selected staging films.',
		busy = false,
		onStart,
		onClose
	}: {
		open: boolean;
		items: T[];
		getId: (item: T) => number;
		subtitle?: string;
		busy?: boolean;
		onStart: (payload: { ids: number[]; settings: BatchReencodeSettings }) => void;
		onClose: () => void;
	} = $props();

	let includeHigh = $state(true);
	let includeMedium = $state(false);
	let includeVariable = $state(false);
	let includeLow = $state(false);
	let settingsMode = $state<'simple' | 'advanced'>('simple');
	let selectedProfile = $state<QualityProfile>('balanced');
	let setEncoder = $state('auto');
	let setQuality = $state<number | null>(null);
	let setPreset = $state('');
	let setCodec = $state<'preserve' | 'hevc' | 'h264'>('preserve');
	let setAllowCpu = $state(true);
	let cropTopOverride = $state<number | null>(null);
	let cropBottomOverride = $state<number | null>(null);

	const selectedIds = $derived.by(() =>
		items
			.filter((item) => {
				if (item.confidence === 'high') return includeHigh;
				if (item.confidence === 'medium') return includeMedium;
				if (item.confidence === 'variable') return includeVariable;
				if (item.confidence === 'low') return includeLow;
				return false;
			})
			.map(getId)
	);
	const selectedCount = $derived(selectedIds.length);
	const canStart = $derived(selectedCount > 0 && !busy);
	const presetOptions = $derived(presetOptionsForEncoder(setEncoder));

	function prettyEncoder(encoder: string): string {
		return ENCODER_LABELS[encoder] ?? encoder;
	}

	function simpleSettings(
		profile: QualityProfile,
		encoder: string
	): {
		quality: number | null;
		preset: string | null;
	} {
		if (encoder !== 'auto') {
			const family = encoderFamilyKey(encoder);
			if (family) {
				const values = PROFILE_SETTINGS[profile][family];
				if (values) return values;
			}
		}
		const fallbackQuality = { speed: 20, balanced: 18, quality: 16 }[profile];
		return { quality: fallbackQuality, preset: null };
	}

	function selectAll() {
		includeHigh = true;
		includeMedium = true;
		includeVariable = true;
		includeLow = true;
	}

	function selectRecommended() {
		includeHigh = true;
		includeMedium = false;
		includeVariable = false;
		includeLow = false;
	}

	function start() {
		if (!canStart) return;
		const simple = simpleSettings(selectedProfile, setEncoder);
		onStart({
			ids: selectedIds,
			settings: {
				quality_profile: selectedProfile,
				encoder: setEncoder === 'auto' ? null : setEncoder,
				quality: settingsMode === 'advanced' ? setQuality : simple.quality,
				preset: settingsMode === 'advanced' ? setPreset || null : simple.preset,
				codec: setCodec,
				allow_cpu: setAllowCpu,
				crop_top_override: cropTopOverride,
				crop_bottom_override: cropBottomOverride
			}
		});
	}
</script>

{#if open}
	<div
		class="modal-backdrop"
		role="dialog"
		aria-modal="true"
		aria-label="Batch re-encode"
		tabindex="-1"
		onclick={(e) => e.target === e.currentTarget && !busy && onClose()}
		onkeydown={(e) => e.key === 'Escape' && !busy && onClose()}
	>
		<div class="modal-card">
			<div class="modal-head">
				<div>
					<h3>Batch permanent re-encode</h3>
					<p>{subtitle}</p>
				</div>
				<button class="close" disabled={busy} onclick={onClose}>✕</button>
			</div>

			<div class="section">
				<div class="section-head">
					<span>Confidence filter</span>
					<div class="mini-actions">
						<button class="mini" disabled={busy} onclick={selectRecommended}>Recommended</button>
						<button class="mini" disabled={busy} onclick={selectAll}>Select all</button>
					</div>
				</div>
				<div class="checks">
					<label><input type="checkbox" bind:checked={includeHigh} disabled={busy} /> High</label>
					<label
						><input type="checkbox" bind:checked={includeMedium} disabled={busy} /> Medium</label
					>
					<label
						><input type="checkbox" bind:checked={includeVariable} disabled={busy} /> Variable</label
					>
					<label><input type="checkbox" bind:checked={includeLow} disabled={busy} /> Low</label>
				</div>
				<div class="count mono">{selectedCount} of {items.length} selected</div>
			</div>

			<div class="section">
				<div class="section-head">
					<span>Encode profile</span>
					<button
						class="mini"
						disabled={busy}
						onclick={() => (settingsMode = settingsMode === 'simple' ? 'advanced' : 'simple')}
					>
						{settingsMode === 'simple' ? 'Advanced' : 'Simple'}
					</button>
				</div>

				{#if settingsMode === 'simple'}
					<div class="profiles">
						{#each ['speed', 'balanced', 'quality'] as const as profile (profile)}
							{@const meta = PROFILE_META[profile]}
							<button
								class="profile"
								class:active={selectedProfile === profile}
								onclick={() => (selectedProfile = profile)}
							>
								<div class="profile-title">
									<span>{meta.icon}</span>
									{meta.label}
								</div>
								<div class="profile-desc">{meta.description}</div>
							</button>
						{/each}
					</div>
				{/if}

				<div class="fields">
					<label class="field">
						<span>Encoder</span>
						<select bind:value={setEncoder}>
							<option value="auto">Auto</option>
							{#each KNOWN_ENCODERS as encoder (encoder)}
								<option value={encoder}>{prettyEncoder(encoder)}</option>
							{/each}
						</select>
					</label>
					<label class="field">
						<span>Codec</span>
						<select bind:value={setCodec}>
							<option value="preserve">Preserve source</option>
							<option value="hevc">HEVC</option>
							<option value="h264">H.264</option>
						</select>
					</label>
					{#if settingsMode === 'advanced'}
						<label class="field">
							<span>Quality (CQ/CRF)</span>
							<input type="number" min="0" max="51" bind:value={setQuality} />
						</label>
						{#if presetOptions.length > 0}
							<label class="field">
								<span>Preset</span>
								<select bind:value={setPreset}>
									<option value="">Default</option>
									{#each presetOptions as preset (preset)}
										<option value={preset}>{preset}</option>
									{/each}
								</select>
							</label>
						{/if}
					{/if}
					<label class="field checkbox">
						<input type="checkbox" bind:checked={setAllowCpu} />
						<span>Allow CPU fallback</span>
					</label>
					<label class="field">
						<span>Crop top override</span>
						<input type="number" min="0" bind:value={cropTopOverride} />
					</label>
					<label class="field">
						<span>Crop bottom override</span>
						<input type="number" min="0" bind:value={cropBottomOverride} />
					</label>
				</div>
			</div>

			<div class="actions">
				<button class="btn-ghost" disabled={busy} onclick={onClose}>Cancel</button>
				<button class="btn-gold" disabled={!canStart} onclick={start}>
					{busy ? 'Queueing…' : `Queue ${selectedCount} re-encodes`}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.56);
		display: grid;
		place-items: center;
		padding: 20px;
		z-index: 50;
	}
	.modal-card {
		width: min(760px, 100%);
		max-height: min(88vh, 860px);
		overflow: auto;
		border: 1px solid var(--line2);
		border-radius: 18px;
		background: var(--panel);
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.modal-head {
		display: flex;
		justify-content: space-between;
		gap: 12px;
	}
	.modal-head h3 {
		margin: 0;
		font-size: 18px;
	}
	.modal-head p {
		margin: 4px 0 0;
		font-size: 12px;
		color: var(--muted);
	}
	.close {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		border-radius: 8px;
		width: 34px;
		height: 34px;
	}
	.section {
		border: 1px solid var(--line);
		border-radius: 14px;
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.section-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 10px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.mini-actions,
	.actions {
		display: flex;
		gap: 8px;
	}
	.checks {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
		font-size: 13px;
	}
	.count {
		font-size: 12px;
		color: var(--muted);
	}
	.profiles {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 10px;
	}
	.profile {
		border: 1px solid var(--line2);
		border-radius: 12px;
		background: var(--panel2);
		padding: 12px;
		text-align: left;
	}
	.profile.active {
		border-color: color-mix(in srgb, var(--gold) 45%, transparent);
		background: color-mix(in srgb, var(--gold) 8%, var(--panel2));
	}
	.profile-title {
		font-size: 13px;
		font-weight: 700;
		display: flex;
		gap: 7px;
		align-items: center;
	}
	.profile-desc {
		margin-top: 6px;
		font-size: 11.5px;
		color: var(--muted);
		line-height: 1.45;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.field span {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.field input,
	.field select {
		border: 1px solid var(--line2);
		background: var(--ink2);
		color: var(--text);
		border-radius: 8px;
		padding: 8px 10px;
		font-size: 13px;
	}
	.field.checkbox {
		flex-direction: row;
		align-items: center;
		gap: 8px;
	}
	.field.checkbox span {
		font-size: 12px;
		text-transform: none;
		letter-spacing: 0;
		color: var(--text);
		font-weight: 500;
	}
	.actions {
		justify-content: flex-end;
	}
	.mini,
	.btn-ghost,
	.btn-gold {
		border-radius: 8px;
		padding: 8px 12px;
		font-size: 13px;
	}
	.mini,
	.btn-ghost {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.btn-gold {
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-weight: 700;
	}
	@media (max-width: 720px) {
		.profiles,
		.fields {
			grid-template-columns: 1fr;
		}
	}
</style>
