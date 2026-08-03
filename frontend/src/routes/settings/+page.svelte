<script lang="ts">
	import { onMount } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import { beforeNavigate } from '$app/navigation';
	import Icon from '$lib/components/Icon.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import IntegrationCard from '$lib/components/settings/IntegrationCard.svelte';
	import PathMappingsCard from '$lib/components/settings/PathMappingsCard.svelte';
	import PosterNamingCard from '$lib/components/settings/PosterNamingCard.svelte';
	import SettingsField from '$lib/components/settings/SettingsField.svelte';
	import { getSettings, putConfiguration } from '$lib/api/system';
	import { CONFIGURATION_CONFLICT_MESSAGE, isConfigurationConflict } from '$lib/api/client';
	import type {
		RuntimeSettings,
		SettingsCatalogEntry,
		SettingsLevel,
		SettingsTab
	} from '$lib/api/types';
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

	// svelte-ignore state_referenced_locally
	let settings = $state<RuntimeSettings | null>(data.settings);
	// svelte-ignore state_referenced_locally
	let loadError = $state<string | null>(data.error);
	// svelte-ignore state_referenced_locally
	let activeTab = $state<SettingsTab>(data.initialTab);
	// svelte-ignore state_referenced_locally
	let activeLevel = $state<SettingsLevel>(
		data.initialLevel === 'advanced' &&
			data.settings &&
			Object.values(data.settings.catalog).some(
				(entry) => entry.visible && entry.tab === data.initialTab && entry.level === 'advanced'
			)
			? 'advanced'
			: 'standard'
	);
	let drafts = $state<Record<string, unknown>>({});
	let saving = $state(false);
	let conflictNote = $state<string | null>(null);
	let secureContext = $state(false);

	const allEntries = $derived(
		settings ? Object.values(settings.catalog).filter((entry) => entry.visible) : []
	);
	const hasAdvanced = $derived(
		allEntries.some((entry) => entry.tab === activeTab && entry.level === 'advanced')
	);
	const dirtyKeys = $derived(Object.keys(drafts));
	const dirtyCount = $derived(dirtyKeys.length);
	const tabDirtyCount = $derived(
		dirtyKeys.filter((key) => settings?.catalog[key]?.tab === activeTab).length
	);
	const hasDedicatedContent = $derived(
		activeLevel === 'standard' && ['connections', 'media', 'posters'].includes(activeTab)
	);

	const activeSections = $derived.by(() => {
		const grouped = new SvelteMap<string, SettingsCatalogEntry[]>();
		for (const entry of allEntries) {
			if (entry.tab !== activeTab || entry.level !== activeLevel) continue;
			if (entry.storage === 'secret_store') continue;
			if (
				activeTab === 'general' &&
				['MARQUEE_ENVIRONMENT', 'MARQUEE_PROCESS_ROLE', 'HOST', 'PORT'].includes(entry.key)
			)
				continue;
			if (activeTab === 'connections' && ['RADARR_URL', 'SONARR_URL'].includes(entry.key)) continue;
			if (
				activeTab === 'media' &&
				[
					'MEDIA_ROOTS',
					'RADARR_PATH_PREFIX',
					'RADARR_MEDIA_PATH',
					'SONARR_PATH_PREFIX',
					'SONARR_MEDIA_PATH'
				].includes(entry.key)
			)
				continue;
			if (
				activeTab === 'posters' &&
				['MOVIE_POSTER_FORMAT', 'SERIES_POSTER_FORMAT', 'SEASON_POSTER_FORMAT'].includes(entry.key)
			)
				continue;
			const rows = grouped.get(entry.section) ?? [];
			rows.push(entry);
			grouped.set(entry.section, rows);
		}
		return [...grouped.entries()].map(([name, entries]) => ({ name, entries }));
	});

	function same(left: unknown, right: unknown): boolean {
		return JSON.stringify(left) === JSON.stringify(right);
	}

	function valueFor(key: string): unknown {
		return Object.hasOwn(drafts, key) ? drafts[key] : settings?.values[key];
	}

	function changeValue(key: string, value: unknown) {
		if (!settings) return;
		if (same(value, settings.values[key])) delete drafts[key];
		else drafts[key] = value;
		conflictNote = null;
	}

	function resetValue(entry: SettingsCatalogEntry) {
		if (!settings || !Object.hasOwn(settings.defaults, entry.key)) return;
		changeValue(entry.key, settings.defaults[entry.key]);
	}

	function resetTabToDefaults() {
		if (!settings) return;
		for (const entry of allEntries) {
			if (
				entry.tab === activeTab &&
				entry.storage === 'revision' &&
				Object.hasOwn(settings.defaults, entry.key)
			) {
				changeValue(entry.key, settings.defaults[entry.key]);
			}
		}
	}

	function resetKeys(keys: string[]) {
		if (!settings) return;
		for (const key of keys) {
			if (Object.hasOwn(settings.defaults, key)) changeValue(key, settings.defaults[key]);
		}
	}

	function updateQuery() {
		if (typeof window === 'undefined') return;
		const url = new URL(window.location.href);
		url.searchParams.set('tab', activeTab);
		if (activeLevel === 'advanced') url.searchParams.set('level', 'advanced');
		else url.searchParams.delete('level');
		window.history.replaceState(window.history.state, '', url);
	}

	function selectTab(tab: SettingsTab) {
		activeTab = tab;
		if (!allEntries.some((entry) => entry.tab === tab && entry.level === 'advanced')) {
			activeLevel = 'standard';
		}
		updateQuery();
	}

	function selectLevel(level: SettingsLevel) {
		activeLevel = level;
		updateQuery();
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

	async function save() {
		if (!settings || !dirtyCount || saving) return;
		saving = true;
		conflictNote = null;
		try {
			const result = await putConfiguration(fetch, { ...drafts }, settings.configuration_version);
			settings = result.settings;
			drafts = {};
			toast(
				result.changed ? 'Settings saved' : 'No settings changed',
				result.changed ? 'good' : 'info'
			);
		} catch (error) {
			if (isConfigurationConflict(error)) {
				const previous = settings.values;
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
			} else {
				toast(error instanceof Error ? error.message : 'Could not save settings', 'bad');
			}
		} finally {
			saving = false;
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
		if (activeLevel !== data.initialLevel) updateQuery();
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

<svelte:head>
	<title>Settings — Marquee</title>
</svelte:head>

<div class="settings-page">
	<SectionHeader
		title="Settings"
		subtitle="One control desk for Marquee, its poster workflow, and connected services."
	/>

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
		<div class="workspace-head">
			<div>
				<span class="kicker">{tabs.find((tab) => tab.id === activeTab)?.hint}</span>
				<h2>{tabs.find((tab) => tab.id === activeTab)?.label}</h2>
			</div>
			{#if hasAdvanced}
				<div class="level-switch" aria-label="Settings detail level">
					<button
						type="button"
						class:active={activeLevel === 'standard'}
						aria-pressed={activeLevel === 'standard'}
						onclick={() => selectLevel('standard')}
					>
						Standard
					</button>
					<button
						type="button"
						class:active={activeLevel === 'advanced'}
						aria-pressed={activeLevel === 'advanced'}
						onclick={() => selectLevel('advanced')}
					>
						Advanced
					</button>
				</div>
			{/if}
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

		{#if activeTab === 'general' && activeLevel === 'standard'}
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

		{#if activeTab === 'connections' && activeLevel === 'standard'}
			<div class="integration-grid">
				{#each ['tmdb', 'radarr', 'sonarr'] as provider (provider)}
					<IntegrationCard
						provider={provider as 'tmdb' | 'radarr' | 'sonarr'}
						{settings}
						{secureContext}
						onSettings={acceptIntegrationSettings}
					/>
				{/each}
			</div>
		{/if}

		{#if activeTab === 'media' && activeLevel === 'standard'}
			<div class="notice info">
				<Icon name="film" size={15} /> Logical roots and Arr path mappings are editable. Host bind mounts
				remain deployment-owned; Marquee never changes Docker mounts.
			</div>
			<PathMappingsCard
				mediaRoots={(valueFor('MEDIA_ROOTS') as string[] | undefined) ?? []}
				radarrPrefix={String(valueFor('RADARR_PATH_PREFIX') ?? '')}
				radarrTarget={String(valueFor('RADARR_MEDIA_PATH') ?? '')}
				sonarrPrefix={String(valueFor('SONARR_PATH_PREFIX') ?? '')}
				sonarrTarget={String(valueFor('SONARR_MEDIA_PATH') ?? '')}
				dirty={[
					'MEDIA_ROOTS',
					'RADARR_PATH_PREFIX',
					'RADARR_MEDIA_PATH',
					'SONARR_PATH_PREFIX',
					'SONARR_MEDIA_PATH'
				].some((key) => Object.hasOwn(drafts, key))}
				disabled={!settings.writable || saving}
				onChange={changeValue}
				onReset={() =>
					resetKeys([
						'MEDIA_ROOTS',
						'RADARR_PATH_PREFIX',
						'RADARR_MEDIA_PATH',
						'SONARR_PATH_PREFIX',
						'SONARR_MEDIA_PATH'
					])}
			/>
			{#if settings.deployment.mounts?.length}
				<section class="mount-strip" aria-label="Container path checks">
					{#each settings.deployment.mounts as mount (mount.path)}
						<div>
							<code>{mount.path}</code><span class:good={mount.readable}
								>{mount.readable ? 'readable' : 'unavailable'}{mount.writable
									? ' · writable'
									: ''}</span
							>
						</div>
					{/each}
				</section>
			{/if}
		{/if}

		{#if activeTab === 'posters' && activeLevel === 'standard'}
			<PosterNamingCard
				movie={String(valueFor('MOVIE_POSTER_FORMAT') ?? 'poster.jpg')}
				series={String(valueFor('SERIES_POSTER_FORMAT') ?? 'show.jpg')}
				season={String(valueFor('SEASON_POSTER_FORMAT') ?? 'season{season:02d}.jpg')}
				dirty={['MOVIE_POSTER_FORMAT', 'SERIES_POSTER_FORMAT', 'SEASON_POSTER_FORMAT'].some((key) =>
					Object.hasOwn(drafts, key)
				)}
				disabled={!settings.writable || saving}
				onChange={changeValue}
				onReset={() =>
					resetKeys(['MOVIE_POSTER_FORMAT', 'SERIES_POSTER_FORMAT', 'SEASON_POSTER_FORMAT'])}
			/>
		{/if}

		{#if activeTab === 'taste' && activeLevel === 'standard'}
			<div class="notice info">
				<Icon name="taste" size={15} /> These are server defaults and learning thresholds. Profile generation,
				maps, enrichment, and model operations remain in <a href="/taste">Taste operations</a>.
			</div>
		{/if}

		{#if activeTab === 'access' && activeLevel === 'standard'}
			<div class="security-grid">
				<section class:warning={!settings.deployment.api_key_configured} class="posture-card">
					<span>API authentication</span>
					<b>{settings.deployment.api_key_configured ? 'Configured' : 'Missing'}</b>
					<small>Bootstrap secret · never returned to the browser</small>
				</section>
				<section class:warning={!settings.deployment.keyring_configured} class="posture-card">
					<span>Settings keyring</span>
					<b>{settings.deployment.keyring_configured ? 'Mounted' : 'Not mounted'}</b>
					<small
						>{settings.secret_store.writable
							? 'Managed credentials writable'
							: 'Credential writes disabled'}</small
					>
				</section>
				<section class:warning={!secureContext} class="posture-card">
					<span>Browser transport</span>
					<b>{secureContext ? 'Secure' : 'HTTP warning'}</b>
					<small>Remote credential changes require HTTPS</small>
				</section>
				<section class:warning={settings.deployment.debug} class="posture-card">
					<span>Debug mode</span>
					<b>{settings.deployment.debug ? 'Enabled' : 'Disabled'}</b>
					<small>Deployment only</small>
				</section>
			</div>
		{/if}

		<div class="cards-grid" class:after-lead={activeTab === 'general'}>
			{#each activeSections as section (section.name)}
				<section class="settings-card">
					<header>
						<Icon
							name={activeTab === 'posters'
								? 'image'
								: (tabs.find((tab) => tab.id === activeTab)?.icon ?? 'settings')}
							size={15}
						/>
						<h3>{section.name}</h3>
						<span
							>{section.entries.length}
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
								dirty={Object.hasOwn(drafts, entry.key)}
								disabled={!settings.writable || saving}
								onChange={(value) => changeValue(entry.key, value)}
								onReset={() => resetValue(entry)}
							/>
						{/each}
					</div>
				</section>
			{/each}
		</div>

		{#if activeSections.length === 0 && !hasDedicatedContent}
			<section class="empty-state compact">
				<h2>No {activeLevel} settings</h2>
				<p>This section contains status or browser-local controls only.</p>
			</section>
		{/if}

		{#if dirtyCount}
			<div class="save-bar" role="status">
				<div>
					<i></i>
					<span
						><b>{dirtyCount}</b> unsaved {dirtyCount === 1 ? 'change' : 'changes'}{tabDirtyCount
							? ` · ${tabDirtyCount} on this tab`
							: ''}</span
					>
				</div>
				<div class="save-actions">
					<button type="button" class="text-button" onclick={() => (drafts = {})} disabled={saving}
						>Discard draft</button
					>
					<button type="button" class="text-button" onclick={resetTabToDefaults} disabled={saving}
						>Reset tab</button
					>
					<button
						type="button"
						class="save-button"
						onclick={save}
						disabled={saving || !settings.writable}
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
		border-radius: var(--radius);
		background: color-mix(in srgb, var(--ink2) 91%, transparent);
		box-shadow: 0 10px 30px color-mix(in srgb, var(--shadow) 24%, transparent);
		backdrop-filter: blur(14px);
	}
	.tab-scroll {
		display: grid;
		grid-template-columns: repeat(8, minmax(112px, 1fr));
		overflow-x: auto;
		padding: 4px;
	}
	.tab-scroll button {
		position: relative;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 7px;
		min-height: 40px;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: var(--muted);
		font-size: 11.5px;
		font-weight: 650;
		white-space: nowrap;
	}
	.tab-scroll button:hover {
		color: var(--text);
		background: color-mix(in srgb, var(--panel2) 55%, transparent);
	}
	.tab-scroll button.active {
		color: var(--text);
		background: var(--panel);
		box-shadow: inset 0 -2px var(--gold);
	}
	.tab-scroll i,
	.save-bar i {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--gold);
	}
	.workspace-head {
		display: flex;
		justify-content: space-between;
		align-items: end;
		gap: 16px;
		margin-bottom: 12px;
	}
	.workspace-head h2 {
		margin: 2px 0 0;
		font-size: 17px;
		font-weight: 680;
	}
	.kicker {
		color: var(--muted);
		font: 650 9px/1.2 var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.08em;
	}
	.level-switch {
		display: inline-grid;
		grid-template-columns: 1fr 1fr;
		padding: 3px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: var(--panel);
	}
	.level-switch button {
		min-width: 88px;
		border: 0;
		border-radius: 5px;
		padding: 6px 10px;
		background: transparent;
		color: var(--muted);
		font-size: 10.5px;
		font-weight: 650;
	}
	.level-switch button.active {
		background: var(--gold-soft);
		color: var(--gold-copy);
	}
	.cards-grid,
	.integration-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
		align-items: start;
	}
	.integration-grid {
		grid-template-columns: repeat(3, minmax(0, 1fr));
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
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
		padding: 8px 9px;
		font-size: 11px;
	}
	.fact-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 1px;
		background: var(--line);
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
		border-radius: 8px;
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
		border-radius: 8px;
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
		border-radius: 8px;
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
	.save-bar {
		position: fixed;
		z-index: 30;
		left: calc(var(--sidebar-w) + 24px);
		right: 24px;
		bottom: 18px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		padding: 10px 12px 10px 15px;
		border: 1px solid color-mix(in srgb, var(--gold) 38%, var(--line));
		border-radius: 9px;
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
	.save-actions button {
		border-radius: 7px;
		padding: 7px 11px;
		font-size: 10.5px;
		font-weight: 650;
	}
	.text-button {
		border: 0;
		background: transparent;
		color: var(--muted);
	}
	.save-button {
		border: 1px solid var(--gold-deep);
		background: var(--gold);
		color: var(--on-gold);
	}
	.save-actions button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	@media (max-width: 1180px) {
		.integration-grid {
			grid-template-columns: 1fr;
		}
		.security-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 840px) {
		.cards-grid {
			grid-template-columns: 1fr;
		}
		.tab-scroll {
			display: flex;
		}
		.tab-scroll button {
			min-width: 112px;
		}
		.save-bar {
			left: 76px;
		}
	}
	@media (max-width: 620px) {
		.settings-page {
			padding-bottom: 125px;
		}
		.workspace-head {
			align-items: stretch;
			flex-direction: column;
		}
		.level-switch {
			align-self: flex-start;
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
			left: 12px;
			right: 12px;
			bottom: 10px;
			align-items: stretch;
			flex-direction: column;
		}
		.save-actions {
			justify-content: flex-end;
		}
		.text-button:first-child {
			display: none;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.tab-scroll,
		.tab-scroll button,
		.level-switch button,
		.save-bar,
		.settings-card {
			scroll-behavior: auto;
			transition: none;
		}
	}
</style>
