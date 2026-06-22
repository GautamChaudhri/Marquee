<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import TabBar from '$lib/components/TabBar.svelte';
	import ScoreBar from '$lib/components/ScoreBar.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import PosterCandidateTile from '$lib/components/PosterCandidateTile.svelte';
	import PosterStack from '$lib/components/PosterStack.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { getRunResults } from '$lib/api/pipeline';
	import { submitFeedback, undoFeedback } from '$lib/api/feedback';
	import { trackJob, type JobProgressDetail } from '$lib/jobs';
	import { toast } from '$lib/toast';
	import { gradientFor } from '$lib/display';
	import type { CandidateView, RunResults, RunResultsResponse } from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// svelte-ignore state_referenced_locally
	let run = $state<RunResultsResponse | null>(data.run);
	let runningDetail = $state<JobProgressDetail>({});
	let runningStatus = $state('running');
	let stop: (() => void) | null = null;

	const results = $derived(run && 'ranked' in run ? (run as RunResults) : null);
	const running = $derived(run != null && !('ranked' in run));
	const autoPick = $derived(results?.auto_pick ?? null);

	const SHORT: Record<string, string> = {
		sha256: 'SHA-256',
		resolution: 'Resolution',
		style: 'Style',
		ocr: 'OCR',
		phash: 'pHash',
		fan_junk: 'Fan-junk',
		errored: 'Errored'
	};

	const stageTabs = $derived.by(() => {
		if (!results) return [];
		const t = [{ id: 'ranked', label: 'Ranked', count: results.ranked.length }];
		for (const g of results.rejected_by_stage) {
			if (g.count > 0) t.push({ id: g.stage, label: SHORT[g.stage] ?? g.label, count: g.count });
		}
		return t;
	});

	let activeStage = $state('ranked');

/** Persisted toggle: 'flat' = visual card stacks in flat grid; 'sectioned' = Design headers with borders. */
let viewMode = $state<'flat' | 'sectioned'>(
	(typeof localStorage !== 'undefined' && (localStorage.getItem('marquee:pipeline:stackView') as 'flat' | 'sectioned' | null)) || 'flat'
);
$effect(() => {
	if (typeof localStorage !== 'undefined') {
		localStorage.setItem('marquee:pipeline:stackView', viewMode);
	}
});

/** Which stack_ids are currently expanded (flat view only). */
let expandedStackIds = $state<Set<number>>(new Set());

function toggleStack(stackId: number) {
	const next = new Set(expandedStackIds);
	if (next.has(stackId)) {
		next.delete(stackId);
	} else {
		next.add(stackId);
	}
	expandedStackIds = next;
}

/** Deterministic palette for expanded stack grouping accents. */
const STACK_PALETTE = [
	'#6366f1', '#f59e0b', '#10b981', '#ef4444',
	'#8b5cf6', '#06b6d4', '#f97316', '#84cc16',
];
function stackColor(stackId: number): string {
	return STACK_PALETTE[Math.abs(stackId) % STACK_PALETTE.length];
}

const currentPosters = $derived.by<CandidateView[]>(() => {
	if (!results) return [];
	if (activeStage === 'ranked') return results.ranked;
	return results.rejected_by_stage.find((g) => g.stage === activeStage)?.posters ?? [];
});

/** Group consecutive ranked posters that share a stack into arrays.
 *  Only groups of ≥2 become visual stacks; singles stay as individual tiles. */
