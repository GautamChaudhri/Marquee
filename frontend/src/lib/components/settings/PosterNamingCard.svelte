<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';

	let {
		movie,
		series,
		season,
		dirty = false,
		disabled = false,
		onChange,
		onReset
	}: {
		movie: string;
		series: string;
		season: string;
		dirty?: boolean;
		disabled?: boolean;
		onChange: (
			key: 'MOVIE_POSTER_FORMAT' | 'SERIES_POSTER_FORMAT' | 'SEASON_POSTER_FORMAT',
			value: string
		) => void;
		onReset: () => void;
	} = $props();

	const moviePreset = $derived(
		movie === '{movie_basename}.jpg' ? 'movie' : movie === 'poster.jpg' ? 'poster' : 'custom'
	);
	const movieCustom = $derived(
		movie.replaceAll('{movie_basename}', '<base_filename>').replace(/\.jpe?g$/i, '')
	);
	const seasonPreview = $derived(season.replace('{season:02d}', '01').replace('{season}', '1'));

	function jpg(value: string, fallback: string): string {
		const base = value.trim().replace(/\.jpe?g$/i, '') || fallback;
		return `${base}.jpg`;
	}

	function setCustom(value: string) {
		onChange(
			'MOVIE_POSTER_FORMAT',
			jpg(value, 'poster').replaceAll('<base_filename>', '{movie_basename}')
		);
	}
</script>

<section class:dirty class="naming-card">
	<header>
		<Icon name="image" size={15} />
		<div>
			<h3>Poster naming</h3>
			<p>Use stable filenames Marquee and your media servers can agree on.</p>
		</div>
		<span>Next job</span>
	</header>

	<div class="naming-grid">
		<div role="group" aria-labelledby="poster-naming-movies" class="naming-group">
			<span id="poster-naming-movies" class="group-label">Films</span>
			<label class="choice">
				<input
					type="radio"
					checked={moviePreset === 'movie'}
					{disabled}
					onchange={() => onChange('MOVIE_POSTER_FORMAT', '{movie_basename}.jpg')}
				/>
				<span>Media filename <code>{'{movie_basename}.jpg'}</code></span>
			</label>
			<label class="choice">
				<input
					type="radio"
					checked={moviePreset === 'poster'}
					{disabled}
					onchange={() => onChange('MOVIE_POSTER_FORMAT', 'poster.jpg')}
				/>
				<span>Conventional <code>poster.jpg</code></span>
			</label>
			<label class="choice custom-choice">
				<input
					type="radio"
					checked={moviePreset === 'custom'}
					{disabled}
					onchange={() => setCustom(movieCustom)}
				/>
				<span>Custom pattern</span>
			</label>
			<input
				type="text"
				value={movieCustom}
				disabled={disabled || moviePreset !== 'custom'}
				oninput={(event) => setCustom((event.currentTarget as HTMLInputElement).value)}
				placeholder="<base_filename>-poster"
				spellcheck="false"
			/>
			<small>Preview: {movie.replace('{movie_basename}', 'Example Movie')}</small>
		</div>

		<div role="group" aria-labelledby="poster-naming-tv" class="naming-group">
			<span id="poster-naming-tv" class="group-label">Television</span>
			<label>
				<span>Show poster filename</span>
				<input
					type="text"
					value={series}
					{disabled}
					oninput={(event) =>
						onChange(
							'SERIES_POSTER_FORMAT',
							jpg((event.currentTarget as HTMLInputElement).value, 'show')
						)}
					spellcheck="false"
				/>
			</label>
			<label>
				<span>Season template</span>
				<input
					type="text"
					value={season}
					{disabled}
					oninput={(event) =>
						onChange('SEASON_POSTER_FORMAT', (event.currentTarget as HTMLInputElement).value)}
					spellcheck="false"
				/>
			</label>
			<small>Preview: {seasonPreview}</small>
		</div>
	</div>
	<footer>
		<code>{movie} · {series} · {season}</code>
		<button type="button" class="pill ghost" onclick={onReset} {disabled}
			>Reset naming defaults</button
		>
	</footer>
</section>

<style>
	.naming-card {
		margin-bottom: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		overflow: hidden;
	}
	.naming-card.dirty {
		border-color: color-mix(in srgb, var(--gold) 38%, var(--line));
	}
	header {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		gap: 9px;
		align-items: center;
		padding: 12px 15px;
		border-bottom: 1px solid var(--line);
		color: var(--muted);
	}
	header h3 {
		margin: 0;
		color: var(--text);
		font-size: 12px;
	}
	header p {
		margin: 2px 0 0;
		font-size: 11px;
	}
	header > span {
		color: var(--muted);
		font: 600 9px/1.2 var(--font-mono);
		text-transform: uppercase;
	}
	.naming-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}
	/* Not a <fieldset>/<legend>: a rendered legend is laid into the block-start
	   border area regardless of the fieldset's display mode, so the card's
	   overflow:hidden sliced these labels in half. role="group" +
	   aria-labelledby keeps the grouping semantics without the special layout. */
	.naming-group {
		display: grid;
		align-content: start;
		gap: 10px;
		margin: 0;
		padding: 15px 16px 16px;
		border: 0;
		min-width: 0;
	}
	.naming-group + .naming-group {
		border-left: 1px solid var(--line);
	}
	.group-label {
		padding: 0;
		color: var(--muted);
		font: 700 9px/1.2 var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	label {
		display: grid;
		gap: 5px;
		color: var(--muted);
		font-size: 11px;
		font-weight: 650;
	}
	.choice {
		display: flex;
		align-items: center;
		gap: 8px;
		min-height: 25px;
	}
	.choice code {
		margin-left: 6px;
		color: var(--muted);
		font-size: 10px;
	}
	.custom-choice {
		margin-bottom: -5px;
	}
	input[type='text'] {
		width: 100%;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--text);
		padding: 8px 9px;
		font: 11px/1.4 var(--font-mono);
	}
	input:disabled {
		opacity: 0.52;
		cursor: not-allowed;
	}
	small {
		color: var(--muted);
		font: 10px/1.4 var(--font-mono);
	}
	footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 10px 15px;
		border-top: 1px solid var(--line);
		background: color-mix(in srgb, var(--panel2) 35%, transparent);
	}
	footer code {
		overflow: hidden;
		color: var(--muted);
		font: 9.5px/1.4 var(--font-mono);
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	footer button {
		flex: none;
	}
	@media (max-width: 720px) {
		.naming-grid {
			grid-template-columns: 1fr;
		}
		.naming-group + .naming-group {
			border-top: 1px solid var(--line);
			border-left: 0;
		}
		footer code {
			display: none;
		}
		footer {
			justify-content: flex-end;
		}
	}
</style>
