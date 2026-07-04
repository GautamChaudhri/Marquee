<script lang="ts">
	import { testSubgen, restartSubgen, getSubgenLogs } from '$lib/api/subtitle-generators';
	import type { SubtitleGenerator } from '$lib/api/types';
	import SubgenConfigModal from './SubgenConfigModal.svelte';
	import { toast } from '$lib/toast';

	let {
		generator,
		settings,
		onRefresh
	}: {
		generator: SubtitleGenerator | null;
		settings: any;
		onRefresh: () => void;
	} = $props();

	let testing = $state(false);
	let testResult = $state<{ language: string; latency: number } | null>(null);

	let restarting = $state(false);
	let restartProgress = $state('');

	let logsOpen = $state(false);
	let logs = $state<string[]>([]);
	let logsInterval: ReturnType<typeof setInterval> | null = null;

	let configOpen = $state(false);

	const deployment = $derived(settings.SUBGEN_DEPLOYMENT || 'disabled');

	async function runTest() {
		testing = true;
		testResult = null;
		try {
			const res = await testSubgen(fetch);
			if (res.ok) {
				testResult = {
					language: res.detected_language || 'unknown',
					latency: res.latency_ms
				};
				toast(
					`Test passed: detected language "${res.detected_language}" in ${res.latency_ms}ms`,
					'good'
				);
			}
		} catch (e: any) {
			toast(e.message || 'Subgen test failed', 'bad');
		} finally {
			testing = false;
		}
	}

	async function triggerRestart() {
		restarting = true;
		restartProgress = 'Requesting restart...';
		try {
			await restartSubgen(fetch);
			restartProgress = 'Restart request accepted. Waiting for model reload...';
			// Poll status/heartbeat or wait a bit
			setTimeout(() => {
				restarting = false;
				restartProgress = '';
				toast('Subgen reload initiated', 'good');
				onRefresh();
			}, 3000);
		} catch (e: any) {
			toast(e.message || 'Failed to trigger restart', 'bad');
			restarting = false;
			restartProgress = '';
		}
	}

	async function fetchLogs() {
		try {
			const res = await getSubgenLogs(fetch, 100);
			logs = res.lines || [];
		} catch {
			// ignore
		}
	}

	function toggleLogs() {
		logsOpen = !logsOpen;
		if (logsOpen) {
			fetchLogs();
			logsInterval = setInterval(fetchLogs, 2000);
		} else {
			if (logsInterval) {
				clearInterval(logsInterval);
				logsInterval = null;
			}
		}
	}

	function handleConfigSave(newSettings: any) {
		configOpen = false;
		onRefresh();
	}
</script>

