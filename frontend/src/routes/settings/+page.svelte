<script lang="ts">
	import TabBar from '$lib/components/TabBar.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { getPipelineConfig, putPipelineConfig, resetDeployedPosters } from '$lib/api/config';
	import { clearOcrLabels } from '$lib/api/pipeline';
	import type { KnobGroup, PipelineConfig } from '$lib/api/config';
	import { toast } from '$lib/toast';
	import SubtitleSettings from '$lib/components/subtitles/SubtitleSettings.svelte';

	let { data } = $props();
	let config: PipelineConfig | null = $derived(data.config);
	let error: string | null = $derived(data.error);

	// ── UI state ─────────────────────────────────────────────────────
	let activeGroup = $state('weights');
	let dirty = $state<Record<string, unknown>>({});
	let saving = $state(false);
	let resetDialogOpen = $state(false);
	let resetBusy = $state(false);
	let masterResetConfirm = $state(false);
	let clearOcrDialogOpen = $state(false);
	let clearOcrBusy = $state(false);

	// ── Derived ──────────────────────────────────────────────────────
	let currentValues = $derived({ ...config?.values, ...dirty });
	let groups = $derived(config?.groups ?? []);
	let tabs = $derived.by(() => {
		const base = (config?.groups ?? []).map((g: KnobGroup) => ({ id: g.id, label: g.label }));
		base.push({ id: 'subtitles', label: 'Subtitles' });
		return base;
	});

	let meta = $derived(config?.meta ?? {});
	let overrides = $derived(config?.overrides ?? {});
	let dirtyCount = $derived(Object.keys(dirty).length);

	// ── Helpers ──────────────────────────────────────────────────────
	function isModified(key: string): boolean {
		return key in overrides || key in dirty;
	}

	function knobDefault(key: string): unknown {
		return config?.defaults[key];
	}

	function resetKnob(key: string) {
		const def = knobDefault(key);
		if (def !== undefined) {
			dirty = { ...dirty, [key]: def };
		}
	}

	function resetGroupToDefaults() {
		const group = groups.find((g: KnobGroup) => g.id === activeGroup);
		if (!group) return;
		const defaults: Record<string, unknown> = {};
		for (const key of group.knobs) {
			const def = config?.defaults[key];
			if (def !== undefined) {
				defaults[key] = def;
			}
		}
		dirty = { ...dirty, ...defaults };
		toast(
			`Staged ${Object.keys(defaults).length} knobs in "${activeGroup}" to defaults — hit Save to apply`,
			'info'
		);
	}

	function resetAllToDefaults() {
		if (!config) return;
		const defaults: Record<string, unknown> = {};
		for (const key of Object.keys(config.defaults)) {
			defaults[key] = config.defaults[key];
		}
		dirty = { ...dirty, ...defaults };
		masterResetConfirm = false;
		toast(`Staged ${Object.keys(defaults).length} knobs to defaults — hit Save to apply`, 'info');
	}

	// ── Actions ──────────────────────────────────────────────────────
	async function saveChanges() {
		if (dirtyCount === 0) return;
		saving = true;
		try {
			const result = await putPipelineConfig(fetch, dirty);
			toast(
				`Saved ${result.applied.length} knob${result.applied.length === 1 ? '' : 's'} — applies on the next run`,
				'good'
			);
			dirty = {};
			// Refresh config to sync overrides.
			const fresh = await getPipelineConfig(fetch);
			config = fresh;
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Save failed', 'bad');
		} finally {
			saving = false;
		}
	}

	async function handleReset() {
		resetBusy = true;
		try {
			const result = await resetDeployedPosters(fetch);
			toast(
				`Reset ${result.reset} movie${result.reset === 1 ? '' : 's'} — posters deleted, re-run the pipeline to replace them`,
				'good'
			);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Reset failed', 'bad');
		} finally {
			resetBusy = false;
			resetDialogOpen = false;
		}
	}

	async function handleClearOcrLabels() {
		clearOcrBusy = true;
		try {
			const result = await clearOcrLabels(fetch);
			toast(
				`Cleared ${result.deleted_capture_dirs} OCR label capture${
					result.deleted_capture_dirs === 1 ? '' : 's'
				}`,
				'good'
			);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Clear failed', 'bad');
		} finally {
			clearOcrBusy = false;
			clearOcrDialogOpen = false;
		}
	}
</script>

<svelte:head>
	<title>Pipeline Settings — Marquee</title>
</svelte:head>

