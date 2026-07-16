<script lang="ts">
	import { getGenerators } from '$lib/api/subtitle-generators';
	import type { SubtitleGenerator } from '$lib/api/types';
	import StatusDot from '../StatusDot.svelte';

	let generators = $state<SubtitleGenerator[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function loadStatus() {
		loading = true;
		error = null;
		try {
			const res = await getGenerators(fetch);
			generators = res.generators || [];
		} catch (e: any) {
			error = e.message || 'Failed to fetch subtitle generators status';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		loadStatus();
	});
</script>

<div class="subgen-status-container">
	<div class="header">
		<h4>AI Subtitle Generators</h4>
		<button class="btn-refresh" onclick={loadStatus} disabled={loading}>
			{loading ? 'Refreshing...' : '🔄 Refresh'}
		</button>
	</div>

	{#if loading && generators.length === 0}
		<div class="skeleton-card">
			<div class="sk-line"></div>
			<div class="sk-line body"></div>
		</div>
	{:else if error}
		<div class="error-box">
			<p class="error-msg">⚠️ {error}</p>
		</div>
	{:else if generators.length === 0}
		<div class="empty-box">No AI subtitle generators configured.</div>
	{:else}
		<div class="generators-grid">
			{#each generators as gen}
				<div class="generator-card" class:offline={!gen.online}>
					<div class="card-head">
						<h5>{gen.name}</h5>
						<div class="status">
							<StatusDot tone={gen.online ? 'good' : 'bad'} />
							<span class="lbl">{gen.online ? 'Online' : 'Offline'}</span>
						</div>
					</div>
					{#if gen.online}
						<div class="card-body">
							<div class="detail-row">
								<span class="label">Type:</span>
								<span class="val uppercase">{gen.type}</span>
							</div>
							<div class="detail-row">
								<span class="label">Version:</span>
								<span class="val">{gen.version || 'unknown'}</span>
							</div>
							<div class="detail-row">
								<span class="label">Whisper Model:</span>
								<span class="val model-name">{gen.model || 'unknown'}</span>
							</div>
							<div class="detail-row">
								<span class="label">Compute Device:</span>
								<span class="val device-badge" class:cuda={gen.device === 'cuda'}>
									{gen.device ? gen.device.toUpperCase() : 'CPU'}
								</span>
							</div>
							<div class="capabilities-section">
								<h6>Capabilities</h6>
								<div class="caps-tags">
									{#if gen.capabilities?.language_hint}
										<span class="cap-tag">Lang Hint</span>
									{/if}
									{#if gen.capabilities?.translate}
										<span class="cap-tag">Translate</span>
									{/if}
									<span class="cap-tag">Concurrent: {gen.capabilities?.concurrent || 1}</span>
								</div>
							</div>
						</div>
					{:else}
						<div class="card-body offline-body">
							<p>
								Generator at <code>{gen.url}</code> is currently offline or unreachable. Check if the
								Subgen container is running and mounted correctly.
							</p>
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.subgen-status-container {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
	}
	.header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 16px;
	}
	.header h4 {
		margin: 0;
		font-size: 14px;
		font-weight: 600;
	}
	.btn-refresh {
		background: transparent;
		border: none;
		color: var(--gold);
		font-size: 12px;
		cursor: pointer;
	}
	.btn-refresh:disabled {
		opacity: 0.5;
	}
	.generators-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: 16px;
	}
	.generator-card {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.generator-card.offline {
		opacity: 0.75;
		border-style: dashed;
	}
	.card-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		border-bottom: 1px solid var(--line);
		padding-bottom: 8px;
	}
	.card-head h5 {
		margin: 0;
		font-size: 13.5px;
		font-weight: 600;
	}
	.status {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.status .lbl {
		font-size: 12px;
		color: var(--muted);
	}
	.card-body {
		display: flex;
		flex-direction: column;
		gap: 8px;
		font-size: 12.5px;
	}
	.detail-row {
		display: flex;
		justify-content: space-between;
		color: var(--muted);
	}
	.detail-row .val {
		color: var(--text);
		font-weight: 550;
	}
	.detail-row .uppercase {
		text-transform: uppercase;
	}
	.model-name {
		font-family: var(--font-mono);
		color: var(--gold) !important;
	}
	.device-badge {
		background: var(--panel);
		border: 1px solid var(--line);
		padding: 1px 5px;
		border-radius: 4px;
		font-size: 11px;
	}
	.device-badge.cuda {
		background: rgba(86, 211, 100, 0.15);
		border-color: rgba(86, 211, 100, 0.3);
		color: #56d364;
	}
	.capabilities-section h6 {
		margin-top: 8px;
		margin-bottom: 6px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--faint2);
	}
	.caps-tags {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.cap-tag {
		background: var(--panel);
		border: 1px solid var(--line);
		padding: 2px 6px;
		border-radius: 4px;
		font-size: 11px;
		color: var(--muted);
	}
	.offline-body p {
		margin: 0;
		color: var(--bad);
		line-height: 1.4;
	}

	.error-box {
		padding: 16px;
		background: rgba(239, 83, 80, 0.08);
		border: 1px solid var(--bad);
		border-radius: var(--radius-sm);
		text-align: center;
	}
	.error-msg {
		color: var(--bad);
		margin: 0;
	}
	.empty-box {
		text-align: center;
		padding: 24px;
		color: var(--muted);
	}

	/* Skeleton */
	.skeleton-card {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.sk-line {
		height: 16px;
		background: var(--line);
		border-radius: 4px;
		width: 40%;
	}
	.sk-line.body {
		width: 80%;
	}
</style>
