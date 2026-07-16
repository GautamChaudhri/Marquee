<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { listArtifacts } from '../../client';
	import type { ArtifactItemResponse } from '../../types';

	let { jobId }: { jobId: string } = $props();
	let items = $state<ArtifactItemResponse[]>([]);
	let cursor = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let controller: AbortController | null = null;

	function size(bytes: number | null): string {
		if (bytes === null) return 'size unavailable';
		if (bytes < 1024) return `${bytes} B`;
		return `${(bytes / 1024).toFixed(1)} KiB`;
	}

	async function loadMore() {
		if (loading) return;
		loading = true;
		controller?.abort();
		controller = new AbortController();
		try {
			const page = await listArtifacts(
				fetch,
				jobId,
				{ cursor: cursor ?? undefined, limit: 50 },
				controller.signal
			);
			items = [...items, ...page.items];
			cursor = page.next_cursor;
			error = null;
		} catch (reason) {
			if (!controller.signal.aborted)
				error = reason instanceof Error ? reason.message : 'Artifacts unavailable';
		} finally {
			loading = false;
		}
	}

	onMount(loadMore);
	onDestroy(() => controller?.abort());
</script>

<section aria-labelledby="artifacts-heading">
	<h2 id="artifacts-heading">Artifacts</h2>
	<p class="hint">
		Retained evidence and outputs. Availability and expiry are server authoritative.
	</p>
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	{#if items.length === 0 && !loading}<p class="empty">
			No artifacts are retained for this job.
		</p>{/if}
	<div class="artifacts">
		{#each items as item (item.id)}<article>
				<div>
					<strong>{item.name}</strong><span
						>{item.kind} · {size(item.size_bytes)} · {item.retention_class}</span
					>{#if item.expires_at}<span>Expires {new Date(item.expires_at).toLocaleString()}</span
						>{/if}
				</div>
				<span class:available={item.available}>{item.status}</span
				>{#if item.download_url && item.available}<a href={item.download_url}>Download</a>{/if}
			</article>{/each}
	</div>
	{#if cursor}<button onclick={loadMore} disabled={loading}
			>{loading ? 'Loading…' : 'Load more artifacts'}</button
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
	.artifacts {
		display: grid;
		gap: 8px;
	}
	article {
		content-visibility: auto;
		contain-intrinsic-size: auto 64px;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto auto;
		gap: 16px;
		align-items: center;
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 12px;
	}
	article div {
		display: grid;
		gap: 3px;
		min-width: 0;
	}
	article span {
		color: var(--muted);
		font-size: 11px;
		overflow-wrap: anywhere;
	}
	article > span.available {
		color: var(--good);
	}
	a {
		color: var(--gold);
	}
	button {
		justify-self: start;
	}
	@media (max-width: 640px) {
		article {
			grid-template-columns: 1fr auto;
		}
		article a {
			grid-column: 1 / -1;
		}
	}
</style>
