<!-- eslint-disable @typescript-eslint/no-explicit-any svelte/require-each-key -->
<script lang="ts">
	import { onMount } from 'svelte';
	import { getInventory, scanSubtitles } from '$lib/api/subtitles';
	import type { SubtitleInventory } from '$lib/api/types';
	import TrackTable from './TrackTable.svelte';
	import { toast } from '$lib/toast';

	let {
		mediaFileId,
		movieId,
		onMutationComplete
	}: {
		mediaFileId: number;
		movieId: number;
		onMutationComplete?: () => void;
	} = $props();

	let inventory = $state<SubtitleInventory | null>(null);
	let loading = $state(true);
	let scanning = $state(false);
	let error = $state<string | null>(null);

	// Load inventory data
	async function loadInventory() {
		loading = true;
		error = null;
		try {
			inventory = await getInventory(fetch, mediaFileId);
			if (onMutationComplete) onMutationComplete();
		} catch (e: any) {
			error = e.message || 'Failed to load subtitle inventory';
		} finally {
			loading = false;
		}
	}

	// Scan subtitles forces a fresh ffprobe scan
	async function forceScan() {
		scanning = true;
		error = null;
		try {
			inventory = await scanSubtitles(fetch, mediaFileId);
			toast('Subtitle inventory rescanned successfully', 'good');
			if (onMutationComplete) onMutationComplete();
		} catch (e: any) {
			error = e.message || 'Failed to scan subtitles';
			toast('Subtitle scan failed', 'bad');
		} finally {
			scanning = false;
		}
	}

	onMount(() => {
		loadInventory();
	});
</script>

