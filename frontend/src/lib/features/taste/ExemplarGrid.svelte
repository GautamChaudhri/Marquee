<script lang="ts">
	import { api, call } from '#lib/api/client.ts';
	import type { Exemplar } from '#lib/api/types.ts';
	import Button from '#lib/ui/Button.svelte';
	import Notice from '#lib/ui/Notice.svelte';
	import { exemplarEnding, exemplarSource } from './taste';

	/** The exemplars, newest first, a page at a time, filtered by polarity and state. */
	const PAGE_SIZE = 48;
	type Polarity = 'positive' | 'negative';
	type State = 'active' | 'superseded' | 'revoked';

	let polarity = $state<Polarity | ''>('');
	let exemplarState = $state<State | ''>('');
	let pageNo = $state(1);
	let items = $state<Exemplar[] | null>(null);
	let total = $state(0);
	let error = $state<string | null>(null);
	let loading = $state(false);

	const pages = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	async function load(p: Polarity | '', s: State | '', n: number) {
		loading = true;
		error = null;
		const result = await call(
			api.GET('/api/taste/exemplars', {
				params: {
					query: {
						...(p ? { polarity: p } : {}),
						...(s ? { state: s } : {}),
						page: n,
						page_size: PAGE_SIZE
					}
				}
			})
		);
		if (p !== polarity || s !== exemplarState || n !== pageNo) return;
		loading = false;
		if (!result.ok) {
			error = result.message;
			return;
		}
		items = result.data.items;
		total = result.data.total;
	}

	$effect(() => {
		void load(polarity, exemplarState, pageNo);
	});

	/** Read the first page again, as after an undo or a rebuild. */
	export function reload() {
		void load(polarity, exemplarState, pageNo);
	}
</script>

<div class="filters">
	<label class="field">
		Polarity
		<select class="control" bind:value={polarity} onchange={() => (pageNo = 1)}>
			<option value="">Any</option>
			<option value="positive">Liked</option>
			<option value="negative">Disliked</option>
		</select>
	</label>
	<label class="field">
		State
		<select class="control" bind:value={exemplarState} onchange={() => (pageNo = 1)}>
			<option value="">Any</option>
			<option value="active">Active</option>
			<option value="superseded">Replaced</option>
			<option value="revoked">Revoked</option>
		</select>
	</label>
	{#if items !== null}<span class="count">{total} {total === 1 ? 'exemplar' : 'exemplars'}</span
		>{/if}
</div>

{#if error}
	<Notice tone="bad" message="The exemplars could not be loaded. {error}">
		{#snippet action()}<Button onclick={reload}>Try again</Button>{/snippet}
	</Notice>
{:else if items === null}
	<Notice tone="loading" message="Loading the exemplars…" />
{:else if items.length === 0}
	<Notice message={polarity || exemplarState ? 'No exemplar matches.' : 'No exemplars yet.'} />
{:else}
	<ul class="grid" data-testid="exemplars" aria-busy={loading}>
		{#each items as exemplar (exemplar.id)}
			{@const ending = exemplarEnding(exemplar)}
			<li class:ended={ending} data-testid="exemplar">
				<img
					src={exemplar.image_url}
					alt="Exemplar of {exemplar.title}"
					width="500"
					height="750"
					loading="lazy"
				/>
				<span class="title">{exemplar.title} <span class="year">{exemplar.year ?? ''}</span></span>
				<span class="meta">
					<span
						class:liked={exemplar.polarity === 'positive'}
						class:disliked={exemplar.polarity === 'negative'}
					>
						{exemplar.polarity === 'positive' ? 'Liked' : 'Disliked'}
					</span>
					· weight {exemplar.weight.toFixed(2)}
				</span>
				<span class="meta">{exemplarSource(exemplar)}</span>
				{#if ending}<span class="meta ending">{ending}</span>{/if}
			</li>
		{/each}
	</ul>
	{#if pages > 1}
		<nav class="pager" aria-label="Exemplar pages">
			<Button disabled={pageNo <= 1 || loading} onclick={() => (pageNo -= 1)}>Newer</Button>
			<span>Page {pageNo} of {pages}</span>
			<Button disabled={pageNo >= pages || loading} onclick={() => (pageNo += 1)}>Older</Button>
		</nav>
	{/if}
{/if}

<style>
	.filters {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 10px 14px;
		margin-bottom: 10px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.count {
		margin-left: auto;
	}
	.grid {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
		gap: 14px 10px;
	}
	li {
		display: flex;
		flex-direction: column;
		gap: 3px;
		min-width: 0;
		font-size: 12px;
	}
	li.ended img {
		opacity: 0.55;
	}
	img {
		width: 100%;
		height: auto;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--panel2);
		margin-bottom: 3px;
	}
	.title {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.year {
		color: var(--muted);
		font-weight: 400;
	}
	.meta {
		color: var(--muted);
	}
	.liked {
		color: var(--good);
	}
	.disliked {
		color: var(--bad);
	}
	.ending {
		color: var(--warn-copy);
	}
	.pager {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 12px;
		margin-top: 14px;
		font-size: 12.5px;
		color: var(--muted);
	}
</style>
