<script lang="ts">
	import { letterboxMeta, toneVar, aspectRatio } from '$lib/display';
	import { toast } from '$lib/toast';
	import type { LetterboxDetail } from '$lib/api/types';
	import {
		getLetterboxState,
		detectLetterbox,
		applyLetterbox,
		ignoreLetterbox,
		removeLetterbox,
		confirmLetterbox,
		reprocessLetterbox
	} from '$lib/api/letterbox';
	import StatusDot from './StatusDot.svelte';
	import Icon from './Icon.svelte';

	let {
		movieId,
		onChanged,
		onAnalyzeAll,
		analyzing = false
	}: {
		movieId: number | null;
		onChanged: () => void;
		onAnalyzeAll: () => void;
		analyzing?: boolean;
	} = $props();

	let detail = $state<LetterboxDetail | null>(null);
	let loading = $state(false);
	let busy = $state(false);
	let loadError = $state<string | null>(null);
	let showConf = $state(false);
	let previewMinute = $state<number | null>(null);

	let lastId = $state<number | null>(null);
	$effect(() => {
		if (movieId === lastId) return;
		lastId = movieId;
		detail = null;
		loadError = null;
		showConf = false;
		previewMinute = null;
		if (movieId == null) return;
		loading = true;
		getLetterboxState(fetch, movieId)
			.then((d) => (detail = d))
			.catch((e) => (loadError = e instanceof Error ? e.message : 'Failed to load'))
			.finally(() => (loading = false));
	});

	const stage = $derived.by(() => {
		const s = detail?.status;
		if (!s) return 'none';
		if (s.startsWith('prefilter')) return 'candidate';
		if (s === 'candidate') return 'detected';
		if (s === 'tagged') return detail?.reviewed ? 'processed' : 'preview';
		if (s === 'not_letterboxed' || s === 'variable_unsafe' || s === 'skipped') return 'clean';
		return 'other';
	});

	const meta = $derived(detail ? letterboxMeta(detail.status) : null);

	const cropLabel = $derived.by(() => {
		const t = detail?.recommended_crop_top ?? 0;
		const b = detail?.recommended_crop_bottom ?? 0;
		return `${t} / ${b} px`;
	});

	const afterHeight = $derived.by(() => {
		const h = detail?.source_height;
		const t = detail?.recommended_crop_top ?? 0;
		const b = detail?.recommended_crop_bottom ?? 0;
		return h ? h - t - b : null;
	});

	const afterAR = $derived.by(() => {
		const w = detail?.source_width;
		const ah = afterHeight;
		return w && ah ? (w / ah).toFixed(2) + ':1' : null;
	});

	// Override preview URLs when the user clicks a sample frame row.
	const activeMinute = $derived(previewMinute ?? detail?.preview_minute ?? 5);
	const beforeUrl = $derived(
		detail && movieId != null
			? `/api/letterbox/movies/${movieId}/preview?mode=before&minute=${activeMinute}`
			: null
	);
	const afterUrl = $derived(
		detail && movieId != null
			? `/api/letterbox/movies/${movieId}/preview?mode=after&minute=${activeMinute}`
			: null
	);

	// Assign a stable color per unique bar value so each group gets its own icon color.
	const BAR_COLORS = ['var(--gold)', 'var(--info)', 'var(--good)', 'var(--warn)', 'var(--bad)', 'var(--muted)'];
	function barColorMap(samples: LetterboxDetail['samples']): Map<number, string> {
		const seen = new Map<number, string>();
		for (const s of samples ?? []) {
			if (!s.ok) continue;
			const bar = Math.round(((s.top_bar ?? 0) + (s.bottom_bar ?? 0)) / 2);
			if (!seen.has(bar)) seen.set(bar, BAR_COLORS[seen.size % BAR_COLORS.length]);
		}
		return seen;
	}

	function confidenceTone(c: string | null | undefined): string {
		if (!c || c === 'none') return 'var(--faint)';
		if (c === 'high') return 'var(--good)';
		if (c === 'low') return 'var(--bad)';
		return 'var(--warn)';
	}

	async function run(fn: () => Promise<unknown>, okMsg: string) {
		if (movieId == null || busy) return;
		busy = true;
		try {
			await fn();
			toast(okMsg, 'good');
			lastId = null;
			onChanged();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Action failed', 'bad');
		} finally {
			busy = false;
		}
	}

	const id = $derived(movieId);
</script>

