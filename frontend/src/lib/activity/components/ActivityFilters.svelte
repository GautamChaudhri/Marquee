<script lang="ts">
	import { onMount } from 'svelte';
	import { getActivityCatalog } from '../client';
	import type { ActivityCatalogResponse } from '../types';
	import type { ActivityUrlState } from '../url-state';

	let {
		state: filterState,
		onApply,
		onClear
	}: {
		state: ActivityUrlState;
		onApply: (state: ActivityUrlState) => void;
		onClear: () => void;
	} = $props();
	let catalog = $state<ActivityCatalogResponse | null>(null);

	onMount(() => {
		let active = true;
		void getActivityCatalog(fetch)
			.then((value) => {
				if (active) catalog = value;
			})
			.catch(() => {
				// Filters remain usable with their current values if catalog discovery fails.
			});
		return () => {
			active = false;
		};
	});
	const SUBJECT_KINDS = [
		['movie', 'Movie'],
		['series', 'Series'],
		['season', 'Season'],
		['episode', 'Episode'],
		['media_file', 'Media file'],
		['track', 'Track'],
		['batch', 'Batch'],
		['model', 'Model'],
		['system', 'System']
	] as const;

	function submit(event: SubmitEvent): void {
		event.preventDefault();
		const data = new FormData(event.currentTarget as HTMLFormElement);
		const value = (key: string) => String(data.get(key) ?? '').trim();
		onApply({
			...filterState,
			q: value('q'),
			featureArea: value('feature_area'),
			jobType: value('type'),
			subjectKind: value('subject_kind'),
			phase: filterState.view === 'queue' ? value('phase') : '',
			outcome: filterState.view === 'history' ? value('outcome') : '',
			attention: value('attention'),
			trigger: value('trigger'),
			rootId: value('root_id'),
			correlationId: value('correlation_id'),
			workerId: value('worker_id'),
			executionClass: value('execution_class'),
			createdAfter: value('created_after'),
			createdBefore: value('created_before'),
			sort: value('sort') || 'default'
		});
	}
</script>

<form onsubmit={submit} aria-label={`${filterState.view} filters`}>
	<label class="search">
		<span>Search subjects</span>
		<input
			name="q"
			type="search"
			maxlength="100"
			value={filterState.q}
			placeholder="Movie, show, file…"
		/>
	</label>
	<label>
		<span>Feature</span>
		<select name="feature_area" value={filterState.featureArea}>
			<option value="">All features</option>
			{#each catalog?.features ?? [] as feature (feature.value)}
				<option value={feature.value}>{feature.label}</option>
			{/each}
		</select>
	</label>
	<label>
		<span>Job type</span>
		<select name="type" value={filterState.jobType}>
			<option value="">All types</option>
			{#each catalog?.job_types ?? [] as jobType (jobType.value)}
				<option value={jobType.value}>{jobType.label}</option>
			{/each}
		</select>
	</label>
	<label>
		<span>Subject</span>
		<select name="subject_kind" value={filterState.subjectKind}>
			<option value="">All subjects</option>
			{#each SUBJECT_KINDS as [kind, label] (kind)}
				<option value={kind}>{label}</option>
			{/each}
		</select>
	</label>
	{#if filterState.view === 'queue'}
		<label>
			<span>State</span>
			<select name="phase" value={filterState.phase}>
				<option value="">All Queue states</option>
				<option value="running">Running</option>
				<option value="stopping">Cancelling</option>
				<option value="queued">Queued / waiting</option>
				<option value="planned">Planned</option>
			</select>
		</label>
	{:else}
		<label>
			<span>Outcome</span>
			<select name="outcome" value={filterState.outcome}>
				<option value="">All outcomes</option>
				<option value="succeeded">Succeeded</option>
				<option value="partially_succeeded">Partially succeeded</option>
				<option value="no_change">No change / not required</option>
				<option value="failed">Failed</option>
				<option value="cancelled">Cancelled</option>
				<option value="superseded">Superseded</option>
				<option value="unsafe">Unsafe</option>
				<option value="dead_letter">Needs operator review</option>
			</select>
		</label>
	{/if}
	<label>
		<span>Attention</span>
		<select name="attention" value={filterState.attention}>
			<option value="">Any severity</option>
			<option value="error">Error</option>
			<option value="warning">Warning</option>
			<option value="normal">Normal</option>
		</select>
	</label>
	<label>
		<span>Trigger</span>
		<select name="trigger" value={filterState.trigger}>
			<option value="">Any trigger</option>
			{#each ['manual', 'schedule', 'policy', 'batch', 'parent', 'healing', 'system', 'webhook'] as trigger (trigger)}
				<option value={trigger}>{trigger}</option>
			{/each}
		</select>
	</label>
	<label>
		<span>Batch / root</span>
		<input name="root_id" value={filterState.rootId} maxlength="32" placeholder="Root job ID" />
	</label>
	<label>
		<span>Correlation</span>
		<input
			name="correlation_id"
			value={filterState.correlationId}
			maxlength="64"
			placeholder="Correlation ID"
		/>
	</label>
	<label>
		<span>Worker</span>
		<input name="worker_id" value={filterState.workerId} maxlength="100" placeholder="Any worker" />
	</label>
	<label>
		<span>Execution class</span>
		<input
			name="execution_class"
			value={filterState.executionClass}
			maxlength="80"
			placeholder="Any class"
		/>
	</label>
	<label>
		<span>Created after</span>
		<input name="created_after" type="date" value={filterState.createdAfter} />
	</label>
	<label>
		<span>Created before</span>
		<input name="created_before" type="date" value={filterState.createdBefore} />
	</label>
	{#if filterState.view === 'history'}
		<label>
			<span>Sort</span>
			<select name="sort" value={filterState.sort}>
				<option value="default">Finished newest first</option>
				<option value="-created">Created newest first</option>
				<option value="created">Created oldest first</option>
			</select>
		</label>
	{/if}
	<div class="buttons">
		<button type="submit">Apply filters</button>
		<button type="button" class="quiet" onclick={onClear}>Clear</button>
	</div>
</form>

<style>
	form {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
		gap: 10px;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
	}
	label {
		display: grid;
		gap: 4px;
		min-width: 0;
	}
	label span {
		color: var(--muted);
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}
	input,
	select {
		width: 100%;
		min-width: 0;
		min-height: 36px;
		padding: 7px 9px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
	}
	.search {
		grid-column: span 2;
	}
	.buttons {
		display: flex;
		align-items: end;
		gap: 6px;
	}
	button {
		min-height: 36px;
		padding: 7px 11px;
		border: 1px solid var(--gold-deep);
		border-radius: 7px;
		background: var(--gold-soft);
		color: var(--gold);
	}
	button.quiet {
		border-color: var(--line2);
		background: var(--panel2);
		color: var(--muted);
	}
	@media (max-width: 520px) {
		.search {
			grid-column: span 1;
		}
	}
</style>
