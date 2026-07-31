<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import { getJobProgressStore } from '$lib/activity/context';
	import ContainedWorkDisclosure from '$lib/activity/components/ContainedWorkDisclosure.svelte';
	import PresentationSections from '$lib/activity/components/PresentationSections.svelte';
	import TimelinePanel from '$lib/activity/components/detail/TimelinePanel.svelte';
	import LogsPanel from '$lib/activity/components/detail/LogsPanel.svelte';
	import ArtifactsPanel from '$lib/activity/components/detail/ArtifactsPanel.svelte';
	import RawDataPanel from '$lib/activity/components/detail/RawDataPanel.svelte';
	import ExecutionPanel from '$lib/activity/components/detail/ExecutionPanel.svelte';
	import type { PageData } from './$types';

	type DetailTab = 'overview' | 'timeline' | 'logs' | 'artifacts' | 'raw' | 'execution';
	const tabs: { id: DetailTab; label: string }[] = [
		{ id: 'overview', label: 'Overview' },
		{ id: 'timeline', label: 'Timeline' },
		{ id: 'logs', label: 'Logs' },
		{ id: 'artifacts', label: 'Artifacts' },
		{ id: 'raw', label: 'Raw Data' },
		{ id: 'execution', label: 'Execution' }
	];

	let { data }: { data: PageData } = $props();
	let presentation = $derived(data.presentation);
	const store = getJobProgressStore();
	const containedWork = $derived(
		(presentation ? store.records.get(presentation.job_id)?.snapshot?.contained_work : null) ??
			presentation?.contained_work ??
			null
	);

	onMount(() => {
		if (!presentation) return;
		store.track(presentation.job_id);
		return () => store.untrack(presentation!.job_id);
	});

	function tabFromUrl(value: string | null): DetailTab {
		return tabs.some((tab) => tab.id === value) ? (value as DetailTab) : 'overview';
	}

	let activeTab = $derived(tabFromUrl(page.url.searchParams.get('tab')));
	const containedEvidence = $derived(containedWork != null);

	function selectTab(tab: DetailTab) {
		const url = new URL(page.url);
		if (tab === 'overview') url.searchParams.delete('tab');
		else url.searchParams.set('tab', tab);
		void goto(`${url.pathname}${url.search}`, { keepFocus: true, noScroll: true });
	}
</script>

<svelte:head>
	<title
		>{presentation
			? `${presentation.subject.display_name} · Activity`
			: 'Job unavailable · Activity'}</title
	>
</svelte:head>

