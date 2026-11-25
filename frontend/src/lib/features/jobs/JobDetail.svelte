<script lang="ts">
	import type { Job, JobEvent } from '#lib/api/types.ts';
	import { formatDuration, formatWhen } from '#lib/format.ts';
	import Button from '#lib/ui/Button.svelte';
	import Notice from '#lib/ui/Notice.svelte';
	import { canCancel, canRetry, detailHeadline, kindLabel, subjectLabel } from './jobs';
	import StateBadge from './StateBadge.svelte';

	/** One job: its state, error, and events, with Cancel and Retry where the API allows. */
	let {
		job,
		events,
		eventsError = null,
		busy = null,
		onCancel,
		onRetry,
		onClose
	}: {
		job: Job;
		events: JobEvent[] | null;
		eventsError?: string | null;
		busy?: 'cancel' | 'retry' | null;
		onCancel: () => void;
		onRetry: () => void;
		onClose: () => void;
	} = $props();
</script>

<section class="detail" aria-label="Job {job.id}" data-testid="job-detail">
	<header>
		<h2>Job {job.id} · {kindLabel(job.kind)}</h2>
		<StateBadge state={job.state} />
		<button class="close" onclick={onClose} aria-label="Close the job">×</button>
	</header>
	<p class="line">{detailHeadline(job)}</p>
	{#if job.error}<p class="error" role="alert">{job.error}</p>{/if}
	<dl>
		{#if job.subject}<dt>Movie</dt>
			<dd>
				<a href="/movies/{job.subject.movie_id}" data-testid="job-movie"
					>{subjectLabel(job.subject)}</a
				>
			</dd>{/if}
		<dt>Queued</dt>
		<dd>{formatWhen(job.created_at)}</dd>
		<dt>Started</dt>
		<dd>{formatWhen(job.started_at)}</dd>
		<dt>Finished</dt>
		<dd>{formatWhen(job.finished_at)}</dd>
		<dt>Took</dt>
		<dd>{formatDuration(job.started_at, job.finished_at)}</dd>
		<dt>Attempt</dt>
		<dd>{job.attempt} of {job.max_attempts}</dd>
		{#if job.parent_id}<dt>Batch</dt>
			<dd>job {job.parent_id}</dd>{/if}
	</dl>
	<div class="buttons">
		{#if canCancel(job)}
			<Button variant="danger" busy={busy === 'cancel'} disabled={busy !== null} onclick={onCancel}>
				{job.state === 'running' ? 'Ask it to stop' : 'Cancel'}
			</Button>
		{/if}
		{#if canRetry(job)}
			<Button busy={busy === 'retry'} disabled={busy !== null} onclick={onRetry}>Retry</Button>
		{/if}
	</div>
	<h3>Events</h3>
	{#if eventsError}
		<Notice tone="bad" message="The events could not be loaded. {eventsError}" />
	{:else if events === null}
		<Notice tone="loading" message="Loading the events…" />
	{:else if events.length === 0}
		<Notice message="No events yet." />
	{:else}
		<ol class="events" data-testid="job-events">
			{#each events as event (event.id)}
				<li>
					<span class="type">{event.type.replaceAll('_', ' ')}</span>
					<span class="when">{formatWhen(event.at)}</span>
					{#if event.message}<span class="message">{event.message}</span>{/if}
				</li>
			{/each}
		</ol>
	{/if}
</section>

<style>
	.detail {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 14px 16px;
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		background: var(--panel);
	}
	header {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	h2 {
		margin: 0;
		font-size: 15px;
	}
	.close {
		margin-left: auto;
		border: 0;
		background: none;
		color: var(--muted);
		font-size: 18px;
		line-height: 1;
	}
	.line {
		margin: 0;
		font-size: 13px;
	}
	.error {
		margin: 0;
		padding: 8px 10px;
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--bad) 10%, var(--panel));
		color: var(--bad);
		font-size: 12.5px;
		overflow-wrap: anywhere;
	}
	dl {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: 4px 12px;
		margin: 0;
		font-size: 12.5px;
	}
	dt {
		color: var(--muted);
	}
	dd {
		margin: 0;
	}
	.buttons {
		display: flex;
		gap: 8px;
	}
	.buttons:empty {
		display: none;
	}
	h3 {
		margin: 6px 0 0;
		font-size: 13px;
	}
	.events {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
		font-size: 12.5px;
	}
	.events li {
		display: grid;
		grid-template-columns: 120px minmax(0, 1fr);
		gap: 0 10px;
	}
	.type {
		font-weight: 600;
		text-transform: capitalize;
	}
	.when {
		color: var(--muted);
	}
	.message {
		grid-column: 2;
		color: var(--muted);
		overflow-wrap: anywhere;
	}
</style>
