<script lang="ts">
	import { onMount } from 'svelte';
	import { api, call } from '#lib/api/client.ts';
	import type { PendingReview } from '#lib/api/types.ts';
	import { thumbnailUrl } from '#lib/features/library/poster.ts';
	import type { RankedCandidate } from '#lib/features/review/panel.ts';
	import { topDesigns } from '#lib/features/review/queue.ts';
	import { formatWhen } from '#lib/format.ts';

	let { item }: { item: PendingReview } = $props();

	const href = $derived(`/review/${item.run_id}`);

	// The queue's item names only the pick; the run's other best designs come from the run
	// itself. Until it answers, or if it cannot, the card shows the pick alone.
	let designs = $state<RankedCandidate[] | null>(null);

	onMount(() => {
		void (async () => {
			const result = await call(
				api.GET('/api/runs/{run_id}', { params: { path: { run_id: item.run_id } } })
			);
			if (result.ok) designs = topDesigns(result.data.candidates);
		})();
	});

	type Shown = { key: string; url: string | null; alt: string; label: string; extra: boolean };
	const shown = $derived<Shown[]>(
		designs?.length
			? designs.map((design, i) => ({
					key: `c${design.id}`,
					url: design.image_url ?? null,
					alt: i === 0 ? 'The run’s top pick' : `The design ranked ${i + 1}`,
					label: i === 0 ? 'Top pick' : `#${i + 1}`,
					// The sixth design shows only on a card wide enough for seven posters.
					extra: i >= 5
				}))
			: [
					{
						key: 'pick',
						url: item.pick_image_url ?? null,
						alt: 'The run’s top pick',
						label: 'Top pick',
						extra: false
					}
				]
	);
</script>

<!-- One run in the list: its facts and the Review button on top, then a row of small posters:
     the one in the folder now (Current), and the run's best designs in rank order, from the
     left. -->
<li class="card" data-testid="review-row">
	<div class="head">
		<div class="facts">
			<h2 class="title">
				<a {href}>{item.title} <span class="year">{item.year ?? ''}</span></a>
			</h2>
			<p class="meta">
				Run {item.run_id} · {item.ranked ?? 0} ranked · {formatWhen(item.finished_at)}
			</p>
			{#if item.blind || item.policy_deployed}
				<p class="marks">
					{#if item.blind}<span class="mark blind">Blind</span>{/if}
					{#if item.policy_deployed}
						<span class="mark policy">The deploy policy already wrote the top pick</span>
					{/if}
				</p>
			{/if}
		</div>
		<a class="go" {href}>Review</a>
	</div>
	<!-- The posters open the review too. Keyboard users reach it through the title and the
	     button, so the pictures are not a third stop. -->
	<a class="pictures" {href} tabindex="-1" aria-label="Review {item.title}">
		<figure class="now" data-testid="queue-current">
			{#if item.poster}
				<img
					src={thumbnailUrl(item.poster, 160)}
					srcset="{thumbnailUrl(item.poster, 160)} 160w, {thumbnailUrl(item.poster, 320)} 320w"
					sizes="96px"
					alt="The poster in the folder now"
					width="160"
					height="240"
					loading="lazy"
					decoding="async"
				/>
			{:else}
				<div class="none">No poster</div>
			{/if}
			<figcaption class="current">Current</figcaption>
		</figure>
		<span class="rule" aria-hidden="true"></span>
		{#each shown as design (design.key)}
			<figure class:extra={design.extra} data-testid="queue-design">
				{#if design.url}
					<img
						src={design.url}
						alt={design.alt}
						width="500"
						height="750"
						loading="lazy"
						decoding="async"
					/>
				{:else}
					<div class="none">No image</div>
				{/if}
				<figcaption class:pick={design.label === 'Top pick'}>{design.label}</figcaption>
			</figure>
		{/each}
	</a>
</li>

<style>
	/* The card spans two rows of the list's grid (see the review page), so the headings of
	   one row share a height and the poster rows beside them line up. */
	.card {
		display: grid;
		grid-row: span 2;
		grid-template-rows: subgrid;
		row-gap: 12px;
		padding: 14px;
		border-radius: var(--radius);
		border: 1px solid var(--line);
		background: var(--panel);
		min-width: 0;
	}
	.head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 12px;
		min-width: 0;
	}
	.facts {
		min-width: 0;
	}
	.title {
		margin: 0;
		font-size: 14.5px;
		font-weight: 600;
		line-height: 1.35;
	}
	.title a {
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
		flex: none;
		padding: 6px 14px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--gold-deep);
		color: var(--gold-copy);
		font-size: 13px;
		white-space: nowrap;
	}
	.go:hover {
		background: var(--gold-soft);
	}

	/* Slots of one width (the current poster and the designs), so a run with fewer designs
	   shows fewer posters of the same size, from the left. A thin rule sets the current poster
	   apart from the run's designs. Six slots, or seven on a card wide enough to keep them
	   near full size; the width is capped so a very wide card stays compact. */
	.pictures {
		--gap: 8px;
		--rule: 1px;
		container-type: inline-size;
		display: flex;
		align-items: flex-start;
		gap: var(--gap);
		align-self: start;
	}
	figure {
		--slots: 6;
		flex: 0 0 min(96px, calc((100% - var(--slots) * var(--gap) - var(--rule)) / var(--slots)));
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}
	figure.extra {
		display: none;
	}
	@container (min-width: 580px) {
		figure {
			--slots: 7;
		}
		figure.extra {
			display: flex;
		}
	}
	.rule {
		flex: 0 0 var(--rule);
		align-self: stretch;
		background: var(--line2);
	}
	img,
	.none {
		display: block;
		width: 100%;
		height: auto;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		border-radius: 5px;
		border: 1px solid var(--line);
		background: var(--panel2);
	}
	.pictures:hover img,
	.pictures:hover .none {
		border-color: var(--faint);
	}
	.none {
		display: grid;
		place-items: center;
		padding: 2px;
		color: var(--faint);
		font-size: 10.5px;
		line-height: 1.2;
		text-align: center;
	}
	figcaption {
		font-size: 11px;
		color: var(--muted);
		text-align: center;
	}
	figcaption.pick {
		color: var(--gold-copy);
	}
	figcaption.current {
		color: var(--purple);
	}
	@media (max-width: 760px) {
		.card {
			padding: 12px;
		}
		.pictures {
			--gap: 6px;
		}
	}
</style>
