<script lang="ts">
	import type { MovieListItem } from '$lib/api/types';
	import type { Tone } from '$lib/display';
	import PosterThumb from '../PosterThumb.svelte';
	import StatusDot from '../StatusDot.svelte';
	import Icon from '../Icon.svelte';
	import SubtitleMovieDetail from './SubtitleMovieDetail.svelte';

	let {
		movie,
		expanded = false,
		onToggle,
		onMutationComplete
	}: {
		movie: MovieListItem;
		expanded: boolean;
		onToggle: () => void;
		onMutationComplete?: () => void;
	} = $props();

	// Parse subtitle coverage safely
	let coverage = $derived(movie.subtitle_coverage as any);
	let embeddedCount = $derived(coverage?.embedded_present ? (coverage?.track_count || 'Yes') : 'None');
	let externalCount = $derived(coverage?.external_present ? 'Yes' : 'None');

	// Collect unique languages
	let languages = $derived.by(() => {
		if (!coverage) return [];
		const langs = new Set<string>();
		if (Array.isArray(coverage.full_dialogue_languages)) {
			coverage.full_dialogue_languages.forEach((l: string) => langs.add(l));
		}
		if (Array.isArray(coverage.forced_only_languages)) {
			coverage.forced_only_languages.forEach((l: string) => langs.add(l));
		}
		return Array.from(langs);
	});

	// Subtitle status classes and label
	let statusLabel = $derived.by(() => {
		if (movie.subtitle_status === 'ok') return 'OK';
		if (movie.subtitle_status === 'gap') return 'Gaps';
		return 'None';
	});

	let statusTone = $derived.by((): Tone => {
		if (movie.subtitle_status === 'ok') return 'good';
		if (movie.subtitle_status === 'gap') return 'warn';
		return 'bad';
	});

	// Map language code to color class for nice visual aesthetics (8-color accent palette)
	const colorPalette = ['lang-1', 'lang-2', 'lang-3', 'lang-4', 'lang-5', 'lang-6', 'lang-7', 'lang-8'];
	function getLanguageClass(lang: string) {
		let hash = 0;
		for (let i = 0; i < lang.length; i++) {
			hash = lang.charCodeAt(i) + ((hash << 5) - hash);
		}
		const index = Math.abs(hash) % colorPalette.length;
		return colorPalette[index];
	}
</script>

<div class="row-container" class:expanded>
	<button class="row-trigger" onclick={onToggle}>
		<span class="thumb">
			<PosterThumb
				title={movie.title}
				posterStatus={movie.poster_status}
				posterUrl={movie.poster_url}
				hdr={movie.hdr}
			/>
		</span>
		<span class="title">
			<strong>{movie.title}</strong>
			<small>{movie.year}</small>
		</span>
		<span class="count-badge" class:zero={embeddedCount === 'None'}>
			{embeddedCount}
		</span>
		<span class="count-badge" class:zero={externalCount === 'None'}>
			{externalCount}
		</span>
		<span class="langs">
			{#if languages.length > 0}
				{#each languages as lang}
					<span class="lang-pill {getLanguageClass(lang)}">{lang.toUpperCase()}</span>
				{/each}
			{:else}
				<span class="muted">—</span>
			{/if}
		</span>
		<span class="status-cell">
			<StatusDot tone={statusTone} />
			<span class="status-lbl">{statusLabel}</span>
		</span>
		<span class="container-badge">{movie.container || 'mkv'}</span>
		<span class="chev" class:rotated={expanded}>
			<Icon name="chevron" size={14} />
		</span>
	</button>

	{#if expanded}
		<div class="detail-wrapper">
			{#if movie.media_file_id}
				<SubtitleMovieDetail
					mediaFileId={movie.media_file_id}
					movieId={movie.id}
					onMutationComplete={onMutationComplete}
				/>
			{:else}
				<div class="error-detail">No media file ID found for this movie. Cannot inspect subtitle tracks.</div>
			{/if}
		</div>
	{/if}
</div>

<style>
	.row-container {
		border-bottom: 1px solid var(--line);
		transition: background-color 0.2s;
	}
	.row-container.expanded {
		background: var(--ink);
	}
	.row-trigger {
		display: grid;
		grid-template-columns: 40px minmax(0, 1fr) 100px 100px 140px 100px 80px 40px;
		align-items: center;
		gap: 12px;
		width: 100%;
		padding: 10px 16px;
		background: transparent;
		border: none;
		text-align: left;
		color: var(--text);
		cursor: pointer;
		transition: background-color 0.15s;
	}
	.row-trigger:hover {
		background: var(--panel2);
	}
	.thumb {
		width: 32px;
		aspect-ratio: 2/3;
		border-radius: 4px;
		overflow: hidden;
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
		font-size: 11px;
	}
	.count-badge {
		background: var(--gold-soft);
		color: var(--gold);
		font-size: 11.5px;
		font-weight: 600;
		padding: 3px 8px;
		border-radius: 12px;
		text-align: center;
		width: max-content;
	}
	.count-badge.zero {
		background: var(--panel2);
		color: var(--faint);
		font-weight: 400;
	}
	.langs {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.lang-pill {
		font-size: 10px;
		font-weight: 700;
		padding: 2px 5px;
		border-radius: 4px;
		color: var(--ink);
	}
	/* 8-color accent palette */
	:global(.lang-1) { background: #ff7b72; color: #fff; }
	:global(.lang-2) { background: #79c0ff; color: #0d1117; }
	:global(.lang-3) { background: #7ee787; color: #0d1117; }
	:global(.lang-4) { background: #d2a8ff; color: #0d1117; }
	:global(.lang-5) { background: #ffca28; color: #0d1117; }
	:global(.lang-6) { background: #ffa657; color: #0d1117; }
	:global(.lang-7) { background: #56d364; color: #0d1117; }
	:global(.lang-8) { background: #ec407a; color: #fff; }

	.status-cell {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.status-lbl {
		font-size: 12.5px;
		color: var(--muted);
	}
	.container-badge {
		font-family: var(--font-mono);
		font-size: 11.5px;
		color: var(--muted);
		text-transform: uppercase;
		background: var(--panel2);
		padding: 2px 6px;
		border-radius: 4px;
		width: max-content;
	}
	.chev {
		display: flex;
		justify-content: flex-end;
		color: var(--faint);
		transition: transform 0.2s;
	}
	.chev.rotated {
		transform: rotate(90deg);
		color: var(--gold);
	}
	.detail-wrapper {
		border-top: 1px solid var(--line);
		background: var(--ink2);
	}
	.error-detail {
		padding: 16px;
		color: var(--bad);
		font-size: 13px;
	}
	.muted {
		color: var(--faint);
	}
</style>
