<script lang="ts">
	import ProgressBar from './ProgressBar.svelte';
	import type { JobProgressDetail } from '$lib/jobs';

	let {
		detail = {},
		status = 'running',
		title = 'Pipeline running',
		onCancel
	}: {
		detail?: JobProgressDetail;
		status?: string;
		title?: string;
		onCancel?: () => void;
	} = $props();

	const STAGE_LABELS: Record<string, string> = {
		fetch: 'Downloading candidates',
		download: 'Downloading candidates',
		sha256: 'Exact-duplicate scan',
		resolution: 'Resolution gate',
		style: 'Style features (CLIP)',
		style_gate: 'Style gate',
		features: 'Style features (CLIP)',
		ocr: 'Text gate (OCR)',
		phash: 'Near-duplicate scan',
		detail: 'Detail features (DINO)',
		fan_junk: 'Fan-junk gate',
		rank: 'Ranking',
		output: 'Finalizing',
		queued: 'Queued',
		prelude: 'Preparing',
		// Media-job stages (subtitle ops, letterbox_reencode, dovi_convert) —
		// these jobs don't share the pipeline's stage vocabulary above.
		preflight: 'Preflight checks',
		remux: 'Remuxing',
		encode: 'Encoding',
		validate: 'Validating output',
		replace: 'Replacing file',
		external: 'Updating external files',
		scan: 'Scanning',
		extract: 'Extracting',
		policy: 'Applying policy'
	};

	/** Humanize any stage key this map doesn't know about yet (new job
	 *  types keep getting a reasonable label without another edit here). */
	function humanizeStage(stage: string): string {
		return stage
			.split('_')
			.map((w) => w.charAt(0).toUpperCase() + w.slice(1))
			.join(' ');
	}

	const isBatch = $derived(typeof detail.movie_total === 'number' && (detail.movie_total ?? 0) > 1);
	const stageLabel = $derived(
		detail.stage ? STAGE_LABELS[detail.stage] ?? humanizeStage(detail.stage) : ''
	);

	const pct = $derived.by<number | null>(() => {
		if (isBatch && detail.movie_total) {
			const done = detail.movies_done ?? (detail.movie_index ? detail.movie_index - 1 : 0);
			return Math.round((done / detail.movie_total) * 100);
		}
		if (typeof detail.total === 'number' && detail.total > 0 && typeof detail.done === 'number') {
			return Math.round((detail.done / detail.total) * 100);
		}
		return null; // indeterminate
	});

	const tone = $derived(
		status === 'failed' || status === 'dead_letter'
			? 'bad'
			: status === 'cancelled' || status === 'interrupted'
				? 'warn'
				: 'gold'
	);
</script>

<div class="run-progress">
	<div class="rp-head">
		<span class="dot mq-pulse" style="--c:var(--{tone})"></span>
		<span class="rp-title">{title}</span>
		{#if pct != null}<span class="rp-pct mono">{pct}%</span>{/if}
		{#if onCancel}
			<button class="rp-cancel" onclick={onCancel}>Cancel</button>
		{/if}
	</div>

	<div class="rp-bar" class:indeterminate={pct == null}>
		<ProgressBar value={pct ?? 100} {tone} height={8} />
	</div>

	<div class="rp-meta">
		{#if isBatch && detail.movie_total}
			<span class="rp-chip">
				Movie {detail.movie_index ?? 0}/{detail.movie_total}
			</span>
		{/if}
		{#if detail.title}<span class="rp-movie">{detail.title}</span>{/if}
		{#if stageLabel}
			<span class="rp-stage">{stageLabel}</span>
		{/if}
		{#if typeof detail.done === 'number' && typeof detail.total === 'number' && detail.total > 0 && !isBatch}
			<span class="rp-chip mono">{detail.done}/{detail.total}</span>
		{/if}
		{#if typeof detail.survivors === 'number'}
			<span class="rp-chip survivors mono">{detail.survivors} kept</span>
		{/if}
	</div>
</div>

<style>
	.run-progress {
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 12px 14px;
		display: flex;
		flex-direction: column;
		gap: 9px;
	}
	.rp-head {
		display: flex;
		align-items: center;
		gap: 9px;
	}
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--c, var(--gold));
		flex: none;
	}
	.rp-title {
		font-size: 13px;
		font-weight: 600;
		color: var(--text);
	}
	.rp-pct {
		margin-left: auto;
		font-size: 12px;
		color: var(--muted);
	}
	.rp-cancel {
		margin-left: 8px;
		padding: 4px 10px;
		border-radius: 7px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		font-size: 12px;
	}
	.rp-cancel:hover {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 40%, transparent);
	}
	.rp-bar.indeterminate :global(.fill) {
		animation: mq-pulse 1.6s infinite;
	}
	.rp-meta {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px 10px;
		font-size: 12px;
		color: var(--muted);
	}
	.rp-movie {
		color: var(--text);
		font-weight: 550;
	}
	.rp-stage {
		color: var(--gold);
	}
	.rp-chip {
		padding: 1px 7px;
		border-radius: 99px;
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 11px;
		color: var(--muted);
	}
	.rp-chip.survivors {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 30%, transparent);
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
