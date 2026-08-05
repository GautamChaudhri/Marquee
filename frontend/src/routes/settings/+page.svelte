<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { beforeNavigate } from '$app/navigation';
	import Icon from '$lib/components/Icon.svelte';
	import ConnectionRegistry from '$lib/components/settings/ConnectionRegistry.svelte';
	import PathMappingsCard from '$lib/components/settings/PathMappingsCard.svelte';
	import PosterNamingCard from '$lib/components/settings/PosterNamingCard.svelte';
	import SettingsField from '$lib/components/settings/SettingsField.svelte';
	import {
		getSettings,
		putConfiguration,
		resetConfiguration,
		type PathMappingTestResult
	} from '$lib/api/system';
	import { CONFIGURATION_CONFLICT_MESSAGE, isConfigurationConflict } from '$lib/api/client';
	import type { RuntimeSettings, SettingsCatalogEntry, SettingsTab } from '$lib/api/types';
	import { filmMode, libraryPosterSize, televisionMode, theme } from '$lib/theme';
	import { SETTINGS_TABS } from '$lib/settings/navigation';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const tabDetails: Record<SettingsTab, { label: string; icon: string; hint: string }> = {
		general: { label: 'General', icon: 'settings', hint: 'Identity and appearance' },
		connections: { label: 'Connections', icon: 'refresh', hint: 'Services and sync' },
		media: { label: 'Media', icon: 'film', hint: 'Roots and path maps' },
		posters: { label: 'Posters', icon: 'image', hint: 'Names and restoration' },
		pipeline: { label: 'Pipeline', icon: 'pipeline', hint: 'Scoring and execution' },
		taste: { label: 'Taste', icon: 'taste', hint: 'Profiles and learning' },
		system: { label: 'System', icon: 'activity', hint: 'Jobs and resources' },
		access: { label: 'Access', icon: 'eye', hint: 'Security posture' }
	};
	const tabs = SETTINGS_TABS.map((id) => ({ id, ...tabDetails[id] }));
	type EditablePathMapping = { arr_path: string; marquee_path: string };
	const POSTER_FORMAT_KEYS = [
		'MOVIE_POSTER_FORMAT',
		'SERIES_POSTER_FORMAT',
		'SEASON_POSTER_FORMAT'
	] as const;
	type PosterFormatKey = (typeof POSTER_FORMAT_KEYS)[number];
	type TestedMediaPath = NonNullable<
		PathMappingTestResult['path_mappings']['radarr'][number]['target']
	>;

	// svelte-ignore state_referenced_locally
	let settings = $state<RuntimeSettings | null>(data.settings);
	// svelte-ignore state_referenced_locally
	let loadError = $state<string | null>(data.error);
	// svelte-ignore state_referenced_locally
	let activeTab = $state<SettingsTab>(data.initialTab);
	// svelte-ignore state_referenced_locally
	let advancedOpen = $state(data.initialLevel === 'advanced');
	let drafts = $state<Record<string, unknown>>({});
	/** Keys staged to be dropped from the revision, so their default applies again. */
	let stagedResets = new SvelteSet<string>();
	let saving = $state(false);
	let resetting = $state(false);
	let posterSaving = $state(false);
	let resetArmed = $state(false);
	let conflictNote = $state<string | null>(null);
	let secureContext = $state(false);
	let advancedRegion = $state<HTMLElement | null>(null);
	let testedMediaPaths = $state<TestedMediaPath[]>([]);

	const allEntries = $derived(
		settings ? Object.values(settings.catalog).filter((entry) => entry.visible) : []
	);
	const dirtyKeys = $derived([...Object.keys(drafts), ...stagedResets]);
	const dirtyCount = $derived(dirtyKeys.length);
	/* Rows read their dirty flag from this rather than probing `drafts` directly:
	   Object.hasOwn goes through the state proxy's getOwnPropertyDescriptor trap,
	   which registers no dependency, so editing a value moved the save-bar count
	   but never lit up the row that changed. */
	const dirtyKeySet = $derived(new Set(dirtyKeys));
	const tabDirtyCount = $derived(
		dirtyKeys.filter((key) => settings?.catalog[key]?.tab === activeTab).length
	);
	const hasDedicatedContent = $derived(['connections', 'media', 'posters'].includes(activeTab));

	// Keys owned by a bespoke card on this tab, so the catalog grid does not render
	// a second, duller control for the same setting.
	const CLAIMED_KEYS: Partial<Record<SettingsTab, string[]>> = {
		general: ['MARQUEE_ENVIRONMENT', 'MARQUEE_PROCESS_ROLE', 'HOST', 'PORT'],
		connections: ['RADARR_URL', 'RADARR_INSTANCE_NAME', 'SONARR_URL', 'SONARR_INSTANCE_NAME'],
		media: [
			'RADARR_PATH_PREFIX',
			'RADARR_MEDIA_PATH',
			'RADARR_PATH_MAPPINGS',
			'SONARR_PATH_PREFIX',
			'SONARR_MEDIA_PATH',
			'SONARR_PATH_MAPPINGS'
		],
		posters: ['MOVIE_POSTER_FORMAT', 'SERIES_POSTER_FORMAT', 'SEASON_POSTER_FORMAT']
	};

	function sectionsFor(level: 'standard' | 'advanced') {
		const claimed = CLAIMED_KEYS[activeTab] ?? [];
		const grouped = new SvelteMap<string, SettingsCatalogEntry[]>();
		for (const entry of allEntries) {
			if (entry.tab !== activeTab || entry.level !== level) continue;
			if (entry.storage === 'secret_store') continue;
			if (claimed.includes(entry.key)) continue;
			const rows = grouped.get(entry.section) ?? [];
			rows.push(entry);
			grouped.set(entry.section, rows);
		}
		return [...grouped.entries()].map(([name, entries]) => ({ name, entries }));
	}

	const standardSections = $derived(sectionsFor('standard'));
	const advancedSections = $derived(sectionsFor('advanced'));
	const advancedCount = $derived(
		advancedSections.reduce((total, section) => total + section.entries.length, 0)
	);
	const resettableCount = $derived(
		allEntries.filter(
			(entry) =>
				entry.storage === 'revision' &&
				(settings?.sources[entry.key] === 'custom' || settings?.sources[entry.key] === 'revision')
		).length
	);

	function same(left: unknown, right: unknown): boolean {
		return JSON.stringify(left) === JSON.stringify(right);
	}

	function valueFor(key: string): unknown {
		// `key in drafts` goes through the state proxy's has() trap, which subscribes.
		// Object.hasOwn does not, so a draft could change without redrawing the row.
		if (key in drafts) return drafts[key];
		// A staged reset previews the default it will restore.
		if (stagedResets.has(key)) return settings?.defaults[key];
		return settings?.values[key];
	}

	function mappingListFor(
		key: string,
		legacyPrefixKey: string,
		legacyTargetKey: string
	): EditablePathMapping[] {
		const value = valueFor(key);
		if (Array.isArray(value)) {
			return value.flatMap((mapping) => {
				if (!mapping || typeof mapping !== 'object') return [];
				const candidate = mapping as Record<string, unknown>;
				return [
					{
						arr_path: String(candidate.arr_path ?? ''),
						marquee_path: String(candidate.marquee_path ?? '')
					}
				];
			});
		}
		const arrPath = String(valueFor(legacyPrefixKey) ?? '');
		const marqueePath = String(valueFor(legacyTargetKey) ?? '');
		return arrPath || marqueePath ? [{ arr_path: arrPath, marquee_path: marqueePath }] : [];
	}

	function pathStatusFor(entry: SettingsCatalogEntry) {
		if (activeTab !== 'media' || entry.section.toLowerCase() !== 'application paths') {
			return undefined;
		}
		const value = valueFor(entry.key);
		if (typeof value !== 'string') return undefined;
		return settings?.deployment.mounts.find((mount) => mount.path === value);
	}

	function changeValue(key: string, value: unknown) {
		if (!settings) return;
		stagedResets.delete(key);
		if (same(value, settings.values[key])) delete drafts[key];
		else drafts[key] = value;
		conflictNote = null;
	}

	/**
	 * Stage a reset rather than writing the default value. Writing it would store
	 * the default as an override, so the key would read "Custom" forever and stop
	 * tracking any future change to the shipped default.
	 */
	function resetValue(entry: SettingsCatalogEntry) {
		if (!settings || entry.storage !== 'revision') return;
		delete drafts[entry.key];
		const source = settings.sources[entry.key];
		if (source === 'custom' || source === 'revision') stagedResets.add(entry.key);
		conflictNote = null;
	}

	function resetKeys(keys: string[]) {
		if (!settings) return;
		for (const key of keys) {
			const entry = settings.catalog[key];
			if (entry) resetValue(entry);
		}
	}

	function discardDraft() {
		drafts = {};
		stagedResets.clear();
	}

	function updateQuery() {
		if (typeof window === 'undefined') return;
		const url = new URL(window.location.href);
		url.searchParams.set('tab', activeTab);
		// A tab with nothing advanced has no advanced view to link to, so the URL
		// never claims one — otherwise the link would open to nothing.
		if (advancedOpen && advancedCount) url.searchParams.set('level', 'advanced');
		else url.searchParams.delete('level');
		window.history.replaceState(window.history.state, '', url);
	}

	function selectTab(tab: SettingsTab) {
		activeTab = tab;
		resetArmed = false;
		updateQuery();
	}

	async function toggleAdvanced() {
		advancedOpen = !advancedOpen;
		updateQuery();
		if (!advancedOpen) return;
		await tick();
		advancedRegion?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}

	function navigateTabs(event: KeyboardEvent) {
		if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
		event.preventDefault();
		const index = tabs.findIndex((tab) => tab.id === activeTab);
		const nextIndex =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? tabs.length - 1
					: (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
		selectTab(tabs[nextIndex].id);
		requestAnimationFrame(() => {
			document
				.querySelector<HTMLButtonElement>(`[data-settings-tab="${tabs[nextIndex].id}"]`)
				?.focus();
		});
	}

	/** Refetch after a lost optimistic race, keeping the draft so nothing is typed twice. */
	async function absorbConflict(previous: Record<string, unknown>) {
		const fresh = await getSettings(fetch);
		const changedElsewhere = Object.keys(fresh.values).filter(
			(key) => !same(previous[key], fresh.values[key])
		);
		settings = fresh;
		conflictNote = changedElsewhere.length
			? `${CONFIGURATION_CONFLICT_MESSAGE} Changed elsewhere: ${changedElsewhere
					.slice(0, 5)
					.map((key) => fresh.catalog[key]?.title ?? key)
					.join(', ')}${changedElsewhere.length > 5 ? '…' : ''}`
			: CONFIGURATION_CONFLICT_MESSAGE;
		toast('Newer settings loaded; your draft is still here', 'info');
	}

	async function save() {
		if (!settings || !dirtyCount || saving) return;
		saving = true;
		conflictNote = null;
		try {
			// Writes and resets travel together so the whole draft lands as one revision.
			const result = await putConfiguration(fetch, { ...drafts }, settings.configuration_version, [
				...stagedResets
			]);
			settings = result.settings;
			discardDraft();
			toast(
				result.changed ? 'Settings saved' : 'No settings changed',
				result.changed ? 'good' : 'info'
			);
		} catch (error) {
			if (isConfigurationConflict(error)) await absorbConflict(settings.values);
			else toast(error instanceof Error ? error.message : 'Could not save settings', 'bad');
		} finally {
			saving = false;
		}
	}

	function clearPosterDrafts() {
		for (const key of POSTER_FORMAT_KEYS) {
			delete drafts[key];
			stagedResets.delete(key);
		}
	}

	async function savePosterFormat(key: PosterFormatKey, value: string): Promise<boolean> {
		if (!settings || posterSaving) return false;
		posterSaving = true;
		conflictNote = null;
		const previous = { ...settings.values };
		try {
			const result = await putConfiguration(
				fetch,
				{ [key]: value },
				settings.configuration_version
			);
			settings = result.settings;
			delete drafts[key];
			stagedResets.delete(key);
			toast(
				result.changed ? 'Poster filename saved' : 'Poster filename was unchanged',
				result.changed ? 'good' : 'info'
			);
			return true;
		} catch (error) {
			if (isConfigurationConflict(error)) await absorbConflict(previous);
			else toast(error instanceof Error ? error.message : 'Could not save poster filename', 'bad');
			return false;
		} finally {
			posterSaving = false;
		}
	}

	async function resetPosterFormats(): Promise<boolean> {
		if (!settings || posterSaving) return false;
		posterSaving = true;
		conflictNote = null;
		const previous = { ...settings.values };
		try {
			const result = await putConfiguration(fetch, {}, settings.configuration_version, [
				...POSTER_FORMAT_KEYS
			]);
			settings = result.settings;
			clearPosterDrafts();
			const count = result.applied?.length ?? 0;
			toast(
				count ? 'Poster naming defaults restored' : 'Poster names already use their defaults',
				count ? 'good' : 'info'
			);
			return true;
		} catch (error) {
			if (isConfigurationConflict(error)) await absorbConflict(previous);
			else toast(error instanceof Error ? error.message : 'Could not reset poster naming', 'bad');
			return false;
		} finally {
			posterSaving = false;
		}
	}

	/** Drop stored overrides server-side. Applies immediately — it is not staged. */
	async function applyReset(scope: 'all' | 'tab') {
		if (!settings || resetting) return;
		resetting = true;
		conflictNote = null;
		try {
			const result = await resetConfiguration(fetch, {
				expected_version: settings.configuration_version,
				scope,
				...(scope === 'tab' ? { tab: activeTab } : {})
			});
			settings = result.settings;
			discardDraft();
			resetArmed = false;
			const count = result.applied?.length ?? 0;
			toast(
				count
					? `${count} ${count === 1 ? 'setting' : 'settings'} restored to defaults`
					: 'Everything was already on its default',
				count ? 'good' : 'info'
			);
		} catch (error) {
			if (isConfigurationConflict(error)) await absorbConflict(settings.values);
			else toast(error instanceof Error ? error.message : 'Could not reset settings', 'bad');
		} finally {
			resetting = false;
		}
	}

	function acceptIntegrationSettings(next: RuntimeSettings) {
		settings = next;
	}

	function deploymentValue(label: string, value: string | number | boolean) {
		return {
			label,
			value: typeof value === 'boolean' ? (value ? 'Configured' : 'Not configured') : value
		};
	}

	onMount(() => {
		// A ?level=advanced deep link lands with the disclosure already open; bring
		// it into view so the link points at something the reader can see. If the
		// tab has no advanced entries, drop the claim from the URL instead.
		if (advancedOpen && advancedCount) {
			tick().then(() => advancedRegion?.scrollIntoView({ block: 'start' }));
		} else if (advancedOpen) {
			advancedOpen = false;
			updateQuery();
		}
		secureContext =
			window.isSecureContext || ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
		const unload = (event: BeforeUnloadEvent) => {
			if (!dirtyCount) return;
			event.preventDefault();
		};
		window.addEventListener('beforeunload', unload);
		return () => window.removeEventListener('beforeunload', unload);
	});

	beforeNavigate((navigation) => {
		if (!dirtyCount || !navigation.to || navigation.to.url.pathname === '/settings') return;
		if (!window.confirm('Leave Settings and discard your unsaved draft?')) navigation.cancel();
	});
</script>

<!-- Standard and advanced render the same grid; only the entry set differs. -->
{#snippet cardGrid(
	sections: { name: string; entries: SettingsCatalogEntry[] }[],
	lead: boolean,
	advanced = false
)}
	{#if settings && sections.length}
		<div class="cards-grid" class:after-lead={lead}>
			{#each sections as section (section.name)}
				<section class="settings-card">
					<header>
						<Icon
							name={activeTab === 'posters'
								? 'image'
								: (tabs.find((tab) => tab.id === activeTab)?.icon ?? 'settings')}
							size={15}
						/>
						<h3>{section.name}</h3>
						<!-- Several sections carry both standard and advanced keys, so without
						     this the same card title would appear twice on one page. -->
						<span
							>{advanced ? 'Advanced · ' : ''}{section.entries.length}
							{section.entries.length === 1 ? 'setting' : 'settings'}</span
						>
					</header>
					<div class="setting-list">
						{#each section.entries as entry (entry.key)}
							<SettingsField
								{entry}
								value={valueFor(entry.key)}
								defaultValue={settings.defaults[entry.key]}
								source={settings.sources[entry.key] ?? 'default'}
								pathStatus={pathStatusFor(entry)}
								dirty={dirtyKeySet.has(entry.key)}
								disabled={!settings.writable || saving || resetting}
								onChange={(value) => changeValue(entry.key, value)}
								onReset={() => resetValue(entry)}
							/>
						{/each}
					</div>
				</section>
			{/each}
		</div>
	{/if}
{/snippet}

<div class="settings-page">
	<!-- The visible "Settings" title lives in the top bar, but the document still
	     needs an h1 above the per-tab h2s. Same pattern as /taste. -->
	<h1 class="sr-only">Settings</h1>

	<nav class="tab-rail" aria-label="Settings sections">
		<div
			class="tab-scroll"
			role="tablist"
			aria-label="Settings sections"
			tabindex="-1"
			onkeydown={navigateTabs}
		>
			{#each tabs as tab (tab.id)}
				<button
					type="button"
					role="tab"
					data-settings-tab={tab.id}
					aria-selected={activeTab === tab.id}
					tabindex={activeTab === tab.id ? 0 : -1}
					class:active={activeTab === tab.id}
					onclick={() => selectTab(tab.id)}
				>
					<Icon name={tab.icon} size={15} />
					<span>{tab.label}</span>
					{#if dirtyKeys.some((key) => settings?.catalog[key]?.tab === tab.id)}
						<i aria-label="Unsaved changes"></i>
					{/if}
				</button>
			{/each}
		</div>
	</nav>

	{#if loadError || !settings}
		<section class="empty-state">
			<Icon name="alert" size={22} />
			<h2>Settings are unavailable</h2>
			<p>{loadError ?? 'Marquee did not return a settings document.'}</p>
		</section>
	{:else}
		<div class="tab-head">
			<div class="tab-title">
				<span class="kicker">{tabs.find((tab) => tab.id === activeTab)?.hint}</span>
				<h2>{tabs.find((tab) => tab.id === activeTab)?.label}</h2>
			</div>
			<div class="tab-head-actions">
				{#if resettableCount}
					<!-- A scoped reset writes straight through, so it would silently throw
					     away an unsaved draft. Make the user land the draft first. -->
					<button
						type="button"
						class="pill ghost"
						onclick={() => applyReset('tab')}
						disabled={resetting || !settings.writable || dirtyCount > 0}
						title={dirtyCount
							? 'Save or discard your draft before resetting'
							: 'Restore this tab to its defaults'}
					>
						Reset this tab
					</button>
				{/if}
			</div>
		</div>

		{#if conflictNote}
			<div class="notice conflict" role="status">
				<Icon name="alert" size={15} />
				{conflictNote}
			</div>
		{/if}
		{#if settings.stale}
			<div class="notice warning" role="status">
				<Icon name="alert" size={15} /> Configuration cache is degraded. Saving is disabled until it recovers.
			</div>
		{/if}

		{#if activeTab === 'general'}
			<div class="cards-grid lead-cards">
				<section class="settings-card preferences-card">
					<header>
						<Icon name="eye" size={15} />
						<h3>Browser appearance</h3>
						<span>Live · this browser</span>
					</header>
					<div class="preference-row">
						<div><b>Theme</b><small>Choose the projection-room palette.</small></div>
						<select aria-label="Theme" bind:value={$theme}
							><option value="dark">Dark</option><option value="light">Light</option></select
						>
					</div>
					<div class="preference-row">
						<div><b>Movie view</b><small>Default movie library arrangement.</small></div>
						<select aria-label="Movie view" bind:value={$filmMode}
							><option value="list">List</option><option value="grid">Grid</option></select
						>
					</div>
					<div class="preference-row">
						<div><b>Television view</b><small>Default television library arrangement.</small></div>
						<select aria-label="Television view" bind:value={$televisionMode}
							><option value="list">List</option><option value="grid">Grid</option></select
						>
					</div>
					<div class="preference-row">
						<div><b>Poster density</b><small>Artwork size in grid views.</small></div>
						<select aria-label="Poster density" bind:value={$libraryPosterSize}
							><option value="small">Compact</option><option value="medium">Balanced</option><option
								value="large">Large</option
							></select
						>
					</div>
				</section>

				<section class="settings-card deployment-card">
					<header>
						<Icon name="activity" size={15} />
						<h3>Running instance</h3>
						<span>Deployment facts</span>
					</header>
					<div class="fact-grid">
						{#each [deploymentValue('Version', settings.deployment.version), deploymentValue('Environment', settings.deployment.environment), deploymentValue('Process role', settings.deployment.process_role), deploymentValue('Bind address', settings.deployment.host), deploymentValue('Port', settings.deployment.port)] as fact (fact.label)}
							<div><span>{fact.label}</span><code>{fact.value}</code></div>
						{/each}
					</div>
					<p>
						These values describe the running container and are changed through deployment
						configuration.
					</p>
				</section>
			</div>
		{/if}

		{#if activeTab === 'connections'}
			<ConnectionRegistry {settings} {secureContext} onSettings={acceptIntegrationSettings} />
		{/if}

		{#if activeTab === 'media'}
			<div class="notice info">
				<Icon name="film" size={15} /> Logical roots and Arr path mappings are editable. Host bind mounts
				remain deployment-owned; Marquee never changes Docker mounts.
			</div>
			<PathMappingsCard
				radarrMappings={mappingListFor(
					'RADARR_PATH_MAPPINGS',
					'RADARR_PATH_PREFIX',
					'RADARR_MEDIA_PATH'
				)}
				sonarrMappings={mappingListFor(
					'SONARR_PATH_MAPPINGS',
					'SONARR_PATH_PREFIX',
					'SONARR_MEDIA_PATH'
				)}
				dirty={[
					'RADARR_PATH_PREFIX',
					'RADARR_MEDIA_PATH',
					'RADARR_PATH_MAPPINGS',
					'SONARR_PATH_PREFIX',
					'SONARR_MEDIA_PATH',
					'SONARR_PATH_MAPPINGS'
				].some((key) => dirtyKeySet.has(key))}
				disabled={!settings.writable || saving}
				onChange={changeValue}
				onTestResults={(facts) => (testedMediaPaths = facts)}
				onReset={() => {
					testedMediaPaths = [];
					resetKeys([
						'RADARR_PATH_PREFIX',
						'RADARR_MEDIA_PATH',
						'RADARR_PATH_MAPPINGS',
						'SONARR_PATH_PREFIX',
						'SONARR_MEDIA_PATH',
						'SONARR_PATH_MAPPINGS'
					]);
				}}
			/>
			{#if testedMediaPaths.length}
				<section class="mount-strip" aria-label="Marquee path checks">
					{#each testedMediaPaths as mount (mount.path)}
						<div>
							<code>{mount.path}</code><span class:good={mount.readable}
								>{mount.readable
									? 'readable'
									: mount.exists
										? 'not readable'
										: 'unavailable'}{mount.writable ? ' · writable' : ''}</span
							>
						</div>
					{/each}
				</section>
			{/if}
		{/if}

		{#if activeTab === 'posters'}
			<PosterNamingCard
				movie={String(settings.values.MOVIE_POSTER_FORMAT ?? 'poster.jpg')}
				series={String(settings.values.SERIES_POSTER_FORMAT ?? 'show.jpg')}
				season={String(settings.values.SEASON_POSTER_FORMAT ?? 'season{season:02d}.jpg')}
				disabled={!settings.writable || saving || resetting || posterSaving}
				onSave={savePosterFormat}
				onReset={resetPosterFormats}
			/>
		{/if}

		{#if activeTab === 'taste'}
			<div class="notice info">
				<Icon name="taste" size={15} /> These are server defaults and learning thresholds. Profile generation,
				maps, enrichment, and model operations remain in <a href="/taste">Taste operations</a>.
			</div>
		{/if}

		{#if activeTab === 'access'}
			<div class="security-grid">
				<section class:warning={!settings.deployment.api_key_configured} class="posture-card">
					<span>API authentication</span>
					<b>{settings.deployment.api_key_configured ? 'Configured' : 'Missing'}</b>
					<small>Bootstrap secret · never returned to the browser</small>
				</section>
				<section class="posture-card">
					<span>Managed credential storage</span>
					<b>{settings.secret_store.writable ? 'Enabled' : 'Optional'}</b>
					<small
						>{settings.secret_store.writable
							? 'Credentials can be rotated in Settings'
							: 'Environment credentials remain usable'}</small
					>
				</section>
				<section class:warning={!secureContext} class="posture-card">
					<span>Browser transport</span>
					<b>{secureContext ? 'HTTPS' : 'Internal HTTP'}</b>
					<small
						>{secureContext
							? 'Encrypted in transit'
							: 'Connection actions are allowed on trusted networks'}</small
					>
				</section>
				<section class:warning={settings.deployment.debug} class="posture-card">
					<span>Debug mode</span>
					<b>{settings.deployment.debug ? 'Enabled' : 'Disabled'}</b>
					<small>Deployment only</small>
				</section>
			</div>
		{/if}

		{@render cardGrid(standardSections, activeTab === 'general')}

		{#if standardSections.length === 0 && !hasDedicatedContent && !advancedCount}
			<section class="empty-state compact">
				<h2>No settings here</h2>
				<p>This section contains status or browser-local controls only.</p>
			</section>
		{/if}

		{#if advancedCount}
			<section class="advanced-block" bind:this={advancedRegion}>
				<div class="advanced-divider">
					<button
						type="button"
						class="pill quiet"
						aria-expanded={advancedOpen}
						aria-controls="settings-advanced"
						onclick={toggleAdvanced}
					>
						<span class="chev" aria-hidden="true">{advancedOpen ? '▴' : '▾'}</span>
						{advancedOpen ? 'Hide advanced' : 'Show advanced'}
						<span class="pill-count">{advancedCount}</span>
					</button>
				</div>
				<div id="settings-advanced" hidden={!advancedOpen}>
					{#if advancedOpen}
						{@render cardGrid(advancedSections, false, true)}
					{/if}
				</div>
			</section>
		{/if}

		{#if activeTab === 'system'}
			<section class="danger-card">
				<header>
					<Icon name="alert" size={15} />
					<div>
						<h3>Reset everything to defaults</h3>
						<p>
							Drops every stored override across all tabs so Marquee's own defaults apply again.
							Credentials, path mounts, and deployment values are untouched.
						</p>
					</div>
					<span>{resettableCount} overridden</span>
				</header>
				<footer>
					{#if resetArmed}
						<button type="button" class="pill ghost" onclick={() => (resetArmed = false)}>
							Cancel
						</button>
					{/if}
					<button
						type="button"
						class="pill danger"
						class:armed={resetArmed}
						onclick={() => (resetArmed ? applyReset('all') : (resetArmed = true))}
						disabled={resetting || !settings.writable || !resettableCount || dirtyCount > 0}
						title={dirtyCount ? 'Save or discard your draft before resetting' : undefined}
					>
						{resetting
							? 'Resetting…'
							: resetArmed
								? `Confirm — reset ${resettableCount} settings`
								: 'Reset everything'}
					</button>
				</footer>
			</section>
		{/if}

		{#if dirtyCount}
			<div class="save-bar" role="status">
				<div>
					<i></i>
					<span
						><b>{dirtyCount}</b> unsaved {dirtyCount === 1 ? 'change' : 'changes'}{tabDirtyCount
							? ` · ${tabDirtyCount} on this tab`
							: ''}{stagedResets.size ? ` · ${stagedResets.size} to reset` : ''}</span
					>
				</div>
				<div class="save-actions">
					<button
						type="button"
						class="pill ghost"
						onclick={discardDraft}
						disabled={saving || resetting}>Discard draft</button
					>
					<button
						type="button"
						class="pill primary"
						onclick={save}
						disabled={saving || resetting || !settings.writable}
					>
						{saving ? 'Saving…' : 'Save all changes'}
					</button>
				</div>
			</div>
		{/if}
	{/if}
</div>

<style>
	.settings-page {
		padding-bottom: 82px;
	}
	.tab-rail {
		position: sticky;
		top: 0;
		z-index: 12;
		margin: 0 0 18px;
		border: 1px solid var(--line);
		border-radius: var(--radius-pill);
		background: color-mix(in srgb, var(--ink2) 91%, transparent);
		box-shadow: 0 10px 30px color-mix(in srgb, var(--shadow) 24%, transparent);
		backdrop-filter: blur(14px);
	}
	.tab-scroll {
		display: grid;
		grid-template-columns: repeat(8, minmax(112px, 1fr));
		gap: 2px;
		overflow-x: auto;
		overscroll-behavior-x: contain;
		scroll-snap-type: x proximity;
		padding: 5px;
	}
	.tab-scroll button {
		position: relative;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 7px;
		min-width: 0;
		min-height: 38px;
		padding: 0 12px;
		border: 1px solid transparent;
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--muted);
		font-size: 11.5px;
		font-weight: 650;
		white-space: nowrap;
		scroll-snap-align: center;
		transition:
			background 0.12s,
			color 0.12s;
	}
	.tab-scroll button > span {
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.tab-scroll button:hover:not(.active) {
		color: var(--text);
		background: var(--panel2);
	}
	.tab-scroll button.active {
		border-color: var(--gold-deep);
		background: var(--gold);
		color: var(--on-gold);
	}
	.tab-scroll button.active i {
		background: var(--on-gold);
	}
	.tab-scroll i,
	.save-bar i {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--gold);
	}
	/* Deliberately NOT .workspace-head: that class is defined globally in
	   workspace.css as a column flex container, and the cascade would drag
	   flex-direction in here and stack the title against the right edge. */
	.tab-head {
		display: flex;
		flex-direction: row;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin-bottom: 14px;
		padding: 7px 10px 7px 18px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.tab-title {
		min-width: 0;
	}
	.tab-head h2 {
		margin: 2px 0 0;
		font-size: 17px;
		font-weight: 680;
		letter-spacing: -0.01em;
	}
	.tab-head-actions {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.kicker {
		color: var(--muted);
		font: 650 9px/1.2 var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.08em;
	}
	.pill-count {
		padding: 1px 7px;
		border-radius: var(--radius-pill);
		background: color-mix(in srgb, currentcolor 18%, transparent);
		font: 600 10px/1.5 var(--font-mono);
	}
	.advanced-block {
		margin-top: 18px;
	}
	.advanced-divider {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
		padding-top: 16px;
		border-top: 1px solid var(--line);
	}
	#settings-advanced {
		margin-top: 14px;
	}
	#settings-advanced[hidden] {
		display: none;
	}
	/* Section cards vary wildly in height (1 setting next to 20), and a two-track
	   grid leaves a column-tall hole beside every short one. Columns pack them
	   instead; break-inside keeps a card whole.
	   The width is a floor, not a hint: a setting row needs ~500px for its label
	   and control tracks, and the card clips (overflow: hidden) below that. Naming
	   a column-width alongside the count drops to one column rather than shearing
	   the controls off at mid-range viewports. */
	.cards-grid {
		columns: 520px 2;
		column-gap: 12px;
	}
	.cards-grid > :global(section) {
		break-inside: avoid;
		margin-bottom: 12px;
	}
	.lead-cards {
		margin-bottom: 12px;
	}
	.settings-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		overflow: hidden;
		min-width: 0;
	}
	.settings-card > header {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: center;
		gap: 8px;
		min-height: 45px;
		padding: 11px 15px;
		border-bottom: 1px solid var(--line);
		color: var(--muted);
	}
	.settings-card > header h3 {
		margin: 0;
		color: var(--text);
		font-size: 12px;
		font-weight: 680;
	}
	.settings-card > header span {
		color: var(--muted);
		font: 600 9px/1.2 var(--font-mono);
		text-transform: uppercase;
	}
	.setting-list {
		min-width: 0;
	}
	.preference-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 140px;
		align-items: center;
		gap: 16px;
		min-height: 64px;
		padding: 11px 16px;
		border-bottom: 1px solid var(--line);
	}
	.preference-row:last-child {
		border-bottom: 0;
	}
	.preference-row div {
		display: grid;
		gap: 3px;
	}
	.preference-row b {
		font-size: 12px;
	}
	.preference-row small {
		color: var(--muted);
		font-size: 11px;
	}
	.preference-row select {
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--text);
		padding: 8px 9px;
		font-size: 11px;
	}
	/* Hairline grid: the 1px gaps show the container through as rules. An odd
	   final cell therefore has to span, or it leaves a bare panel-coloured gap. */
	.fact-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 1px;
		background: var(--line);
	}
	.fact-grid div:last-child:nth-child(odd) {
		grid-column: 1 / -1;
	}
	.fact-grid div {
		display: grid;
		gap: 5px;
		min-height: 76px;
		padding: 14px 16px;
		background: var(--panel);
	}
	.fact-grid span {
		color: var(--muted);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.fact-grid code {
		color: var(--text);
		font: 11px/1.4 var(--font-mono);
		word-break: break-word;
	}
	.deployment-card > p {
		margin: 0;
		padding: 13px 16px;
		color: var(--muted);
		font-size: 11px;
	}
	.notice {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		margin-bottom: 12px;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		color: var(--muted);
		font-size: 11.5px;
	}
	.notice :global(svg) {
		flex: none;
		margin-top: 1px;
	}
	.notice.warning,
	.notice.conflict {
		border-color: color-mix(in srgb, var(--warn) 32%, var(--line));
		color: var(--warn-copy);
	}
	.notice.info {
		border-color: color-mix(in srgb, var(--info) 25%, var(--line));
	}
	.notice.info a {
		color: var(--gold);
		text-decoration: underline;
		text-underline-offset: 2px;
	}
	.mount-strip {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
		gap: 1px;
		margin-bottom: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--line);
	}
	.mount-strip div {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 9px 11px;
		background: var(--panel);
	}
	.mount-strip code {
		overflow: hidden;
		color: var(--muted);
		font: 10px/1.4 var(--font-mono);
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.mount-strip span {
		color: var(--bad);
		font-size: 9px;
		text-transform: uppercase;
		white-space: nowrap;
	}
	.mount-strip span.good {
		color: var(--good);
	}
	.security-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 10px;
		margin-bottom: 12px;
	}
	.posture-card {
		display: grid;
		gap: 5px;
		padding: 13px 14px;
		border: 1px solid color-mix(in srgb, var(--good) 28%, var(--line));
		border-radius: var(--radius);
		background: var(--panel);
	}
	.posture-card.warning {
		border-color: color-mix(in srgb, var(--warn) 38%, var(--line));
	}
	.posture-card span {
		color: var(--muted);
		font: 600 9px/1.2 var(--font-mono);
		text-transform: uppercase;
	}
	.posture-card b {
		color: var(--good);
		font-size: 13px;
	}
	.posture-card.warning b {
		color: var(--warn-copy);
	}
	.posture-card small {
		color: var(--muted);
		font-size: 10.5px;
	}
	.empty-state {
		display: grid;
		place-items: center;
		min-height: 260px;
		padding: 30px;
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		color: var(--muted);
		text-align: center;
	}
	.empty-state.compact {
		min-height: 150px;
		margin-top: 12px;
	}
	.empty-state h2 {
		margin: 8px 0 0;
		color: var(--text);
		font-size: 14px;
	}
	.empty-state p {
		margin: 4px 0 0;
		font-size: 12px;
	}
	/* Sticky, not fixed: a fixed bar has to guess the sidebar width and drifts
	   out of alignment the moment the sidebar collapses. */
	.save-bar {
		position: sticky;
		z-index: 30;
		bottom: 18px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin-top: 18px;
		padding: 9px 10px 9px 18px;
		border: 1px solid color-mix(in srgb, var(--gold) 38%, var(--line));
		border-radius: var(--radius-pill);
		background: color-mix(in srgb, var(--panel) 94%, transparent);
		box-shadow: 0 16px 42px var(--shadow);
		backdrop-filter: blur(16px);
	}
	.save-bar > div:first-child {
		display: flex;
		align-items: center;
		gap: 9px;
		color: var(--muted);
		font-size: 11.5px;
	}
	.save-actions {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.danger-card {
		margin-top: 18px;
		border: 1px solid color-mix(in srgb, var(--bad) 26%, var(--line));
		border-radius: var(--radius);
		background: var(--panel);
		overflow: hidden;
	}
	.danger-card > header {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: start;
		gap: 10px;
		padding: 13px 16px;
		color: var(--bad);
	}
	.danger-card h3 {
		margin: 0;
		color: var(--text);
		font-size: 12px;
		font-weight: 680;
	}
	.danger-card p {
		margin: 4px 0 0;
		color: var(--muted);
		font-size: 11.5px;
		line-height: 1.45;
	}
	.danger-card > header > span {
		color: var(--muted);
		font: 600 9px/1.2 var(--font-mono);
		text-transform: uppercase;
		white-space: nowrap;
	}
	.danger-card footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding: 10px 15px;
		border-top: 1px solid var(--line);
		background: color-mix(in srgb, var(--panel2) 35%, transparent);
	}
	.danger-card .armed {
		border-color: var(--bad);
		background: color-mix(in srgb, var(--bad) 16%, transparent);
		color: var(--bad);
	}
	@media (max-width: 1180px) {
		.security-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 840px) {
		.cards-grid {
			columns: 1;
		}
		.tab-scroll {
			display: flex;
		}
		.tab-scroll button {
			min-width: 112px;
		}
	}
	/* The navigation keeps its pill silhouette while a wrapped save bar squares off. */
	@media (max-width: 1080px) {
		.save-bar {
			border-radius: var(--radius);
		}
	}
	@media (max-width: 620px) {
		.settings-page {
			padding-bottom: 24px;
		}
		.tab-head {
			align-items: stretch;
			flex-direction: column;
			padding: 12px 14px;
		}
		.tab-head-actions {
			justify-content: flex-start;
		}
		.security-grid {
			grid-template-columns: 1fr;
		}
		.preference-row {
			grid-template-columns: 1fr;
			gap: 8px;
		}
		.fact-grid {
			grid-template-columns: 1fr;
		}
		.save-bar {
			bottom: 10px;
			align-items: stretch;
			flex-direction: column;
		}
		.save-actions {
			justify-content: flex-end;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.tab-scroll,
		.tab-scroll button,
		.save-bar,
		.settings-card {
			scroll-behavior: auto;
			transition: none;
		}
	}
</style>
