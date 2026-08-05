<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { getOcrStatus, putConfiguration, type OcrStatus } from '$lib/api/system';
	import { bytesH } from '$lib/display';
	import { toast } from '$lib/toast';

	let {
		initial = null,
		configVersion,
		configuredDevice,
		configuredWorkers
	}: {
		initial?: OcrStatus | null;
		/** Optimistic-concurrency token from /settings — a stale write is rejected. */
		configVersion: number;
		configuredDevice: string;
		configuredWorkers: number;
	} = $props();

	// svelte-ignore state_referenced_locally
	let status = $state<OcrStatus | null>(initial);
	// svelte-ignore state_referenced_locally
	let version = $state(configVersion);
	// What is persisted, versus the in-flight edit below it.
	// svelte-ignore state_referenced_locally
	let savedDevice = $state(configuredDevice);
	// svelte-ignore state_referenced_locally
	let savedWorkers = $state(configuredWorkers);
	// svelte-ignore state_referenced_locally
	let device = $state(configuredDevice);
	// svelte-ignore state_referenced_locally
	let workers = $state(configuredWorkers);
	let busy = $state(false);

	const plan = $derived(status?.plan ?? null);
	const gpu = $derived(plan?.gpus[0] ?? null);
	const onGpu = $derived(plan?.expected_device?.startsWith('gpu') ?? false);
	const effective = $derived(status?.effective_workers ?? 0);
	const activeWorkers = $derived(status?.workers.active.length ?? 0);

	/** OCR_WORKERS stores 0 for "auto", but showing a 0 in a worker-count stepper
	 *  reads as "no workers". The control shows the real number either way, and
	 *  writes 0 back whenever that number is the host default. */
	const autoWorkers = $derived(plan?.auto_workers ?? effective ?? 1);
	const shownWorkers = $derived(workers === 0 ? autoWorkers : workers);
	const isAuto = $derived(shownWorkers === autoWorkers);
	/** Kept short so it can sit centred under the number rather than wrapping; the
	 *  full explanation lives in the title attribute. */
	const workerHint = $derived(isAuto ? 'Default' : 'Overridden');
	const workerTitle = $derived(
		isAuto
			? `Auto-sized for this host (${autoWorkers} workers)`
			: `Manually set; the host default is ${autoWorkers}`
	);
	const dirty = $derived(device !== savedDevice || workers !== savedWorkers);

	/** Persist 0 when the count lands back on the host default, so "auto" survives
	 *  the round trip instead of being frozen as an explicit number. */
	function setWorkers(next: number) {
		const clamped = Math.min(16, Math.max(1, next));
		workers = clamped === autoWorkers ? 0 : clamped;
	}

	const DEVICES = [
		{ id: 'auto', label: 'Auto', hint: 'GPU when Paddle CUDA works, else CPU.' },
		{ id: 'gpu', label: 'GPU', hint: 'Force GPU. Runs fail if CUDA is unusable.' },
		{ id: 'cpu', label: 'CPU', hint: 'Force CPU regardless of hardware.' }
	];

	async function save() {
		if (!dirty || busy) return;
		busy = true;
		try {
			const result = await putConfiguration(
				fetch,
				{ OCR_DEVICE: device, OCR_WORKERS: workers },
				version
			);
			version = result.configuration_version;
			savedDevice = device;
			savedWorkers = workers;
			status = await getOcrStatus(fetch).catch(() => status);
			toast('OCR execution updated — applies on the next run', 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not update OCR settings', 'bad');
		} finally {
			busy = false;
		}
	}

	function revert() {
		device = savedDevice;
		workers = savedWorkers;
	}
</script>

{#if plan}
	<section class="ocr-card">
		<header>
			<div class="title">
				<span class="icon" class:gpu={onGpu}><Icon name="layers" size={16} /></span>
				<div>
					<h2>OCR Execution</h2>
					<p>The text gate is the slowest stage — this is the hardware it runs on.</p>
				</div>
			</div>
			<span class="badge" class:on={onGpu} class:off={!onGpu} class:err={plan.error}>
				{#if plan.error}
					Misconfigured
				{:else if onGpu}
					GPU
				{:else}
					CPU
				{/if}
			</span>
		</header>

		<div class="body">
			<dl class="facts">
				<div>
					<dt>Device</dt>
					<dd>{plan.error ? 'Unavailable' : onGpu ? (gpu?.name ?? 'CUDA device 0') : 'CPU'}</dd>
					<small>
						{#if plan.error}
							Runs will fail until this is changed
						{:else if !plan.confirmed}
							Predicted from auto mode
						{:else}
							Forced by OCR_DEVICE={plan.requested}
						{/if}
					</small>
				</div>
				<div>
					<dt>Paddle Build</dt>
					<dd>{plan.gpu_build ? 'CUDA' : 'CPU-only'}</dd>
					<small>{plan.gpu_build ? 'paddlepaddle-gpu' : 'paddlepaddle'}</small>
				</div>
				<div>
					<dt>VRAM</dt>
					<dd>{gpu ? bytesH(gpu.vram_total) : '—'}</dd>
					<small>{gpu ? `${bytesH(gpu.vram_free)} free` : 'no NVIDIA card visible'}</small>
				</div>
				<div>
					<dt>Worker Pool</dt>
					<dd>{effective}</dd>
					<small>{activeWorkers ? `${activeWorkers} running now` : 'idle'}</small>
				</div>
			</dl>

			{#if plan.error}
				<p class="error" role="alert">{plan.error}</p>
			{:else if !plan.confirmed}
				<p class="note">
					Auto mode resolves against a CUDA probe inside the OCR worker. This reads the installed
					wheel and the visible card, so it is what the next run should pick — not a measurement.
				</p>
			{/if}

			<div class="controls">
				<div class="field">
					<span class="flabel" id="ocr-device-label">Device</span>
					<div class="seg" role="group" aria-labelledby="ocr-device-label">
						{#each DEVICES as option (option.id)}
							<button
								class:sel={device === option.id}
								disabled={busy}
								title={option.hint}
								onclick={() => (device = option.id)}
							>
								{option.label}
							</button>
						{/each}
					</div>
				</div>

				<div class="field">
					<label class="flabel" for="ocr-workers">Workers</label>
					<div class="stepper">
						<button
							disabled={busy || shownWorkers <= 1}
							aria-label="Fewer workers"
							onclick={() => setWorkers(shownWorkers - 1)}>−</button
						>
						<input
							id="ocr-workers"
							type="number"
							min="1"
							max="16"
							value={shownWorkers}
							disabled={busy}
							oninput={(e) => setWorkers(Number(e.currentTarget.value))}
						/>
						<button
							disabled={busy || shownWorkers >= 16}
							aria-label="More workers"
							onclick={() => setWorkers(shownWorkers + 1)}>+</button
						>
					</div>
					<!-- Centred on the stepper, which puts it under the number rather than
					     under the decrement button. -->
					<small class="under-stepper" title={workerTitle}>{workerHint}</small>
				</div>

				<div class="apply">
					{#if dirty}
						<button class="ghost" disabled={busy} onclick={revert}>Revert</button>
					{/if}
					<button class="primary" disabled={busy || !dirty} onclick={save}>
						{busy ? 'Saving…' : dirty ? 'Apply' : 'Saved'}
					</button>
				</div>
			</div>
		</div>
	</section>
{/if}

<style>
	.ocr-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		margin-bottom: 18px;
		overflow: hidden;
	}
	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 14px;
		padding: 13px 16px;
		border-bottom: 1px solid var(--line);
	}
	.title {
		display: flex;
		align-items: center;
		gap: 11px;
	}
	.icon {
		display: grid;
		place-items: center;
		width: 34px;
		height: 34px;
		flex: none;
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--panel2);
		color: var(--muted);
	}
	.icon.gpu {
		border-color: color-mix(in srgb, var(--good) 32%, transparent);
		background: color-mix(in srgb, var(--good) 10%, transparent);
		color: var(--good);
	}
	h2 {
		margin: 0;
		font-size: 13px;
		font-weight: 680;
	}
	header p {
		margin: 2px 0 0;
		color: var(--muted);
		font-size: 11.5px;
	}
	.badge {
		flex: none;
		padding: 3px 11px;
		border-radius: 999px;
		border: 1px solid transparent;
		font: 700 10.5px/1.5 var(--font-mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	.badge.on {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 36%, transparent);
		background: color-mix(in srgb, var(--good) 10%, transparent);
	}
	.badge.off {
		color: var(--muted);
		border-color: var(--line2);
		background: var(--panel2);
	}
	.badge.err {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 40%, transparent);
		background: color-mix(in srgb, var(--bad) 9%, transparent);
	}
	.body {
		display: grid;
		gap: 14px;
		padding: 15px 16px;
	}
	.facts {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 1px;
		margin: 0;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: var(--line);
		overflow: hidden;
	}
	.facts > div {
		display: grid;
		gap: 2px;
		align-content: start;
		padding: 10px 12px;
		background: var(--panel2);
	}
	dt {
		color: var(--faint);
		font: 650 9px/1.2 var(--font-mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	dd {
		margin: 0;
		color: var(--text);
		font-size: 12.5px;
		font-weight: 600;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.facts small {
		color: var(--muted);
		font-size: 10.5px;
	}
	.note,
	.error {
		margin: 0;
		font-size: 11.5px;
		line-height: 1.5;
	}
	.note {
		color: var(--faint);
	}
	.error {
		padding: 9px 11px;
		border: 1px solid color-mix(in srgb, var(--bad) 30%, transparent);
		border-radius: 7px;
		background: color-mix(in srgb, var(--bad) 7%, transparent);
		color: var(--bad);
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 18px;
		padding-top: 4px;
		border-top: 1px solid var(--line);
	}
	.field {
		display: grid;
		gap: 5px;
		padding-top: 10px;
	}
	.flabel {
		color: var(--faint);
		font: 650 9px/1.2 var(--font-mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	.field small {
		color: var(--muted);
		font-size: 10.5px;
	}
	/* The stepper is the widest thing in this field, so centring here lands on the
	   number. Keep the hint short or it widens the field and the centring drifts. */
	.under-stepper {
		text-align: center;
	}
	.seg {
		display: inline-flex;
		border: 1px solid var(--line2);
		border-radius: 8px;
		overflow: hidden;
	}
	.seg button {
		border: 0;
		background: var(--panel2);
		color: var(--muted);
		padding: 6px 14px;
		font-size: 12px;
		font-weight: 600;
		cursor: pointer;
	}
	.seg button + button {
		border-left: 1px solid var(--line2);
	}
	.seg button:hover:not(:disabled) {
		color: var(--text);
	}
	.seg button.sel {
		background: var(--gold-soft);
		color: var(--gold-copy);
	}
	.stepper {
		display: inline-flex;
		align-items: stretch;
		border: 1px solid var(--line2);
		border-radius: 8px;
		overflow: hidden;
	}
	.stepper button {
		width: 30px;
		border: 0;
		background: var(--panel2);
		color: var(--muted);
		font-size: 14px;
		cursor: pointer;
	}
	.stepper button:hover:not(:disabled) {
		color: var(--text);
	}
	.stepper input {
		width: 52px;
		border: 0;
		border-inline: 1px solid var(--line2);
		background: var(--panel);
		color: var(--text);
		padding: 6px 0;
		font: 600 12.5px/1 var(--font-mono);
		text-align: center;
		appearance: textfield;
	}
	.stepper input::-webkit-inner-spin-button {
		appearance: none;
	}
	.apply {
		display: flex;
		gap: 8px;
		margin-left: auto;
		padding-top: 10px;
	}
	.apply button {
		border-radius: 8px;
		padding: 7px 15px;
		font-size: 12.5px;
		font-weight: 600;
		cursor: pointer;
	}
	.primary {
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
	}
	.ghost {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	@media (max-width: 980px) {
		.facts {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 620px) {
		.facts {
			grid-template-columns: 1fr;
		}
		.apply {
			margin-left: 0;
		}
	}
</style>
