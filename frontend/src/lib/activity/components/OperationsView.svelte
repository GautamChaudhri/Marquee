<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { getOperations, getOperationsHistory } from '../client';
	import type { OperationsHistoryResponse, OperationsSnapshot } from '../types';

	type Window = '15m' | '1h' | '6h' | '24h';
	let snapshot = $state<OperationsSnapshot | null>(null);
	let history = $state<OperationsHistoryResponse | null>(null);
	let historyWindow = $state<Window>('1h');
	let loading = $state(false);
	let historyLoading = $state(false);
	let error = $state<string | null>(null);
	let snapshotController: AbortController | null = null;
	let historyController: AbortController | null = null;

	function percent(value: number | null | undefined): string {
		return value === null || value === undefined ? 'Unavailable' : `${value.toFixed(1)}%`;
	}

	function bytes(value: number | null | undefined): string {
		if (value === null || value === undefined) return 'Unavailable';
		if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
		if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
		return `${(value / 1024 ** 3).toFixed(1)} GiB`;
	}

	async function refresh() {
		if (loading) return;
		loading = true;
		snapshotController?.abort();
		snapshotController = new AbortController();
		try {
			snapshot = await getOperations(fetch, snapshotController.signal);
			error = null;
		} catch (reason) {
			if (!snapshotController.signal.aborted) {
				error = reason instanceof Error ? reason.message : 'Operations snapshot unavailable';
			}
		} finally {
			loading = false;
		}
	}

	async function loadHistory() {
		if (historyLoading) return;
		historyLoading = true;
		historyController?.abort();
		historyController = new AbortController();
		try {
			history = await getOperationsHistory(
				fetch,
				{ window: historyWindow, resolution: 120 },
				historyController.signal
			);
			error = null;
		} catch (reason) {
			if (!historyController.signal.aborted) {
				error = reason instanceof Error ? reason.message : 'Operations history unavailable';
			}
		} finally {
			historyLoading = false;
		}
	}

	onMount(refresh);
	onDestroy(() => {
		snapshotController?.abort();
		historyController?.abort();
	});
</script>