const groupedRanked = $derived.by<Array<CandidateView | CandidateView[]>>(() => {
	const r = results?.ranked;
	if (!r?.length) return [];
	const hasStacks = !!(results?.stacks?.length);
	if (!hasStacks) return r;
	const out: Array<CandidateView | CandidateView[]> = [];
	let i = 0;
	while (i < r.length) {
		const c = r[i];
		if (c.stack_id != null && (c.stack_size ?? 1) > 1) {
			const sid = c.stack_id;
			const group: CandidateView[] = [];
			while (i < r.length && r[i].stack_id === sid) {
				group.push(r[i]);
				i++;
			}
			// Only treat as a stack group when ≥2 are consecutive
			if (group.length > 1) {
				out.push(group);
			} else {
				out.push(group[0]);
			}
		} else {
			out.push(c);
			i++;
		}
	}
	return out;
});

	function contribSegments(c: Record<string, number> | null) {
		if (!c) return [];
		const entries = Object.entries(c).filter(([, v]) => Number.isFinite(v));
		const max = Math.max(1e-6, ...entries.map(([, v]) => Math.abs(v)));
		return entries
			.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
			.slice(0, 8)
			.map(([k, v]) => ({ value: Math.abs(v) / max, label: k }));
	}

	// ── Live tracking while running ──────────────────────────────────────────────
	function startTracking(eventsUrl: string) {
		stop?.();
		stop = trackJob(
			fetch,
			data.runId,
			{
				onProgress: ({ status, detail }) => {
					runningStatus = status;
					runningDetail = detail;
				},
				onDone: () => void reload()
			},
			{ eventsUrl }
		);
	}
	async function reload() {
		try {
			run = await getRunResults(fetch, data.runId);
		} catch {
			/* keep the running view; the poll will retry */
		}
	}
	onMount(() => {
		if (run && !('ranked' in run)) startTracking(run.events_url);
	});
	onDestroy(() => stop?.());

	// ── Feedback (pick / approve / reject) ───────────────────────────────────────
	let pickOpen = $state(false);
	let pickTarget = $state<CandidateView | null>(null);
	let deploy = $state(true);
	let busy = $state(false);
	let rejectOpen = $state(false);
	let lastEventId = $state<string | null>(null);

	const pickIsAuto = $derived(
		!!pickTarget && !!autoPick && pickTarget.orig_filename === autoPick.orig_filename
	);

	function openPick(c: CandidateView) {
		if (!results) return;
		pickTarget = c;
		deploy = true;
		pickOpen = true;
	}

	async function confirmPick() {
		if (!pickTarget || !results) return;
		busy = true;
		try {
			const res = await submitFeedback(fetch, {
				run_id: results.run_id,
				action: pickIsAuto ? 'approve' : 'override',
				selected_filename: pickTarget.orig_filename,
				deploy
			});
			lastEventId = res.event_id;
			const where = res.deployed_to ? ' · deployed' : res.deploy_error ? ' · deploy failed' : '';
			toast(`Poster selected${where}`, res.deploy_error ? 'info' : 'good');
			if (res.deploy_error) toast(res.deploy_error, 'bad', 5000);
			pickOpen = false;
			await reload();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Selection failed', 'bad');
		} finally {
			busy = false;
		}
	}

	async function confirmReject() {
		if (!results) return;
		busy = true;
		try {
			const res = await submitFeedback(fetch, { run_id: results.run_id, action: 'reject_all' });
			lastEventId = res.event_id;
			toast('Auto-pick rejected — all candidates dismissed', 'info');
			rejectOpen = false;
			await reload();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Reject failed', 'bad');
		} finally {
			busy = false;
		}
	}

	async function undoLast() {
		if (!lastEventId) return;
		try {
			await undoFeedback(fetch, lastEventId);
			toast('Reverted', 'good');
			lastEventId = null;
			await reload();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Undo failed', 'bad');
		}
	}

	const pickGrad = $derived(pickTarget ? gradientFor(pickTarget.orig_filename) : gradientFor('?'));
</script>

<div class="crumb">
	<a href="/pipeline">Pipeline</a><span>/</span><span>Run {data.runId.slice(0, 8)}</span>
</div>

