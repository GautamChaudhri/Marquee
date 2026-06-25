<script lang="ts">
	import type { MovieListItem } from '$lib/api/types';
	import type { Tone } from '$lib/display';
	import PosterThumb from '../PosterThumb.svelte';
	import StatusDot from '../StatusDot.svelte';

	let {
		movie
	}: {
		movie: MovieListItem;
	} = $props();

	// Parse subtitle coverage safely
	let coverage = $derived(movie.subtitle_coverage as any);
	let embeddedCount = $derived(coverage?.embedded_present ? (coverage?.track_count || 'Yes') : 'None');
	let externalCount = $derived(coverage?.external_present ? 'Yes' : 'None');

	// Collect unique subtitle languages
	let subtitleLangs = $derived.by(() => {
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

	// Collect audio languages
	let audioLangs = $derived.by(() => {
		if (!coverage || !Array.isArray(coverage.audio_languages)) return [];
		return coverage.audio_languages;
	});

	let audioChannels = $derived.by(() => {
		if (!coverage || !coverage.audio_channels_by_language) return [];
		const preferred = Array.isArray(coverage.preferred_audio_languages)
			? coverage.preferred_audio_languages
			: audioLangs;
		const labels = new Set<string>();
		preferred.forEach((lang: string) => {
			const channelLabels = coverage.audio_channels_by_language?.[lang] || [];
			channelLabels.forEach((label: string) => labels.add(label));
		});
		return Array.from(labels).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
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

<div class="row-container">
	<a class="row-trigger" href="/audio-subs/{movie.id}">
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
		<span class="langs">
			{#if audioLangs.length > 0}
				{#each audioLangs as lang}
					<span class="lang-pill {getLanguageClass(lang)}">{lang.toUpperCase()}</span>
				{/each}
			{:else}
				<span class="muted">—</span>
			{/if}
		</span>
		<span class="channel-pills">
			{#if audioChannels.length > 0}
				{#each audioChannels as label}
					<span class="channel-pill">{label}</span>
				{/each}
			{:else}
				<span class="muted">—</span>
			{/if}
		</span>
		<span class="langs">
			{#if subtitleLangs.length > 0}
				{#each subtitleLangs as lang}
					<span class="lang-pill {getLanguageClass(lang)}">{lang.toUpperCase()}</span>
				{/each}
			{:else}
				<span class="muted">—</span>
			{/if}
		</span>
		<span class="count-badge" class:zero={embeddedCount === 'None'}>
			{embeddedCount}
		</span>
		<span class="count-badge" class:zero={externalCount === 'None'}>
			{externalCount}
		</span>
		<span class="status-cell">
			<StatusDot tone={statusTone} />
			<span class="status-lbl">{statusLabel}</span>
		</span>
		<span class="container-badge">{movie.container || 'mkv'}</span>
		<span class="chev">
			→
		</span>
	</a>
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
		grid-template-columns: 40px minmax(0, 1fr) 120px 120px 120px 100px 100px 100px 80px 40px;
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
		text-decoration: none;
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
	.channel-pills {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}
	.channel-pill {
		background: color-mix(in srgb, var(--gold) 16%, transparent);
		border: 1px solid color-mix(in srgb, var(--gold) 28%, transparent);
		color: var(--gold);
		border-radius: 999px;
		font-family: var(--font-mono);
		font-size: 10.5px;
		font-weight: 700;
		padding: 2px 7px;
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
		font-weight: bold;
		font-size: 14px;
	}
	.muted {
		color: var(--faint);
	}
</style>
