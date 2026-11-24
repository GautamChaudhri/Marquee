<script lang="ts">
	import { onDestroy } from 'svelte';
	import { api, call } from '$lib/api/client';
	import type { Evaluation, Job, TasteStatus } from '$lib/api/types';
	import { describeJob, followJob, isFinal } from '$lib/features/jobs/jobs';
	import { formatWhen } from '$lib/format';
	import { toast } from '$lib/stores/toast';
	import Button from '$lib/ui/Button.svelte';
	import ConfirmDialog from '$lib/ui/ConfirmDialog.svelte';
	import Notice from '$lib/ui/Notice.svelte';
	import { compareLine, duration, headline, progressLine, stateLine } from './evaluation';

	/** The blind evaluation: the latest finished one's headline, the one before it, and the
	 *  one running now with its progress. Starting one asks first. */
	let {
		evaluation,
		evaluable,
		onChanged
	}: {
		evaluation: TasteStatus['evaluation'];
		/** How many movies an evaluation would cover, for the confirmation. */
		evaluable: number;
		onChanged: () => void;
	} = $props();

	let confirming = $state(false);
	let busy = $state(false);
	let note = $state<string | null>(null);
	let running = $state<Evaluation | null>(null);
	let batch = $state<Job | null>(null);
	let stop: () => void = () => {};

	const current = $derived.by(() => {
		const item = running ?? evaluation.running ?? null;
		return item && item.state === 'running' ? item : null;
	});
	const latest = $derived(evaluation.latest);

	/** Read the running evaluation again for its progress. A read that comes back after the
	 *  batch ended is dropped, so a finished evaluation never shows as running. */
	async function refresh(id: number) {
		const result = await call(
			api.GET('/api/taste/evaluations/{evaluation_id}', {
				params: { path: { evaluation_id: id } }
			})
		);
		if (result.ok && following === id) running = result.data;
	}

	let following = $state<number | null>(null);

	function follow(item: Evaluation) {
		if (item.job_id === null || item.job_id === undefined) return;
		stop();
		following = item.id;
		stop = followJob(
			item.job_id,
			(job) => {
				batch = job;
				if (!isFinal(job.state)) {
					void refresh(item.id);
					return;
				}
				following = null;
				running = null;
				batch = null;
				toast(
					`Blind evaluation ${item.id}: ${describeJob(job)}`,
					job.state === 'succeeded' ? 'good' : 'bad'
				);
				onChanged();
			},
			(message) => (note = message)
		);
	}

	$effect(() => {
		const item = evaluation.running;
		if (item && following === null && running === null) follow(item);
	});

	async function start() {
		busy = true;
		note = null;
		const result = await call(api.POST('/api/taste/evaluations', { body: {} }));
		busy = false;
		confirming = false;
		if (!result.ok) {
			note = result.message;
			return;
		}
		running = result.data.evaluation;
		batch = result.data.batch;
		follow(result.data.evaluation);
	}

	onDestroy(() => stop());
</script>

<section class="panel" data-testid="evaluation">
	<h2>Blind evaluation</h2>
	<p class="hint">
		Each movie is run again without its own poster in the profile, and Marquee looks for your poster
		among what it would have picked from. It changes nothing in your library.
	</p>
	{#if latest}
		<p class="line strong" data-testid="evaluation-headline">{headline(latest.summary.seed)}</p>
		{#if latest.summary.feedback.evaluated > 0}
			<p class="line">
				Posters you chose from Marquee’s picks are counted apart: {latest.summary.feedback
					.same_design} of {latest.summary.feedback.evaluated} were the blind pick’s design.
			</p>
		{/if}
		{#if stateLine(latest)}<p class="line warn">{stateLine(latest)}</p>{/if}
		<p class="line">
			Evaluation {latest.id}, {formatWhen(latest.finished_at ?? latest.created_at)}, ranked by the
			{latest.scorer} scorer.
			<a href="/taste/evaluations/{latest.id}">See every poster</a>
		</p>
		{#if evaluation.previous}
			<p class="line" data-testid="evaluation-previous">
				Before: {compareLine(evaluation.previous)}
			</p>
		{/if}
	{:else if !current}
		<Notice message="No blind evaluation yet." />
	{/if}
	{#if current}
		<p class="line" role="status" data-testid="evaluation-progress">
			Evaluation {current.id} is running: {progressLine(current)}
			{#if batch}<a href="/activity?job={batch.id}">Activity</a>{/if}
			<a href="/taste/evaluations/{current.id}">Results so far</a>
		</p>
	{/if}
	<div class="actions">
		<Button {busy} disabled={current !== null} onclick={() => (confirming = true)}
			>Run a blind evaluation</Button
		>
	</div>
	{#if note}<p class="line" role="status">{note}</p>{/if}
</section>

<ConfirmDialog
	open={confirming}
	title="Run a blind evaluation?"
	message={`This runs ${evaluable} movies again, each without its own poster in the taste profile, ` +
		`${duration(evaluable)} on the GPU, and downloads their candidates from TMDB again. It changes ` +
		'no poster in your library, no answer, and no taste profile.'}
	confirmLabel="Run it"
	{busy}
	onConfirm={start}
	onCancel={() => (confirming = false)}
/>

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding: 14px 16px;
		border-radius: var(--radius);
		border: 1px solid var(--line);
		background: var(--panel);
	}
	h2 {
		margin: 0 0 4px;
		font-size: 15px;
	}
	.line,
	.hint {
		margin: 0;
		font-size: 13px;
		color: var(--muted);
	}
	.line.strong {
		color: var(--text);
	}
	.line.warn {
		color: var(--warn-copy);
	}
	.line a {
		color: var(--gold-copy);
		margin-left: 4px;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 12px;
	}
</style>
