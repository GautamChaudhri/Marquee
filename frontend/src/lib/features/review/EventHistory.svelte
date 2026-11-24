<script lang="ts">
	import type { FeedbackEvent } from '$lib/api/types';
	import { formatWhen } from '$lib/format';
	import Button from '$lib/ui/Button.svelte';
	import { canUndo, eventLine } from './review';

	/** A run's earlier answers, newest first, each with Undo unless it was undone or is
	 *  itself an undo. */
	let {
		events,
		busyId = null,
		onUndo
	}: {
		events: FeedbackEvent[];
		busyId?: number | null;
		onUndo: (event: FeedbackEvent) => void;
	} = $props();

	const newestFirst = $derived([...events].sort((a, b) => b.id - a.id));
</script>

<ol class="events" data-testid="run-events">
	{#each newestFirst as event (event.id)}
		<li class:revoked={event.revoked_by}>
			<span class="what">{eventLine(event)}</span>
			<span class="when">answer {event.id} · {formatWhen(event.created_at)}</span>
			{#if event.revoked_by}<span class="undone">Undone by answer {event.revoked_by}</span>{/if}
			{#if canUndo(event)}
				<Button busy={busyId === event.id} disabled={busyId !== null} onclick={() => onUndo(event)}>
					Undo
				</Button>
			{/if}
		</li>
	{/each}
</ol>

<style>
	.events {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	li {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 4px 12px;
		padding: 8px 10px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--panel);
		font-size: 13px;
	}
	li.revoked .what {
		text-decoration: line-through;
		color: var(--muted);
	}
	.what {
		overflow-wrap: anywhere;
	}
	.when,
	.undone {
		color: var(--muted);
		font-size: 12px;
	}
	li :global(button) {
		margin-left: auto;
	}
</style>