<div class="movie-detail">
	{#if loading}
		<div class="skeleton-wrapper">
			<div class="skeleton line header"></div>
			<div class="skeleton line body1"></div>
			<div class="skeleton line body2"></div>
		</div>
	{:else if error}
		<div class="error-panel">
			<p class="error-msg">⚠️ {error}</p>
			<button class="btn primary" onclick={loadInventory}>Retry</button>
		</div>
	{:else if inventory}
		<div class="detail-header">
			<div class="meta-info">
				<span class="meta-item">
					<strong>Container:</strong>
					<span class="badge">{(inventory.container || '').toUpperCase()}</span>
				</span>
				<span class="meta-item">
					<strong>Duration:</strong>
					{inventory.duration_seconds ? Math.round(inventory.duration_seconds / 60) : '—'} mins
				</span>
				<span class="meta-item">
					<strong>Last Scanned:</strong>
					{inventory.scanned_at ? new Date(inventory.scanned_at).toLocaleString() : 'Never'}
				</span>
			</div>
			<button class="btn secondary btn-sm" onclick={forceScan} disabled={scanning}>
				{scanning ? 'Scanning...' : 'Rescan File'}
			</button>
		</div>

		<div class="detail-grid">
			<!-- Track Table Section -->
			<div class="panel-section tracks-section">
				<h3>Subtitle Tracks</h3>
				{#if !inventory.tracks || inventory.tracks.length === 0}
					<p class="empty-msg">No subtitle tracks found in this file.</p>
				{:else}
					<TrackTable
						tracks={inventory.tracks}
						{mediaFileId}
						{movieId}
						capabilities={inventory.capabilities}
						onMutationComplete={loadInventory}
					/>
				{/if}
			</div>

			<!-- Sidebar Info Section -->
			<div class="sidebar-section">
				<!-- Audio streams -->
				<div class="panel-card">
					<h4>Audio Streams</h4>
					<div class="audio-list">
						{#each inventory.audio_streams || [] as audio}
							<div class="audio-item">
								<span class="audio-idx">#{audio.index}</span>
								<span class="audio-lang">{(audio.language || '').toUpperCase()}</span>
								<span class="audio-codec">{audio.codec}</span>
								<span class="audio-chan">{audio.channels} ch</span>
							</div>
						{/each}
					</div>
				</div>

				<!-- Coverage summary -->
				<div class="panel-card">
					<h4>Coverage Summary</h4>
					<div class="coverage-list">
						<div class="coverage-item">
							<span>Full Dialogue:</span>
							<span class="val">
								{((inventory.coverage && inventory.coverage.full_dialogue_languages) || [])
									.map((l) => l.toUpperCase())
									.join(', ') || 'None'}
							</span>
						</div>
						<div class="coverage-item">
							<span>Forced Only:</span>
							<span class="val">
								{((inventory.coverage && inventory.coverage.forced_only_languages) || [])
									.map((l) => l.toUpperCase())
									.join(', ') || 'None'}
							</span>
						</div>
						<div class="coverage-item">
							<span>SDH present:</span>
							<span class="val">
								{((inventory.coverage && inventory.coverage.sdh_languages) || [])
									.map((l) => l.toUpperCase())
									.join(', ') || 'None'}
							</span>
						</div>
						{#if inventory.coverage && inventory.coverage.missing_preferred_languages && inventory.coverage.missing_preferred_languages.length > 0}
							<div class="coverage-item gap">
								<span>Missing Preferred:</span>
								<span class="val text-warn">
									{inventory.coverage.missing_preferred_languages
										.map((l) => l.toUpperCase())
										.join(', ')}
								</span>
							</div>
						{/if}
					</div>
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	.movie-detail {
		padding: 20px;
		background: var(--ink2);
		border-radius: 0 0 var(--radius) var(--radius);
	}
	.detail-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 20px;
		border-bottom: 1px solid var(--line);
		padding-bottom: 12px;
	}
	.meta-info {
		display: flex;
		gap: 20px;
		font-size: 13px;
		color: var(--muted);
	}
	.meta-item strong {
		color: var(--text);
	}
	.badge {
		font-family: var(--font-mono);
		background: var(--panel2);
		border: 1px solid var(--line);
		padding: 1px 5px;
		border-radius: 4px;
		color: var(--gold);
	}
	.detail-grid {
		display: grid;
		grid-template-columns: 1fr 280px;
		gap: 20px;
	}
	@media (max-width: 900px) {
		.detail-grid {
			grid-template-columns: 1fr;
		}
	}
	.panel-section h3 {
		font-size: 15px;
		font-weight: 600;
		color: var(--text);
		margin-top: 0;
		margin-bottom: 12px;
	}
	.sidebar-section {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.panel-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
	}
	.panel-card h4 {
		margin-top: 0;
		margin-bottom: 10px;
		font-size: 12.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
	}
	.audio-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.audio-item {
		display: grid;
		grid-template-columns: 30px 50px 60px 1fr;
		font-size: 13px;
		color: var(--text);
		padding-bottom: 4px;
		border-bottom: 1px solid var(--line2);
	}
	.audio-item:last-child {
		border-bottom: none;
	}
	.audio-idx {
		color: var(--muted);
		font-family: var(--font-mono);
	}
	.audio-lang {
		font-weight: 600;
	}
	.audio-codec {
		font-family: var(--font-mono);
		color: var(--muted);
	}
	.audio-chan {
		text-align: right;
		color: var(--muted);
	}
	.coverage-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
		font-size: 13px;
	}
	.coverage-item {
		display: flex;
		justify-content: space-between;
		color: var(--muted);
	}
	.coverage-item.gap {
		border-top: 1px dashed var(--line);
		padding-top: 8px;
		margin-top: 4px;
	}
	.coverage-item .val {
		font-weight: 500;
		color: var(--text);
	}
	.coverage-item .text-warn {
		color: var(--warn);
	}
	.empty-msg {
		color: var(--muted);
		font-size: 13px;
		font-style: italic;
	}
	.error-panel {
		padding: 20px;
		background: rgba(239, 83, 80, 0.1);
		border: 1px solid var(--bad);
		border-radius: var(--radius-sm);
		text-align: center;
	}
	.error-msg {
		color: var(--bad);
		margin-bottom: 12px;
	}
	/* Skeleton loading animation */
	.skeleton-wrapper {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.skeleton {
		background: linear-gradient(90deg, var(--panel) 25%, var(--panel2) 50%, var(--panel) 75%);
		background-size: 200% 100%;
		animation: loading 1.5s infinite;
		border-radius: 4px;
	}
	.skeleton.line {
		height: 16px;
	}
	.skeleton.header {
		width: 40%;
		height: 24px;
	}
	.skeleton.body1 {
		width: 80%;
	}
	.skeleton.body2 {
		width: 60%;
	}

	@keyframes loading {
		0% {
			background-position: 200% 0;
		}
		100% {
			background-position: -200% 0;
		}
	}

	/* Common button styles */
	.btn {
		font-size: 13px;
		font-weight: 500;
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
		outline: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		transition:
			background-color 0.15s,
			opacity 0.15s;
	}
	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--on-gold);
	}
	.btn.primary:hover:not(:disabled) {
		background: var(--gold-deep);
	}
	.btn.secondary {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.btn.secondary:hover:not(:disabled) {
		background: var(--panel);
	}
	.btn-sm {
		padding: 5px 10px;
		font-size: 12px;
	}
</style>
