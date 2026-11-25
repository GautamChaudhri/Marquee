<script lang="ts">
	import type { FeedbackResult, Job } from '#lib/api/types.ts';
	import { describeJob, followJob } from '#lib/features/jobs/jobs.ts';
	import Button from '#lib/ui/Button.svelte';
	import Notice from '#lib/ui/Notice.svelte';
	import ExemplarChanges from './ExemplarChanges.svelte';
	import { canUndo, folderLine, recordedLine } from './review';

	/** What an answer did, kept on the page until the owner moves on: the event, the
	 *  exemplars, the deploy and the rebuild followed to their end, and Undo and Next. */
	let {
		result,
		status,
		asked,
		deployAsked,
		movieId,
		undone = null,
		undoBusy = false,
		undoError = null,
		nextBusy = false,
		nextNote = null,
		onUndo,
		onNext
	}: {
		result: FeedbackResult;
		status: number;
		asked: string;
		deployAsked: boolean;
		movieId: number;
		undone?: FeedbackResult | null;
		undoBusy?: boolean;
		undoError?: string | null;
		nextBusy?: boolean;
		nextNote?: string | null;
		onUndo: () => void;
		onNext: () => void;
	} = $props();

	let deployJob = $state<Job | null>(null);
	let rebuildJob = $state<Job | null>(null);
	let undoRebuild = $state<Job | null>(null);

	function follow(id: number | null | undefined, set: (job: Job) => void): () => void {
		return id ? followJob(id, set) : () => {};
	}

	$effect(() => {
		deployJob = null;
		rebuildJob = null;
		const stops = [
			follow(result.jobs.deploy, (job) => (deployJob = job)),
			follow(result.jobs.rebuild, (job) => (rebuildJob = job))
		];
		return () => stops.forEach((stop) => stop());
	});

	$effect(() => {
		undoRebuild = null;
		const stop = follow(undone?.jobs.rebuild, (job) => (undoRebuild = job));
		return stop;
	});

	const lines = $derived(recordedLine(result.event, status, asked));
	const folder = $derived(
		folderLine(result.event.action, deployAsked, result.jobs.deploy, result.deploy_note)
	);
</script>

<section class="result" aria-label="What your answer did" data-testid="answer-result">
	{#each lines as line (line)}<p class="lead">{line}</p>{/each}
	<p data-testid="answer-folder">{folder}</p>
	{#if result.jobs.deploy}
		<p class="job" data-testid="deploy-job">
			Deploy job {result.jobs.deploy}: {deployJob ? describeJob(deployJob) : 'reading…'}
			<a href="/activity?job={result.jobs.deploy}">Activity</a>
		</p>
	{/if}
	{#if result.jobs.rebuild}
		<p class="job" data-testid="rebuild-job">
			Taste rebuild job {result.jobs.rebuild}: {rebuildJob ? describeJob(rebuildJob) : 'reading…'}
			<a href="/activity?job={result.jobs.rebuild}">Activity</a>
		</p>
	{:else}
		<p class="job">No rebuild was needed: the exemplars did not change.</p>
	{/if}
	<ExemplarChanges changes={result.exemplars} />

	{#if undone}
		<div class="undone" data-testid="undo-result">
			<p class="lead">
				Undone (answer {undone.event.id}). The poster in the folder was not put back.
				<a href="/movies/{movieId}">Restore it from the movie’s poster history</a> if you want the one
				from before.
			</p>
			{#if undone.jobs.rebuild}
				<p class="job">
					Taste rebuild job {undone.jobs.rebuild}: {undoRebuild
						? describeJob(undoRebuild)
						: 'reading…'}
				</p>
			{/if}
			<ExemplarChanges changes={undone.exemplars} />
		</div>
	{/if}
	{#if undoError}<Notice tone="bad" message="The answer was not undone. {undoError}" />{/if}
	{#if nextNote}<p class="note" role="status">{nextNote}</p>{/if}

	<div class="buttons">
		{#if !undone && canUndo(result.event)}
			<Button busy={undoBusy} onclick={onUndo}>Undo</Button>
		{/if}
		<Button variant="primary" busy={nextBusy} onclick={onNext}>Next</Button>
	</div>
</section>

<style>
	.result {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding: 14px 16px;
		border-radius: var(--radius);
		border: 1px solid var(--gold-deep);
		background: var(--gold-soft);
		font-size: 13px;
	}
	p {
		margin: 0;
	}
	.lead {
		font-weight: 550;
	}
	.job,
	.note {
		color: var(--muted);
	}
	.job a,
	.undone a {
		color: var(--gold-copy);
		margin-left: 4px;
	}
	.undone {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding-top: 8px;
		border-top: 1px solid var(--line2);
	}
	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin-top: 4px;
	}
</style>
