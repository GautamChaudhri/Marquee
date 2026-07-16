<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import { toast } from '$lib/toast';
	import {
		analyzeAll,
		healDrift,
		applyBatch,
		batchReencode,
		confirmLetterbox,
		listColumn
	} from '$lib/api/letterbox';
	import type { BatchReencodeSettings, LetterboxColumnItem } from '$lib/api/types';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import LetterboxCard from '$lib/components/LetterboxCard.svelte';
	import LetterboxDetail from '$lib/components/LetterboxDetail.svelte';
	import BatchReencodeModal from '$lib/components/letterbox/BatchReencodeModal.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Variant = 'candidates' | 'detected' | 'preview' | 'notlb' | 'processed';
	type TabKey = 'cleared' | 'candidates' | 'staging' | 'preview' | 'processed';
	type ColKey = 'candidates' | 'detected' | 'preview' | 'notLetterboxed' | 'processed';

	// ── Reactive tray state (initialized from load, updated via SSE + poll) ─────
	// svelte-ignore state_referenced_locally
	// eslint-disable-next-line svelte/prefer-writable-derived -- cols is mutated locally (optimistic moves + poll refresh); $state avoids prop-ownership warnings on nested writes
	let cols = $state(data.columns);

	// Sync columns when data changes (e.g., from URL navigation / sort toggle)
	$effect(() => {
		cols = data.columns;
	});

	// ── Tabs + selection (local state — the 5 columns are already loaded, so
	//    switching tabs / selecting a film is instant and never re-runs load) ────
	const TABS: { key: TabKey; label: string; col: ColKey; variant: Variant; color: string }[] = [
		{
			key: 'cleared',
			label: 'Cleared',
			col: 'notLetterboxed',
			variant: 'notlb',
			color: 'var(--bad)'
		},
		{
			key: 'candidates',
			label: 'Candidates',
			col: 'candidates',
			variant: 'candidates',
			color: 'var(--warn)'
		},
		{
			key: 'staging',
			label: 'Staging',
			col: 'detected',
			variant: 'detected',
			color: 'var(--info)'
		},
		{ key: 'preview', label: 'Preview', col: 'preview', variant: 'preview', color: 'var(--dovi)' },
		{
			key: 'processed',
			label: 'Processed',
			col: 'processed',
			variant: 'processed',
			color: 'var(--good)'
		}
	];

	let activeTab = $state<TabKey>((page.url.searchParams.get('tab') as TabKey) || 'staging');
	const activeTabCfg = $derived(TABS.find((t) => t.key === activeTab) ?? TABS[2]);
	const activeItems = $derived(cols[activeTabCfg.col].items);
	const activeTotal = $derived(cols[activeTabCfg.col].total);

	let selectedId = $state<number | null>(null);
	const defaultSel = $derived(activeItems[0]?.movie_id ?? null);
	const selected = $derived(selectedId ?? defaultSel);

	function select(id: number) {
		selectedId = id;
	}

	function setTab(key: TabKey) {
		if (key === activeTab) return;
		activeTab = key;
		const cfg = TABS.find((t) => t.key === key);
		selectedId = cfg ? (cols[cfg.col].items[0]?.movie_id ?? null) : null;

		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('tab', key);
		goto(`/letterbox/movies?${sp.toString()}`, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	const activeNote = $derived.by(() => {
		switch (activeTab) {
			case 'candidates':
				return 'resolution probe';
			case 'staging':
				return detectedTotal > 0 ? `${detectedTotal} ready` : 'awaiting candidates';
			case 'preview':
				return 'review & confirm crop tags';
			case 'cleared':
				return `verified clear · Open Matte ${st?.full_frame ?? 0}`;
			case 'processed':
				return 'crop tag applied · reversible';
		}
	});

	function toggleDsort() {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('dsort', data.detectedDesc ? 'asc' : 'desc');
		goto(`/letterbox/movies?${sp.toString()}`, { keepFocus: true, noScroll: true });
	}

	// ── Board actions ──────────────────────────────────────────────────────────
	let healing = $state(false);
	let processing = $state(false);
	let confirming = $state(false);
	let batchReencodeOpen = $state(false);
	let batchReencodeBusy = $state(false);

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

		// Optimistic update: move all detected movies to preview tray
		const movedItems = [...cols.detected.items];
		cols.detected.items = [];
		cols.detected.total = 0;
		for (const item of movedItems) {
			item.status = 'tagged';
			item.reviewed = false;
		}
		cols.preview.items = [...movedItems, ...cols.preview.items];
		cols.preview.total += movedItems.length;

		try {
			await applyBatch(fetch, ids);
			toast(`Applied crop tags to ${ids.length} ${ids.length === 1 ? 'movie' : 'movies'}`, 'good');
			await refreshTrays();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Process failed', 'bad');
			await refreshTrays();
		} finally {
			processing = false;
		}
	}

	async function confirmAll() {
		const ids = cols.preview.items.map((i) => i.movie_id);
		if (ids.length === 0 || confirming) return;
		confirming = true;

		// Optimistic update: move all preview movies to processed tray
		const movedItems = [...cols.preview.items];
		cols.preview.items = [];
		cols.preview.total = 0;
		for (const item of movedItems) {
			item.reviewed = true;
		}
		cols.processed.items = [...movedItems, ...cols.processed.items];
		cols.processed.total += movedItems.length;

		try {
			await Promise.all(ids.map((id) => confirmLetterbox(fetch, id)));
			toast(`Confirmed ${ids.length} ${ids.length === 1 ? 'movie' : 'movies'}`, 'good');
			await refreshTrays();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Confirm failed', 'bad');
			await refreshTrays();
		} finally {
			confirming = false;
		}
	}

	async function startBatchReencode(payload: { ids: number[]; settings: BatchReencodeSettings }) {
		if (batchReencodeBusy) return;
		batchReencodeBusy = true;
		try {
			const result = await batchReencode(fetch, payload.ids, payload.settings);
			bindJobs(result.job_ids);
			if (result.count > 0) {
				toast(`Queued ${result.count} permanent re-encodes`, 'good');
			} else {
				toast('No eligible films matched the selected confidence filter', 'info');
			}
			if (result.skipped.length > 0) {
				toast(
					`Skipped ${result.skipped.length} ineligible film${result.skipped.length === 1 ? '' : 's'}`,
					'info'
				);
			}
			batchReencodeOpen = false;
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not queue permanent re-encodes', 'bad');
		} finally {
			batchReencodeBusy = false;
		}
	}

	// ── Analyze ────────────────────────────────────────────────────────────────
	let analyzing = $state(false);
	let initiatedJobIds = $state<string[]>([]);

	function bindJobs(jobIds: string[]) {
		const additions = jobIds.filter((jobId) => !initiatedJobIds.includes(jobId));
		if (additions.length > 0) initiatedJobIds = [...initiatedJobIds, ...additions];
	}

	/** Fetch updated tray data from the backend and merge into reactive state. */
	async function refreshTrays() {
		try {
			const [candidates, detected, preview, notLb, processed] = await Promise.all([
				listColumn(fetch, {
					status: 'prefilter_candidate,prefilter_unknown',
					sort: 'confidence',
					page_size: 25
				}),
				listColumn(fetch, {
					status: 'candidate',
					sort: 'confidence',
					desc: data.detectedDesc,
					page_size: 25
				}),
				listColumn(fetch, {
					status: 'tagged',
					reviewed: false,
					sort: 'recent',
					page_size: 25
				}),
				listColumn(fetch, {
					status: 'not_letterboxed',
					sort: 'recent',
					page_size: 25
				}),
				listColumn(fetch, {
					status: 'tagged',
					reviewed: true,
					sort: 'recent',
					page_size: 25
				})
			]);

			cols = {
				candidates,
				detected,
				preview: {
					items: preview.items.filter((i) => !i.reviewed),
					total: preview.items.filter((i) => !i.reviewed).length
				},
				notLetterboxed: notLb,
				processed: {
					items: processed.items.filter((i) => i.reviewed),
					total: processed.items.filter((i) => i.reviewed).length
				}
			};
		} catch {
			// The activity card retains the job outcome; a later navigation reloads the trays.
		}
	}

	async function doAnalyze() {
		if (analyzing) return;
		analyzing = true;
		try {
			const ref = await analyzeAll(fetch);
			bindJobs([ref.job_id]);
			toast('Queued candidate analysis', 'good');
		} catch (e) {
			analyzing = false;
			toast(e instanceof Error ? e.message : 'Analysis failed to start', 'bad');
		}
	}

	function handleJobSettled() {
		analyzing = false;
		void refreshTrays();
	}

	onMount(() => {
		// Refresh trays when user returns to tab (ensures data consistency)
		const onVisibilityChange = () => {
			if (document.visibilityState === 'visible' && !analyzing) {
				refreshTrays();
			}
		};
		document.addEventListener('visibilitychange', onVisibilityChange);

		return () => {
			document.removeEventListener('visibilitychange', onVisibilityChange);
		};
	});

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
	// ── "View all" modal ───────────────────────────────────────────────────────
	interface ModalCfg {
		variant: Variant;
		status: string;
		reviewed?: boolean;
		sort: 'title' | 'confidence' | 'crop' | 'recent';
		label: string;
	}
	let modal = $state<ModalCfg | null>(null);
	let modalItems = $state<LetterboxColumnItem[]>([]);
	let modalLoading = $state(false);

	async function openModal(cfg: ModalCfg) {
		modal = cfg;
		modalItems = [];
		modalLoading = true;
		try {
			const r = await listColumn(fetch, {
				status: cfg.status,
				reviewed: cfg.reviewed,
				sort: cfg.sort,
				page_size: 200
			});
			modalItems = r.items;
		} catch {
			// silently degrade
		} finally {
			modalLoading = false;
		}
	}

	function closeModal() {
		modal = null;
		modalItems = [];
	}

	const MODAL_CFGS: Record<Variant, ModalCfg> = {
		candidates: {
			variant: 'candidates',
			status: 'prefilter_candidate,prefilter_unknown',
			sort: 'confidence',
			label: 'Candidates'
		},
		detected: { variant: 'detected', status: 'candidate', sort: 'confidence', label: 'Staging' },
		preview: {
			variant: 'preview',
			status: 'tagged',
			reviewed: false,
			sort: 'recent',
			label: 'Preview & Confirm'
		},
		notlb: {
			variant: 'notlb',
			status: 'not_letterboxed',
			sort: 'recent',
			label: 'Cleared Candidates'
		},
		processed: {
			variant: 'processed',
			status: 'tagged',
			reviewed: true,
			sort: 'recent',
			label: 'Processed'
		}
	};
</script>

{#snippet list(items: typeof cols.candidates.items, total: number, variant: Variant)}
	{#if items.length === 0}
		<div class="list-empty">No films in this stage.</div>
	{:else}
		{#each items as item (item.movie_id)}
			<LetterboxCard {item} {variant} selected={item.movie_id === selected} onSelect={select} />
		{/each}
		{#if total > items.length}
			<button class="list-more" onclick={() => openModal(MODAL_CFGS[variant])}
				>view all {total}</button
			>
		{/if}
	{/if}
{/snippet}

<div class="page">
	<SectionHeader title="Letterbox" subtitle="Black-bar detection & crop tagging">
		{#snippet action()}
			<button class="btn-sec" onclick={doHeal} disabled={healing}>
				{healing ? 'Healing…' : 'Heal drifted tags'}
			</button>
		{/snippet}
	</SectionHeader>
	<FeatureActivityPanel
		scopeKey="feature:letterbox:movies"
		query={{ feature_area: 'letterbox', subject_kind: 'movie' }}
		jobIds={initiatedJobIds}
		heading="Movie letterbox activity"
		onSettled={handleJobSettled}
	/>

	{#if st}
		<div class="statusbar">
			<span class="chip">
				<StatusDot tone={st.enabled ? 'good' : 'bad'} size={7} />
				{st.enabled ? 'Detection enabled' : 'Disabled'}
			</span>
			<span class="chip mono">method: {st.method}</span>
			<span class="chip mono">Open Matte: {st.full_frame ?? 0}</span>
			{#each Object.entries(st.binaries) as [name, ok] (name)}
				<span class="chip mono" class:bad={!ok}>{name} {ok ? '✓' : '✗'}</span>
			{/each}
			<span class="chip mono faint">last scan {relTime(st.last_scan)}</span>
		</div>
	{:else if data.error}
		<div class="banner err">{data.error} The backend may be offline.</div>
	{/if}

	<!-- Two fixed panes: tabbed film list | persistent inspector -->
	<div class="split">
		<section class="pane left" style="--accent:{activeTabCfg.color}">
			<div class="tabs" role="tablist" aria-label="Letterbox stages">
				{#each TABS as t (t.key)}
					<button
						class="tab"
						class:active={t.key === activeTab}
						style="--tab-c:{t.color}"
						role="tab"
						aria-selected={t.key === activeTab}
						onclick={() => setTab(t.key)}
					>
						<span class="tab-label">{t.label}</span>
						<span class="tab-count">{cols[t.col].total}</span>
					</button>
				{/each}
			</div>

			<div class="lane-bar">
				<span class="lane-note">{activeNote}</span>
				<div class="lane-actions">
					{#if activeTab === 'candidates'}
						<button class="tb gold" onclick={doAnalyze} disabled={analyzing}>
							{analyzing ? 'Analyzing…' : 'Analyze →'}
						</button>
					{:else if activeTab === 'staging'}
						<button
							class="sort-toggle"
							onclick={toggleDsort}
							title={data.detectedDesc ? 'Confidence: high → low' : 'Confidence: low → high'}
						>
							conf {data.detectedDesc ? '▼' : '▲'}
						</button>
						<span class="tb quick-active">⚡ All Quick</span>
						<button
							class="tb perm-active"
							onclick={() => (batchReencodeOpen = true)}
							disabled={cols.detected.total === 0}
						>
							🔧 Batch Re-encode
						</button>
						<button
							class="tb gold"
							onclick={processDetected}
							disabled={processing || detectedTotal === 0}
						>
							{processing ? 'Processing…' : 'Process →'}
						</button>
					{:else if activeTab === 'preview'}
						<button
							class="tb gold"
							onclick={confirmAll}
							disabled={confirming || cols.preview.total === 0}
						>
							<Icon name="refresh" size={13} />
							{confirming ? 'Confirming…' : 'Confirm all'}
						</button>
					{/if}
				</div>
			</div>

			<div class="list">{@render list(activeItems, activeTotal, activeTabCfg.variant)}</div>
		</section>

		<section class="pane right">
			<LetterboxDetail
				movieId={selected}
				onChanged={refreshTrays}
				onAnalyzeAll={doAnalyze}
				{analyzing}
			/>
		</section>
	</div>
</div>

<!-- "View all" modal -->
{#if modal}
	<div
		class="modal-backdrop"
		role="dialog"
		aria-modal="true"
		aria-label={modal.label}
		tabindex="-1"
		onclick={(e) => {
			if (e.target === e.currentTarget) closeModal();
		}}
		onkeydown={(e) => e.key === 'Escape' && closeModal()}
	>
		<div class="modal-card">
			<div class="modal-head">
				<span class="modal-title">{modal.label}</span>
				<button class="modal-close" onclick={closeModal} aria-label="Close">
					<Icon name="x" size={16} />
				</button>
			</div>
			<div class="modal-body">
				{#if modalLoading}
					<div class="modal-loading">Loading…</div>
				{:else if modalItems.length === 0}
					<div class="modal-loading">No items found.</div>
				{:else}
					<div class="modal-grid">
						{#each modalItems as item (item.movie_id)}
							<LetterboxCard
								{item}
								variant={modal.variant}
								selected={item.movie_id === selected}
								onSelect={(id) => {
									select(id);
									closeModal();
								}}
							/>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}

<BatchReencodeModal
	open={batchReencodeOpen}
	items={cols.detected.items}
	getId={(item: LetterboxColumnItem) => item.movie_id}
	busy={batchReencodeBusy}
	onStart={startBatchReencode}
	onClose={() => {
		if (!batchReencodeBusy) batchReencodeOpen = false;
	}}
/>

<style>
	.page {
		display: flex;
		flex-direction: column;
		height: calc(100vh - var(--header-h) - 48px);
		min-height: 480px;
	}

	.statusbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-bottom: 12px;
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
	.banner.err {
		padding: 11px 14px;
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--bad) 10%, transparent);
		border: 1px solid color-mix(in srgb, var(--bad) 30%, transparent);
		color: var(--bad);
		font-size: 13px;
		margin-bottom: 12px;
	}

	/* two-pane split */
	.split {
		display: flex;
		gap: 14px;
		flex: 1;
		min-height: 0;
	}
	.pane {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: 10px;
		display: flex;
		flex-direction: column;
		min-height: 0;
		min-width: 0;
		overflow: hidden;
	}
	.pane.left {
		flex: 0 0 40%;
		border-top: 2px solid var(--accent);
	}
	.pane.right {
		flex: 1 1 60%;
		overflow-y: auto;
	}

	/* tabs */
	.tabs {
		display: flex;
		flex-wrap: wrap;
		align-items: stretch;
		gap: 2px;
		padding: 0 6px;
		border-bottom: 1px solid var(--line);
		flex: none;
	}
	.tab {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		padding: 10px 10px;
		margin-bottom: -1px;
		background: transparent;
		border: none;
		border-bottom: 2px solid transparent;
		color: var(--muted);
		font-size: 12.5px;
		font-weight: 600;
		cursor: pointer;
		white-space: nowrap;
	}
	.tab:hover {
		color: var(--text);
	}
	.tab.active {
		color: var(--text);
		border-bottom-color: var(--tab-c);
	}
	.tab-count {
		font-family: var(--font-mono);
		font-size: 10px;
		padding: 1px 6px;
		border-radius: 6px;
		background: color-mix(in srgb, var(--tab-c) 16%, transparent);
		color: var(--tab-c);
	}

	/* contextual action bar for the active tab */
	.lane-bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 9px 12px;
		border-bottom: 1px solid var(--panel2);
		flex: none;
	}
	.lane-note {
		font-size: 11px;
		color: var(--faint);
	}
	.lane-actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
	}

	/* vertical film list */
	.list {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		display: flex;
		flex-direction: column;
	}
	.list-empty {
		padding: 28px 14px;
		color: var(--faint2);
		font-size: 13px;
		text-align: center;
	}
	.list-more {
		flex: none;
		width: 100%;
		text-align: center;
		font-size: 11.5px;
		color: var(--muted);
		padding: 10px 0;
		border: none;
		border-top: 1px solid var(--panel2);
		background: transparent;
		cursor: pointer;
	}
	.list-more:hover {
		color: var(--text);
		background: var(--ink2);
	}

	/* action buttons */
	.tb {
		flex: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 5px;
		border-radius: 7px;
		padding: 6px 11px;
		font-weight: 600;
		font-size: 11.5px;
		white-space: nowrap;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
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
	.tb.perm-active {
		background: color-mix(in srgb, var(--gold) 8%, transparent);
		color: var(--gold);
		border-color: color-mix(in srgb, var(--gold) 28%, transparent);
	}
	.tb:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.sort-toggle {
		font-family: var(--font-mono);
		font-size: 10.5px;
		color: var(--faint);
		background: transparent;
		border: 1px solid var(--panel2);
		border-radius: 6px;
		padding: 4px 7px;
		cursor: pointer;
	}
	.sort-toggle:hover {
		color: var(--text);
		border-color: var(--faint);
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

	/* modal */
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: color-mix(in srgb, var(--bg) 60%, transparent);
		backdrop-filter: blur(4px);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 100;
	}
	.modal-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		width: 100%;
		max-width: 560px;
		max-height: 70vh;
		display: flex;
		flex-direction: column;
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5);
	}
	.modal-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.modal-title {
		font-size: 14px;
		font-weight: 700;
		color: var(--text);
	}
	.modal-close {
		display: grid;
		place-items: center;
		width: 28px;
		height: 28px;
		border: none;
		background: transparent;
		color: var(--muted);
		border-radius: 6px;
		cursor: pointer;
	}
	.modal-close:hover {
		background: var(--panel2);
		color: var(--text);
	}
	.modal-body {
		flex: 1;
		overflow-y: auto;
		padding: 12px;
	}
	.modal-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: 8px;
	}
	.modal-loading {
		text-align: center;
		color: var(--faint);
		font-size: 13px;
		padding: 32px 0;
	}

	/* stack the panes on narrow screens */
	@media (max-width: 860px) {
		.page {
			height: auto;
			min-height: 0;
		}
		.split {
			flex-direction: column;
			min-height: 0;
		}
		.pane {
			overflow: visible;
		}
		.pane.left {
			flex: none;
		}
		.pane.right {
			flex: none;
			overflow-y: visible;
		}
		.list {
			max-height: 420px;
		}
	}
</style>
