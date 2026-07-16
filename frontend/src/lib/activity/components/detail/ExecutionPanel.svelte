<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { listAttempts } from '../../client';
	import type { AttemptItem } from '../../types';

	let { jobId }: { jobId: string } = $props();
	let items = $state<AttemptItem[]>([]);
	let cursor = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let controller: AbortController | null = null;

	async function loadMore() {
		if (loading) return;
		loading = true;
		controller?.abort();
		controller = new AbortController();
		try {
			const page = await listAttempts(
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
				error = reason instanceof Error ? reason.message : 'Execution audit unavailable';
		} finally {
			loading = false;
		}
	}

	onMount(loadMore);
	onDestroy(() => controller?.abort());
</script>

<section aria-labelledby="execution-heading">
	<h2 id="execution-heading">Execution</h2>
	<p class="hint">Worker admission and process audit. This panel is diagnostic, not job meaning.</p>
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	{#if items.length === 0 && !loading}<p class="empty">No attempts have been admitted.</p>{/if}
	<div class="attempts">
		{#each items as attempt (attempt.number)}<details>
				<summary
					><strong>Attempt #{attempt.number}</strong><span
						>{attempt.phase}{attempt.outcome ? ` · ${attempt.outcome}` : ''}</span
					></summary
				>
				<dl>
					<div>
						<dt>Worker</dt>
						<dd>{attempt.worker_node_id ?? 'unassigned'}</dd>
					</div>
					<div>
						<dt>Build</dt>
						<dd>{attempt.worker_build ?? 'unknown'}</dd>
					</div>
					<div>
						<dt>Admitted</dt>
						<dd>{attempt.admitted_at ? new Date(attempt.admitted_at).toLocaleString() : '—'}</dd>
					</div>
					<div>
						<dt>Started</dt>
						<dd>{attempt.started_at ? new Date(attempt.started_at).toLocaleString() : '—'}</dd>
					</div>
					<div>
						<dt>Finished</dt>
						<dd>{attempt.finished_at ? new Date(attempt.finished_at).toLocaleString() : '—'}</dd>
					</div>
					<div>
						<dt>Exit</dt>
						<dd>{attempt.exit_code ?? attempt.exit_signal ?? '—'}</dd>
					</div>
					<div>
						<dt>Failure class</dt>
						<dd>{attempt.failure_class ?? '—'}</dd>
					</div>
				</dl>
			</details>{/each}
	</div>
	{#if cursor}<button onclick={loadMore} disabled={loading}
			>{loading ? 'Loading…' : 'Load more attempts'}</button
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
	.attempts {
		display: grid;
		gap: 8px;
	}
	details {
		content-visibility: auto;
		contain-intrinsic-size: auto 48px;
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 12px;
	}
	summary {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		cursor: pointer;
	}
	summary span {
		color: var(--muted);
	}
	dl {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
		gap: 10px;
		margin: 14px 0 0;
	}
	dt {
		color: var(--faint2);
		font-size: 10px;
		text-transform: uppercase;
	}
	dd {
		margin: 2px 0 0;
		overflow-wrap: anywhere;
	}
	button {
		justify-self: start;
	}
</style>
