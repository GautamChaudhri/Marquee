<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { getRawDocument } from '../../client';
	import type { RawDocumentKind } from '../../types';

	let { jobId }: { jobId: string } = $props();
	const kinds: RawDocumentKind[] = ['request', 'plan', 'result', 'error'];
	let kind = $state<RawDocumentKind>('request');
	let document = $state<unknown>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let controller: AbortController | null = null;

	async function load(next: RawDocumentKind) {
		kind = next;
		loading = true;
		document = null;
		controller?.abort();
		controller = new AbortController();
		try {
			document = await getRawDocument(fetch, jobId, kind, controller.signal);
			error = null;
		} catch (reason) {
			if (!controller.signal.aborted)
				error = reason instanceof Error ? reason.message : `${kind} data unavailable`;
		} finally {
			loading = false;
		}
	}

	onMount(() => load(kind));
	onDestroy(() => controller?.abort());
</script>

<section aria-labelledby="raw-heading">
	<div class="heading">
		<div>
			<h2 id="raw-heading">Raw Data</h2>
			<p>Redacted canonical documents for diagnostics; not used to explain job semantics.</p>
		</div>
		<a href={`/api/jobs/${jobId}/raw/${kind}?download=true`}>Download {kind}</a>
	</div>
	<div class="kinds" role="tablist" aria-label="Raw document kind">
		{#each kinds as candidate (candidate)}<button
				role="tab"
				aria-selected={kind === candidate}
				onclick={() => load(candidate)}>{candidate}</button
			>{/each}
	</div>
	{#if loading}<p>Loading {kind}…</p>{/if}
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	{#if document !== null}<pre>{JSON.stringify(document, null, 2)}</pre>{/if}
</section>

<style>
	section {
		display: grid;
		gap: 12px;
	}
	.heading {
		display: flex;
		justify-content: space-between;
		gap: 12px;
	}
	h2,
	p {
		margin: 0;
	}
	.heading p {
		color: var(--muted);
		font-size: 12px;
	}
	a {
		color: var(--gold);
		font-size: 12px;
	}
	.kinds {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	button[aria-selected='true'] {
		border-color: var(--gold);
		color: var(--gold);
	}
	.error {
		color: var(--bad);
	}
	pre {
		max-height: 60vh;
		overflow: auto;
		margin: 0;
		padding: 14px;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font: 11px var(--font-mono);
	}
	@media (max-width: 640px) {
		.heading {
			display: grid;
		}
	}
</style>
