<script lang="ts">
	import { tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	type PosterFormatKey = 'MOVIE_POSTER_FORMAT' | 'SERIES_POSTER_FORMAT' | 'SEASON_POSTER_FORMAT';
	type Preset = { id: string; label: string; value: string };

	const MOVIE_PRESETS: Preset[] = [
		{ id: 'conventional', label: 'Conventional', value: 'poster' },
		{ id: 'media-filename', label: 'Media filename', value: '{movie_basename}' }
	];
	const SERIES_PRESETS: Preset[] = [{ id: 'conventional', label: 'Conventional', value: 'show' }];
	const SEASON_PRESETS: Preset[] = [
		{ id: 'conventional', label: 'Conventional', value: 'season{season:02d}' }
	];

	let {
		movie,
		series,
		season,
		disabled = false,
		onSave,
		onReset
	}: {
		movie: string;
		series: string;
		season: string;
		disabled?: boolean;
		onSave: (key: PosterFormatKey, value: string) => Promise<boolean>;
		onReset: () => Promise<boolean>;
	} = $props();

	function filenameStem(value: string): string {
		return value.trim().replace(/\.jpe?g$/i, '');
	}

	function presetFor(value: string, presets: Preset[]): Preset | undefined {
		const stem = filenameStem(value);
		return presets.find((preset) => preset.value === stem);
	}

	function previewFor(key: PosterFormatKey, value: string): string {
		const stem = filenameStem(value) || 'poster';
		if (key === 'MOVIE_POSTER_FORMAT')
			return `${stem.replaceAll('{movie_basename}', 'Example Movie')}.jpg`;
		if (key === 'SEASON_POSTER_FORMAT') {
			return `${stem.replace('{season:02d}', '01').replace('{season}', '1')}.jpg`;
		}
		return `${stem}.jpg`;
	}

	let movieValue = $state('');
	let movieCommitted = $state('');
	let seriesValue = $state('');
	let seriesCommitted = $state('');
	let seasonValue = $state('');
	let seasonCommitted = $state('');
	let savingKey = $state<PosterFormatKey | null>(null);
	let resetting = $state(false);

	// Parent settings can change after a direct save or an optimistic-lock refresh.
	// Preserve a local edit during a conflict, but let Cancel return to the newest
	// committed value rather than the value from when the page first loaded.
	$effect(() => {
		const next = filenameStem(movie);
		if (movieValue === movieCommitted) movieValue = next;
		movieCommitted = next;
	});
	$effect(() => {
		const next = filenameStem(series);
		if (seriesValue === seriesCommitted) seriesValue = next;
		seriesCommitted = next;
	});
	$effect(() => {
		const next = filenameStem(season);
		if (seasonValue === seasonCommitted) seasonValue = next;
		seasonCommitted = next;
	});

	const movieDirty = $derived(movieValue !== movieCommitted);
	const seriesDirty = $derived(seriesValue !== seriesCommitted);
	const seasonDirty = $derived(seasonValue !== seasonCommitted);
	const hasChanges = $derived(movieDirty || seriesDirty || seasonDirty);

	function valueFor(key: PosterFormatKey): string {
		if (key === 'MOVIE_POSTER_FORMAT') return movieValue;
		if (key === 'SERIES_POSTER_FORMAT') return seriesValue;
		return seasonValue;
	}

	function committedFor(key: PosterFormatKey): string {
		if (key === 'MOVIE_POSTER_FORMAT') return movieCommitted;
		if (key === 'SERIES_POSTER_FORMAT') return seriesCommitted;
		return seasonCommitted;
	}

	function setValue(key: PosterFormatKey, value: string) {
		const next = filenameStem(value);
		if (key === 'MOVIE_POSTER_FORMAT') movieValue = next;
		else if (key === 'SERIES_POSTER_FORMAT') seriesValue = next;
		else seasonValue = next;
	}

	function focusEditor(key: PosterFormatKey) {
		document.getElementById(`poster-name-${key}`)?.focus();
	}

	function cancelValue(key: PosterFormatKey) {
		setValue(key, committedFor(key));
	}

	async function saveValue(key: PosterFormatKey) {
		const value = valueFor(key);
		if (disabled || savingKey || !value.trim()) return;
		savingKey = key;
		try {
			await onSave(key, value);
		} finally {
			savingKey = null;
		}
	}

	async function resetDefaults() {
		if (disabled || resetting) return;
		resetting = true;
		try {
			const reset = await onReset();
			if (reset) {
				// A direct reset discards every poster-name override, so it must also
				// discard a local poster draft rather than leave it ready to re-save.
				await tick();
				movieValue = filenameStem(movie);
				movieCommitted = movieValue;
				seriesValue = filenameStem(series);
				seriesCommitted = seriesValue;
				seasonValue = filenameStem(season);
				seasonCommitted = seasonValue;
			}
		} finally {
			resetting = false;
		}
	}
</script>

{#snippet filenameRail(key: PosterFormatKey, title: string, description: string, presets: Preset[])}
	{@const value = valueFor(key)}
	{@const selectedPreset = presetFor(value, presets)}
	{@const isDirty = value !== committedFor(key)}
	<div class:dirty={isDirty} class="filename-rail">
		<div class="filename-copy">
			<label for={`poster-name-${key}`}>{title}</label>
			<small>{description}</small>
			<div class="preset-controls" role="group" aria-label={`${title} presets`}>
				{#each presets as preset (preset.id)}
					<button
						type="button"
						class:active={selectedPreset?.id === preset.id}
						aria-pressed={selectedPreset?.id === preset.id}
						onclick={() => setValue(key, preset.value)}
						disabled={disabled || savingKey !== null}
					>
						<span class="selection-dot" aria-hidden="true"></span>
						<span>{preset.label}</span>
					</button>
				{/each}
				<button
					type="button"
					class:active={!selectedPreset}
					aria-pressed={!selectedPreset}
					onclick={() => focusEditor(key)}
					disabled={disabled || savingKey !== null}
					title="Edit the filename below to use a custom name"
				>
					<span class="selection-dot" aria-hidden="true"></span>
					<span>Custom</span>
				</button>
			</div>
		</div>
		<div class="filename-editor">
			<input
				id={`poster-name-${key}`}
				type="text"
				{value}
				aria-label={title}
				aria-describedby={`poster-name-meta-${key}`}
				placeholder={presets[0]?.value ?? 'poster'}
				spellcheck="false"
				disabled={disabled || savingKey !== null}
				oninput={(event) => setValue(key, (event.currentTarget as HTMLInputElement).value)}
			/>
			<div id={`poster-name-meta-${key}`} class="filename-meta">
				<span>Preview: {previewFor(key, value)}</span>
			</div>
			{#if isDirty}
				<div class="row-actions">
					<button
						type="button"
						class="pill ghost"
						onclick={() => cancelValue(key)}
						disabled={disabled || savingKey !== null}
					>
						Cancel
					</button>
					<button
						type="button"
						class="pill primary"
						onclick={() => saveValue(key)}
						disabled={disabled || savingKey !== null || !value.trim()}
					>
						{savingKey === key ? 'Saving…' : 'Save'}
					</button>
				</div>
			{/if}
		</div>
	</div>
{/snippet}

<section class:dirty={hasChanges} class="naming-card">
	<header>
		<Icon name="image" size={15} />
		<div>
			<h3>Poster naming</h3>
			<p>Choose predictable filename stems for deployed poster files.</p>
		</div>
		<span>Next job</span>
	</header>

	<div class="naming-groups">
		<section class="naming-group" aria-labelledby="poster-naming-movies">
			<div class="group-heading">
				<span id="poster-naming-movies" class="group-label">Films</span>
			</div>
			{@render filenameRail(
				'MOVIE_POSTER_FORMAT',
				'Film poster filename',
				'Use the media filename or a conventional poster name.',
				MOVIE_PRESETS
			)}
		</section>

		<section class="naming-group" aria-labelledby="poster-naming-television">
			<div class="group-heading">
				<span id="poster-naming-television" class="group-label">Television</span>
			</div>
			{@render filenameRail(
				'SERIES_POSTER_FORMAT',
				'Show poster filename',
				'Use the conventional show poster name or enter your own.',
				SERIES_PRESETS
			)}
			{@render filenameRail(
				'SEASON_POSTER_FORMAT',
				'Season poster filename',
				'Keep the season placeholder or enter your own template.',
				SEASON_PRESETS
			)}
		</section>
	</div>

	<footer>
		<button
			type="button"
			class="pill ghost"
			onclick={resetDefaults}
			disabled={disabled || resetting}
		>
			{resetting ? 'Resetting…' : 'Reset naming defaults'}
		</button>
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
	.naming-group + .naming-group {
		border-top: 1px solid var(--line);
	}
	.group-heading {
		padding: 12px 15px 7px;
	}
	.group-label {
		color: var(--muted);
		font: 700 9px/1.2 var(--font-mono);
		letter-spacing: 0.08em;
		text-transform: uppercase;
	}
	.filename-rail {
		display: grid;
		grid-template-columns: minmax(155px, 0.56fr) minmax(0, 1.44fr);
		gap: 16px;
		align-items: start;
		padding: 10px 15px 14px;
	}
	.filename-rail + .filename-rail {
		border-top: 1px dashed color-mix(in srgb, var(--line2) 72%, transparent);
	}
	.filename-rail.dirty {
		background: color-mix(in srgb, var(--gold) 4%, transparent);
	}
	.filename-copy {
		display: grid;
		gap: 4px;
		padding-top: 5px;
	}
	.filename-copy label {
		color: var(--text);
		font-size: 11px;
		font-weight: 700;
	}
	.filename-copy small,
	.filename-meta {
		color: var(--muted);
		font: 10px/1.4 var(--font-mono);
	}
	.filename-editor {
		display: grid;
		gap: 7px;
		min-width: 0;
	}
	.preset-controls {
		display: grid;
		justify-items: start;
		gap: 3px;
		margin-top: 7px;
	}
	.preset-controls button {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		min-height: 22px;
		border: 0;
		border-radius: 3px;
		background: transparent;
		color: var(--muted);
		padding: 2px 3px 2px 0;
		font: 650 10px/1.25 var(--font-mono);
		letter-spacing: 0.01em;
		text-align: left;
	}
	.preset-controls button.active {
		color: var(--text);
		font-weight: 700;
	}
	.preset-controls button:hover:not(:disabled):not(.active) {
		color: var(--text);
	}
	.selection-dot {
		display: grid;
		width: 12px;
		height: 12px;
		flex: 0 0 12px;
		place-items: center;
		border: 1px solid var(--faint);
		border-radius: 50%;
	}
	.selection-dot::after {
		width: 5px;
		height: 5px;
		border-radius: 50%;
		background: transparent;
		content: '';
	}
	.preset-controls button.active .selection-dot {
		border-color: var(--gold);
	}
	.preset-controls button.active .selection-dot::after {
		background: var(--gold);
	}
	.preset-controls button:focus-visible,
	input:focus-visible,
	.row-actions button:focus-visible,
	footer button:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	input {
		width: 100%;
		min-width: 0;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--text);
		padding: 8px 9px;
		font: 11px/1.4 var(--font-mono);
	}
	.filename-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		min-height: 14px;
	}
	.row-actions {
		display: flex;
		justify-content: flex-end;
		gap: 7px;
		padding-top: 1px;
	}
	button:disabled,
	input:disabled {
		cursor: not-allowed;
		opacity: 0.52;
	}
	footer {
		display: flex;
		justify-content: flex-end;
		padding: 10px 15px;
		border-top: 1px solid var(--line);
		background: color-mix(in srgb, var(--panel2) 35%, transparent);
	}
	@media (max-width: 720px) {
		.filename-rail {
			grid-template-columns: 1fr;
			gap: 9px;
		}
		.filename-copy {
			padding-top: 0;
		}
		.row-actions {
			justify-content: flex-start;
		}
	}
</style>
