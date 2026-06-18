<script lang="ts">
	import { goto } from '$app/navigation';
	import { posterStatusMeta, letterboxMeta } from '$lib/display';
	import type { MovieListItem } from '$lib/api/types';
	import PosterThumb from './PosterThumb.svelte';
	import HdrBadge from './HdrBadge.svelte';
	import StatusDot from './StatusDot.svelte';
	import Icon from './Icon.svelte';

	let { items }: { items: MovieListItem[] } = $props();
</script>

<div class="list">
	<div class="row head">
		<span></span>
		<span>Title</span>
		<span>Res</span>
		<span>Poster</span>
		<span>HDR</span>
		<span>Subs</span>
		<span>Letterbox</span>
		<span></span>
	</div>
	{#each items as m (m.id)}
		<button class="row" onclick={() => goto(`/films/${m.id}`)}>
			<span class="thumb"
				><PosterThumb title={m.title} posterStatus={m.poster_status} posterUrl={m.poster_url} hdr={m.hdr} /></span
			>
			<span class="title">
				<strong>{m.title}</strong>
				<small
					>{m.year}{#if m.genres?.length}
						· {m.genres.join(', ')}{/if}</small
				>
			</span>
			<span class="mono">{m.resolution ?? '—'}</span>
			<span class="cell">
				<StatusDot tone={posterStatusMeta[m.poster_status].tone} />
				<span class="lbl">{posterStatusMeta[m.poster_status].label}</span>
			</span>
			<span><HdrBadge kind={m.hdr} /></span>
			<span class="subs" class:gap={m.subtitle_status === 'gap'}>{m.subtitle_status ?? '—'}</span>
			<span class="cell">
				{#if letterboxMeta(m.letterbox_status)}
					{@const lb = letterboxMeta(m.letterbox_status)}
					<StatusDot tone={lb!.tone} /><span class="lbl">{lb!.label}</span>
				{:else}<span class="muted">—</span>{/if}
			</span>
			<span class="chev"><Icon name="chevron" size={15} /></span>
		</button>
	{/each}
</div>

<style>
	.list {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--panel);
	}
	.row {
		display: grid;
		grid-template-columns: 40px minmax(0, 1fr) 56px 132px 72px 64px 124px 28px;
		align-items: center;
		gap: 12px;
		width: 100%;
		padding: 8px 14px;
		background: transparent;
		border: none;
		border-bottom: 1px solid var(--line);
		text-align: left;
		color: var(--text);
	}
	.row:last-child {
		border-bottom: none;
	}
	.row.head {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint2);
		font-weight: 700;
		background: var(--ink2);
		cursor: default;
	}
	button.row:hover {
		background: var(--panel2);
	}
	.thumb {
		width: 32px;
	}
	.title {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.title strong {
		font-weight: 600;
		font-size: 13.5px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.title small {
		color: var(--muted);
		font-size: 11.5px;
	}
	.mono {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--muted);
	}
	.cell {
		display: flex;
		align-items: center;
		gap: 7px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.lbl {
		white-space: nowrap;
	}
	.subs {
		font-size: 12px;
		color: var(--good);
		text-transform: capitalize;
	}
	.subs.gap {
		color: var(--low);
	}
	.muted {
		color: var(--faint);
	}
	.chev {
		color: var(--faint);
		display: flex;
		justify-content: flex-end;
	}
</style>