<div class="panel">
	{#if movieId == null}
		<div class="empty">
			<Icon name="letterbox" size={32} stroke={1} />
			<span>Select a movie from the board to inspect it.</span>
		</div>
	{:else if loading}
		<div class="empty">Loading…</div>
	{:else if loadError || !detail}
		<div class="empty err">{loadError ?? 'No detail available.'}</div>
	{:else if stage === 'detected' || stage === 'preview' || stage === 'processed'}
		<!-- ── 2-row grid: before row + after row, actions span both ── -->
		<div class="grid-det">
			<!-- Row 1, Col 1: badge + title + year + BEFORE dims/AR -->
			<div class="meta-top">
				<div class="badge-row">
					<div class="badge" style="--c:{toneVar(meta?.tone ?? 'muted')}">
						<StatusDot tone={meta?.tone ?? 'muted'} size={7} />
						{meta?.label ?? detail.status}
					</div>
				</div>
				<h3>{detail.title ?? `Movie ${detail.movie_id}`}</h3>
				<div class="year">
					{detail.year ?? '—'}{#if detail.source_height} · {detail.source_height}p{/if}
				</div>
				<dl>
					{#if detail.source_width && detail.source_height}
						<dt>Dimensions</dt>
						<dd class="mono">{detail.source_width}×{detail.source_height}</dd>
					{/if}
					{#if aspectRatio(detail.source_width, detail.source_height)}
						<dt>Aspect ratio</dt>
						<dd class="mono">{aspectRatio(detail.source_width, detail.source_height)}:1</dd>
					{/if}
				</dl>
			</div>

			<!-- Row 1, Col 2: before image -->
			<div class="frame-cell">
				{#if beforeUrl}
					<img class="frame" src={beforeUrl} alt="before crop" loading="lazy" />
				{:else}
					<div class="frame unanalyzed"><span class="ph">No preview</span></div>
				{/if}
			</div>

			<!-- Col 3, spans both rows: actions -->
			<div class="actions det-actions">
				<div class="alabel">Actions</div>
				{#if stage === 'detected'}
					<div class="fix-card active">
						<div class="fix-head">⚡ Quick · Crop Tag <span class="fix-on">Active</span></div>
						<div class="fix-body">MKV pixel-crop tag. Instant & reversible. No quality loss.</div>
					</div>
					<div class="fix-card disabled">
						<div class="fix-head">🛠 Permanent · Re-encode</div>
						<div class="fix-body">FFmpeg re-encode. Coming soon.</div>
					</div>
					<button
						class="btn-gold"
						disabled={busy}
						onclick={() => run(() => applyLetterbox(fetch, id!), 'Crop tag applied')}
					>
						Apply crop tag →
					</button>
					<button
						class="btn-ghost"
						disabled={busy}
						onclick={() => run(() => ignoreLetterbox(fetch, id!), 'Skipped')}
					>
						Skip
					</button>
				{:else if stage === 'preview'}
					<div class="applied-card">
						<div class="alabel">MKV pixel-crop value</div>
						<div class="mono gold big">
							{detail.applied_crop_top ?? detail.recommended_crop_top ?? 0}:{detail.applied_crop_bottom ??
								detail.recommended_crop_bottom ??
								0}:0:0
						</div>
					</div>
					<button
						class="btn-gold"
						disabled={busy}
						onclick={() => run(() => confirmLetterbox(fetch, id!), 'Confirmed & finished')}
					>
						<Icon name="refresh" size={14} /> Confirm & finish
					</button>
					<button
						class="btn-sec"
						disabled={busy}
						onclick={() => run(() => removeLetterbox(fetch, id!), 'Tag removed · reverted')}
					>
						Remove tag · revert
					</button>
					<button
						class="btn-ghost"
						disabled={busy}
						onclick={() => run(() => reprocessLetterbox(fetch, id!), 'Reprocessed')}
					>
						Reprocess with new settings
					</button>
					<div class="note good">Reversible. Remove the tag at any time with zero quality impact.</div>
				{:else if stage === 'processed'}
					<div class="applied-card">
						<div class="alabel">Applied crop</div>
						<div class="mono gold big">
							{detail.applied_crop_top ?? 0}:{detail.applied_crop_bottom ?? 0}:0:0
						</div>
					</div>
					<button
						class="btn-sec"
						disabled={busy}
						onclick={() => run(() => removeLetterbox(fetch, id!), 'Tag removed')}
					>
						Remove tag
					</button>
					<button
						class="btn-ghost"
						disabled={busy}
						onclick={() => run(() => reprocessLetterbox(fetch, id!), 'Reprocessed')}
					>
						Reprocess
					</button>
				{/if}
			</div>

			<!-- Row 2, Col 1: AFTER dims/AR/crop/confidence/method — aligns with after image -->
			<div class="meta-bot">
				<dl>
					{#if detail.source_width && afterHeight}
						<dt>Dimensions</dt>
						<dd class="mono">{detail.source_width}×{afterHeight}</dd>
					{/if}
					{#if afterAR}
						<dt>Aspect ratio</dt>
						<dd class="mono">{afterAR}</dd>
					{/if}
					<dt>Crop T / B</dt>
					<dd class="mono gold">{cropLabel}</dd>
					{#if detail.confidence && detail.confidence !== 'none'}
						<dt>Confidence</dt>
						<dd>
							<button
								class="conf-btn"
								style="color:{confidenceTone(detail.confidence)}"
								onclick={() => (showConf = !showConf)}
							>
								{detail.confidence}
								<span class="caret">{showConf ? '▲' : '▼'}</span>
							</button>
						</dd>
					{/if}
				</dl>
				{#if showConf}
					<div class="conf-expand">
						{#if detail.samples && detail.samples.length > 0}
							{@const okSamples = detail.samples.filter((s) => s.ok)}
							{@const sampleBars = okSamples.map((s) => Math.round(((s.top_bar ?? 0) + (s.bottom_bar ?? 0)) / 2))}
							{@const barMed = sampleBars.length > 0 ? [...sampleBars].sort((a, b) => a - b)[Math.floor(sampleBars.length / 2)] : 0}
							{@const agreeCount = sampleBars.filter((b) => Math.abs(b - barMed) <= 2).length}
							{@const colorMap = barColorMap(detail.samples)}
							<div class="ce-summary">
								{#if agreeCount === okSamples.length && okSamples.length > 0}
									All {okSamples.length} agree
								{:else}
									{agreeCount}/{detail.samples.length} agree
								{/if}
								<span class="ce-hint">· click to preview that frame</span>
							</div>
							{#each detail.samples as s (s.minute)}
								{@const bar = s.ok ? Math.round(((s.top_bar ?? 0) + (s.bottom_bar ?? 0)) / 2) : null}
								{@const barColor = bar != null ? (colorMap.get(bar) ?? 'var(--faint)') : 'var(--faint)'}
								{@const isActive = s.minute === activeMinute}
								<button
									class="ce-row"
									class:ce-active={isActive}
									onclick={() => (previewMinute = s.minute)}
									title="Preview frame at {s.minute} min"
								>
									<span class="mono ce-min">{s.minute}min</span>
									{#if s.ok}
										<span class="mono ce-val">{s.top_bar ?? '?'}/{s.bottom_bar ?? '?'} px</span>
										<span class="ce-dot" style="color:{barColor}">●</span>
									{:else}
										<span class="ce-err">{s.error ?? 'failed'}</span>
										<span class="ce-dot" style="color:var(--bad)">✕</span>
									{/if}
								</button>
							{/each}
						{:else}
							<div class="ce-summary">No sample data available.</div>
						{/if}
					</div>
				{/if}
				{#if detail.detect_method}
					<div class="method-tag mono">{detail.detect_method}</div>
				{/if}
			</div>

			<!-- Row 2, Col 2: after image — defines the row's height -->
			<div class="frame-cell">
				{#if afterUrl}
					<img class="frame good" src={afterUrl} alt="after crop" loading="lazy" />
				{:else}
					<div class="frame unanalyzed"><span class="ph">No preview</span></div>
				{/if}
			</div>
		</div>
	{:else}
		<!-- ── Single-row grid for candidate / clean / other ── -->
		<div class="grid">
			<div class="meta">
				<div class="badge" style="--c:{toneVar(meta?.tone ?? 'muted')}; margin-bottom:10px;">
					<StatusDot tone={meta?.tone ?? 'muted'} size={7} />
					{meta?.label ?? detail.status}
				</div>
				<h3>{detail.title ?? `Movie ${detail.movie_id}`}</h3>
				<div class="year">
					{detail.year ?? '—'}{#if detail.source_height} · {detail.source_height}p{/if}
				</div>
				<dl>
					{#if detail.source_width && detail.source_height}
						<dt>Dimensions</dt>
						<dd class="mono">{detail.source_width}×{detail.source_height}</dd>
					{/if}
					{#if aspectRatio(detail.source_width, detail.source_height)}
						<dt>Aspect ratio</dt>
						<dd class="mono">{aspectRatio(detail.source_width, detail.source_height)}:1</dd>
					{/if}
				</dl>
				{#if detail.ineligible_reason}
					<div class="note warn">{detail.ineligible_reason}</div>
				{/if}
				{#if detail.error}
					<div class="note err">{detail.error}</div>
				{/if}
			</div>

			<div class="preview">
				{#if stage === 'candidate'}
					<div class="ptitle">Suspected frame · unanalyzed</div>
					<div class="frame unanalyzed">
						<span class="ph">? awaiting frame analysis</span>
					</div>
					<div class="note">
						Resolution scan flagged this file. Run frame analysis to confirm and measure exact
						crop values before applying any fix.
					</div>
				{:else if detail.preview_urls}
					<img class="frame" src={detail.preview_urls.before} alt="before crop" loading="lazy" />
					<img class="frame good" src={detail.preview_urls.after} alt="after crop" loading="lazy" />
				{:else}
					<div class="frame unanalyzed"><span class="ph">No preview available</span></div>
				{/if}
			</div>

			<div class="actions">
				<div class="alabel">Actions</div>
				{#if stage === 'candidate'}
					<button class="btn-gold" onclick={onAnalyzeAll} disabled={analyzing}>
						<Icon name="refresh" size={15} /> Analyze all candidates
					</button>
					<button
						class="btn-sec"
						disabled={busy}
						onclick={() => run(() => detectLetterbox(fetch, id!), 'Analysis complete')}
					>
						Analyze this film only
					</button>
					<button
						class="btn-ghost"
						disabled={busy}
						onclick={() => run(() => ignoreLetterbox(fetch, id!), 'Marked as not letterboxed')}
					>
						Skip · mark as not LB
					</button>
				{:else if stage === 'clean'}
					<div class="note">
						No fix needed.{#if detail.status === 'variable_unsafe'}
							Variable aspect ratio — unsafe to crop.{/if}
					</div>
					<button
						class="btn-sec"
						disabled={busy}
						onclick={() => run(() => detectLetterbox(fetch, id!), 'Re-detected')}
					>
						Re-detect
					</button>
				{:else}
					<button
						class="btn-sec"
						disabled={busy}
						onclick={() => run(() => detectLetterbox(fetch, id!), 'Re-detected')}
					>
						Re-detect
					</button>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	.panel {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		padding: 18px;
		margin-bottom: 22px;
	}
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 10px;
		padding: 48px 24px;
		color: var(--faint);
		font-size: 13px;
		text-align: center;
	}
	.empty.err {
		color: var(--bad);
	}

	/* Single-row layout (candidate / clean / other) */
	.grid {
		display: grid;
		grid-template-columns: 200px 1fr 240px;
		gap: 24px;
	}
	@media (max-width: 900px) {
		.grid,
		.grid-det {
			grid-template-columns: 1fr !important;
		}
	}

	/* 2-row layout for detected / preview / processed */
	.grid-det {
		display: grid;
		grid-template-columns: 200px 1fr 240px;
		/* Rows auto-size to content; the image cell defines the row height */
		gap: 14px 24px;
	}
	.meta-top {
		grid-column: 1;
		grid-row: 1;
	}
	.frame-cell {
		grid-column: 2;
		/* row determined by DOM order */
		min-width: 0;
	}
	.det-actions {
		grid-column: 3;
		grid-row: 1 / 3; /* spans both rows */
		align-self: start;
	}
	.meta-bot {
		grid-column: 1;
		grid-row: 2;
		align-self: start;
	}

	/* meta shared */
	.badge-row {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 10px;
	}
	.badge {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--c, var(--muted));
		padding: 3px 8px;
		border: 1px solid var(--line2);
		border-radius: 99px;
	}
	h3 {
		margin: 0;
		font-size: 17px;
		font-weight: 650;
	}
	.year {
		font-size: 12px;
		color: var(--muted);
		margin-top: 2px;
		margin-bottom: 14px;
	}
	.method-tag {
		display: inline-block;
		font-size: 10.5px;
		color: var(--faint);
		border: 1px solid var(--line);
		border-radius: 4px;
		padding: 2px 7px;
		margin-top: 10px;
	}
	.conf-btn {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		background: transparent;
		border: none;
		padding: 0;
		font-size: 13px;
		font-weight: 600;
		text-transform: capitalize;
		cursor: pointer;
	}
	.caret {
		font-size: 9px;
		opacity: 0.6;
	}
	.conf-expand {
		margin-top: 8px;
		padding: 8px 10px;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.ce-summary {
		font-size: 10.5px;
		color: var(--muted);
		margin-bottom: 4px;
	}
	.ce-hint {
		color: var(--faint);
		font-size: 10px;
	}
	.ce-row {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 11px;
		width: 100%;
		padding: 3px 4px;
		border-radius: 4px;
		border: none;
		background: transparent;
		text-align: left;
		cursor: pointer;
		color: var(--text);
	}
	.ce-row:hover {
		background: var(--panel2);
	}
	.ce-active {
		background: var(--ink2);
		outline: 1px solid var(--line2);
	}
	.ce-min {
		color: var(--faint);
		min-width: 36px;
	}
	.ce-val {
		color: var(--text);
		flex: 1;
	}
	.ce-dot {
		font-size: 11px;
		flex-shrink: 0;
	}
	.ce-err {
		font-size: 10px;
		color: var(--bad);
		flex: 1;
	}
	dl {
		display: grid;
		grid-template-columns: 1fr;
		gap: 0;
		margin: 0;
	}
	dt {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
		font-weight: 600;
		margin-top: 8px;
	}
	dd {
		margin: 2px 0 0;
		font-size: 13px;
		color: var(--text);
	}
	dd.gold {
		color: var(--gold);
	}

	/* preview (used by single-row layout) */
	.preview {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.ptitle {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.frame {
		width: 100%;
		aspect-ratio: 16 / 9;
		object-fit: cover;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: linear-gradient(150deg, #1a1410, #0a0806);
	}
	.frame.good {
		border-color: color-mix(in srgb, var(--good) 50%, var(--line2));
		/* After-crop image has a wider AR than 16:9 — let its natural height show */
		aspect-ratio: auto;
		height: auto;
		object-fit: initial;
	}
	.frame.unanalyzed {
		display: flex;
		align-items: center;
		justify-content: center;
		border-style: dashed;
		background: repeating-linear-gradient(
			45deg,
			var(--ink2),
			var(--ink2) 10px,
			var(--panel) 10px,
			var(--panel) 20px
		);
	}
	.ph {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--gold);
	}

	/* actions */
	.actions {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.alabel {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.fix-card {
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		padding: 10px 12px;
	}
	.fix-card.active {
		border-color: var(--gold);
		background: var(--gold-soft);
	}
	.fix-card.disabled {
		opacity: 0.5;
	}
	.fix-head {
		font-size: 12.5px;
		font-weight: 600;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.fix-on {
		font-size: 10px;
		color: var(--on-gold);
		background: var(--gold);
		padding: 1px 6px;
		border-radius: 99px;
		margin-left: auto;
	}
	.fix-body {
		font-size: 11px;
		color: var(--muted);
		margin-top: 4px;
		line-height: 1.4;
	}
	.applied-card {
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		padding: 10px 12px;
	}
	.big {
		font-size: 16px;
		margin-top: 3px;
	}
	.gold {
		color: var(--gold);
	}
	.mono {
		font-family: var(--font-mono);
	}

	/* notes */
	.note {
		font-size: 11.5px;
		color: var(--muted);
		line-height: 1.5;
		padding: 9px 11px;
		background: var(--ink2);
		border-left: 3px solid var(--line2);
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
	}
	.note.good {
		border-left-color: var(--good);
		color: var(--good);
	}
	.note.warn {
		border-left-color: var(--warn);
		color: var(--warn);
	}
	.note.err {
		border-left-color: var(--bad);
		color: var(--bad);
	}

	/* buttons */
	.btn-gold,
	.btn-sec,
	.btn-ghost {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 7px;
		padding: 9px 14px;
		border-radius: 8px;
		font-size: 13px;
		font-weight: 600;
		white-space: nowrap;
	}
	.btn-gold {
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
	}
	.btn-sec {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-weight: 500;
	}
	.btn-ghost {
		border: 1px solid transparent;
		background: transparent;
		color: var(--muted);
		font-weight: 500;
	}
	.btn-ghost:hover {
		color: var(--text);
		background: var(--panel2);
	}
	.btn-gold:disabled,
	.btn-sec:disabled,
	.btn-ghost:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
