<script lang="ts">
	import { onMount } from 'svelte';
	import { api, call } from '#lib/api/client.ts';
	import type { PendingReview } from '#lib/api/types.ts';
	import QueueCard from '#lib/features/review/QueueCard.svelte';
	import QueueTile from '#lib/features/review/QueueTile.svelte';
	import { asQueueView, QUEUE_VIEWS, queueView } from '#lib/features/review/queue.ts';
	import { pendingReviews } from '#lib/stores/review.ts';
	import Button from '#lib/ui/Button.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import Notice from '#lib/ui/Notice.svelte';

	let items = $state<PendingReview[] | null>(null);
	let error = $state<string | null>(null);

	const view = $derived(asQueueView($queueView));

	async function load() {
		error = null;
		const result = await call(api.GET('/api/review'));
		if (!result.ok) {
			error = result.message;
			return;
		}
		items = result.data.items;
		pendingReviews.set(result.data.items.length);
	}

	onMount(() => {
		void load();
	});
</script>

<svelte:head><title>Review · Marquee</title></svelte:head>

<h1 class="sr-only">Review</h1>

<p class="intro">
	Runs whose pick nobody has answered yet. Answering one teaches Marquee your taste: keep the pick,
	choose another design, rank them, or refuse them all.
</p>

{#if error}
	<Notice tone="bad" message="The review queue could not be loaded. {error}">
		{#snippet action()}<Button onclick={load}>Try again</Button>{/snippet}
	</Notice>
{:else if items === null}
	<Notice tone="loading" message="Loading the review queue…" />
{:else if items.length === 0}
	<Notice message="Nothing is waiting for a review." />
{:else}
	<div class="bar">
		<p class="count">{items.length} {items.length === 1 ? 'run is' : 'runs are'} waiting.</p>
		<div class="views" role="group" aria-label="Show the queue as">
			{#each QUEUE_VIEWS as option (option.value)}
				<button
					type="button"
					aria-pressed={view === option.value}
					onclick={() => queueView.set(option.value)}
				>
					<Icon name={option.icon} size={14} />
					{option.label}
				</button>
			{/each}
		</div>
	</div>

	{#if view === 'gallery'}
		<ul class="gallery" data-testid="review-gallery">
			{#each items as item (item.run_id)}
				<QueueTile {item} />
			{/each}
		</ul>
	{:else}
		<ul class="queue" data-testid="review-queue">
			{#each items as item (item.run_id)}
				<QueueCard {item} />
			{/each}
		</ul>
	{/if}
{/if}

<style>
	.intro {
		margin: 0 0 14px;
		font-size: 13px;
		color: var(--muted);
		max-width: 70ch;
	}
	.bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 8px 14px;
		margin-bottom: 12px;
	}
	.count {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
	}

	/* The view switch: two buttons in one outline, the chosen one lit. */
	.views {
		display: inline-flex;
		padding: 2px;
		gap: 2px;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: var(--panel);
	}
	.views button {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		min-height: 28px;
		padding: 0 10px;
		border: 0;
		border-radius: 5px;
		background: transparent;
		color: var(--muted);
		font: inherit;
		font-size: 12.5px;
		cursor: pointer;
	}
	.views button:hover {
		color: var(--text);
	}
	.views button[aria-pressed='true'] {
		background: var(--gold-soft);
		color: var(--gold-copy);
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	/* List: two columns of cards; each card spans two of these rows (QueueCard). */
	.queue {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}
	/* Gallery: one tile per run, showing only its pick. */
	.gallery {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
		gap: 18px 14px;
	}
	@media (max-width: 760px) {
		.queue {
			grid-template-columns: minmax(0, 1fr);
		}
		.gallery {
			grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
			gap: 14px 10px;
		}
	}
</style>
