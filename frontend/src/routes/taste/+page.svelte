<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { api, call } from '$lib/api/client';
	import type { Job, TasteStatus } from '$lib/api/types';
	import { describeJob, followJob, isFinal } from '$lib/features/jobs/jobs';
	import ExemplarGrid from '$lib/features/taste/ExemplarGrid.svelte';
	import RecentFeedback from '$lib/features/taste/RecentFeedback.svelte';
	import { agreementLines, exemplarCounts, residualLine } from '$lib/features/taste/taste';
	import { formatWhen } from '$lib/format';
	import { refreshPendingReviews } from '$lib/stores/review';
	import { toast } from '$lib/stores/toast';
	import Button from '$lib/ui/Button.svelte';
	import Notice from '$lib/ui/Notice.svelte';

	let status = $state<TasteStatus | null>(null);
	let error = $state<string | null>(null);
	let grid = $state<ExemplarGrid | null>(null);

	async function load() {
		error = null;
		const result = await call(api.GET('/api/taste'));
		if (!result.ok) {
			error = result.message;
			return;
		}
		status = result.data;
	}

	onMount(() => {
		void load();
	});

	const agreement = $derived(status ? agreementLines(status.agreement) : null);

	// Rebuild now
	let rebuildJob = $state<Job | null>(null);
	let rebuildBusy = $state(false);
	let rebuildNote = $state<string | null>(null);
	let stopRebuild: () => void = () => {};

	async function rebuild() {
		rebuildBusy = true;
		rebuildNote = null;
		const result = await call(api.POST('/api/taste/rebuild', { body: { force: false } }));
		if (!result.ok) {
			rebuildBusy = false;
			rebuildNote = result.message;
			return;
		}
		rebuildJob = result.data;
		if (result.status === 200) rebuildNote = `A rebuild is already ${result.data.state}.`;
		stopRebuild();
		stopRebuild = followJob(
			result.data.id,
			(job) => {
				rebuildJob = job;
				if (!isFinal(job.state)) return;
				rebuildBusy = false;
				rebuildNote = null;
				toast(
					`Rebuild job ${job.id}: ${describeJob(job)}`,
					job.state === 'succeeded' ? 'good' : 'bad'
				);
				void load();
				grid?.reload();
			},
			(message) => (rebuildNote = message)
		);
	}

	function changed() {
		void load();
		grid?.reload();
		void refreshPendingReviews();
	}

	onDestroy(() => stopRebuild());
</script>

<svelte:head><title>Taste · Marquee</title></svelte:head>

{#if error}
	<Notice tone="bad" message="The taste profile could not be loaded. {error}">
		{#snippet action()}<Button onclick={load}>Try again</Button>{/snippet}
	</Notice>
{:else if !status}
	<Notice tone="loading" message="Loading the taste profile…" />
{:else}
	<div class="top">
		<section class="panel" data-testid="profile">
			<h2>Profile</h2>
			{#if status.profile}
				<dl>
					<dt>Profile</dt>
					<dd>
						{status.profile.id} ·
						<code title={status.profile.sha256}>{status.profile.sha256.slice(0, 12)}</code>
					</dd>
					<dt>Built</dt>
					<dd>{formatWhen(status.profile.created_at)} ({status.profile.source})</dd>
					<dt>Liked</dt>
					<dd>{status.profile.positives}</dd>
					<dt>Disliked</dt>
					<dd>{status.profile.negatives}</dd>
				</dl>
			{:else}
				<Notice message="No taste profile is registered yet." />
			{/if}
			<p class="line">{exemplarCounts(status.exemplars)}</p>
			{#if status.rebuild_due}
				<p class="due" data-testid="rebuild-due">
					A rebuild is due: the exemplars changed since this profile was built.
				</p>
			{/if}
			<div class="actions">
				<Button variant="primary" busy={rebuildBusy} onclick={rebuild}>Rebuild now</Button>
				{#if rebuildJob}
					<span class="job" data-testid="rebuild-job">
						Rebuild job {rebuildJob.id}: {describeJob(rebuildJob)}
						<a href="/activity?job={rebuildJob.id}">Activity</a>
					</span>
				{/if}
			</div>
			{#if rebuildNote}<p class="line" role="status">{rebuildNote}</p>{/if}
		</section>

		<section class="panel" data-testid="agreement">
			<h2>Agreement</h2>
			<p class="hint">
				How often your answer kept the AI’s pick, counted only over blind runs: runs made while the
				movie had no liked poster in the profile.
			</p>
			{#each agreement?.blind ?? [] as line (line)}<p class="line strong">{line}</p>{/each}
			{#each agreement?.apart ?? [] as line (line)}<p class="line">{line}</p>{/each}
			<h3>Residual</h3>
			<p class="line" data-testid="residual">{residualLine(status.residual)}</p>
			<p class="line">
				{status.pending_reviews}
				{status.pending_reviews === 1 ? 'run waits' : 'runs wait'} for a review.
				<a href="/review">Review</a>
			</p>
		</section>
	</div>

	<section class="block">
		<h2>Recent feedback</h2>
		<RecentFeedback onChanged={changed} />
	</section>

	<section class="block">
		<h2>Exemplars</h2>
		<ExemplarGrid bind:this={grid} />
	</section>
{/if}

<style>
	.top {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 14px;
	}
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
	h3 {
		margin: 8px 0 0;
		font-size: 13.5px;
	}
	dl {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: 4px 14px;
		margin: 0;
		font-size: 13px;
	}
	dt {
		color: var(--muted);
	}
	dd {
		margin: 0;
	}
	code {
		font-family: var(--font-mono);
		font-size: 12px;
	}
	.line,
	.hint,
	.due {
		margin: 0;
		font-size: 13px;
	}
	.hint,
	.line {
		color: var(--muted);
	}
	.line.strong {
		color: var(--text);
	}
	.line a,
	.job a {
		color: var(--gold-copy);
	}
	.due {
		color: var(--warn-copy);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px 12px;
	}
	.job {
		font-size: 13px;
		color: var(--muted);
	}
	.block {
		margin-top: 26px;
	}
	.block h2 {
		margin-bottom: 10px;
	}
	@media (max-width: 900px) {
		.top {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
