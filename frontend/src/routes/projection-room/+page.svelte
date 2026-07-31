<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import {
		bulkJobActions,
		cancelJob,
		getActivityAttention,
		pauseJob,
		resumeJob,
		retryJob,
		setJobPriority
	} from '$lib/activity/client';
	import ActivityAttentionStrip from '$lib/activity/components/ActivityAttentionStrip.svelte';
	import ActivityDisplayControls from '$lib/activity/components/ActivityDisplayControls.svelte';
	import ActivityFilters from '$lib/activity/components/ActivityFilters.svelte';
	import ActivityRow from '$lib/activity/components/ActivityRow.svelte';
	import { getJobProgressStore } from '$lib/activity/context';
	import {
		DEFAULT_ACTIVITY_PREFERENCES,
		loadActivityPreferences,
		normalizeActivityPreferences,
		saveActivityPreferences,
		type ActivityPreferences
	} from '$lib/activity/preferences';
	import type { ActivityAttentionResponse, CommandResponse, JobRow } from '$lib/activity/types';
	import {
		activityListQuery,
		activityParams,
		activityScopeKey,
		parseActivityUrl,
		type ActivityUrlState,
		type ActivityView
	} from '$lib/activity/url-state';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const store = getJobProgressStore();
	let urlState = $derived(parseActivityUrl(page.url.searchParams));
	// Only the bare Activity entry gets a smart default. A bookmarked Queue URL or a
	// filtered queue stays put, even when it happens to have no matching rows.
	let autoHistoryEligible = $state(
		page.url.pathname === '/projection-room' && page.url.searchParams.size === 0
	);
	let scopeKey = $state('');
	let attention = $derived<ActivityAttentionResponse | null>(data.attention);
	let preferences = $state<ActivityPreferences>({ ...DEFAULT_ACTIVITY_PREFERENCES });
	let attentionController: AbortController | null = null;
	let attentionTimer: ReturnType<typeof setInterval> | null = null;
	let popstateHandler: (() => void) | null = null;
	let selectedIds = $state<string[]>([]);
	let selectMode = $state(false);
	let bulkPriority = $state(50);
	let bulkBusy = $state(false);

	const scope = $derived(scopeKey ? (store.scopeViews.get(scopeKey) ?? null) : null);
	const records = $derived(
		urlState.view === 'operations'
			? []
			: store.recordsForScope(scopeKey).filter((record) => record.partition === urlState.view)
	);
	const selectedRecords = $derived(records.filter((record) => selectedIds.includes(record.jobId)));
	const selectedClasses = $derived([
		...new Set(selectedRecords.map((record) => record.row?.execution_class).filter(Boolean))
	]);

	$effect(() => {
		if (urlState.view === 'operations') {
			scopeKey = '';
			return;
		}
		const key = activityScopeKey(urlState);
		scopeKey = key;
		const handle = untrack(() => store.acquireScope(key, activityListQuery(urlState)));
		return () => handle.release();
	});

	$effect(() => {
		// Queue remains the normal explicit view. On a plain visit, wait until its
		// authoritative server result arrives; then surface retained outcomes when no
		// active work exists instead of presenting an empty landing state.
		if (
			!autoHistoryEligible ||
			urlState.view !== 'queue' ||
			!scope ||
			scope.loading ||
			scope.error ||
			records.length > 0
		) {
			return;
		}
		navigate({ ...urlState, view: 'history', phase: '', outcome: '', sort: 'default' }, true);
	});

	function navigate(next: ActivityUrlState, replace = false): void {
		// A view button is an explicit choice, even though SvelteKit's page URL does
		// not synchronously update when this page owns the History API mutation.
		autoHistoryEligible = false;
		const url = `/projection-room?${activityParams(next).toString()}`;
		if (replace) window.history.replaceState(page.state, '', url);
		else window.history.pushState(page.state, '', url);
		urlState = next;
	}

	function setView(view: ActivityView): void {
		navigate({ ...urlState, view, phase: '', outcome: '', sort: 'default' });
	}

	function clearFilters(): void {
		navigate(parseActivityUrl(new URLSearchParams(`view=${urlState.view}`)), true);
	}

	function changePreferences(next: ActivityPreferences): void {
		preferences = normalizeActivityPreferences(next);
		saveActivityPreferences(localStorage, preferences);
	}

	async function refreshAttention(): Promise<void> {
		if (document.hidden) return;
		attentionController?.abort();
		attentionController = new AbortController();
		try {
			attention = await getActivityAttention((input, init) =>
				fetch(input, { ...init, signal: attentionController?.signal })
			);
		} catch {
			// Preserve the last-good attention summary.
		}
	}

	type LifecycleAction = 'cancel' | 'pause' | 'resume' | 'change_priority' | 'retry';
	const BULK_ACTIONS: LifecycleAction[] = ['cancel', 'pause', 'resume', 'retry'];

	async function command(
		row: JobRow,
		action: LifecycleAction,
		priority?: number
	): Promise<CommandResponse> {
		try {
			const response =
				action === 'cancel'
					? await cancelJob(fetch, row.job_id, row.fence_token)
					: action === 'pause'
						? await pauseJob(fetch, row.job_id, row.fence_token)
						: action === 'resume'
							? await resumeJob(fetch, row.job_id, row.fence_token)
							: action === 'retry'
								? await retryJob(fetch, row.job_id, row.fence_token)
								: await setJobPriority(
										fetch,
										row.job_id,
										priority ?? row.priority,
										row.fence_token
									);
			store.track(row.job_id);
			if (response.replacement_job_id) store.track(response.replacement_job_id);
			store.refreshScope(scopeKey);
			void refreshAttention();
			toast(
				response.replacement_job_id
					? 'Retry successor queued. The original terminal record remains unchanged.'
					: `${response.action} accepted within ${response.execution_class}.`,
				'good'
			);
			return response;
		} catch (reason) {
			toast('The job changed before the command could be accepted. Refreshing Activity.', 'bad');
			store.refreshScope(scopeKey);
			throw reason;
		}
	}

	function select(jobId: string, selected: boolean): void {
		selectedIds = selected
			? [...new Set([...selectedIds, jobId])].slice(0, 100)
			: selectedIds.filter((id) => id !== jobId);
	}

	// Leaving selection mode discards the selection: a hidden checkbox that is still
	// checked would arm a bulk command nobody can see.
	function endSelectMode(): void {
		selectMode = false;
		selectedIds = [];
	}

	async function runBulk(action: LifecycleAction): Promise<void> {
		if (bulkBusy) return;
		const eligible = selectedRecords.filter(
			(record) => record.row?.allowed_actions.includes(action) && record.row != null
		);
		if (eligible.length === 0) {
			toast('None of the selected jobs currently offer that server capability.', 'bad');
			return;
		}
		bulkBusy = true;
		try {
			const response = await bulkJobActions(fetch, {
				items: eligible.map((record, index) => ({
					request_id: `activity-${index}-${record.jobId}`.slice(0, 80),
					job_id: record.jobId,
					action,
					expected_fence_token: record.row!.fence_token,
					priority: action === 'change_priority' ? bulkPriority : null
				}))
			});
			const succeeded = response.items.filter((item) => item.success);
			const failed = response.items.filter((item) => !item.success);
			for (const item of succeeded) {
				store.track(item.job_id);
				if (item.response?.replacement_job_id) store.track(item.response.replacement_job_id);
			}
			toast(
				`${succeeded.length} command${succeeded.length === 1 ? '' : 's'} accepted; ${failed.length} conflicted.`,
				failed.length ? 'bad' : 'good'
			);
			selectedIds = failed.map((item) => item.job_id);
			store.refreshScope(scopeKey);
			void refreshAttention();
		} catch {
			toast('Bulk commands could not be submitted. The selection is preserved.', 'bad');
		} finally {
			bulkBusy = false;
		}
	}

	onMount(() => {
		preferences = loadActivityPreferences(localStorage);
		popstateHandler = () => {
			const params = new URL(window.location.href).searchParams;
			autoHistoryEligible = params.size === 0;
			urlState = parseActivityUrl(params);
		};
		window.addEventListener('popstate', popstateHandler);
		void refreshAttention();
		attentionTimer = setInterval(() => void refreshAttention(), 30_000);
	});

	onDestroy(() => {
		if (popstateHandler) window.removeEventListener('popstate', popstateHandler);
		attentionController?.abort();
		if (attentionTimer) clearInterval(attentionTimer);
	});
