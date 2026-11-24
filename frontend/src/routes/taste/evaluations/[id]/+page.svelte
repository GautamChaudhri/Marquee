<script lang="ts">
	import { page } from '$app/state';
	import { api, call } from '$lib/api/client';
	import type { Evaluation, EvaluationResult } from '$lib/api/types';
	import { headline, outcomeLine, progressLine, stateLine } from '$lib/features/taste/evaluation';
	import { formatWhen } from '$lib/format';
	import Button from '$lib/ui/Button.svelte';
	import Notice from '$lib/ui/Notice.svelte';

	/** One blind evaluation, a movie per row: your poster, the blind pick, and what happened. */
	const PAGE_SIZE = 50;
	type Outcome = EvaluationResult['outcome'];
	type Source = 'seed' | 'feedback';

	const evaluationId = $derived(Number(page.params.id));
	let item = $state<Evaluation | null>(null);
	let itemError = $state<string | null>(null);
	let outcome = $state<Outcome | ''>('');
	let source = $state<Source | ''>('');
	let pageNo = $state(1);
	let rows = $state<EvaluationResult[] | null>(null);
	let total = $state(0);
	let error = $state<string | null>(null);
	let loading = $state(false);
	const pages = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	async function loadItem(id: number) {
		itemError = null;
		const result = await call(
			api.GET('/api/taste/evaluations/{evaluation_id}', {
				params: { path: { evaluation_id: id } }
			})
		);
		if (id !== evaluationId) return;
		if (!result.ok) {
			itemError = result.status === 404 ? 'There is no such evaluation.' : result.message;
			return;
		}
		item = result.data;
	}

	async function loadRows(id: number, o: Outcome | '', s: Source | '', n: number) {
		loading = true;
		error = null;
		const result = await call(
			api.GET('/api/taste/evaluations/{evaluation_id}/results', {
				params: {
					path: { evaluation_id: id },
					query: {
						...(o ? { outcome: o } : {}),
						...(s ? { source: s } : {}),
						page: n,
						page_size: PAGE_SIZE
					}
				}
			})
		);
		if (id !== evaluationId || o !== outcome || s !== source || n !== pageNo) return;
		loading = false;
		if (!result.ok) {
			error = result.message;
			return;
		}
		rows = result.data.items;
		total = result.data.total;
	}

	$effect(() => {
		void loadItem(evaluationId);
	});
	$effect(() => {
		void loadRows(evaluationId, outcome, source, pageNo);
	});
</script>

<svelte:head><title>Blind evaluation · Marquee</title></svelte:head>

<a class="back" href="/taste">← Taste</a>

