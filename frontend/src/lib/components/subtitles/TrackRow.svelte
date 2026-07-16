<script lang="ts">
	import type { SubtitleTrack } from '$lib/api/types';
	import { bytesH } from '$lib/display';

	let {
		track,
		selected,
		onToggle,
		onSelect,
		canCheck = true
	}: {
		track: SubtitleTrack;
		selected: boolean;
		onToggle: (e: MouseEvent) => void;
		onSelect: () => void;
		canCheck?: boolean;
	} = $props();

	// Render flags
	let flags = $derived.by(() => {
		const f = [];
		if (track.is_default) f.push({ label: 'DEFAULT', tone: 'info' });
		if (track.is_forced) f.push({ label: 'FORCED', tone: 'warn' });
		if (track.is_sdh) f.push({ label: 'SDH', tone: 'good' });
		if (track.is_commentary) f.push({ label: 'COMMENTARY', tone: 'dovi' });
		if (track.is_generated) f.push({ label: 'AI', tone: 'generated' });
		return f;
	});
</script>

<tr class="track-row" class:selected onclick={() => onSelect()}>
	<td class="checkbox-cell" onclick={(e) => e.stopPropagation()}>
		{#if canCheck}
			<input type="checkbox" checked={selected} onclick={(e) => onToggle(e)} />
		{/if}
	</td>
	<td>
		<span class="source-badge" class:external={track.source === 'external'}>
			{track.source === 'embedded' ? 'EMBED' : 'EXT'}
		</span>
	</td>
	<td class="lang-cell">
		<strong>{track.language_tag.toUpperCase()}</strong>
		{#if track.title}
			<span class="track-title" title={track.title}>({track.title})</span>
		{/if}
	</td>
	<td class="mono">{track.codec}</td>
	<td>
		<span class="kind-lbl" class:bitmap={track.kind === 'bitmap'}>
			{track.kind}
		</span>
	</td>
	<td>
		<div class="flags-list">
			{#each flags as flag}
				<span class="flag-pill {flag.tone}">{flag.label}</span>
			{/each}
			{#if flags.length === 0}
				<span class="muted">—</span>
			{/if}
		</div>
	</td>
	<td class="mono size-col">
		{track.size_bytes != null ? bytesH(track.size_bytes) : '—'}
	</td>
</tr>

<style>
	.track-row {
		border-bottom: 1px solid var(--line2);
		cursor: pointer;
		transition: background-color 0.15s;
	}
	.track-row:hover {
		background: var(--panel2);
	}
	.track-row.selected {
		background: rgba(255, 194, 75, 0.05);
	}
	td {
		padding: 10px 12px;
		font-size: 13px;
		vertical-align: middle;
		color: var(--text);
	}
	.checkbox-cell {
		width: 32px;
		text-align: center;
	}
	.checkbox-cell input {
		cursor: pointer;
		width: 15px;
		height: 15px;
		accent-color: var(--gold);
	}
	.source-badge {
		background: rgba(121, 192, 255, 0.15);
		color: #79c0ff;
		font-size: 10px;
		font-weight: 700;
		padding: 2px 6px;
		border-radius: 4px;
		font-family: var(--font-mono);
		display: inline-block;
	}
	.source-badge.external {
		background: rgba(86, 211, 100, 0.15);
		color: #56d364;
	}
	.lang-cell {
		display: flex;
		align-items: center;
		gap: 6px;
		flex-wrap: wrap;
	}
	.track-title {
		color: var(--muted);
		font-size: 12px;
		max-width: 150px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.mono {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--muted);
	}
	.kind-lbl {
		font-size: 12px;
		color: var(--text);
		text-transform: capitalize;
	}
	.kind-lbl.bitmap {
		color: var(--warn);
	}
	.flags-list {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.flag-pill {
		font-size: 9px;
		font-weight: 700;
		padding: 1px 4px;
		border-radius: 3px;
	}
	.flag-pill.info {
		background: rgba(121, 192, 255, 0.15);
		color: #79c0ff;
	}
	.flag-pill.warn {
		background: rgba(255, 166, 87, 0.15);
		color: #ffa657;
	}
	.flag-pill.good {
		background: rgba(86, 211, 100, 0.15);
		color: #56d364;
	}
	.flag-pill.dovi {
		background: rgba(210, 168, 255, 0.15);
		color: #d2a8ff;
	}
	.flag-pill.generated {
		background: rgba(255, 123, 114, 0.15);
		color: #ff7b72;
	}

	.size-col {
		text-align: right;
	}
	.muted {
		color: var(--faint);
	}
</style>
