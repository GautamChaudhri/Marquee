<script lang="ts">
	import type { PendingReview } from '#lib/api/types.ts';

	let { item }: { item: PendingReview } = $props();

	const count = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
</script>

<!-- One run in the gallery: only its pick, the top-ranked poster. Under it a quiet line says
     how many groups (designs) the run's ranked posters make and how many posters there are,
     then the title and year. -->
<li data-testid="review-tile">
	<a class="tile" href="/review/{item.run_id}">
		<span class="frame">
			{#if item.pick_image_url}
				<img
					src={item.pick_image_url}
					alt=""
					width="500"
					height="750"
					loading="lazy"
					decoding="async"
				/>
			{:else}
				<span class="none">No image</span>
			{/if}
			{#if item.blind || item.policy_deployed}
				<span class="marks">
					{#if item.blind}<span class="mark blind">Blind</span>{/if}
					{#if item.policy_deployed}
						<span class="mark policy" title="The deploy policy already wrote the top pick">
							Written by policy
						</span>
					{/if}
				</span>
			{/if}
		</span>
		<span class="counts" data-testid="tile-counts">
			<span class="groups">{count(item.designs, 'group')}</span>
			<span class="sep" aria-hidden="true">·</span>
			<span class="posters">{count(item.ranked ?? 0, 'poster')}</span>
		</span>
		<span class="caption">
			<span class="name">{item.title}</span>
			<span class="year">{item.year ?? ''}</span>
		</span>
	</a>
</li>

<style>
	.tile {
		display: flex;
		flex-direction: column;
		gap: 7px;
		min-width: 0;
		color: var(--text);
	}
	.frame {
		position: relative;
		display: block;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: var(--panel2);
		border: 1px solid var(--line);
	}
	.tile:hover .frame {
		border-color: var(--faint);
	}
	img {
		display: block;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.none {
		display: grid;
		place-items: center;
		height: 100%;
		color: var(--faint);
		font-size: 12px;
	}
	.marks {
		position: absolute;
		left: 6px;
		right: 6px;
		bottom: 6px;
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}
	.mark {
		padding: 1px 8px;
		border-radius: var(--radius-pill);
		font-size: 11px;
		border: 1px solid var(--line2);
		background: var(--panel);
	}
	.mark.blind {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 45%, transparent);
	}
	.mark.policy {
		color: var(--warn-copy);
		border-color: color-mix(in srgb, var(--warn) 45%, transparent);
	}
	/* Two quiet tints, so the two numbers read apart without pulling the eye off the poster. */
	.counts {
		display: flex;
		flex-wrap: wrap;
		gap: 0 6px;
		font-size: 11.5px;
		line-height: 1.3;
		font-variant-numeric: tabular-nums;
	}
	.groups {
		color: color-mix(in srgb, var(--gold) 62%, var(--muted));
	}
	.posters {
		color: color-mix(in srgb, var(--info) 62%, var(--muted));
	}
	.sep {
		color: var(--faint);
	}
	.caption {
		display: flex;
		flex-direction: column;
		gap: 1px;
		font-size: 12.5px;
		line-height: 1.3;
		min-width: 0;
	}
	.name {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.year {
		color: var(--muted);
		font-size: 11.5px;
	}
</style>
