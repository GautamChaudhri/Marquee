<!-- eslint-disable @typescript-eslint/no-explicit-any svelte/require-each-key -->
<script lang="ts">
	import type { SubtitlePlan } from '$lib/api/types';
	import { bytesH } from '$lib/display';

	let { plan }: { plan: SubtitlePlan } = $props();

	// Format track differences nicely
	let beforeTracks = $derived((plan.before as any)?.tracks || []);
	let afterTracks = $derived((plan.after as any)?.tracks || []);

	// Storage details
	let storage = $derived(plan.storage);
	let warnings = $derived(plan.warnings || []);

	// Hardlink warning
	let isHardlinked = $derived((plan as any).hardlinked ?? false);

	type PlanWarning = {
		code?: string;
		message?: string;
	};

	function asPlanWarning(warning: unknown): PlanWarning {
		return warning && typeof warning === 'object' ? (warning as PlanWarning) : {};
	}
</script>

<div class="plan-review">
	<!-- Hardlink alert -->
	{#if isHardlinked}
		<div class="alert danger">
			<span class="icon">⚠️</span>
			<div class="content">
				<strong>Hardlink Detected</strong>
				<p>
					This media file is a hardlink (e.g. from Radarr/Sonarr seeding). Mutating it directly will
					break the link and copy the file, doubling disk usage if seeding continues.
				</p>
			</div>
		</div>
	{/if}

	<!-- Warnings -->
	{#if warnings.length > 0}
		<div class="warnings-section">
			<h5>Warnings & Safety Alerts</h5>
			{#each warnings as warning}
				{@const item = asPlanWarning(warning)}
				<div class="alert warning">
					<span class="icon">⚠️</span>
					<div class="content">
						<strong>{item.code ? item.code.replace(/_/g, ' ').toUpperCase() : 'Warning'}</strong>
						{#if item.message}
							<p>{item.message}</p>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}

	<!-- Coverage / Track Comparison -->
	<div class="diff-section">
		<h5>Track Changes</h5>
		<div class="diff-grid">
			<div class="diff-panel before">
				<h6>Before</h6>
				<div class="track-list">
					{#each beforeTracks as t}
						<div class="track-item">
							<span class="badge" class:external={t.source === 'external'}>
								{t.source === 'embedded' ? 'EMB' : 'EXT'}
							</span>
							<strong class="lang">{t.language_tag.toUpperCase()}</strong>
							<span class="codec mono">{t.codec}</span>
							{#if t.is_forced}<span class="pill forced">forced</span>{/if}
							{#if t.is_sdh}<span class="pill sdh">sdh</span>{/if}
						</div>
					{/each}
				</div>
			</div>

			<div class="diff-arrow">➡️</div>

			<div class="diff-panel after">
				<h6>After (remuxed)</h6>
				<div class="track-list">
					{#each afterTracks as t}
						<div class="track-item">
							<span class="badge" class:external={t.source === 'external'}>
								{t.source === 'embedded' ? 'EMB' : 'EXT'}
							</span>
							<strong class="lang">{t.language_tag.toUpperCase()}</strong>
							<span class="codec mono">{t.codec}</span>
							{#if t.is_forced}<span class="pill forced">forced</span>{/if}
							{#if t.is_sdh}<span class="pill sdh">sdh</span>{/if}
						</div>
					{/each}
					{#if afterTracks.length === 0}
						<div class="empty-track-list">All subtitle tracks removed.</div>
					{/if}
				</div>
			</div>
		</div>
	</div>

	<!-- Storage impact -->
	{#if storage}
		<div class="storage-info">
			<h5>Storage Impact</h5>
			<div class="metrics">
				<div class="metric">
					<span class="label">Original Size</span>
					<span class="val">{bytesH(storage.source_bytes || 0)}</span>
				</div>
				<div class="metric">
					<span class="label">Est. Temporary Needed</span>
					<span class="val">{bytesH(storage.estimated_temp_bytes || 0)}</span>
				</div>
				<div class="metric">
					<span class="label">Free Disk Space</span>
					<span
						class="val"
						class:low-space={storage.free_bytes != null &&
							storage.estimated_temp_bytes != null &&
							storage.free_bytes < storage.estimated_temp_bytes * 1.5}
					>
						{bytesH(storage.free_bytes || 0)}
					</span>
				</div>
			</div>
			{#if storage.free_bytes != null && storage.estimated_temp_bytes != null && storage.free_bytes < storage.estimated_temp_bytes * 1.5}
				<p class="space-warning">
					⚠️ Free disk space is low relative to the required temporary size. Remuxing might fail due
					to lack of space.
				</p>
			{/if}
		</div>
	{/if}
</div>

<style>
	.plan-review {
		display: flex;
		flex-direction: column;
		gap: 16px;
		max-height: 70vh;
		overflow-y: auto;
		text-align: left;
	}
	h5 {
		margin-top: 0;
		margin-bottom: 8px;
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
	}
	.alert {
		display: flex;
		gap: 12px;
		padding: 12px;
		border-radius: var(--radius-sm);
		font-size: 13px;
		margin-bottom: 8px;
	}
	.alert.warning {
		background: rgba(255, 166, 87, 0.08);
		border: 1px solid rgba(255, 166, 87, 0.3);
		color: #ffa657;
	}
	.alert.danger {
		background: rgba(239, 83, 80, 0.08);
		border: 1px solid rgba(239, 83, 80, 0.3);
		color: #ff7b72;
	}
	.alert .icon {
		font-size: 16px;
		line-height: 1;
	}
	.alert .content strong {
		display: block;
		margin-bottom: 4px;
		font-weight: 600;
	}
	.alert .content p {
		margin: 0;
		line-height: 1.4;
		opacity: 0.9;
	}

	.diff-section {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
		background: var(--panel2);
	}
	.diff-grid {
		display: flex;
		align-items: center;
		gap: 14px;
	}
	.diff-panel {
		flex: 1;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 10px;
		min-height: 120px;
	}
	.diff-panel h6 {
		margin-top: 0;
		margin-bottom: 8px;
		font-size: 11px;
		text-transform: uppercase;
		color: var(--muted);
		border-bottom: 1px solid var(--line);
		padding-bottom: 4px;
	}
	.diff-arrow {
		font-size: 20px;
		color: var(--muted);
	}
	.track-list {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.track-item {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 12.5px;
	}
	.badge {
		font-size: 8.5px;
		font-weight: 700;
		background: rgba(121, 192, 255, 0.15);
		color: #79c0ff;
		padding: 1px 4px;
		border-radius: 3px;
		font-family: var(--font-mono);
	}
	.badge.external {
		background: rgba(86, 211, 100, 0.15);
		color: #56d364;
	}
	.lang {
		font-weight: 600;
	}
	.codec.mono {
		font-family: var(--font-mono);
		color: var(--muted);
		font-size: 11px;
	}
	.pill {
		font-size: 8px;
		font-weight: 700;
		padding: 1px 3px;
		border-radius: 2px;
		text-transform: uppercase;
	}
	.pill.forced {
		background: rgba(255, 166, 87, 0.15);
		color: #ffa657;
	}
	.pill.sdh {
		background: rgba(86, 211, 100, 0.15);
		color: #56d364;
	}

	.empty-track-list {
		color: var(--faint);
		font-size: 12.5px;
		font-style: italic;
		text-align: center;
		padding-top: 20px;
	}

	.storage-info {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
		background: var(--panel2);
	}
	.storage-info .metrics {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 12px;
	}
	.metric {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.metric .label {
		font-size: 11px;
		color: var(--muted);
	}
	.metric .val {
		font-size: 13.5px;
		font-weight: 600;
		font-family: var(--font-mono);
	}
	.metric .val.low-space {
		color: var(--bad);
	}
	.space-warning {
		margin-top: 10px;
		margin-bottom: 0;
		font-size: 12px;
		color: var(--bad);
	}
</style>
