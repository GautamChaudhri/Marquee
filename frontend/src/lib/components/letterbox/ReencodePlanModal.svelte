<script lang="ts">
	import { onMount } from 'svelte';
	import { bytesH } from '$lib/display';
	import { toast } from '$lib/toast';
	import { confirmMutation } from '$lib/activity/client';
	import type { ReencodePlan } from '$lib/api/types';
	import { createTvReencodePlan } from '$lib/api/letterbox';
	import {
		ENCODER_LABELS,
		KNOWN_ENCODERS,
		PROFILE_META,
		PROFILE_SETTINGS,
		profileFamilyKey,
		presetOptionsForEncoder,
		type QualityProfile
	} from '$lib/letterbox/encodeSettings';

	let {
		seriesId,
		episodeId,
		onClose,
		onConfirmed
	}: {
		seriesId: number;
		episodeId: number;
		onClose: () => void;
		onConfirmed: (jobId: string) => void;
	} = $props();

	let plan = $state<ReencodePlan | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let confirming = $state(false);
	let settingsMode = $state<'simple' | 'advanced'>('simple');
	let selectedProfile = $state<QualityProfile>('balanced');
	let setEncoder = $state('auto');
	let setQuality = $state<number | null>(null);
	let setPreset = $state('');
	let setCodec = $state<'preserve' | 'hevc' | 'h264'>('preserve');
	let setAllowCpu = $state(true);

	const presetOptions = $derived(presetOptionsForEncoder(setEncoder));

	function prettyEncoder(encoder: string): string {
		return ENCODER_LABELS[encoder] ?? encoder;
	}

	function getProfileOverrides(): { quality?: number; preset?: string | null } {
		if (!plan) return {};
		const key = profileFamilyKey(plan.encoder.family, plan.encoder.encoder);
		const settings = PROFILE_SETTINGS[selectedProfile]?.[key];
		return settings ? { quality: settings.quality, preset: settings.preset } : {};
	}

	async function loadPlan() {
		loading = true;
		error = null;
		try {
			const profileOv = settingsMode === 'simple' ? getProfileOverrides() : {};
			plan = await createTvReencodePlan(fetch, seriesId, episodeId, {
				allow_cpu_fallback: settingsMode === 'advanced' ? setAllowCpu : true,
				encoder: setEncoder === 'auto' ? null : setEncoder,
				quality: settingsMode === 'advanced' ? setQuality : (profileOv.quality ?? null),
				preset: settingsMode === 'advanced' ? setPreset || null : (profileOv.preset ?? null),
				codec: setCodec
			});
		} catch (e) {
			plan = null;
			const body = (e as { body?: { detail?: { message?: string } } })?.body;
			error =
				body?.detail?.message ?? (e instanceof Error ? e.message : 'Could not plan re-encode');
		} finally {
			loading = false;
		}
	}

	function selectProfile(p: QualityProfile) {
		selectedProfile = p;
		void loadPlan();
	}

	onMount(() => {
		void loadPlan();
	});

	async function confirm() {
		if (!plan || confirming) return;
		confirming = true;
		try {
			await confirmMutation(fetch, plan.job_id, plan.plan_version, plan.configuration_version);
			onConfirmed(plan.job_id);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not start encode', 'bad');
		} finally {
			confirming = false;
		}
	}

	function discard() {
		if (confirming) return;
		onClose();
	}
</script>

<div
	class="modal-backdrop"
	role="dialog"
	aria-modal="true"
	aria-label="Re-encode episode"
	tabindex="-1"
	onclick={(e) => e.target === e.currentTarget && discard()}
	onkeydown={(e) => e.key === 'Escape' && discard()}
