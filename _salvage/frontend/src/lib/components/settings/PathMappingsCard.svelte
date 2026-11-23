<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { testPathMappings, type PathMappingTestResult } from '$lib/api/system';
	import { toast } from '$lib/toast';

	type MappingKey = 'RADARR_PATH_MAPPINGS' | 'SONARR_PATH_MAPPINGS';
	type EditablePathMapping = { arr_path: string; marquee_path: string };
	type PathFact = NonNullable<PathMappingTestResult['path_mappings']['radarr'][number]['target']>;

	let {
		radarrMappings,
		sonarrMappings,
		dirty = false,
		disabled = false,
		onChange,
		onReset,
		onTestResults
	}: {
		radarrMappings: EditablePathMapping[];
		sonarrMappings: EditablePathMapping[];
		dirty?: boolean;
		disabled?: boolean;
		onChange: (key: string, value: unknown) => void;
		onReset: () => void;
		onTestResults?: (facts: PathFact[]) => void;
	} = $props();

	let testing = $state(false);

	function change(key: string, value: unknown) {
		onTestResults?.([]);
		onChange(key, value);
	}

	function addMapping(key: MappingKey, mappings: EditablePathMapping[]) {
		change(key, [...mappings, { arr_path: '', marquee_path: '' }]);
	}

	function updateMapping(
		key: MappingKey,
		mappings: EditablePathMapping[],
		index: number,
		field: keyof EditablePathMapping,
		value: string
	) {
		change(
			key,
			mappings.map((mapping, mappingIndex) =>
				mappingIndex === index ? { ...mapping, [field]: value } : mapping
			)
		);
	}

	function removeMapping(key: MappingKey, mappings: EditablePathMapping[], index: number) {
		change(
			key,
			mappings.filter((_, mappingIndex) => mappingIndex !== index)
		);
	}

	function requestMappings(mappings: EditablePathMapping[]): EditablePathMapping[] {
		// Empty rows are a harmless drafting affordance; a half-filled row still reaches the
		// server, which can name the missing side of the mapping in its validation error.
		return mappings.filter((mapping) => mapping.arr_path.trim() || mapping.marquee_path.trim());
	}

	async function testPaths() {
		testing = true;
		try {
			const result = await testPathMappings(fetch, {
				media_roots: [],
				radarr_mappings: requestMappings(radarrMappings),
				sonarr_mappings: requestMappings(sonarrMappings)
			});
			const mappingFacts = Object.values(result.path_mappings).flatMap((mappings) =>
				mappings.flatMap((mapping) => (mapping.target ? [mapping.target] : []))
			);
			const allFacts = [...result.media_roots, ...mappingFacts];
			onTestResults?.(mappingFacts);
			toast(
				allFacts.every((fact) => fact.readable)
					? 'All configured paths are readable'
					: 'Some configured paths are unavailable',
				allFacts.every((fact) => fact.readable) ? 'good' : 'info'
			);
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not validate path mappings', 'bad');
		} finally {
			testing = false;
		}
	}
</script>

