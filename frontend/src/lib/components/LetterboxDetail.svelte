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

	// Re-fetch whenever the selected movie changes.
	let lastId = $state<number | null>(null);
	$effect(() => {
		if (movieId === lastId) return;
		lastId = movieId;
		detail = null;
		loadError = null;
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

	async function run(fn: () => Promise<unknown>, okMsg: string) {
		if (movieId == null || busy) return;
		busy = true;
		try {
			await fn();
			toast(okMsg, 'good');
			lastId = null; // force re-fetch of detail
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
	{:else}
		<div class="grid">
			<!-- ── Left meta column ── -->
			<div class="meta">
				<div class="badge" style="--c:{toneVar(meta?.tone ?? 'muted')}">
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
					{#if stage === 'detected' || stage === 'preview' || stage === 'processed'}
						<dt>Crop T / B</dt>
						<dd class="mono gold">{cropLabel}</dd>
						{#if detail.confidence && detail.confidence !== 'none'}
							<dt>Confidence</dt>
							<dd class="cap">{detail.confidence}</dd>
						{/if}
					{/if}
					{#if detail.detect_method}
						<dt>Method</dt>
						<dd class="mono">{detail.detect_method}</dd>
					{/if}
				</dl>

				{#if detail.ineligible_reason}
					<div class="note warn">{detail.ineligible_reason}</div>
				{/if}
				{#if detail.error}
					<div class="note err">{detail.error}</div>
				{/if}
			</div>

			<!-- ── Center preview column ── -->
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
					<div class="ptitle">Before · detected bars</div>
					<img class="frame" src={detail.preview_urls.before} alt="before crop" loading="lazy" />
					<div class="ptitle">After · cropped output</div>
					<img class="frame good" src={detail.preview_urls.after} alt="after crop" loading="lazy" />
				{:else}
					<div class="frame unanalyzed"><span class="ph">No preview available</span></div>
				{/if}
			</div>

			<!-- ── Right actions column ── -->
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
				{:else if stage === 'detected'}
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
	.grid {
		display: grid;
		grid-template-columns: 200px 1fr 240px;
		gap: 24px;
	}
	@media (max-width: 900px) {
		.grid {
			grid-template-columns: 1fr;
		}
	}

	/* meta */
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
		margin-bottom: 10px;
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
		margin-top: 10px;
	}
	dd {
		margin: 2px 0 0;
		font-size: 13px;
		color: var(--text);
	}
	dd.gold {
		color: var(--gold);
	}
	dd.cap {
		text-transform: capitalize;
	}

	/* preview */
	.preview {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.ptitle {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
		margin-top: 6px;
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
	}
	.frame.unanalyzed {
		display: flex;
		align-items: center;
		justify-content: center;
		border-style: dashed;
		background: repeating-linear-gradient(45deg, var(--ink2), var(--ink2) 10px, var(--panel) 10px, var(--panel) 20px);
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