{#if data.error || !run}
	<div class="errstate">
		<Icon name="pipeline" size={40} stroke={1} />
		<strong>Could not load run</strong>
		<span>{data.error ?? 'Unknown error'}</span>
		<button onclick={() => goto('/pipeline')}>← Back to Pipeline</button>
	</div>
{:else if running}
	<div class="running-wrap">
		<h2>{results?.movie?.title ?? 'Pipeline run'}</h2>
		<RunProgress detail={runningDetail} status={runningStatus} title="Selecting poster" />
		<p class="run-hint">Live progress — results appear here as soon as the run finishes.</p>
	</div>
{:else if results}
	<!-- ── Header / auto-pick hero ── -->
	<div class="hero">
		<div
			class="hero-poster"
			style="--c0:{gradientFor(results.movie.title ?? '?')[0]}; --c1:{gradientFor(
				results.movie.title ?? '?'
			)[1]}"
		>
			{#if autoPick}
				<img src={autoPick.poster_url} alt="Auto-pick" />
			{:else}
				<div class="no-pick">No rankable candidate</div>
			{/if}
		</div>
		<div class="hero-body">
			<div class="hero-top">
				<div>
					<div class="hero-title">{results.movie.title ?? 'Unknown'}</div>
					<div class="hero-sub">
						<span class="scorer-tag">{results.scorer ?? 'scored'}</span>
						{#if results.reviewed}
							<span class="reviewed"><StatusDot tone="good" size={6} /> Reviewed</span>
							{#if lastEventId}<button class="undo" onclick={undoLast}>Undo</button>{/if}
						{/if}
					</div>
				</div>
			</div>

			{#if autoPick}
				<div class="auto-line">
					<span class="auto-badge">AUTO-PICK</span>
					{#if autoPick.final_score != null}<span class="mono score"
							>{autoPick.final_score.toFixed(3)}</span
						>{/if}
				</div>
				{#if autoPick.explanations?.length}
					<ul class="explain">
						{#each autoPick.explanations.slice(0, 5) as line, i (i)}<li>{line}</li>{/each}
					</ul>
				{/if}
				{#if autoPick.contributions}
					<div class="contrib"><ScoreBar segments={contribSegments(autoPick.contributions)} /></div>
				{/if}
			{/if}

			<div class="hero-actions">
				<button
					class="btn-gold"
					onclick={() => autoPick && openPick(autoPick)}
					disabled={!autoPick || results.reviewed}
				>
					Approve auto-pick
				</button>
				<button class="btn-ghost" onclick={() => (rejectOpen = true)} disabled={results.reviewed}>
					Reject all
				</button>
			</div>
		</div>
	</div>

	<!-- ── Stage tabs ── -->
	<div class="tabwrap">
		<TabBar tabs={stageTabs} active={activeStage} onSelect={(id) => (activeStage = id)} />
	</div>

	<p class="grid-hint">
		{#if activeStage === 'ranked'}
			{#if results.stacks?.length}
				<span class="hint-left">
					{#if viewMode === 'flat'}
						Stacked posters share a design — click the stack to fan out all variants. Click any poster
						to choose it.
					{:else}
						Posters are grouped into <strong>stacks</strong> of the same design — variants differ only in
						title position, text, or crop. Designs are ranked by their best few variants, so the
						auto-pick (1A) may not be the single highest-scored poster. Click any poster to choose it.
					{/if}
				</span>
				<span class="hint-toggle">
					<button
						class="view-btn"
						class:active={viewMode === 'flat'}
						onclick={() => (viewMode = 'flat')}
						title="Flat grid with visual card stacks"
					>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
						Flat
					</button>
					<button
						class="view-btn"
						class:active={viewMode === 'sectioned'}
						onclick={() => (viewMode = 'sectioned')}
						title="Sectioned stacks with Design headers"
					>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="8" rx="1"/><rect x="3" y="13" width="18" height="8" rx="1"/></svg>
						Sectioned
					</button>
				</span>
			{:else}
				Click any poster to set it as the chosen one — it deploys to the movie folder and trains the
				Key Art Engine.
			{/if}
		{:else}
			Rejected at this stage. Click to override and choose it anyway.
		{/if}
	</p>

	{#if activeStage === 'ranked' && results.stacks?.length}
		{#if viewMode === 'flat'}
			<div class="poster-grid">
				{#each groupedRanked as item (Array.isArray(item) ? (item as CandidateView[])[0].orig_filename : (item as CandidateView).orig_filename)}
					{#if Array.isArray(item)}
						{@const group = item as CandidateView[]}
						{@const sid = group[0].stack_id ?? 0}
						{#if expandedStackIds.has(sid)}
							{#each group as c (c.orig_filename)}
								<div
									class="expanded-tile"
									style="--group-accent: {stackColor(sid)}"
								>
									<PosterCandidateTile
										candidate={c}
										kind="ranked"
										selectable={!results.reviewed}
										onSelect={openPick}
									/>
								</div>
							{/each}
							<button
								class="stack-collapse-inline"
								onclick={() => toggleStack(sid)}
								title="Collapse this stack"
							>
								<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
								Collapse
							</button>
						{:else}
							<PosterStack
								members={group}
								selectable={!results.reviewed}
								onSelect={openPick}
								onToggle={() => toggleStack(sid)}
							/>
						{/if}
					{:else}
						<PosterCandidateTile
							candidate={item as CandidateView}
							kind="ranked"
							selectable={!results.reviewed}
							onSelect={openPick}
						/>
					{/if}
				{/each}
			</div>
		{:else}
			<div class="stacks">
				{#each results.stacks as st (st.stack_id)}
					<section class="stack">
						<div class="stack-head">
							<span class="stack-name">Design {st.stack_rank}</span>
							{#if st.stack_score != null}
								<span class="stack-score mono">{st.stack_score.toFixed(3)}</span>
							{/if}
							<span class="stack-size">{st.size} variant{st.size === 1 ? '' : 's'}</span>
						</div>
						<div class="poster-grid">
							{#each st.members as c (c.orig_filename)}
								<PosterCandidateTile
									candidate={c}
									kind="ranked"
									selectable={!results.reviewed}
									onSelect={openPick}
								/>
							{/each}
						</div>
					</section>
				{/each}
			</div>
		{/if}
	{:else if currentPosters.length === 0}
		<div class="empty-tab">No posters in this group.</div>
	{:else}
		<div class="poster-grid">
			{#each currentPosters as c (c.orig_filename)}
				<PosterCandidateTile
					candidate={c}
					kind={activeStage === 'ranked' ? 'ranked' : 'rejected'}
					selectable={!results.reviewed}
					onSelect={openPick}
				/>
			{/each}
		</div>
	{/if}
{/if}

<!-- ── Pick confirm ── -->
<ConfirmDialog
	open={pickOpen}
	title={pickIsAuto ? 'Approve auto-pick' : 'Choose this poster'}
	confirmLabel={pickIsAuto ? 'Approve' : 'Set as chosen'}
	{busy}
	onConfirm={confirmPick}
	onCancel={() => (pickOpen = false)}
>
	{#if pickTarget}
		<div class="pick-row">
			<div class="pick-poster" style="--c0:{pickGrad[0]}; --c1:{pickGrad[1]}">
				<img src={pickTarget.poster_url} alt="Selected poster" />
			</div>
			<div class="pick-info">
				{#if pickTarget.rank != null}
					<div class="pick-meta">
						Rank {pickTarget.rank}{#if pickTarget.final_score != null}
							· <span class="mono">{pickTarget.final_score.toFixed(3)}</span>{/if}
					</div>
				{:else if pickTarget.rejection_explanation}
					<div class="pick-meta low">{pickTarget.rejection_explanation}</div>
				{/if}
				<p class="pick-note">
					Writes a positive label, adds it to your taste profile{#if !pickIsAuto}, and marks the
						auto-pick as passed over{/if}.
				</p>
				<label class="toggle">
					<input type="checkbox" bind:checked={deploy} />
					Deploy to the movie folder now
				</label>
			</div>
		</div>
	{/if}
</ConfirmDialog>

<!-- ── Reject-all confirm ── -->
<ConfirmDialog
	open={rejectOpen}
	title="Reject all candidates"
	message="Records a negative label for the auto-pick and leaves this movie without a chosen poster. You can re-run later."
	confirmLabel="Reject all"
	tone="bad"
	{busy}
	onConfirm={confirmReject}
	onCancel={() => (rejectOpen = false)}
/>

<style>
	.crumb {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		color: var(--muted);
		margin-bottom: 18px;
	}
	.crumb a {
		color: var(--muted);
		text-decoration: none;
	}
	.crumb a:hover {
		color: var(--text);
	}
	.crumb span:last-child {
		color: var(--text);
	}

	.running-wrap {
		display: flex;
		flex-direction: column;
		gap: 14px;
		max-width: 560px;
	}
	.running-wrap h2 {
		margin: 0;
		font-size: 18px;
		font-weight: 650;
	}
	.run-hint {
		margin: 0;
		font-size: 12.5px;
		color: var(--faint);
	}

	/* ── Hero ── */
	.hero {
		display: grid;
		grid-template-columns: 168px 1fr;
		gap: 22px;
		align-items: start;
		margin-bottom: 24px;
	}
	@media (max-width: 620px) {
		.hero {
			grid-template-columns: 1fr;
		}
	}
	.hero-poster {
		aspect-ratio: 2 / 3;
		border-radius: var(--radius);
		overflow: hidden;
		border: 1px solid var(--line2);
		background: linear-gradient(165deg, var(--c0), var(--c1));
		box-shadow: 0 8px 26px var(--shadow);
		position: relative;
	}
	.hero-poster img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.no-pick {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		font-size: 12px;
		color: var(--faint);
		text-align: center;
		padding: 12px;
	}
	.hero-body {
		display: flex;
		flex-direction: column;
		gap: 12px;
		min-width: 0;
	}
	.hero-title {
		font-size: 20px;
		font-weight: 700;
		letter-spacing: -0.01em;
	}
	.hero-sub {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 4px;
		font-size: 12px;
		color: var(--muted);
	}
	.scorer-tag {
		font-family: var(--font-mono);
		padding: 1px 7px;
		border-radius: 6px;
		background: var(--panel2);
		border: 1px solid var(--line);
	}
	.reviewed {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		color: var(--good);
	}
	.undo {
		background: none;
		border: none;
		color: var(--info);
		font-size: 12px;
		text-decoration: underline;
		padding: 0;
	}
	.auto-line {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.auto-badge {
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.07em;
		padding: 2px 7px;
		border-radius: 6px;
		background: var(--gold);
		color: var(--on-gold);
	}
	.score {
		font-size: 14px;
		color: var(--gold);
	}
	.explain {
		margin: 0;
		padding-left: 16px;
		display: flex;
		flex-direction: column;
		gap: 3px;
		font-size: 12.5px;
		color: var(--muted);
		line-height: 1.4;
	}
	.contrib {
		max-width: 320px;
	}
	.hero-actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		margin-top: 2px;
	}

	/* ── Tabs + grid ── */
	.tabwrap {
		padding-bottom: 12px;
		border-bottom: 1px solid var(--line);
		margin-bottom: 12px;
	}
	.grid-hint {
		margin: 0 0 14px;
		font-size: 12px;
		color: var(--faint);
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.hint-left {
		flex: 1;
		min-width: 0;
	}
	.hint-toggle {
		display: flex;
		gap: 2px;
		flex-shrink: 0;
	}
	.view-btn {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		padding: 3px 8px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--panel);
		color: var(--muted);
		font-size: 11px;
		font-weight: 550;
		cursor: pointer;
		transition: color 0.12s ease, border-color 0.12s ease, background 0.12s ease;
	}
	.view-btn:hover {
		color: var(--text);
		border-color: var(--line2);
	}
	.view-btn.active {
		color: var(--text);
		border-color: var(--gold-deep);
		background: var(--panel2);
	}
	.poster-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
		gap: 14px;
	}
	.stack-collapse-inline {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		padding: 4px 9px;
		border-radius: 6px;
		border: 1px solid var(--line);
		background: var(--panel2);
		color: var(--muted);
		font-size: 11px;
		font-weight: 550;
		cursor: pointer;
		white-space: nowrap;
		align-self: center;
		transition: color 0.12s ease, border-color 0.12s ease;
	}
	.stack-collapse-inline:hover {
		color: var(--text);
		border-color: var(--gold);
	}
	.expanded-tile {
		border-left: 3px solid var(--group-accent);
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
		background: color-mix(in srgb, var(--group-accent) 5%, transparent);
		padding: 0 0 0 3px;
	}
	.stacks {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.stack {
		padding: 12px 12px 14px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
	}
	.stack-head {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin-bottom: 10px;
	}
	.stack-name {
		font-size: 13px;
		font-weight: 650;
		color: var(--text);
	}
	.stack-score {
		font-size: 12px;
		color: var(--gold);
	}
	.stack-size {
		font-size: 11.5px;
		color: var(--faint);
		margin-left: auto;
	}
	.empty-tab {
		padding: 40px;
		text-align: center;
		color: var(--faint);
		font-size: 13px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
	}

	/* ── Pick dialog ── */
	.pick-row {
		display: flex;
		gap: 14px;
	}
	.pick-poster {
		width: 92px;
		flex: none;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		overflow: hidden;
		border: 1px solid var(--line2);
		background: linear-gradient(165deg, var(--c0), var(--c1));
		position: relative;
	}
	.pick-poster img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.pick-info {
		display: flex;
		flex-direction: column;
		gap: 8px;
		min-width: 0;
	}
	.pick-meta {
		font-size: 13px;
		font-weight: 600;
		color: var(--text);
	}
	.pick-meta.low {
		color: var(--low);
		font-weight: 500;
	}
	.pick-note {
		margin: 0;
		font-size: 12px;
		color: var(--muted);
		line-height: 1.45;
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12.5px;
		color: var(--text);
	}

	/* ── Buttons / error ── */
	.btn-gold {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
	}
	.btn-ghost {
		padding: 9px 16px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: transparent;
		color: var(--muted);
		font-size: 13px;
	}
	.btn-ghost:hover:not(:disabled) {
		color: var(--bad);
	}
	.btn-gold:disabled,
	.btn-ghost:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.mono {
		font-family: var(--font-mono);
	}
	.errstate {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 10px;
		padding: 80px 24px;
		color: var(--faint);
		text-align: center;
	}
	.errstate strong {
		color: var(--text);
		font-size: 15px;
	}
	.errstate button {
		margin-top: 8px;
		padding: 8px 18px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--text);
		font-size: 13px;
	}
</style>
