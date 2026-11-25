<script lang="ts">
	import { onMount } from 'svelte';
	import { api, call } from '#lib/api/client.ts';
	import type { PendingReview } from '#lib/api/types.ts';
	import { thumbnailUrl } from '#lib/features/library/poster.ts';
	import { formatWhen } from '#lib/format.ts';
	import { pendingReviews } from '#lib/stores/review.ts';
	import Button from '#lib/ui/Button.svelte';
	import Notice from '#lib/ui/Notice.svelte';

	let items = $state<PendingReview[] | null>(null);
	let error = $state<string | null>(null);

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
	<p class="count">{items.length} {items.length === 1 ? 'run is' : 'runs are'} waiting.</p>
	<ul class="queue" data-testid="review-queue">
		{#each items as item (item.run_id)}
			<li data-testid="review-row">
				<a class="pictures" href="/review/{item.run_id}" aria-label="Review {item.title}">
					<figure>
						{#if item.poster}
							<img
								src={thumbnailUrl(item.poster, 160)}
								alt="The poster in the folder now"
								width="160"
								height="240"
								loading="lazy"
							/>
						{:else}
							<div class="none">No poster</div>
						{/if}
						<figcaption>Now</figcaption>
					</figure>
					<figure>
						{#if item.pick_image_url}
							<img
								src={item.pick_image_url}
								alt="The run’s pick"
								width="500"
								height="750"
								loading="lazy"
							/>
						{:else}
							<div class="none">No image</div>
						{/if}
						<figcaption>Pick</figcaption>
					</figure>
				</a>
				<div class="facts">
					<a class="title" href="/review/{item.run_id}">
						{item.title} <span class="year">{item.year ?? ''}</span>
					</a>
					<p class="meta">
						Run {item.run_id} · {item.ranked ?? 0} ranked · {formatWhen(item.finished_at)}
					</p>
					<p class="marks">
						{#if item.blind}<span class="mark blind">Blind</span>{/if}
						{#if item.policy_deployed}
							<span class="mark policy">The deploy policy already wrote the pick</span>
						{/if}
					</p>
				</div>
				<a class="go" href="/review/{item.run_id}">Review</a>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.intro {
		margin: 0 0 14px;
		font-size: 13px;
		color: var(--muted);
		max-width: 70ch;
	}
	.count {
		margin: 0 0 10px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.queue {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	li {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: center;
		gap: 14px;
		padding: 10px 12px;
		border-radius: var(--radius);
		border: 1px solid var(--line);
		background: var(--panel);
	}
	.pictures {
		display: flex;
		gap: 6px;
	}
	figure {
		margin: 0;
		width: 64px;
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	figure img,
	.none {
		width: 64px;
		height: 96px;
		object-fit: cover;
		border-radius: 5px;
		border: 1px solid var(--line);
		background: var(--panel2);
	}
	.none {
		display: grid;
		place-items: center;
		color: var(--faint);
		font-size: 10.5px;
		text-align: center;
	}
	figcaption {
		font-size: 10.5px;
		color: var(--faint);
		text-align: center;
	}
	.title {
		font-weight: 600;
		color: var(--text);
	}
	.year {
		color: var(--muted);
		font-weight: 400;
	}
	.meta,
	.marks {
		margin: 4px 0 0;
		font-size: 12px;
		color: var(--muted);
	}
	.marks {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.mark {
		padding: 1px 8px;
		border-radius: var(--radius-pill);
		font-size: 11px;
		border: 1px solid var(--line2);
	}
	.mark.blind {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 45%, transparent);
	}
	.mark.policy {
		color: var(--warn-copy);
		border-color: color-mix(in srgb, var(--warn) 45%, transparent);
	}
	.go {
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--gold-deep);
		color: var(--gold-copy);
		font-size: 13px;
	}
	@media (max-width: 720px) {
		li {
			grid-template-columns: auto minmax(0, 1fr);
			gap: 10px;
		}
		.go {
			grid-column: 1 / -1;
			text-align: center;
		}
		figure,
		figure img,
		.none {
			width: 52px;
		}
		figure img,
		.none {
			height: 78px;
		}
	}
</style>