{#snippet mappingGroup(
	title: string,
	arrLabel: string,
	settingKey: MappingKey,
	mappings: EditablePathMapping[]
)}
	<section class="library-group" aria-label={`${title} library paths`}>
		<header>
			<h4>{title}</h4>
			<button
				type="button"
				class="add-path"
				onclick={() => addMapping(settingKey, mappings)}
				{disabled}
			>
				+ Add path
			</button>
		</header>
		<div class="path-heading" aria-hidden="true">
			<span>{arrLabel}</span><span>Marquee Path</span>
		</div>
		<div class="mapping-rows">
			{#if mappings.length}
				{#each mappings as mapping, index (index)}
					<div class="mapping-row">
						<label>
							<span class="sr-only">{arrLabel} {index + 1}</span>
							<input
								type="text"
								value={mapping.arr_path}
								placeholder={arrLabel === 'Radarr Path' ? '/movies' : '/tv'}
								aria-label={`${arrLabel} ${index + 1}`}
								{disabled}
								oninput={(event) =>
									updateMapping(
										settingKey,
										mappings,
										index,
										'arr_path',
										(event.currentTarget as HTMLInputElement).value
									)}
							/>
						</label>
						<i aria-hidden="true">→</i>
						<label>
							<span class="sr-only">Marquee Path {index + 1}</span>
							<input
								type="text"
								value={mapping.marquee_path}
								placeholder={arrLabel === 'Radarr Path' ? '/media/movies' : '/media/tv'}
								aria-label={`Marquee Path ${title} ${index + 1}`}
								{disabled}
								oninput={(event) =>
									updateMapping(
										settingKey,
										mappings,
										index,
										'marquee_path',
										(event.currentTarget as HTMLInputElement).value
									)}
							/>
						</label>
						<button
							type="button"
							class="remove-path"
							onclick={() => removeMapping(settingKey, mappings, index)}
							aria-label={`Remove ${title} path ${index + 1}`}
							{disabled}
						>
							Remove
						</button>
					</div>
				{/each}
			{:else}
				<p class="empty-paths">No paths have been added.</p>
			{/if}
		</div>
	</section>
{/snippet}

<section class:dirty class="path-card">
	<header>
		<Icon name="film" size={15} />
		<div>
			<h3>Library paths</h3>
			<p>Map Arr folders to the paths available inside Marquee.</p>
		</div>
		<span>Next job</span>
	</header>

	<div class="mapping-list">
		{@render mappingGroup('Films', 'Radarr Path', 'RADARR_PATH_MAPPINGS', radarrMappings)}
		{@render mappingGroup('Television', 'Sonarr Path', 'SONARR_PATH_MAPPINGS', sonarrMappings)}
	</div>

	<footer>
		<button type="button" class="pill ghost" onclick={onReset} {disabled}
			>Reset path defaults</button
		>
		<button type="button" class="pill quiet" onclick={testPaths} disabled={disabled || testing}
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
	.path-card > header {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		gap: 9px;
		align-items: center;
		padding: 12px 15px;
		border-bottom: 1px solid var(--line);
		color: var(--muted);
	}
	.path-card > header h3 {
		margin: 0;
		color: var(--text);
		font-size: 12px;
	}
	.path-card > header p {
		margin: 2px 0 0;
		font-size: 11px;
	}
	.path-card > header > span {
		color: var(--muted);
		font: 600 9px/1.2 var(--font-mono);
		text-transform: uppercase;
	}
	.mapping-list {
		min-width: 0;
	}
	.library-group + .library-group {
		border-top: 1px solid var(--line);
	}
	.library-group > header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 11px 15px 7px;
	}
	.library-group h4 {
		margin: 0;
		color: var(--text);
		font-size: 12px;
		font-weight: 700;
	}
	.add-path,
	.remove-path {
		border: 1px solid var(--line2);
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--muted);
		font-size: 10px;
		font-weight: 650;
	}
	.add-path {
		padding: 5px 9px;
		color: var(--gold-copy);
	}
	.add-path:hover:not(:disabled) {
		border-color: color-mix(in srgb, var(--gold) 48%, var(--line2));
		background: color-mix(in srgb, var(--gold) 8%, transparent);
	}
	.remove-path {
		padding: 5px 8px;
	}
	.remove-path:hover:not(:disabled) {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 42%, var(--line2));
	}
	.add-path:focus-visible,
	.remove-path:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	.add-path:disabled,
	.remove-path:disabled {
		cursor: not-allowed;
		opacity: 0.52;
	}
	.path-heading,
	.mapping-row {
		display: grid;
		grid-template-columns: minmax(120px, 1fr) auto minmax(120px, 1fr) auto;
		align-items: center;
		gap: 9px;
		padding-inline: 15px;
	}
	.path-heading {
		padding-bottom: 6px;
		color: var(--muted);
		font: 650 9px/1.2 var(--font-mono);
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}
	.path-heading span:last-child {
		grid-column: 3;
	}
	.mapping-row > i {
		color: var(--faint);
		font-style: normal;
	}
	.mapping-row {
		padding-block: 8px 12px;
	}
	.mapping-row + .mapping-row {
		border-top: 1px dashed color-mix(in srgb, var(--line2) 72%, transparent);
		padding-top: 12px;
	}
	.mapping-row label {
		display: block;
		min-width: 0;
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
	input:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	input:disabled {
		cursor: not-allowed;
		opacity: 0.52;
	}
	.empty-paths {
		margin: 0;
		padding: 3px 15px 13px;
		color: var(--muted);
		font-size: 11px;
	}
	.path-card > footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding: 10px 15px;
		border-top: 1px solid var(--line);
	}
	@media (max-width: 680px) {
		.path-heading {
			display: none;
		}
		.mapping-row {
			grid-template-columns: 1fr;
			gap: 7px;
		}
		.mapping-row > i {
			display: none;
		}
		.remove-path {
			justify-self: start;
		}
	}
</style>
