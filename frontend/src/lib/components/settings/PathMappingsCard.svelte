<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { testPathMappings, type PathMappingTestResult } from '$lib/api/system';
	import { toast } from '$lib/toast';

	let {
		mediaRoots,
		radarrPrefix,
		radarrTarget,
		sonarrPrefix,
		sonarrTarget,
		dirty = false,
		disabled = false,
		onChange,
		onReset
	}: {
		mediaRoots: string[];
		radarrPrefix: string;
		radarrTarget: string;
		sonarrPrefix: string;
		sonarrTarget: string;
		dirty?: boolean;
		disabled?: boolean;
		onChange: (key: string, value: unknown) => void;
		onReset: () => void;
	} = $props();

	let testing = $state(false);
	let result = $state<PathMappingTestResult | null>(null);

	function roots(raw: string): string[] {
		return raw
			.split('\n')
			.map((value) => value.trim())
			.filter(Boolean);
	}

	function change(key: string, value: unknown) {
		result = null;
		onChange(key, value);
	}

	async function testPaths() {
		testing = true;
		try {
			result = await testPathMappings(fetch, {
				media_roots: mediaRoots,
				...(radarrPrefix ? { radarr_path_prefix: radarrPrefix } : {}),
				...(radarrTarget ? { radarr_media_path: radarrTarget } : {}),
				...(sonarrPrefix ? { sonarr_path_prefix: sonarrPrefix } : {}),
				...(sonarrTarget ? { sonarr_media_path: sonarrTarget } : {})
			});
			const facts = [
				...result.media_roots,
				...Object.values(result.mappings).flatMap((mapping) =>
					mapping.target ? [mapping.target] : []
				)
			];
			toast(
				facts.every((fact) => fact.readable)
					? 'All configured paths are readable'
					: 'Some configured paths are unavailable',
				facts.every((fact) => fact.readable) ? 'good' : 'info'
			);
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not validate path mappings', 'bad');
		} finally {
			testing = false;
		}
	}
</script>