{#if itemError}
	<Notice tone="bad" message={itemError} />
{:else if !item}
	<Notice tone="loading" message="Loading the evaluation…" />
{:else}
	<section class="summary" data-testid="evaluation-summary">
		<h1>Blind evaluation {item.id}</h1>
		<p class="line">
			{formatWhen(item.created_at)} · {item.queued}
			{item.queued === 1 ? 'movie' : 'movies'} · ranked by the {item.scorer} scorer · profile
			<code>{item.profile_sha256.slice(0, 12)}</code>
		</p>
		{#if item.state === 'running'}
			<p class="line" role="status">Still running: {progressLine(item)}</p>
		{/if}
		{#if stateLine(item)}<p class="line warn">{stateLine(item)}</p>{/if}
		<p class="strong">{headline(item.summary.seed)}</p>
		{#if item.summary.feedback.evaluated > 0}
			<p class="line">
				Posters you chose from Marquee’s picks: {item.summary.feedback.same_design} of {item.summary
					.feedback.evaluated} were the blind pick’s design.
			</p>
		{/if}
	</section>

	<div class="filters">
		<label class="field">
			What happened
			<select class="control" bind:value={outcome} onchange={() => (pageNo = 1)}>
				<option value="">Any</option>
				<option value="ranked">Ranked</option>
				<option value="gated">Removed by a gate</option>
				<option value="not_offered">Not among the candidates</option>
				<option value="failed">Failed</option>
			</select>
		</label>
		<label class="field">
			Your poster
			<select class="control" bind:value={source} onchange={() => (pageNo = 1)}>
				<option value="">Any</option>
				<option value="seed">Picked by hand</option>
				<option value="feedback">Chosen in a review</option>
			</select>
		</label>
		{#if rows !== null}<span class="count">{total} {total === 1 ? 'movie' : 'movies'}</span>{/if}
	</div>

	{#if error}
		<Notice tone="bad" message="The results could not be loaded. {error}" />
	{:else if rows === null}
		<Notice tone="loading" message="Loading the results…" />
	{:else if rows.length === 0}
		<Notice message={outcome || source ? 'No movie matches.' : 'No result yet.'} />
	{:else}
		<ul class="rows" aria-busy={loading}>
			{#each rows as row (row.movie_id)}
				<li data-testid="evaluation-result" class={row.outcome}>
					<figure>
						{#if row.exemplar_image_url}
							<img src={row.exemplar_image_url} alt="Your poster of {row.title}" loading="lazy" />
						{:else}<div class="blank"></div>{/if}
						<figcaption>Yours</figcaption>
					</figure>
					<figure>
						{#if row.pick_image_url}
							<img src={row.pick_image_url} alt="The blind pick for {row.title}" loading="lazy" />
						{:else}<div class="blank"></div>{/if}
						<figcaption>Blind pick</figcaption>
					</figure>
					<div class="text">
						<span class="title">{row.title} <span class="year">{row.year ?? ''}</span></span>
						<span class="outcome">{outcomeLine(row)}</span>
						<span class="meta">
							{row.exemplar_source === 'feedback' ? 'Chosen in a review' : 'Picked by hand'}
							{#if row.matched_by}
								· found by {row.matched_by === 'file'
									? 'its TMDB file'
									: 'likeness'}{row.match_cosine != null
									? ` (${row.match_cosine.toFixed(2)})`
									: ''}{/if}
						</span>
						{#if row.run_id}
							<a href="/review/{row.run_id}">Open the masked run</a>
						{:else}
							<span class="meta">Its run was replaced by a newer evaluation.</span>
						{/if}
					</div>
				</li>
			{/each}
		</ul>
		{#if pages > 1}
			<nav class="pager" aria-label="Result pages">
				<Button disabled={pageNo <= 1 || loading} onclick={() => (pageNo -= 1)}>Previous</Button>
				<span>Page {pageNo} of {pages}</span>
				<Button disabled={pageNo >= pages || loading} onclick={() => (pageNo += 1)}>Next</Button>
			</nav>
		{/if}
	{/if}
{/if}

<style>
	.back {
		display: inline-block;
		margin-bottom: 14px;
		color: var(--gold-copy);
		font-size: 13px;
	}
	h1 {
		margin: 0 0 6px;
		font-size: 18px;
	}
	.summary {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin-bottom: 18px;
	}
	.line {
		margin: 0;
		font-size: 13px;
		color: var(--muted);
	}
	.line.warn {
		color: var(--warn-copy);
	}
	.strong {
		margin: 0;
		font-size: 14px;
	}
	code {
		font-family: var(--font-mono);
		font-size: 12px;
	}
	.filters {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 10px 14px;
		margin-bottom: 12px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.count {
		margin-left: auto;
	}
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr));
		gap: 12px;
	}
	li {
		display: grid;
		grid-template-columns: 84px 84px minmax(0, 1fr);
		gap: 10px;
		padding: 10px;
		border-radius: var(--radius);
		border: 1px solid var(--line);
		background: var(--panel);
	}
	li.gated,
	li.not_offered,
	li.failed {
		border-color: var(--warn);
	}
	figure {
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	img,
	.blank {
		width: 100%;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--panel2);
	}
	figcaption {
		font-size: 11px;
		color: var(--muted);
		text-align: center;
	}
	.text {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
		font-size: 13px;
	}
	.title {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.year,
	.meta {
		color: var(--muted);
		font-weight: 400;
	}
	.text a {
		color: var(--gold-copy);
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
	@media (max-width: 480px) {
		li {
			grid-template-columns: 64px 64px minmax(0, 1fr);
		}
	}
</style>
