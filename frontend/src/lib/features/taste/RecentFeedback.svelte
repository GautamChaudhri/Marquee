<script lang="ts">
	import { api, call } from '$lib/api/client';
	import type { FeedbackListItem } from '$lib/api/types';
	import { canUndo, eventLine, movieLabel } from '$lib/features/review/review';
	import { formatWhen } from '$lib/format';
	import Button from '$lib/ui/Button.svelte';
	import Notice from '$lib/ui/Notice.svelte';

	/** The newest acts of feedback: what was done, to which movie, and when, with Undo where
	 *  it applies and a link to the run's review. */
	let { onChanged }: { onChanged: () => void } = $props();

	const PAGE_SIZE = 20;
	let items = $state<FeedbackListItem[] | null>(null);
	let total = $state(0);
	let pageNo = $state(1);
	let error = $state<string | null>(null);
	let busy = $state<number | null>(null);
	let note = $state<{ text: string; movieId: number } | null>(null);
	let undoError = $state<string | null>(null);

	const pages = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	async function load(n: number) {
		error = null;
		const result = await call(
			api.GET('/api/feedback', { params: { query: { page: n, page_size: PAGE_SIZE } } })
		);
		if (n !== pageNo) return;
		if (!result.ok) {
			error = result.message;
			return;
		}
		items = result.data.items;
		total = result.data.total;
	}

	$effect(() => {
		void load(pageNo);
	});

	async function undo(item: FeedbackListItem) {
		busy = item.id;
		note = null;
		undoError = null;
		const result = await call(
			api.POST('/api/feedback/{event_id}/revoke', { params: { path: { event_id: item.id } } })
		);
		busy = null;
		if (!result.ok) {
			undoError = result.message;
			return;
		}
		note = {
			text: `Answer ${item.id} on ${movieLabel(item)} was undone. The poster in the folder was not put back.`,
			movieId: item.movie_id
		};
		await load(pageNo);
		onChanged();
	}
</script>

{#if note}
	<p class="note" role="status">
		{note.text} <a href="/movies/{note.movieId}">Restore it from the movie’s poster history</a>.
	</p>
{/if}
{#if undoError}<Notice tone="bad" message="The answer was not undone. {undoError}" />{/if}

{#if error}
	<Notice tone="bad" message="The recent feedback could not be loaded. {error}">
		{#snippet action()}<Button onclick={() => load(pageNo)}>Try again</Button>{/snippet}
	</Notice>
{:else if items === null}
	<Notice tone="loading" message="Loading the recent feedback…" />
{:else if items.length === 0}
	<Notice message="No feedback yet. Answer a run on the Review page." />
{:else}
	<ol class="feedback" data-testid="recent-feedback">
		{#each items as item (item.id)}
			<li class:revoked={item.revoked_by}>
				<a class="movie" href="/movies/{item.movie_id}">{movieLabel(item)}</a>
				<span class="what">{eventLine(item)}</span>
				<span class="when">answer {item.id} · {formatWhen(item.created_at)}</span>
				{#if item.revoked_by}<span class="when">Undone by answer {item.revoked_by}</span>{/if}
				<span class="links">
					{#if item.run_id}<a href="/review/{item.run_id}">Run {item.run_id}</a>{/if}
					{#if canUndo(item)}
						<Button busy={busy === item.id} disabled={busy !== null} onclick={() => undo(item)}>
							Undo
						</Button>
					{/if}
				</span>
			</li>
		{/each}
	</ol>
	{#if pages > 1}
		<nav class="pager" aria-label="Feedback pages">
			<Button disabled={pageNo <= 1} onclick={() => (pageNo -= 1)}>Newer</Button>
			<span>Page {pageNo} of {pages}</span>
			<Button disabled={pageNo >= pages} onclick={() => (pageNo += 1)}>Older</Button>
		</nav>
	{/if}
{/if}

<style>
	.note {
		margin: 0 0 8px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.note a,
	.links a {
		color: var(--gold-copy);
	}
	.feedback {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 12px;
		padding: 8px 10px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--panel);
		font-size: 13px;
	}
	.movie {
		font-weight: 600;
		color: var(--text);
	}
	.what {
		overflow-wrap: anywhere;
	}
	li.revoked .what {
		text-decoration: line-through;
		color: var(--muted);
	}
	.when {
		font-size: 12px;
		color: var(--muted);
	}
	.links {
		margin-left: auto;
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 12.5px;
	}
	.pager {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 12px;
		margin-top: 12px;
		font-size: 12.5px;
		color: var(--muted);
	}
</style>