<div class="subgen-card mq-rise">
	<div class="header">
		<div class="title-section">
			<h3>Whisper AI Subtitle Generator</h3>
			<p class="desc">Automatic Speech Recognition (ASR) engine configuration.</p>
		</div>
		<div class="badges">
			<span class={`badge deployment ${deployment}`}>
				{deployment.toUpperCase()}
			</span>
			{#if generator && deployment !== 'disabled'}
				<span class={`badge state ${generator.online ? 'online' : 'offline'}`}>
					{generator.online ? 'ONLINE' : 'OFFLINE'}
				</span>
			{/if}
		</div>
	</div>

	<div class="content">
		{#if deployment === 'disabled'}
			<div class="disabled-state">
				<p>Whisper AI Subtitle Generation is currently disabled.</p>
				<button class="btn primary" onclick={() => (configOpen = true)}>Configure & Enable</button>
			</div>
		{:else if generator}
			<div class="info-grid">
				<div class="info-item">
					<span class="label">Provider</span>
					<span class="value">{generator.type.toUpperCase()}</span>
				</div>
				<div class="info-item">
					<span class="label">Model in use</span>
					<span class="value">{generator.model || 'none'}</span>
				</div>
				<div class="info-item">
					<span class="label">Hardware device</span>
					<span class="value">{generator.device || 'none'}</span>
				</div>
				{#if generator.capabilities}
					<div class="info-item">
						<span class="label">Capabilities</span>
						<span class="value">
							{#if generator.capabilities.language_hint}
								Language Detection
							{/if}
							{#if generator.capabilities.translate}
								· Translate
							{/if}
						</span>
					</div>
				{/if}
			</div>

			<!-- Queue Depth & Version -->
			<div class="metric-row">
				{#if (generator as any).queue_depth}
					{@const q = (generator as any).queue_depth}
					<div class="queue-depth">
						<strong>{q.processing || 0}</strong> processing · <strong>{q.queued || 0}</strong>
						queued
						{#if (generator as any).last_activity_line}
							<span class="activity-line">— {(generator as any).last_activity_line}</span>
						{/if}
					</div>
				{/if}
				{#if generator.version}
					<span class="version">v{generator.version}</span>
				{/if}
			</div>

			<!-- Live test output -->
			{#if testResult}
				<div class="test-result mq-rise">
					<span>Detected language: <strong>{testResult.language.toUpperCase()}</strong></span>
					<span>Latency: <strong>{testResult.latency} ms</strong></span>
				</div>
			{/if}

			{#if restarting}
				<div class="restart-banner">
					<div class="spinner"></div>
					<span>{restartProgress}</span>
				</div>
			{/if}

			<!-- Actions -->
			<div class="actions">
				<button class="btn secondary" onclick={() => (configOpen = true)}>Configure</button>
				<button class="btn secondary" onclick={toggleLogs}>
					{logsOpen ? 'Hide Logs' : 'View Logs'}
				</button>
				{#if deployment === 'embedded'}
					<button class="btn secondary" onclick={triggerRestart} disabled={restarting}>
						{restarting ? 'Reloading...' : 'Restart'}
					</button>
				{/if}
				<button class="btn primary" onclick={runTest} disabled={testing || !generator.online}>
					{testing ? 'Testing...' : 'Test Generator'}
				</button>
			</div>

			{#if logsOpen}
				<div class="logs-drawer mq-rise">
					<div class="logs-header">
						<span>Subgen Process Logs (Tailing)</span>
						<button class="icon-btn" onclick={fetchLogs}>🔄</button>
					</div>
					<div class="logs-content">
						{#each logs as line}
							<div class="log-line">{line}</div>
						{:else}
							<div class="empty-logs">No log lines found.</div>
						{/each}
					</div>
				</div>
			{/if}
		{:else}
			<div class="loading-state">Connecting to subgen service...</div>
		{/if}
	</div>

	{#if configOpen}
		<SubgenConfigModal {settings} onClose={() => (configOpen = false)} onSave={handleConfigSave} />
	{/if}
</div>

<style>
	.subgen-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		border-bottom: 1px solid var(--line);
		padding-bottom: 12px;
		gap: 16px;
	}
	.title-section h3 {
		margin: 0;
		font-size: 15px;
		font-weight: 650;
	}
	.title-section .desc {
		margin: 4px 0 0;
		font-size: 12.5px;
		color: var(--muted);
	}
	.badges {
		display: flex;
		gap: 6px;
	}
	.badge {
		font-size: 10px;
		font-weight: 750;
		padding: 2px 6px;
		border-radius: 4px;
		letter-spacing: 0.05em;
	}
	.badge.deployment.disabled {
		background: var(--ink3);
		color: var(--muted);
	}
	.badge.deployment.external {
		background: color-mix(in srgb, var(--info) 15%, transparent);
		color: var(--info);
		border: 1px solid color-mix(in srgb, var(--info) 30%, transparent);
	}
	.badge.deployment.embedded {
		background: color-mix(in srgb, var(--gold) 15%, transparent);
		color: var(--gold);
		border: 1px solid color-mix(in srgb, var(--gold) 30%, transparent);
	}
	.badge.state.online {
		background: rgba(86, 211, 100, 0.15);
		color: #56d364;
	}
	.badge.state.offline {
		background: rgba(239, 83, 80, 0.15);
		color: #ff7b72;
	}
	.info-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
		gap: 12px;
		background: var(--ink2);
		padding: 12px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
	}
	.info-item {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.info-item .label {
		font-size: 10px;
		text-transform: uppercase;
		font-weight: 700;
		color: var(--faint2);
	}
	.info-item .value {
		font-size: 12.5px;
		font-weight: 600;
	}
	.metric-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 12px;
		color: var(--muted);
	}
	.activity-line {
		color: var(--faint2);
		font-style: italic;
		margin-left: 4px;
	}
	.test-result {
		background: color-mix(in srgb, var(--good) 10%, var(--ink));
		border: 1px solid var(--good-soft);
		padding: 8px 12px;
		border-radius: var(--radius-sm);
		display: flex;
		justify-content: space-between;
		font-size: 12.5px;
	}
	.restart-banner {
		display: flex;
		align-items: center;
		gap: 10px;
		background: var(--ink3);
		border: 1px dashed var(--line);
		padding: 8px 12px;
		border-radius: var(--radius-sm);
		font-size: 12px;
	}
	.spinner {
		width: 14px;
		height: 14px;
		border: 2px solid var(--muted);
		border-top-color: var(--text);
		border-radius: 50%;
		animation: spin 1s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.actions {
		display: flex;
		gap: 8px;
		justify-content: flex-end;
		margin-top: 4px;
		flex-wrap: wrap;
	}
	.btn {
		font-size: 13px;
		font-weight: 600;
		padding: 8px 14px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--ink);
	}
	.btn.primary:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.btn.secondary {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.btn.secondary:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.disabled-state {
		text-align: center;
		padding: 32px;
		color: var(--muted);
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 12px;
	}
	.disabled-state p {
		margin: 0;
		font-size: 13.5px;
	}
	.logs-drawer {
		background: var(--ink);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		margin-top: 10px;
		overflow: hidden;
		display: flex;
		flex-direction: column;
		height: 200px;
	}
	.logs-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 8px 12px;
		background: var(--panel2);
		border-bottom: 1px solid var(--line);
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		color: var(--faint2);
	}
	.logs-content {
		padding: 10px;
		overflow-y: auto;
		flex: 1;
		font-family: var(--font-mono);
		font-size: 11.5px;
		color: #e0e0e0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.log-line {
		white-space: pre-wrap;
		word-break: break-all;
	}
	.empty-logs {
		color: var(--muted);
		text-align: center;
		padding: 32px;
	}
	.loading-state {
		text-align: center;
		padding: 32px;
		color: var(--muted);
	}
</style>
