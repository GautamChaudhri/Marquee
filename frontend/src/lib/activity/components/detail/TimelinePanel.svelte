<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { listEvents } from '../../client';
	import type { EventItem } from '../../types';

	let { jobId, contained = false }: { jobId: string; contained?: boolean } = $props();
	let items = $state<EventItem[]>([]);
	let cursor = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let controller: AbortController | null = null;

	async function loadMore() {
		if (loading) return;
		loading = true;
		error = null;
		controller?.abort();
		controller = new AbortController();
		try {
			const page = await listEvents(
				fetch,
				jobId,
				{ cursor: cursor ?? undefined, limit: 100, scope: contained ? 'contained' : 'self' },
				controller.signal
			);
			items = [...items, ...page.items];
			cursor = page.next_cursor;
		} catch (reason) {
			if (!controller.signal.aborted)
				error = reason instanceof Error ? reason.message : 'Timeline unavailable';
		} finally {
			loading = false;
		}
	}

	onMount(loadMore);
	onDestroy(() => controller?.abort());

	function originName(event: EventItem): string {
		const value = event.origin_subject.display_name ?? event.origin_subject.title;
		return typeof value === 'string' && value ? value : event.origin_job_id;
	}
</script>

<section aria-labelledby="timeline-heading">
	<h2 id="timeline-heading">Timeline</h2>
	<p class="hint">Durable semantic events, newest canonical state preserved across reconnects.</p>
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	{#if items.length === 0 && !loading}<p class="empty">No timeline events are retained.</p>{/if}
	<div class="timeline">
		{#each items as event (event.id)}
			<article class="event">
				{#if contained && event.origin_job_id !== jobId}<a
						class="origin"
						href={`/projection-room/jobs/${event.origin_job_id}`}>{originName(event)}</a
					>{/if}
				<time datetime={event.created_at ?? undefined}
					>{event.created_at
						? new Date(event.created_at).toLocaleString()
						: 'Time unavailable'}</time
				>
				<strong>{event.state}</strong>
				{#if event.stage}<span class="stage">{event.stage}</span>{/if}
				{#if event.message}<p>{event.message}</p>{/if}
			</article>
		{/each}
	</div>
	{#if cursor}<button onclick={loadMore} disabled={loading}
			>{loading ? 'Loading…' : 'Load more events'}</button
		>{/if}
</section>

<style>
	section {
		display: grid;
		gap: 12px;
	}
	h2,
	p {
		margin: 0;
	}
	.hint,
	.empty {
		color: var(--muted);
		font-size: 12px;
	}
	.error {
		color: var(--bad);
	}
	.timeline {
		max-height: 60vh;
		overflow: auto;
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	.event {
		content-visibility: auto;
		contain-intrinsic-size: auto 72px;
		display: grid;
		grid-template-columns: minmax(170px, auto) auto 1fr;
		gap: 8px 12px;
		padding: 10px 12px;
		border-bottom: 1px solid var(--line2);
	}
	.event:last-child {
		border-bottom: 0;
	}
	time,
	.stage {
		color: var(--muted);
		font: 11px var(--font-mono);
	}
	.event p {
		grid-column: 2 / -1;
		color: var(--muted);
		overflow-wrap: anywhere;
	}
	.origin {
		grid-column: 1 / -1;
		color: var(--muted);
		font-size: 11px;
	}
	button {
		justify-self: start;
	}
	@media (max-width: 640px) {
		.event {
			grid-template-columns: 1fr;
		}
		.event p {
			grid-column: 1;
		}
	}
</style>
