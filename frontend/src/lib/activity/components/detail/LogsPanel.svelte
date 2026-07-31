<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { listAttemptLogs, listAttempts } from '../../client';
	import type { AttemptItem, AttemptLogLine, AttemptLogPage } from '../../types';

	let { jobId, contained = false }: { jobId: string; contained?: boolean } = $props();
	let attempts = $state<AttemptItem[]>([]);
	let selected = $state<AttemptItem | null>(null);
	let lines = $state<AttemptLogLine[]>([]);
	let pageMeta = $state<AttemptLogPage | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let controller: AbortController | null = null;

	async function choose(attempt: AttemptItem) {
		selected = attempt;
		lines = [];
		pageMeta = null;
		await loadMore();
	}

	async function loadMore() {
		if (selected === null || loading) return;
		loading = true;
		error = null;
		controller?.abort();
		controller = new AbortController();
		try {
			const page = await listAttemptLogs(
				fetch,
				selected.origin_job_id,
				selected.number,
				{
					after: pageMeta?.next_cursor ?? undefined,
					limit: 200
				},
				controller.signal
			);
			lines = [...lines, ...page.items];
			pageMeta = page;
		} catch (reason) {
			if (!controller.signal.aborted)
				error = reason instanceof Error ? reason.message : 'Logs unavailable';
		} finally {
			loading = false;
		}
	}

	onMount(async () => {
		try {
			controller = new AbortController();
			const response = await listAttempts(
				fetch,
				jobId,
				{ limit: 50, scope: contained ? 'contained' : 'self' },
				controller.signal
			);
			attempts = response.items;
			if (attempts.length) await choose(attempts[0]);
		} catch (reason) {
			error = reason instanceof Error ? reason.message : 'Attempts unavailable';
		}
	});
	onDestroy(() => controller?.abort());

	function attemptKey(attempt: AttemptItem): string {
		return `${attempt.origin_job_id}:${attempt.number}`;
	}

	function originName(attempt: AttemptItem): string {
		const value = attempt.origin_subject.display_name ?? attempt.origin_subject.title;
		return typeof value === 'string' && value ? value : attempt.origin_job_id;
	}
</script>

<section aria-labelledby="logs-heading">
	<div class="heading">
		<div>
			<h2 id="logs-heading">Logs</h2>
			<p>Bounded retained output for one execution attempt.</p>
		</div>
		{#if selected !== null}<a
				class="download"
				href={`/api/jobs/${selected.origin_job_id}/attempts/${selected.number}/logs/download`}
				>Download attempt log</a
			>{/if}
	</div>
	{#if attempts.length}<label
			>Attempt<select
				value={selected ? attemptKey(selected) : ''}
				onchange={(event) => {
					const attempt = attempts.find((item) => attemptKey(item) === event.currentTarget.value);
					if (attempt) void choose(attempt);
				}}
				>{#each attempts as attempt (attemptKey(attempt))}<option value={attemptKey(attempt)}
						>{contained ? `${originName(attempt)} · ` : ''}#${attempt.number} · {attempt.phase}{attempt.outcome
							? ` · ${attempt.outcome}`
							: ''}</option
					>{/each}</select
			></label
		>{/if}
	{#if pageMeta}<div class="retention" role="status">
			<span
				>{pageMeta.freshness} · {pageMeta.sealed ? 'sealed' : 'active'} · {pageMeta.compression}</span
			>{#if pageMeta.truncated}<strong>Earlier output was truncated.</strong
				>{/if}{#if pageMeta.expires_at}<span
					>Retained until {new Date(pageMeta.expires_at).toLocaleString()}</span
				>{/if}
		</div>{/if}
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	{#if !attempts.length && !error}<p class="empty">No execution attempts have logs.</p>{/if}
	<div class="logs" role="log" aria-live="off">
		{#each lines as line (line.cursor)}<div class={`line ${line.level}`}>
				<time datetime={line.timestamp}>{new Date(line.timestamp).toLocaleTimeString()}</time><span
					>{line.source}</span
				>{#if line.stage}<span>{line.stage}</span>{/if}
				<pre>{line.message}</pre>
			</div>{/each}
	</div>
	{#if pageMeta?.next_cursor !== null && pageMeta}<button onclick={loadMore} disabled={loading}
			>{loading ? 'Loading…' : 'Load more lines'}</button
		>{/if}
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
		align-items: start;
	}
	h2,
	p {
		margin: 0;
	}
	.heading p,
	.empty {
		color: var(--muted);
		font-size: 12px;
	}
	.download {
		color: var(--gold);
		font-size: 12px;
	}
	label {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.retention {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 16px;
		color: var(--muted);
		font-size: 12px;
	}
	.retention strong,
	.error {
		color: var(--warn);
	}
	.logs {
		max-height: 60vh;
		overflow: auto;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		font: 11px var(--font-mono);
	}
	.line {
		content-visibility: auto;
		contain-intrinsic-size: auto 30px;
		display: grid;
		grid-template-columns: 80px 55px 100px minmax(0, 1fr);
		gap: 8px;
		padding: 6px 8px;
		border-bottom: 1px solid var(--line2);
	}
	.line time,
	.line > span {
		color: var(--faint);
	}
	.line.error pre {
		color: var(--bad);
	}
	.line.warning pre {
		color: var(--warn);
	}
	pre {
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		color: var(--text);
		font: inherit;
	}
	button {
		justify-self: start;
	}
	@media (max-width: 640px) {
		.heading {
			display: grid;
		}
		.line {
			grid-template-columns: 1fr 1fr;
		}
		.line pre {
			grid-column: 1 / -1;
		}
	}
</style>