<div class="page">
	<SectionHeader
		title="Pipeline settings"
		subtitle="Tune every knob of the poster pipeline. Changes apply on the next run — no restart needed."
	/>

	{#if error}
		<div class="err-banner">⚠ {error}</div>
	{:else if config}
		<!-- Group tabs -->
		<div class="tabs-wrap">
			<TabBar
				{tabs}
				active={activeGroup}
				onSelect={(id: string) => (activeGroup = id)}
			/>
		</div>

		{#if activeGroup === 'subtitles'}
			<SubtitleSettings bind:settings={data.settings} />
		{:else}
			<div class="knobs-section">
			<!-- Group description -->
			{#each groups.filter((g: KnobGroup) => g.id === activeGroup) as group (group.id)}
				<div class="group-header">
					{#if group.description}
						<p class="group-desc">{group.description}</p>
					{/if}
					<button
						class="group-reset-btn"
						onclick={resetGroupToDefaults}
						title="Reset all knobs in this group to their defaults"
					>
						Reset to defaults
					</button>
				</div>

				<!-- Knobs for this group -->
				<div class="knob-grid">
					{#each group.knobs as key (key)}
						{@const m = meta[key]}
						{@const val = currentValues[key]}
						{@const def = knobDefault(key)}
						{@const mod = isModified(key)}
						{#if m}
							<div class="knob-row" class:mod>
								<div class="knob-label-area">
									<span class="knob-name">{key}</span>
									{#if m.help}<span class="knob-help">{m.help}</span>{/if}
								</div>

								<div class="knob-control">
									{#if m.kind === 'bool'}
										<label class="toggle">
											<input
												type="checkbox"
												checked={Boolean(val)}
												onchange={(e) =>
													(dirty = {
														...dirty,
														[key]: (e.target as HTMLInputElement).checked
													})}
											/>
											<span class="toggle-track"></span>
										</label>
									{:else if m.kind === 'enum' && m.options}
										<select
											class="enum-select"
											value={String(val)}
											onchange={(e) =>
												(dirty = {
													...dirty,
													[key]: (e.target as HTMLSelectElement).value
												})}
										>
											{#each m.options as opt (opt)}
												<option value={opt}>{opt}</option>
											{/each}
										</select>
									{:else if m.kind === 'weight' || m.kind === 'float' || m.kind === 'int'}
										<div class="slider-row">
											<input
												type="range"
												class="slider"
												min={m.min ?? 0}
												max={m.max ?? 1}
												step={m.step ?? 0.01}
												value={Number(val)}
												oninput={(e) => {
													const v =
														m.kind === 'int'
															? parseInt((e.target as HTMLInputElement).value)
															: parseFloat((e.target as HTMLInputElement).value);
													dirty = { ...dirty, [key]: v };
												}}
											/>
											<span class="slider-val">
												{typeof val === 'number'
													? m.kind === 'int'
														? val
														: val.toFixed(m.step && m.step < 1 ? 3 : 1)
													: String(val)}
											</span>
										</div>
									{:else}
										<input
											type="text"
											class="str-input"
											value={String(val ?? '')}
											oninput={(e) =>
												(dirty = {
													...dirty,
													[key]: (e.target as HTMLInputElement).value
												})}
										/>
									{/if}
									{#if mod}
										<button
											class="reset-btn"
											title="Reset to default: {def}"
											onclick={() => resetKnob(key)}>↺</button
										>
									{/if}
								</div>
							</div>
						{/if}
					{/each}
				</div>
			{/each}
		</div>

		<!-- Save bar -->
		<div class="save-bar">
			<button class="save-btn" disabled={dirtyCount === 0 || saving} onclick={saveChanges}>
				{#if saving}
					<span class="spin">⟳</span> Saving…
				{:else}
					Save
					{dirtyCount > 0 ? ` (${dirtyCount} knob${dirtyCount === 1 ? '' : 's'})` : ''}
				{/if}
			</button>
			<span class="save-hint"
				>Applied to the live config — next pipeline run uses the new values.</span
			>
			<button
				class="master-reset-btn"
				disabled={saving}
				onclick={() => (masterResetConfirm = true)}
				title="Stage all knobs to their code defaults (hit Save to commit)"
			>
				Reset all to defaults
			</button>
		</div>

		<!-- Danger zone -->
		<div class="danger-zone">
			<SectionHeader title="Danger zone" subtitle="Destructive maintenance actions." />
			<div class="danger-card">
				<div class="danger-info">
					<span class="danger-label">Delete all deployed posters</span>
					<span class="danger-desc">
						Removes every <code>poster.jpg</code> next to your movies and marks all movies as missing
						so the pipeline can re-run from scratch. Does NOT touch your taste profile, the Key Art Engine,
						or your labels. The underlying poster cache is preserved.
					</span>
				</div>
				<button class="danger-btn" onclick={() => (resetDialogOpen = true)}>
					Delete all deployed posters
				</button>
			</div>
			{#if data.settings?.app?.debug}
				<div class="danger-card">
					<div class="danger-info">
						<span class="danger-label">Clear OCR label captures</span>
						<span class="danger-desc">
							Deletes every debug-only OCR false-rejection and false-acceptance capture under
							<code>data/debug/ocr-labels</code>. This does not touch pipeline run archives, movie
							posters, or taste-profile data.
						</span>
					</div>
					<button class="danger-btn" onclick={() => (clearOcrDialogOpen = true)}>
						Clear OCR label captures
					</button>
				</div>
			{/if}
		</div>
		{/if}

		<!-- Confirm dialogs -->
		<ConfirmDialog
			open={resetDialogOpen}
			title="Delete all deployed posters?"
			message="This deletes every poster.jpg next to your movies and marks all movies as missing. The pipeline will need to re-run to replace them. This cannot be undone."
			confirmLabel="Delete all posters"
			cancelLabel="Cancel"
			tone="bad"
			busy={resetBusy}
			onConfirm={handleReset}
			onCancel={() => (resetDialogOpen = false)}
		/>
		<ConfirmDialog
			open={masterResetConfirm}
			title="Reset all pipeline settings to defaults?"
			message="This stages every knob back to its code default. You must still hit Save to persist the changes. Current overrides in pipeline_overrides.json will be replaced."
			confirmLabel="Stage all defaults"
			cancelLabel="Cancel"
			tone="bad"
			onConfirm={resetAllToDefaults}
			onCancel={() => (masterResetConfirm = false)}
		/>
		<ConfirmDialog
			open={clearOcrDialogOpen}
			title="Clear OCR label captures?"
			message="This deletes every debug OCR label capture under data/debug/ocr-labels. It does not affect pipeline archives, deployed posters, or taste-profile data."
			confirmLabel="Clear OCR captures"
			cancelLabel="Cancel"
			tone="bad"
			busy={clearOcrBusy}
			onConfirm={handleClearOcrLabels}
			onCancel={() => (clearOcrDialogOpen = false)}
		/>
	{:else}
		<div class="loading">Loading settings…</div>
	{/if}
</div>

<style>
	.page {
		max-width: 840px;
		margin: 0 auto;
		padding: 24px 32px 64px;
	}

	.err-banner {
		padding: 12px 16px;
		border-radius: var(--radius);
		background: color-mix(in srgb, var(--bad) 12%, transparent);
		border: 1px solid color-mix(in srgb, var(--bad) 25%, var(--line2));
		color: var(--bad);
		font-size: 13px;
	}

	.loading {
		color: var(--muted);
		font-size: 14px;
		padding: 24px 0;
	}

	/* ── Tabs ────────────────────────────────────────────────── */
	.tabs-wrap {
		margin: 16px 0 0;
		overflow-x: auto;
	}

	.group-desc {
		margin: 0;
		color: var(--muted);
		font-size: 13px;
		line-height: 1.5;
		flex: 1;
	}

	.group-header {
		display: flex;
		align-items: center;
		gap: 12px;
		margin: 0 0 12px;
	}

	.group-reset-btn {
		padding: 3px 10px;
		font-size: 11px;
		line-height: 1.4;
		border-radius: 5px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		cursor: pointer;
		flex-shrink: 0;
		white-space: nowrap;
	}
	.group-reset-btn:hover {
		color: var(--gold);
		border-color: var(--gold-deep);
	}

	/* ── Knob grid ───────────────────────────────────────────── */
	.knobs-section {
		margin-top: 12px;
	}

	.knob-grid {
		display: flex;
		flex-direction: column;
		gap: 0;
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.knob-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		min-height: 44px;
		padding: 8px 14px;
		gap: 12px;
		border-bottom: 1px solid var(--line);
		transition: background 0.15s;
	}
	.knob-row:last-child {
		border-bottom: none;
	}
	.knob-row.mod {
		background: color-mix(in srgb, var(--gold) 6%, transparent);
	}

	.knob-label-area {
		display: flex;
		flex-direction: column;
		gap: 1px;
		min-width: 0;
		flex: 1;
	}

	.knob-name {
		font-size: 12.5px;
		font-weight: 600;
		color: var(--text);
		font-family: var(--font-mono);
	}

	.knob-help {
		font-size: 11px;
		color: var(--faint);
		line-height: 1.3;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* ── Controls ────────────────────────────────────────────── */
	.knob-control {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-shrink: 0;
	}

	/* Toggle */
	.toggle {
		position: relative;
		display: inline-flex;
		align-items: center;
		cursor: pointer;
	}
	.toggle input {
		position: absolute;
		opacity: 0;
		width: 0;
		height: 0;
	}
	.toggle-track {
		width: 40px;
		height: 22px;
		border-radius: 11px;
		background: var(--faint2);
		transition: background 0.15s;
		position: relative;
	}
	.toggle-track::after {
		content: '';
		position: absolute;
		top: 2px;
		left: 2px;
		width: 18px;
		height: 18px;
		border-radius: 50%;
		background: var(--text);
		transition: transform 0.15s;
	}
	.toggle input:checked + .toggle-track {
		background: var(--gold-deep);
	}
	.toggle input:checked + .toggle-track::after {
		transform: translateX(18px);
	}

	/* Select */
	.enum-select {
		padding: 5px 28px 5px 10px;
		border-radius: 6px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12.5px;
		font-family: inherit;
		min-width: 100px;
		appearance: none;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238a909f'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 8px center;
	}

	/* Slider */
	.slider-row {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.slider {
		width: 140px;
		accent-color: var(--gold-deep);
	}
	.slider-val {
		font-size: 12px;
		color: var(--text);
		font-family: var(--font-mono);
		min-width: 42px;
		text-align: right;
	}

	/* Text */
	.str-input {
		padding: 5px 10px;
		border-radius: 6px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12.5px;
		font-family: var(--font-mono);
		width: 120px;
	}

	/* Reset */
	.reset-btn {
		padding: 2px 6px;
		font-size: 14px;
		line-height: 1;
		border-radius: 5px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		cursor: pointer;
		transition: color 0.15s;
	}
	.reset-btn:hover {
		color: var(--gold);
	}

	/* ── Save bar ────────────────────────────────────────────── */
	.save-bar {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-top: 20px;
		padding: 14px 16px;
		border-radius: var(--radius);
		background: var(--panel);
		border: 1px solid var(--line);
	}

	.save-btn {
		padding: 9px 22px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
	}
	.save-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.save-hint {
		font-size: 12px;
		color: var(--muted);
		flex: 1;
	}

	.master-reset-btn {
		padding: 5px 14px;
		font-size: 12px;
		line-height: 1.4;
		border-radius: 6px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		cursor: pointer;
		flex-shrink: 0;
		white-space: nowrap;
	}
	.master-reset-btn:hover {
		color: var(--warn);
		border-color: var(--warn);
	}

	/* ── Danger zone ─────────────────────────────────────────── */
	.danger-zone {
		margin-top: 36px;
		padding-top: 24px;
		border-top: 1px solid var(--line);
	}

	.danger-card {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin-top: 12px;
		padding: 14px 16px;
		border-radius: var(--radius);
		border: 1px solid color-mix(in srgb, var(--bad) 30%, var(--line2));
		background: color-mix(in srgb, var(--bad) 4%, var(--panel));
	}

	.danger-info {
		display: flex;
		flex-direction: column;
		gap: 4px;
		flex: 1;
		min-width: 0;
	}

	.danger-label {
		font-size: 13px;
		font-weight: 650;
		color: var(--bad);
	}

	.danger-desc {
		font-size: 12px;
		color: var(--muted);
		line-height: 1.5;
	}
	.danger-desc :global(code) {
		font-size: 11px;
		padding: 1px 5px;
		border-radius: 3px;
		background: var(--panel2);
		font-family: var(--font-mono);
	}

	.danger-btn {
		padding: 9px 16px;
		border-radius: 8px;
		border: 1px solid color-mix(in srgb, var(--bad) 45%, transparent);
		background: color-mix(in srgb, var(--bad) 12%, var(--panel2));
		color: var(--bad);
		font-size: 12.5px;
		font-weight: 600;
		flex-shrink: 0;
		white-space: nowrap;
	}
	.danger-btn:hover {
		background: color-mix(in srgb, var(--bad) 20%, var(--panel2));
	}

	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
	.spin {
		display: inline-block;
		animation: spin 1s linear infinite;
	}
</style>