>
	<div class="modal-card">
		<div class="modal-head">
			<div>
				<h3>Re-encode episode</h3>
				<p>Plan a durable crop + re-encode for this episode.</p>
			</div>
			<button class="close" disabled={confirming} onclick={discard}>✕</button>
		</div>

		{#if loading}
			<div class="plan-status">Planning…</div>
		{:else if error}
			<div class="plan-status plan-error">{error}</div>
		{:else if plan}
			<div class="section">
				<div class="section-head"><span>Source</span></div>
				<dl class="plan-grid">
					<dt>Resolution</dt>
					<dd class="mono">{plan.source.width}×{plan.source.height}</dd>
					<dt>Codec</dt>
					<dd class="mono">{plan.source.codec ?? '—'}</dd>
					<dt>Size</dt>
					<dd class="mono">{bytesH(plan.source.size_bytes)}</dd>
					<dt>Crop T / B</dt>
					<dd class="mono">{plan.crop.top}px / {plan.crop.bottom}px</dd>
				</dl>
			</div>

			<div class="section">
				<div class="section-head">
					<span>Encode profile</span>
					<button
						class="mini"
						disabled={confirming}
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
								disabled={confirming}
								onclick={() => selectProfile(profile)}
							>
								<div class="profile-title"><span>{meta.icon}</span>{meta.label}</div>
								<div class="profile-desc">{meta.description}</div>
							</button>
						{/each}
					</div>
				{/if}
				<div class="fields">
					<label class="field">
						<span>Encoder</span>
						<select bind:value={setEncoder} disabled={confirming} onchange={loadPlan}>
							<option value="auto">Auto</option>
							{#each KNOWN_ENCODERS as encoder (encoder)}
								<option value={encoder}>{prettyEncoder(encoder)}</option>
							{/each}
						</select>
					</label>
					<label class="field">
						<span>Codec</span>
						<select bind:value={setCodec} disabled={confirming} onchange={loadPlan}>
							<option value="preserve">Preserve source</option>
							<option value="hevc">HEVC</option>
							<option value="h264">H.264</option>
						</select>
					</label>
					{#if settingsMode === 'advanced'}
						<label class="field">
							<span>Quality (CQ/CRF)</span>
							<input
								type="number"
								min="0"
								max="51"
								bind:value={setQuality}
								disabled={confirming}
								onchange={loadPlan}
							/>
						</label>
						{#if presetOptions.length > 0}
							<label class="field">
								<span>Preset</span>
								<select bind:value={setPreset} disabled={confirming} onchange={loadPlan}>
									<option value="">Default</option>
									{#each presetOptions as preset (preset)}
										<option value={preset}>{preset}</option>
									{/each}
								</select>
							</label>
						{/if}
					{/if}
					<label class="field checkbox">
						<input
							type="checkbox"
							bind:checked={setAllowCpu}
							disabled={confirming}
							onchange={loadPlan}
						/>
						<span>Allow CPU fallback</span>
					</label>
				</div>
			</div>

			<div class="section">
				<div class="section-head"><span>Encoder plan</span></div>
				<dl class="plan-grid">
					<dt>Chosen</dt>
					<dd class="mono">
						{prettyEncoder(plan.encoder.encoder)} · q{plan.encoder.quality}{plan.encoder.preset
							? ` · ${plan.encoder.preset}`
							: ''}
					</dd>
					<dt>Acceleration</dt>
					<dd>
						{plan.acceleration?.enabled
							? 'NVIDIA NVDEC → GPU crop → NVENC'
							: `CPU decode/crop${plan.acceleration?.reason ? ` · ${plan.acceleration.reason}` : ''}`}
					</dd>
				</dl>
			</div>

			{#if plan.warnings.length > 0}
				<div class="section warnings">
					<div class="section-head"><span>Warnings</span></div>
					<ul>
						{#each plan.warnings as w (w.code)}
							<li>{w.message}</li>
						{/each}
					</ul>
				</div>
			{/if}
		{/if}

		<div class="actions">
			<button class="btn-ghost" disabled={confirming} onclick={discard}>Cancel</button>
			<button class="btn-gold" disabled={!plan || confirming || loading} onclick={confirm}>
				{confirming ? 'Starting…' : 'Confirm re-encode'}
			</button>
		</div>
	</div>
</div>

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
		width: min(560px, 100%);
		max-height: min(88vh, 780px);
		overflow: auto;
		border: 1px solid var(--line2);
		border-radius: 18px;
		background: var(--panel);
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.modal-head {
		display: flex;
		justify-content: space-between;
		gap: 12px;
	}
	.modal-head h3 {
		margin: 0;
		font-size: 17px;
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
		width: 32px;
		height: 32px;
	}
	.plan-status {
		font-size: 13px;
		color: var(--muted);
		padding: 8px 2px;
	}
	.plan-error {
		color: var(--bad);
	}
	.section {
		border: 1px solid var(--line);
		border-radius: 14px;
		padding: 12px;
		display: flex;
		flex-direction: column;
		gap: 10px;
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
	.plan-grid {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 4px 10px;
		font-size: 13px;
	}
	.plan-grid dt {
		color: var(--muted);
	}
	.profiles {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 8px;
	}
	.profile {
		border: 1px solid var(--line2);
		border-radius: 12px;
		background: var(--panel2);
		padding: 10px;
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
		margin-top: 4px;
		font-size: 11px;
		color: var(--muted);
		line-height: 1.4;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 10px;
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
		padding: 7px 9px;
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
	.warnings ul {
		margin: 0;
		padding-left: 18px;
		font-size: 12.5px;
		color: var(--warn);
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
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
	@media (max-width: 620px) {
		.profiles,
		.fields {
			grid-template-columns: 1fr;
		}
	}
</style>