</script>

<svelte:head><title>Activity · Marquee</title></svelte:head>

<ActivityAttentionStrip summary={attention} />

<nav class="views" aria-label="Activity views">
	{#each [{ id: 'queue', label: 'Queue', detail: 'Running and expected work' }, { id: 'history', label: 'History', detail: 'Completed outcomes' }, { id: 'operations', label: 'Operations', detail: 'Infrastructure diagnostics' }] as item (item.id)}
		<button
			type="button"
			data-view={item.id}
			class:active={urlState.view === item.id}
			aria-current={urlState.view === item.id ? 'page' : undefined}
			onclick={() => setView(item.id as ActivityView)}
		>
			<strong>{item.label}</strong><span>{item.detail}</span>
		</button>
	{/each}
</nav>

{#if urlState.view === 'operations'}
	{#await import('$lib/activity/components/OperationsView.svelte') then module}
		<module.default />
	{:catch}
		<p class="state error">Operations could not be loaded. Queue and History remain available.</p>
	{/await}
{:else}
	<section class="activity-view" aria-labelledby="activity-view-title">
		<div class="toolbar">
			<div>
				<h2 id="activity-view-title">{urlState.view === 'queue' ? 'Queue' : 'History'}</h2>
				<p>
					{urlState.view === 'queue'
						? 'Attention and running work come first; server ordering is authoritative.'
						: 'Newest terminal outcomes appear first unless a different server sort is selected.'}
				</p>
			</div>
			<div class="toolbar-controls">
				<button
					type="button"
					class="select-toggle"
					class:active={selectMode}
					aria-pressed={selectMode}
					onclick={() => (selectMode ? endSelectMode() : (selectMode = true))}
				>
					{selectMode ? 'Done' : 'Select'}
				</button>
				<ActivityDisplayControls {preferences} onChange={changePreferences} />
			</div>
		</div>

		<ActivityFilters
			state={urlState}
			onApply={(next) => navigate(next, true)}
			onClear={clearFilters}
		/>

		{#if selectedRecords.length}
			<div class="bulk" aria-label="Bulk Activity commands">
				<strong>{selectedRecords.length} selected</strong>
				{#each BULK_ACTIONS as action (action)}
					{#if selectedRecords.some((record) => record.row?.allowed_actions.includes(action))}
						<button type="button" onclick={() => runBulk(action)} disabled={bulkBusy}
							>{action}</button
						>
					{/if}
				{/each}
				{#if selectedRecords.some( (record) => record.row?.allowed_actions.includes('change_priority') )}
					<label>
						<span
							>Priority · {selectedClasses.length === 1
								? selectedClasses[0]
								: 'mixed classes'}</span
						>
						<input bind:value={bulkPriority} type="number" min="0" max="100" step="5" />
					</label>
					<button
						type="button"
						onclick={() => runBulk('change_priority')}
						disabled={bulkBusy || selectedClasses.length !== 1}>Set priority</button
					>
				{/if}
				<button type="button" class="quiet" onclick={endSelectMode}>Clear selection</button>
			</div>
		{/if}

		{#if scope?.error}
			<p class="state warning" role="status">{scope.error}</p>
		{/if}
		{#if store.connection === 'reconnecting' || store.connection === 'stale'}
			<p class="state warning" role="status">
				Activity is reconnecting. Last-good results stay visible while the server is repaired.
			</p>
		{:else if store.connection === 'incompatible'}
			<p class="state error" role="alert">
				This Activity client is incompatible with the server contract. Refresh after updating
				Marquee.
			</p>
		{/if}

		<div class="list" class:compact={preferences.density === 'compact'} aria-busy={scope?.loading}>
			{#each records as record (record.jobId)}
				<ActivityRow
					{record}
					columns={preferences.columns}
					density={preferences.density}
					connection={store.connection}
					selectable={selectMode}
					selected={selectedIds.includes(record.jobId)}
					onSelected={(value) => select(record.jobId, value)}
					onCommand={command}
				/>
			{:else}
				{#if scope?.loading}
					<p class="state">Loading bounded {urlState.view} results…</p>
				{:else}
					<div class="empty">
						<strong
							>{urlState.view === 'queue' ? 'Nothing is waiting.' : 'No outcomes match.'}</strong
						>
						<span>
							{urlState.view === 'queue'
								? 'New work will appear here automatically.'
								: 'Clear filters or start work from a feature page.'}
						</span>
					</div>
				{/if}
			{/each}
		</div>

		<div class="pagination">
			<button type="button" onclick={() => store.refreshScope(scopeKey)} disabled={scope?.loading}>
				Refresh
			</button>
			{#if scope?.nextCursor}
				<button type="button" onclick={() => store.loadMore(scopeKey)} disabled={scope.loading}>
					Load more
				</button>
			{/if}
		</div>
	</section>
{/if}

<style>
	.views {
		display: grid;
		grid-template-columns: 1fr 1fr minmax(180px, 0.7fr);
		gap: 8px;
		margin: 14px 0 18px;
	}
	.views button {
		display: grid;
		gap: 2px;
		min-width: 0;
		padding: 11px 13px;
		text-align: left;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		color: var(--muted);
	}
	.views button:last-child {
		margin-left: 8px;
	}
	.views button[data-view='queue'].active {
		border-color: color-mix(in srgb, var(--info) 72%, var(--line));
		background: color-mix(in srgb, var(--info) 13%, var(--panel));
		color: var(--info);
	}
	.views button[data-view='history'].active {
		border-color: var(--gold-deep);
		background: var(--gold-soft);
		color: var(--gold);
	}
	.views button[data-view='operations'].active {
		border-color: color-mix(in srgb, var(--dovi) 72%, var(--line));
		background: color-mix(in srgb, var(--dovi) 13%, var(--panel));
		color: var(--dovi);
	}
	.views button.active span {
		color: var(--text);
	}
	.views strong {
		font-size: 13px;
	}
	.views span {
		color: var(--muted);
		font-size: 10.5px;
	}
	.activity-view {
		display: grid;
		gap: 14px;
	}
	.toolbar {
		display: flex;
		align-items: start;
		justify-content: space-between;
		gap: 12px;
	}
	.toolbar h2 {
		margin: 0;
		font-size: 17px;
	}
	.toolbar p {
		margin: 3px 0 0;
		color: var(--muted);
		font-size: 12px;
	}
	.toolbar-controls {
		display: flex;
		align-items: start;
		flex: none;
		gap: 8px;
	}
	.select-toggle {
		min-height: 34px;
		padding: 7px 12px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel);
		color: var(--muted);
		font-size: 12px;
	}
	.select-toggle:hover {
		border-color: var(--gold);
	}
	.select-toggle.active {
		border-color: var(--gold-deep);
		background: var(--gold-soft);
		color: var(--gold);
	}
	.select-toggle:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	.list {
		display: grid;
		gap: 10px;
		min-width: 0;
	}
	.list > :global(*) {
		content-visibility: auto;
		contain-intrinsic-size: auto 280px;
	}
	.list.compact {
		gap: 6px;
	}
	.state {
		margin: 0;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--muted);
		font-size: 12px;
	}
	.state.warning {
		border-color: color-mix(in srgb, var(--warn) 38%, var(--line));
	}
	.state.error {
		border-color: color-mix(in srgb, var(--bad) 38%, var(--line));
		color: var(--bad);
	}
	.empty {
		display: grid;
		gap: 4px;
		padding: 34px 16px;
		text-align: center;
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		color: var(--muted);
	}
	.empty strong {
		color: var(--text);
	}
	.pagination {
		display: flex;
		justify-content: center;
		gap: 8px;
	}
	.bulk {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 7px;
		position: sticky;
		top: 8px;
		z-index: 4;
		padding: 9px 10px;
		border: 1px solid var(--gold-deep);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		box-shadow: 0 8px 24px var(--shadow);
		font-size: 11px;
	}
	.bulk button,
	.bulk label {
		min-height: 32px;
		padding: 6px 9px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel);
		color: var(--text);
	}
	.bulk label {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: var(--muted);
	}
	.bulk input {
		width: 56px;
		border: 0;
		background: transparent;
		color: var(--text);
	}
	.bulk .quiet {
		margin-left: auto;
		color: var(--muted);
	}
	.pagination button {
		min-height: 36px;
		padding: 7px 12px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel);
		color: var(--text);
	}
	@media (max-width: 700px) {
		.views {
			grid-template-columns: 1fr 1fr;
		}
		.views button:last-child {
			grid-column: 1 / -1;
			margin-left: 0;
		}
	}
	@media (max-width: 480px) {
		.views {
			grid-template-columns: 1fr;
		}
		.views button:last-child {
			grid-column: auto;
		}
		.toolbar {
			align-items: stretch;
		}
	}
</style>
