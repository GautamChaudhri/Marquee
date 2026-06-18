<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { subscribe } from '$lib/sse';
	import { toast } from '$lib/toast';
	import {
		scanLibrary,
		analyzeAll,
		healDrift,
		applyBatch,
		confirmLetterbox
	} from '$lib/api/letterbox';
	import type { LetterboxAnalyzeSummary } from '$lib/api/types';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import LetterboxCard from '$lib/components/LetterboxCard.svelte';
	import LetterboxDetail from '$lib/components/LetterboxDetail.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// ── Selection (URL-driven) ─────────────────────────────────────────────────
	const cols = $derived(data.columns);
	const selFromUrl = $derived(Number(page.url.searchParams.get('sel')) || null);
	const defaultSel = $derived(
		cols.detected.items[0]?.movie_id ?? cols.candidates.items[0]?.movie_id ?? null
	);
	const selected = $derived(selFromUrl ?? defaultSel);

	function select(id: number) {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('sel', String(id));
		goto(`/letterbox?${sp.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// ── Board actions ──────────────────────────────────────────────────────────
	let scanning = $state(false);
	let healing = $state(false);
	let processing = $state(false);
	let confirming = $state(false);

	async function doScan() {
		scanning = true;
		try {
			await scanLibrary(fetch);
			toast('Library scanned', 'good');
			await invalidateAll();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Scan failed', 'bad');
		} finally {
			scanning = false;
		}
	}

	async function doHeal() {
		healing = true;
		try {
			const r = await healDrift(fetch);
			toast(`Heal complete — ${r.reapplied} re-applied of ${r.checked} checked`, 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Heal failed', 'bad');
		} finally {
			healing = false;
		}
	}

	async function processDetected() {
		const ids = cols.detected.items.map((i) => i.movie_id);
		if (ids.length === 0 || processing) return;
		processing = true;
		try {
			await applyBatch(fetch, ids);
			toast(`Applied crop tags to ${ids.length} ${ids.length === 1 ? 'movie' : 'movies'}`, 'good');
			await invalidateAll();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Process failed', 'bad');
		} finally {
			processing = false;
		}
	}

	async function confirmAll() {
		const ids = cols.preview.items.map((i) => i.movie_id);
		if (ids.length === 0 || confirming) return;
		confirming = true;
		try {
			await Promise.all(ids.map((id) => confirmLetterbox(fetch, id)));
			toast(`Confirmed ${ids.length} ${ids.length === 1 ? 'movie' : 'movies'}`, 'good');
			await invalidateAll();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Confirm failed', 'bad');
		} finally {
			confirming = false;
		}
	}

	// ── Analyze (batch frame analysis over SSE) ────────────────────────────────
	let analyzing = $state(false);
	let progress = $state(0);
	let progressTotal = $state(0);
	let progressDone = $state(0);
	let result = $state<LetterboxAnalyzeSummary | null>(null);
	let unsub: (() => void) | null = null;

	async function doAnalyze() {
		if (analyzing) return;
		analyzing = true;
		result = null;
		progress = 0;
		progressDone = 0;
		progressTotal = 0;
		try {
			const ref = await analyzeAll(fetch);
			progressTotal = ref.total;
			if (ref.total === 0) {
				toast('No candidates to analyze', 'info');
				analyzing = false;
				return;
			}
			unsub = subscribe(ref.events_url, ['message', 'done'], (type, raw) => {
				if (type === 'done') {
					finishAnalyze();
					return;
				}
				const d = (raw ?? {}) as Record<string, unknown>;
				if (d.state === 'done' && d.summary) {
					result = d.summary as unknown as LetterboxAnalyzeSummary;
				} else if (typeof d.completed === 'number') {
					progressDone = d.completed as number;
					if (typeof d.total === 'number') progressTotal = d.total as number;
					progress = progressTotal ? (progressDone / progressTotal) * 100 : 0;
				}
			});
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Analysis failed to start', 'bad');
			analyzing = false;
		}
	}

	function finishAnalyze() {
		analyzing = false;
		progress = 100;
		unsub?.();
		unsub = null;
		if (result) {
			const nlb = result.not_letterboxed;
			toast(
				`Analysis complete — ${result.candidate} detected${nlb ? `, ${nlb} not letterboxed` : ''}`,
				'good'
			);
		} else {
			toast('Analysis complete', 'good');
		}
		invalidateAll();
	}

	$effect(() => () => unsub?.());

	// ── Header chips ───────────────────────────────────────────────────────────
	function relTime(iso: string | null): string {
		if (!iso) return 'never';
		const diff = Date.now() - new Date(iso).getTime();
		const h = Math.floor(diff / 3.6e6);
		if (h < 1) return `${Math.max(1, Math.floor(diff / 6e4))}m ago`;
		if (h < 24) return `${h}h ago`;
		return `${Math.floor(h / 24)}d ago`;
	}

	const st = $derived(data.status);
	const detectedTotal = $derived(cols.detected.total);
</script>

<SectionHeader title="Letterbox" subtitle="Black-bar detection & crop tagging">
	{#snippet action()}
		<button class="btn-sec" onclick={doHeal} disabled={healing}>
			{healing ? 'Healing…' : 'Heal drifted tags'}
		</button>
	{/snippet}
</SectionHeader>

{#if st}
	<div class="statusbar">
		<span class="chip">
			<StatusDot tone={st.enabled ? 'good' : 'bad'} size={7} />
			{st.enabled ? 'Detection enabled' : 'Disabled'}
		</span>
		<span class="chip mono">method: {st.method}</span>
		{#each Object.entries(st.binaries) as [name, ok] (name)}
			<span class="chip mono" class:bad={!ok}>{name} {ok ? '✓' : '✗'}</span>
		{/each}
		<span class="chip mono faint">last scan {relTime(st.last_scan)}</span>
	</div>
{:else if data.error}
	<div class="banner err">{data.error} The backend may be offline.</div>
{/if}

<div class="info">
	<Icon name="letterbox" size={15} />
	<span>
		MKV pixel-crop tags are honored by <strong>Plex Desktop · VLC · mpv</strong> — but
		<strong>not</strong> Plex Web or Mobile. Detection never writes to media; tags are reversible.
	</span>
</div>

{#if analyzing || result}
	<div class="analyze-bar" class:done={!analyzing}>
		<div class="ab-row">
			<span class="ab-label">
				{#if analyzing}
					<span class="spin">⟳</span> Analyzing candidates… {progressDone}/{progressTotal}
				{:else if result}
					✓ Analysis complete — <strong>{result.candidate}</strong> detected ·
					<strong>{result.not_letterboxed}</strong> not letterboxed · {result.variable} unsafe
				{/if}
			</span>
			{#if !analyzing}
				<button class="ab-dismiss" onclick={() => (result = null)} aria-label="Dismiss">
					<Icon name="x" size={14} />
				</button>
			{/if}
		</div>
		{#if analyzing}<ProgressBar value={progress} tone="gold" />{/if}
	</div>
{/if}

<LetterboxDetail movieId={selected} onChanged={invalidateAll} onAnalyzeAll={doAnalyze} {analyzing} />

<!-- Stage breadcrumb -->
<div class="flow">
	<span class="pill" style="--c:var(--warn)">Candidates</span>
	<span class="arrow">→ analyze →</span>
	<span class="pill" style="--c:var(--bad)">Not Letterboxed</span>
	<span class="dotsep">·</span>
	<span class="pill" style="--c:var(--info)">Detected</span>
	<span class="arrow">→ select fix → process →</span>
	<span class="pill" style="--c:var(--dovi)">Preview &amp; Confirm</span>
	<span class="arrow">→ confirm →</span>
	<span class="pill" style="--c:var(--good)">Done</span>
</div>

{#snippet rows(items: typeof cols.candidates.items, total: number, variant: 'candidates' | 'detected' | 'preview' | 'notlb' | 'processed')}
	{#if items.length === 0}
		<div class="tray-empty">—</div>
	{:else}
		{#each items as item (item.movie_id)}
			<LetterboxCard {item} {variant} selected={item.movie_id === selected} onSelect={select} />
		{/each}
		{#if total > items.length}<div class="tray-more">view all {total}</div>{/if}
	{/if}
{/snippet}

<!-- Top trays: Candidates | Detected | Preview & Confirm -->
<div class="trays top">
	<section class="tray" style="--accent:var(--warn)">
		<div class="tray-head">
			<div class="tray-title-row">
				<span class="tray-title">Candidates</span>
				<span class="tray-count">{cols.candidates.total}</span>
				<span class="spacer"></span>
				<span class="tray-note">res probe</span>
			</div>
			<div class="tray-actions">
				<button class="tb sec" onclick={doScan} disabled={scanning}>
					<Icon name="refresh" size={13} />
					{scanning ? 'Scanning…' : 'Scan library'}
				</button>
				<button class="tb gold" onclick={doAnalyze} disabled={analyzing}>
					{analyzing ? 'Analyzing…' : 'Analyze →'}
				</button>
			</div>
		</div>
		<div class="tray-body">{@render rows(cols.candidates.items, cols.candidates.total, 'candidates')}</div>
	</section>

	<section class="tray" style="--accent:var(--info)">
		<div class="tray-head">
			<div class="tray-title-row">
				<span class="tray-title">Detected</span>
				<span class="tray-count">{cols.detected.total}</span>
				<span class="spacer"></span>
				<span class="tray-note ready">{detectedTotal}/{detectedTotal} ready</span>
			</div>
			<div class="tray-actions">
				<span class="tb quick-active">⚡ All Quick</span>
				<span class="tb perm-disabled" title="Re-encode coming soon">🔧 All Perm</span>
				<button class="tb gold" onclick={processDetected} disabled={processing || detectedTotal === 0}>
					{processing ? 'Processing…' : 'Process →'}
				</button>
			</div>
		</div>
		<div class="tray-body">{@render rows(cols.detected.items, cols.detected.total, 'detected')}</div>
	</section>

	<section class="tray" style="--accent:var(--dovi)">
		<div class="tray-head simple">
			<span class="tray-title">Preview &amp; Confirm</span>
			<span class="tray-count">{cols.preview.total}</span>
			<span class="spacer"></span>
			<button
				class="tb gold sm"
				onclick={confirmAll}
				disabled={confirming || cols.preview.total === 0}
			>
				<Icon name="refresh" size={13} />
				{confirming ? 'Confirming…' : 'Confirm all'}
			</button>
		</div>
		<div class="tray-body">{@render rows(cols.preview.items, cols.preview.total, 'preview')}</div>
	</section>
</div>

<!-- Bottom trays: Not Letterboxed | Processed -->
<div class="trays bottom">
	<section class="tray" style="--accent:var(--bad)">
		<div class="tray-head simple">
			<span class="tray-title">Not Letterboxed</span>
			<span class="tray-count">{cols.notLetterboxed.total}</span>
			<span class="spacer"></span>
			<span class="tray-note">no fix needed</span>
		</div>
		<div class="tray-body">
			{@render rows(cols.notLetterboxed.items, cols.notLetterboxed.total, 'notlb')}
		</div>
	</section>

	<section class="tray" style="--accent:var(--good)">
		<div class="tray-head simple">
			<span class="tray-title">Processed</span>
			<span class="tray-count">{cols.processed.total}</span>
		</div>
		<div class="tray-body">{@render rows(cols.processed.items, cols.processed.total, 'processed')}</div>
	</section>
</div>

<style>
	.statusbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-bottom: 14px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-size: 11.5px;
		color: var(--muted);
		padding: 3px 9px;
		border: 1px solid var(--line);
		border-radius: 99px;
		background: var(--panel);
	}
	.chip.bad {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 30%, var(--line));
	}
	.chip.faint {
		color: var(--faint);
	}
	.mono {
		font-family: var(--font-mono);
	}
	.info {
		display: flex;
		align-items: flex-start;
		gap: 9px;
		padding: 11px 14px;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-left: 3px solid var(--info);
		border-radius: var(--radius-sm);
		font-size: 12px;
		color: var(--muted);
		line-height: 1.5;
		margin-bottom: 16px;
	}
	.info :global(svg) {
		flex: none;
		margin-top: 1px;
		color: var(--info);
	}
	.banner.err {
		padding: 11px 14px;
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--bad) 10%, transparent);
		border: 1px solid color-mix(in srgb, var(--bad) 30%, transparent);
		color: var(--bad);
		font-size: 13px;
		margin-bottom: 16px;
	}

	.analyze-bar {
		border: 1px solid var(--gold-deep);
		background: var(--gold-soft);
		border-radius: var(--radius-sm);
		padding: 11px 14px;
		margin-bottom: 16px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.analyze-bar.done {
		border-color: color-mix(in srgb, var(--good) 40%, var(--line2));
		background: color-mix(in srgb, var(--good) 8%, transparent);
	}
	.ab-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	.ab-label {
		font-size: 12.5px;
		color: var(--text);
	}
	.ab-label strong {
		color: var(--gold);
	}
	.analyze-bar.done .ab-label strong {
		color: var(--good);
	}
	.ab-dismiss {
		flex: none;
		display: grid;
		place-items: center;
		width: 24px;
		height: 24px;
		border: none;
		background: transparent;
		color: var(--muted);
		border-radius: 6px;
	}
	.ab-dismiss:hover {
		background: var(--panel2);
		color: var(--text);
	}

	/* breadcrumb */
	.flow {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 7px;
		margin-bottom: 14px;
	}
	.pill {
		padding: 4px 11px;
		border-radius: 20px;
		font-size: 11px;
		font-weight: 700;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 12%, transparent);
	}
	.arrow,
	.dotsep {
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--faint);
	}

	/* trays */
	.trays {
		display: grid;
		gap: 14px;
	}
	.trays.top {
		grid-template-columns: 1fr 1fr 1fr;
		margin-bottom: 14px;
	}
	.trays.bottom {
		grid-template-columns: 2fr 1fr;
	}
	@media (max-width: 1000px) {
		.trays.top,
		.trays.bottom {
			grid-template-columns: 1fr;
		}
	}
	.tray {
		background: var(--panel);
		border: 1px solid var(--line);
		border-top: 2px solid var(--accent);
		border-radius: 12px;
		overflow: hidden;
		display: flex;
		flex-direction: column;
	}
	.tray-head {
		padding: 12px 14px 10px;
		border-bottom: 1px solid var(--panel2);
	}
	.tray-head.simple {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 14px;
	}
	.tray-title-row {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 9px;
	}
	.tray-title {
		font-size: 12.5px;
		font-weight: 700;
		color: var(--accent);
	}
	.tray-count {
		font-family: var(--font-mono);
		font-size: 11px;
		background: color-mix(in srgb, var(--accent) 14%, transparent);
		color: var(--accent);
		padding: 1px 7px;
		border-radius: 7px;
	}
	.spacer {
		flex: 1;
	}
	.tray-note {
		font-size: 11px;
		color: var(--faint);
	}
	.tray-note.ready {
		font-family: var(--font-mono);
		color: var(--good);
	}
	.tray-actions {
		display: flex;
		gap: 5px;
	}
	.tb {
		flex: 1;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 5px;
		border-radius: 7px;
		padding: 6px 8px;
		font-weight: 600;
		font-size: 11.5px;
		white-space: nowrap;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.tb.sm {
		flex: none;
		padding: 6px 13px;
	}
	.tb.gold {
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		border-color: var(--gold-deep);
	}
	.tb.quick-active {
		background: color-mix(in srgb, var(--info) 10%, transparent);
		color: var(--info);
		border-color: color-mix(in srgb, var(--info) 28%, transparent);
		cursor: default;
	}
	.tb.perm-disabled {
		background: color-mix(in srgb, var(--low) 8%, transparent);
		color: var(--low);
		border-color: color-mix(in srgb, var(--low) 22%, transparent);
		opacity: 0.55;
		cursor: not-allowed;
	}
	.tb:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.tray-body {
		display: flex;
		flex-direction: column;
		min-height: 60px;
	}
	.tray-empty {
		text-align: center;
		color: var(--faint2);
		font-size: 13px;
		padding: 20px 0;
	}
	.tray-more {
		text-align: center;
		font-size: 11.5px;
		color: var(--muted);
		padding: 8px 0;
		border-top: 1px solid var(--panel2);
	}

	.btn-sec {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		padding: 8px 14px;
		border-radius: 8px;
		font-size: 13px;
		font-weight: 500;
		white-space: nowrap;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.btn-sec:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.spin {
		display: inline-block;
		animation: spin 1s linear infinite;
	}
</style>