{#if !presentation}
	<SectionHeader
		title="Job unavailable"
		subtitle={data.error ?? 'This job or its retained presentation is no longer available.'}
	/>
	<a class="back" href="/projection-room?view=history">← Back to Activity</a>
{:else}
	<a class="back" href="/projection-room?view=history">← Back to Activity</a>
	<SectionHeader
		title={presentation.subject.display_name}
		subtitle={`${presentation.label} · ${presentation.job_id}`}
	/>

	{#if presentation.subject.missing_live_subject}
		<p class="subject-notice" role="status">
			The live library subject was deleted or is unavailable. This retained job snapshot remains
			authoritative.
		</p>
	{/if}

	<div class="hero">
		<div>
			<span class={`status ${presentation.status.tone}`}>{presentation.status.label}</span><strong
				>{presentation.action.headline}</strong
			>{#if presentation.action.explanation}<p>{presentation.action.explanation}</p>{/if}
		</div>
		<div class="meta">
			<span>{presentation.feature_label}</span><span>{presentation.trigger.label}</span><span
				>{presentation.presentation_family}</span
			>
		</div>
	</div>

	{#if presentation.attention.level !== 'normal'}
		<div class={`attention ${presentation.attention.level}`} role="status">
			<strong>{presentation.attention.message}</strong>{#if presentation.attention.remediation}<span
					>{presentation.attention.remediation}</span
				>{/if}
		</div>
	{/if}

	<nav class="tabs" aria-label="Job detail sections">
		{#each tabs as tab (tab.id)}
			<button
				class:active={activeTab === tab.id}
				aria-current={activeTab === tab.id ? 'page' : undefined}
				onclick={() => selectTab(tab.id)}>{tab.label}</button
			>
		{/each}
	</nav>

	<div class="panel">
		{#if activeTab === 'overview'}
			<div class="overview">
				{#if presentation.warnings.length}<div class="warnings">
						<h2>Warnings</h2>
						<ul>
							{#each presentation.warnings as warning, i (i)}<li>{warning.message}</li>{/each}
						</ul>
					</div>{/if}
				{#if presentation.failures.length}<div class="failures">
						<h2>Failures</h2>
						<ul>
							{#each presentation.failures as failure, i (i)}<li>
									<strong>{failure.message}</strong>{#if failure.remediation}<span
											>{failure.remediation}</span
										>{/if}
								</li>{/each}
						</ul>
					</div>{/if}
				{#if containedWork}
					<!-- The detail page is already the diagnostic destination, so contained work
					     starts open and uses the same bounded component as the Activity card. -->
					<ContainedWorkDisclosure jobId={presentation.job_id} summary={containedWork} open />
				{/if}
				{#if presentation.sections.length}<PresentationSections
						sections={presentation.sections}
					/>{:else}<p class="empty">No additional presentation sections are available.</p>{/if}
			</div>
		{:else if activeTab === 'timeline'}
			<TimelinePanel jobId={presentation.job_id} contained={containedEvidence} />
		{:else if activeTab === 'logs'}
			<LogsPanel jobId={presentation.job_id} contained={containedEvidence} />
		{:else if activeTab === 'artifacts'}
			<ArtifactsPanel jobId={presentation.job_id} contained={containedEvidence} />
		{:else if activeTab === 'raw'}
			<RawDataPanel jobId={presentation.job_id} />
		{:else}
			<ExecutionPanel jobId={presentation.job_id} />
		{/if}
	</div>
{/if}

<style>
	.back {
		display: inline-block;
		margin: 0 0 12px;
		color: var(--muted);
		font-size: 12px;
	}
	.back:hover {
		color: var(--gold);
	}
	.subject-notice {
		margin: 0 0 14px;
		border-left: 3px solid var(--warn);
		padding: 8px 12px;
		color: var(--muted);
	}
	.hero {
		display: flex;
		justify-content: space-between;
		align-items: start;
		gap: 18px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
		margin: 12px 0;
	}
	.hero > div:first-child {
		display: grid;
		gap: 7px;
	}
	.hero strong {
		font-size: 16px;
	}
	.hero p {
		margin: 0;
		color: var(--muted);
	}
	.status {
		justify-self: start;
		border: 1px solid var(--line2);
		border-radius: 999px;
		padding: 3px 8px;
		font-size: 11px;
	}
	.status.positive {
		color: var(--good);
	}
	.status.negative {
		color: var(--bad);
	}
	.status.warning {
		color: var(--warn);
	}
	.meta {
		display: flex;
		flex-wrap: wrap;
		justify-content: end;
		gap: 6px;
	}
	.meta span {
		background: var(--panel2);
		border-radius: 999px;
		padding: 4px 8px;
		color: var(--muted);
		font-size: 11px;
		text-transform: capitalize;
	}
	.attention {
		display: grid;
		gap: 3px;
		margin: 12px 0;
		border: 1px solid var(--line2);
		border-left: 3px solid var(--warn);
		border-radius: 8px;
		padding: 10px 12px;
	}
	.attention.error {
		border-left-color: var(--bad);
	}
	.attention span {
		color: var(--muted);
		font-size: 12px;
	}
	.tabs {
		display: flex;
		gap: 4px;
		overflow-x: auto;
		margin: 18px 0 12px;
		border-bottom: 1px solid var(--line);
	}
	.tabs button {
		flex: none;
		border: 0;
		border-bottom: 2px solid transparent;
		border-radius: 0;
		background: transparent;
		padding: 9px 12px;
		color: var(--muted);
	}
	.tabs button.active {
		border-bottom-color: var(--gold);
		color: var(--gold);
	}
	.panel {
		min-height: 280px;
	}
	.overview {
		display: grid;
		gap: 16px;
	}
	.warnings,
	.failures {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px;
	}
	.warnings {
		color: var(--warn);
	}
	.failures {
		color: var(--bad);
	}
	.warnings h2,
	.failures h2 {
		margin: 0 0 8px;
		font-size: 13px;
	}
	.warnings ul,
	.failures ul {
		margin: 0;
		padding-left: 20px;
	}
	.failures li {
		display: grid;
		gap: 3px;
	}
	.failures span,
	.empty {
		color: var(--muted);
	}
	@media (max-width: 640px) {
		.hero {
			display: grid;
		}
		.meta {
			justify-content: start;
		}
	}
</style>