<section class="operations" aria-labelledby="operations-title">
	<header>
		<div>
			<p class="eyebrow">Secondary view · loaded on demand</p>
			<h2 id="operations-title">Operations</h2>
			<p>Bounded host and transport diagnostics remain separate from Queue and History.</p>
		</div>
		<button onclick={refresh} disabled={loading}
			>{loading ? 'Refreshing…' : 'Refresh snapshot'}</button
		>
	</header>
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	{#if !snapshot && loading}<p class="state">Loading Operations snapshot…</p>{/if}

	{#if snapshot}
		<p class="generated">
			Generated <time datetime={snapshot.generated_at}
				>{new Date(snapshot.generated_at).toLocaleString()}</time
			>
			· contract v{snapshot.version}
		</p>
		<div class="panels">
			<article>
				<h3>Node</h3>
				<strong>{snapshot.node.cpu_model}</strong>
				<dl>
					<div>
						<dt>CPU</dt>
						<dd>{percent(snapshot.node.cpu_percent)}</dd>
					</div>
					<div>
						<dt>RAM</dt>
						<dd>{percent(snapshot.node.ram_percent)}</dd>
					</div>
					<div>
						<dt>GPU</dt>
						<dd>{snapshot.node.gpu_model ?? 'Not available'}</dd>
					</div>
					<div>
						<dt>GPU utilization</dt>
						<dd>{percent(snapshot.node.gpu_percent)}</dd>
					</div>
					<div>
						<dt>Uptime</dt>
						<dd>{snapshot.node.uptime}</dd>
					</div>
				</dl>
			</article>
			<article>
				<h3>Workers & transport</h3>
				<dl>
					<div>
						<dt>Active</dt>
						<dd>{snapshot.workers.active}</dd>
					</div>
					<div>
						<dt>Queued</dt>
						<dd>{snapshot.workers.queued}</dd>
					</div>
					<div>
						<dt>Listener</dt>
						<dd>{snapshot.events.listener_healthy ? 'Healthy' : 'Needs attention'}</dd>
					</div>
					<div>
						<dt>Picked</dt>
						<dd>{snapshot.transport.picked}</dd>
					</div>
					<div>
						<dt>Held failed</dt>
						<dd>{snapshot.transport.held_failed}</dd>
					</div>
					<div>
						<dt>Oldest eligible</dt>
						<dd>
							{snapshot.transport.oldest_eligible_age_seconds == null
								? 'None'
								: `${snapshot.transport.oldest_eligible_age_seconds.toFixed(1)}s`}
						</dd>
					</div>
				</dl>
			</article>
			<article>
				<h3>Database & events</h3>
				<dl>
					<div>
						<dt>Connections</dt>
						<dd>{snapshot.database.observed_connections}</dd>
					</div>
					<div>
						<dt>Pool checked out</dt>
						<dd>{snapshot.database.pool_checked_out ?? 'Unavailable'}</dd>
					</div>
					<div>
						<dt>Configured budget</dt>
						<dd>{snapshot.database.budget.configured}/{snapshot.database.budget.maximum}</dd>
					</div>
					<div>
						<dt>Within budget</dt>
						<dd>{snapshot.database.budget.within_budget ? 'Yes' : 'No'}</dd>
					</div>
					<div>
						<dt>Event source</dt>
						<dd>{snapshot.events.source}</dd>
					</div>
					<div>
						<dt>Schema contracts</dt>
						<dd>{snapshot.contracts?.length ?? 0}</dd>
					</div>
				</dl>
			</article>
			<article>
				<h3>Storage</h3>
				<dl>
					<div>
						<dt>Disk</dt>
						<dd>{percent(snapshot.storage.disk_percent)}</dd>
					</div>
					<div>
						<dt>Disk used</dt>
						<dd>{bytes(snapshot.storage.disk_used_bytes)}</dd>
					</div>
					<div>
						<dt>Poster cache</dt>
						<dd>{snapshot.storage.poster_cache_items} items</dd>
					</div>
					<div>
						<dt>Cache size</dt>
						<dd>{bytes(snapshot.storage.poster_cache_bytes)}</dd>
					</div>
					<div>
						<dt>Network received</dt>
						<dd>{bytes(snapshot.node.network_received_bytes)}</dd>
					</div>
					<div>
						<dt>Network sent</dt>
						<dd>{bytes(snapshot.node.network_sent_bytes)}</dd>
					</div>
				</dl>
			</article>
		</div>

		<div class="history-controls">
			<label
				>History window<select bind:value={historyWindow}
					><option value="15m">15 minutes</option><option value="1h">1 hour</option><option
						value="6h">6 hours</option
					><option value="24h">24 hours</option></select
				></label
			><button onclick={loadHistory} disabled={historyLoading}
				>{historyLoading ? 'Loading…' : history ? 'Reload history' : 'Load bounded history'}</button
			>
		</div>
		{#if history}
			<div class="history" aria-label="Operations history">
				<p>
					{history.points.length} downsampled points · {history.jobs.length} overlapping jobs{history.jobs_truncated
						? ' (job overlay truncated)'
						: ''}
				</p>
				<div class="history-rows">
					{#each history.points as point (point.ts)}<div>
							<time datetime={point.ts}>{new Date(point.ts).toLocaleTimeString()}</time><span
								>CPU {percent(point.cpu_avg)}</span
							><span>RAM {percent(point.ram_pct)}</span><span>GPU {percent(point.gpu_util)}</span
							><span>{point.active_jobs} active jobs</span>
						</div>{/each}
				</div>
			</div>
		{/if}
	{/if}
</section>

<style>
	.operations {
		display: grid;
		gap: 16px;
	}
	header {
		display: flex;
		justify-content: space-between;
		gap: 16px;
		align-items: start;
	}
	h2,
	h3,
	p {
		margin: 0;
	}
	header > div {
		display: grid;
		gap: 4px;
	}
	header > div > p:last-child,
	.generated,
	.state {
		color: var(--muted);
		font-size: 12px;
	}
	.eyebrow {
		color: var(--gold);
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	.error {
		color: var(--bad);
	}
	button,
	select {
		min-height: 36px;
		padding: 7px 11px;
		border: 1px solid var(--gold-deep);
		border-radius: 7px;
		background: var(--gold-soft);
		color: var(--gold);
	}
	select {
		border-color: var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	button:focus-visible,
	select:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	button:disabled {
		opacity: 0.65;
	}
	.panels {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}
	article {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px;
	}
	h3 {
		margin-bottom: 10px;
		font-size: 13px;
	}
	article > strong {
		display: block;
		margin-bottom: 10px;
		overflow-wrap: anywhere;
	}
	dl {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 9px;
		margin: 0;
	}
	dt {
		color: var(--muted);
		font-size: 10px;
		text-transform: uppercase;
	}
	dd {
		margin: 2px 0 0;
		overflow-wrap: anywhere;
	}
	.history-controls {
		display: flex;
		gap: 10px;
		align-items: end;
	}
	label {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: 11px;
	}
	.history {
		display: grid;
		gap: 8px;
	}
	.history > p {
		color: var(--muted);
		font-size: 12px;
	}
	.history-rows {
		max-height: 360px;
		overflow: auto;
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	.history-rows > div {
		content-visibility: auto;
		contain-intrinsic-size: auto 35px;
		display: grid;
		grid-template-columns: 100px repeat(4, 1fr);
		gap: 10px;
		padding: 8px 10px;
		border-bottom: 1px solid var(--line2);
		font-size: 11px;
	}
	.history-rows time {
		color: var(--muted);
		font-family: var(--font-mono);
	}
	@media (max-width: 760px) {
		header,
		.history-controls {
			display: grid;
		}
		.panels {
			grid-template-columns: 1fr;
		}
		.history-rows > div {
			grid-template-columns: 1fr 1fr;
		}
	}
</style>