<section class:dirty class="path-card">
	<header>
		<Icon name="film" size={15} />
		<div>
			<h3>Library paths</h3>
			<p>Translate remote Arr paths into container-visible media roots.</p>
		</div>
		<span>Next job</span>
	</header>

	<div class="path-layout">
		<div class="mapping-list">
			<div class="mapping-row">
				<div class="provider"><b>Radarr</b><small>Remote prefix → Marquee path</small></div>
				<label
					><span>Arr path prefix</span><input
						type="text"
						value={radarrPrefix}
						{disabled}
						placeholder="/movies"
						oninput={(event) =>
							change('RADARR_PATH_PREFIX', (event.currentTarget as HTMLInputElement).value || null)}
					/></label
				>
				<i>→</i>
				<label
					><span>Container media path</span><input
						type="text"
						value={radarrTarget}
						{disabled}
						placeholder="/media/movies"
						oninput={(event) =>
							change('RADARR_MEDIA_PATH', (event.currentTarget as HTMLInputElement).value || null)}
					/></label
				>
			</div>
			<div class="mapping-row">
				<div class="provider"><b>Sonarr</b><small>Remote prefix → Marquee path</small></div>
				<label
					><span>Arr path prefix</span><input
						type="text"
						value={sonarrPrefix}
						{disabled}
						placeholder="/tv"
						oninput={(event) =>
							change('SONARR_PATH_PREFIX', (event.currentTarget as HTMLInputElement).value || null)}
					/></label
				>
				<i>→</i>
				<label
					><span>Container media path</span><input
						type="text"
						value={sonarrTarget}
						{disabled}
						placeholder="/media/tv"
						oninput={(event) =>
							change('SONARR_MEDIA_PATH', (event.currentTarget as HTMLInputElement).value || null)}
					/></label
				>
			</div>
		</div>
		<label class="roots-field">
			<span>Additional allowed media roots</span>
			<textarea
				rows="5"
				value={mediaRoots.join('\n')}
				{disabled}
				placeholder="One absolute container path per line"
				oninput={(event) =>
					change('MEDIA_ROOTS', roots((event.currentTarget as HTMLTextAreaElement).value))}
			></textarea>
			<small
				>Arr media targets are included automatically. Host mount sources are never exposed here.</small
			>
		</label>
	</div>

	{#if result}
		<div class="path-results" aria-live="polite">
			{#each [...result.media_roots, ...Object.values(result.mappings).flatMap( (mapping) => (mapping.target ? [mapping.target] : []) )] as fact (fact.path)}
				<div>
					<code>{fact.path}</code><span class:good={fact.readable}
						>{fact.readable ? 'Readable' : fact.exists ? 'Not readable' : 'Not found'}{fact.writable
							? ' · Writable'
							: ''}</span
					>
				</div>
			{/each}
		</div>
	{/if}

	<footer>
		<button type="button" class="reset" onclick={onReset} {disabled}>Reset path defaults</button>
		<button type="button" class="test" onclick={testPaths} disabled={disabled || testing}
			>{testing ? 'Testing…' : 'Test accessibility'}</button
		>
	</footer>
</section>

<style>
	.path-card {
		margin-bottom: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		overflow: hidden;
	}
	.path-card.dirty {
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
	.path-layout {
		display: grid;
		grid-template-columns: minmax(0, 1.7fr) minmax(220px, 0.7fr);
	}
	.mapping-list {
		min-width: 0;
	}
	.mapping-row {
		display: grid;
		grid-template-columns: 130px minmax(130px, 1fr) auto minmax(130px, 1fr);
		align-items: end;
		gap: 9px;
		padding: 13px 15px;
		border-bottom: 1px solid var(--line);
	}
	.mapping-row:last-child {
		border-bottom: 0;
	}
	.provider {
		display: grid;
		gap: 2px;
		align-self: center;
	}
	.provider b {
		font-size: 12px;
	}
	.provider small,
	.roots-field small {
		color: var(--muted);
		font-size: 10px;
	}
	.mapping-row label,
	.roots-field {
		display: grid;
		gap: 5px;
		color: var(--muted);
		font-size: 10px;
		font-weight: 650;
	}
	.mapping-row i {
		align-self: center;
		color: var(--muted);
		font-style: normal;
	}
	input,
	textarea {
		width: 100%;
		min-width: 0;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
		padding: 8px 9px;
		font: 11px/1.4 var(--font-mono);
	}
	textarea {
		resize: vertical;
	}
	input:disabled,
	textarea:disabled {
		opacity: 0.52;
		cursor: not-allowed;
	}
	.roots-field {
		padding: 14px 15px;
		border-left: 1px solid var(--line);
	}
	.path-results {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 1px;
		border-top: 1px solid var(--line);
		background: var(--line);
	}
	.path-results div {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 8px 11px;
		background: var(--panel2);
	}
	.path-results code {
		overflow: hidden;
		color: var(--muted);
		font: 9.5px/1.4 var(--font-mono);
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.path-results span {
		flex: none;
		color: var(--bad);
		font-size: 9px;
		text-transform: uppercase;
	}
	.path-results span.good {
		color: var(--good);
	}
	footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding: 10px 15px;
		border-top: 1px solid var(--line);
	}
	footer button {
		border-radius: 7px;
		padding: 7px 10px;
		font-size: 10.5px;
		font-weight: 650;
	}
	.reset {
		border: 0;
		background: transparent;
		color: var(--muted);
	}
	.test {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	@media (max-width: 920px) {
		.path-layout {
			grid-template-columns: 1fr;
		}
		.roots-field {
			border-top: 1px solid var(--line);
			border-left: 0;
		}
	}
	@media (max-width: 680px) {
		.mapping-row {
			grid-template-columns: 1fr;
		}
		.mapping-row i {
			display: none;
		}
	}
</style>
