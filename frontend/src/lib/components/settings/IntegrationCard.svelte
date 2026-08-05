<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import {
		clearIntegrationCredential,
		getSettings,
		putIntegration,
		testIntegration,
		type IntegrationProvider,
		type IntegrationStatusFacts
	} from '$lib/api/system';
	import { listMovies, listSeries, syncLibraries } from '$lib/api/library';
	import { ApiError, CONFIGURATION_CONFLICT_MESSAGE } from '$lib/api/client';
	import type { RuntimeSettings } from '$lib/api/types';
	import { toast } from '$lib/toast';

	let {
		provider,
		settings,
		secureContext,
		onSettings
	}: {
		provider: IntegrationProvider;
		settings: RuntimeSettings;
		secureContext: boolean;
		onSettings: (settings: RuntimeSettings) => void;
	} = $props();

	const details = {
		tmdb: {
			title: 'The Movie Database',
			short: 'TMDB',
			secretKey: 'TMDB_READ_ACCESS_TOKEN',
			urlKey: null,
			description: 'Metadata, images, and provider identifiers.'
		},
		radarr: {
			title: 'Radarr',
			short: 'Radarr',
			secretKey: 'RADARR_API_KEY',
			urlKey: 'RADARR_URL',
			description: 'Movie library discovery and synchronization.'
		},
		sonarr: {
			title: 'Sonarr',
			short: 'Sonarr',
			secretKey: 'SONARR_API_KEY',
			urlKey: 'SONARR_URL',
			description: 'Television library discovery and synchronization.'
		}
	} as const;

	const detail = $derived(details[provider]);
	const secret = $derived(settings.secrets[detail.secretKey]);
	const configured = $derived(Boolean(secret?.configured));
	const savedUrl = $derived(detail.urlKey ? String(settings.values[detail.urlKey] ?? '') : '');
	let url = $state('');
	let credential = $state('');
	let urlDirty = $state(false);
	let busy = $state<'test' | 'save' | 'clear' | 'sync' | null>(null);
	let clearArmed = $state(false);
	/** Facts the last successful probe read back off the service. */
	let probe = $state<IntegrationStatusFacts | null>(null);
	let probeError = $state<string | null>(null);
	let libraryCount = $state<number | null>(null);

	$effect(() => {
		if (!urlDirty) url = savedUrl;
	});

	// Radarr and Sonarr each own one side of the library, so the row each service
	// is responsible for is the most direct evidence that the link works.
	onMount(async () => {
		if (provider === 'tmdb' || !configured) return;
		try {
			const page =
				provider === 'radarr'
					? await listMovies(fetch, { page_size: 1 })
					: await listSeries(fetch, { page_size: 1 });
			libraryCount = page.total;
		} catch {
			libraryCount = null;
		}
	});

	/**
	 * Catch the obvious URL mistakes before spending a round trip: the server
	 * normalizes and rejects properly, this just gives faster feedback.
	 */
	const urlProblem = $derived.by(() => {
		if (!detail.urlKey || !url.trim()) return null;
		let parsed: URL;
		try {
			parsed = new URL(url.trim());
		} catch {
			return 'Enter a full URL, including http:// or https://.';
		}
		if (!['http:', 'https:'].includes(parsed.protocol)) return 'Only http:// and https:// work.';
		if (!parsed.hostname) return 'That URL has no host.';
		return null;
	});

	const facts = $derived.by(() => {
		if (!probe) return [];
		const rows: string[] = [];
		const name = probe.appName ?? detail.short;
		rows.push(probe.version ? `${name} ${probe.version}` : name);
		if (probe.instanceName && probe.instanceName !== probe.appName) rows.push(probe.instanceName);
		if (probe.osName)
			rows.push(probe.osVersion ? `${probe.osName} ${probe.osVersion}` : probe.osName);
		if (probe.isDocker) rows.push('Docker');
		if (probe.runtimeVersion) rows.push(`Runtime ${probe.runtimeVersion}`);
		if (probe.imageBaseUrl) rows.push('Images reachable');
		return rows;
	});

	function message(error: unknown, fallback: string): string {
		return error instanceof Error && error.message ? error.message : fallback;
	}

	async function test() {
		if (busy) return;
		busy = 'test';
		probeError = null;
		try {
			const result = await testIntegration(fetch, provider, {
				...(detail.urlKey ? { url } : {}),
				...(credential ? { credential } : {})
			});
			probe = result.status ?? {};
			toast(`${detail.short} connection verified`, 'good');
		} catch (error) {
			probe = null;
			probeError = message(error, `${detail.short} connection failed`);
			toast(probeError, 'bad');
		} finally {
			credential = '';
			busy = null;
		}
	}

	async function sync() {
		if (busy || provider === 'tmdb') return;
		busy = 'sync';
		try {
			const job = await syncLibraries(fetch);
			toast(
				job.disposition === 'reused' ? 'A library sync is already running' : 'Library sync started',
				'info'
			);
		} catch (error) {
			toast(message(error, 'Could not start a library sync'), 'bad');
		} finally {
			busy = null;
		}
	}

	async function save() {
		if (busy || (!urlDirty && !credential) || (credential && !settings.secret_store.writable))
			return;
		busy = 'save';
		try {
			const result = await putIntegration(fetch, provider, {
				expected_version: settings.configuration_version,
				expected_secret_generation: secret?.generation ?? 0,
				...(detail.urlKey ? { url } : {}),
				...(credential ? { credential } : {})
			});
			credential = '';
			urlDirty = false;
			clearArmed = false;
			probe = result.status ?? {};
			probeError = null;
			onSettings(result.settings);
			toast(`${detail.short} settings saved`, 'good');
		} catch (error) {
			if (error instanceof ApiError && error.status === 409) {
				onSettings(await getSettings(fetch));
				toast(CONFIGURATION_CONFLICT_MESSAGE, 'info');
			} else {
				toast(message(error, `Could not save ${detail.short}`), 'bad');
			}
		} finally {
			credential = '';
			busy = null;
		}
	}

	async function clearCredential() {
		if (!clearArmed) {
			clearArmed = true;
			return;
		}
		if (busy) return;
		busy = 'clear';
		try {
			const result = await clearIntegrationCredential(fetch, provider, secret?.generation ?? 0);
			credential = '';
			clearArmed = false;
			probe = null;
			probeError = null;
			onSettings(result.settings);
			toast(`${detail.short} credential cleared`, 'info');
		} catch (error) {
			toast(message(error, `Could not clear ${detail.short} credential`), 'bad');
		} finally {
			busy = null;
		}
	}
</script>

<section class="integration-card">
	<header>
		<div class="provider-mark" class:ready={configured}>
			<Icon name={configured ? 'check' : 'settings'} size={16} />
		</div>
		<div>
			<h3>{detail.title}</h3>
			<p>{detail.description}</p>
		</div>
		<span class:ready={configured} class="status">
			{configured ? 'Configured' : 'Not configured'}
		</span>
	</header>

	<div class="fields">
		{#if detail.urlKey}
			<label>
				<span>Service URL</span>
				<input
					type="url"
					class:invalid={Boolean(urlProblem)}
					bind:value={url}
					oninput={() => (urlDirty = true)}
					placeholder="http://service:port"
					spellcheck="false"
					autocomplete="url"
					aria-invalid={Boolean(urlProblem)}
				/>
				{#if urlProblem}<small class="field-error">{urlProblem}</small>{/if}
			</label>
		{/if}
		<label>
			<span>{provider === 'tmdb' ? 'Read access token' : 'API key'}</span>
			<input
				type="password"
				bind:value={credential}
				name={`${provider}-replacement-credential`}
				aria-label={provider === 'tmdb' ? 'Read access token' : 'API key'}
				autocomplete="new-password"
				placeholder={configured ? 'Leave blank to keep current credential' : 'Enter credential'}
				disabled={Boolean(busy)}
			/>
		</label>
	</div>

	{#if probe}
		<div class="probe good" role="status">
			<Icon name="check" size={14} />
			<div class="chips">
				{#each facts as fact (fact)}<span>{fact}</span>{/each}
				{#if libraryCount !== null}
					<span
						>{libraryCount.toLocaleString()}
						{provider === 'radarr' ? 'movies' : 'series'}</span
					>
				{/if}
			</div>
		</div>
	{:else if probeError}
		<div class="probe bad" role="status">
			<Icon name="alert" size={14} />
			<p>{probeError}</p>
		</div>
	{:else if configured && libraryCount !== null}
		<div class="probe" role="status">
			<Icon name={provider === 'radarr' ? 'film' : 'tv'} size={14} />
			<div class="chips">
				<span
					>{libraryCount.toLocaleString()}
					{provider === 'radarr' ? 'movies' : 'series'} tracked</span
				>
				<span>Syncs every {settings.sync.interval_minutes} min</span>
			</div>
		</div>
	{/if}

	<div class="secret-facts">
		<span>Source: {secret?.source ?? 'default'}</span>
		<span>Applies: Next job</span>
		<span>Generation: {secret?.generation ?? 0}</span>
		{#if secret?.updated_at}<span>Rotated: {new Date(secret.updated_at).toLocaleDateString()}</span
			>{/if}
	</div>
	{#if !settings.secret_store.writable}
		<p class="store-warning">
			This installation uses an environment-supplied credential. It can be tested, but enable
			managed credential storage to replace or clear it here.
		</p>
	{/if}
	{#if !secureContext}
		<p class="store-warning">
			Internal HTTP mode is active. Connection actions are allowed, but any key typed here travels
			unencrypted across this network.
		</p>
	{/if}

	<footer>
		<div class="probe-actions">
			<button type="button" class="pill quiet" onclick={test} disabled={Boolean(busy)}>
				{busy === 'test' ? 'Testing…' : 'Test connection'}
			</button>
			{#if provider !== 'tmdb' && configured}
				<button type="button" class="pill ghost" onclick={sync} disabled={Boolean(busy)}>
					{busy === 'sync' ? 'Starting…' : 'Sync now'}
				</button>
			{/if}
		</div>
		<div class="credential-actions">
			{#if configured}
				<button
					type="button"
					class:armed={clearArmed}
					class="pill danger clear"
					onclick={clearCredential}
					disabled={Boolean(busy) || !settings.secret_store.writable}
				>
					{busy === 'clear' ? 'Clearing…' : clearArmed ? 'Confirm clear' : 'Clear credential'}
				</button>
			{/if}
			<button
				type="button"
				class="pill primary"
				onclick={save}
				disabled={Boolean(busy) ||
					Boolean(urlProblem) ||
					(!urlDirty && !credential) ||
					Boolean(credential && !settings.secret_store.writable)}
			>
				{busy === 'save' ? 'Saving…' : configured ? 'Test & save' : 'Test & configure'}
			</button>
		</div>
	</footer>
</section>

<style>
	.integration-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		overflow: hidden;
	}
	header {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		gap: 11px;
		align-items: start;
		padding: 15px 16px;
		border-bottom: 1px solid var(--line);
	}
	.provider-mark {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		color: var(--muted);
		background: var(--panel2);
	}
	.provider-mark.ready {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 35%, var(--line));
	}
	h3 {
		margin: 0;
		color: var(--text);
		font-size: 13px;
		font-weight: 680;
	}
	header p {
		margin: 3px 0 0;
		color: var(--muted);
		font-size: 11.5px;
	}
	.status {
		padding: 3px 7px;
		border: 1px solid var(--line2);
		border-radius: 999px;
		color: var(--muted);
		font: 650 9px/1.2 var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.status.ready {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 35%, var(--line));
	}
	.fields {
		display: grid;
		gap: 13px;
		padding: 16px;
	}
	label {
		display: grid;
		gap: 6px;
		color: var(--muted);
		font-size: 11px;
		font-weight: 650;
	}
	input {
		width: 100%;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--text);
		padding: 9px 10px;
		font: 12px/1.4 var(--font-mono);
	}
	input:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	input.invalid {
		border-color: color-mix(in srgb, var(--bad) 55%, var(--line2));
	}
	.field-error {
		color: var(--bad);
		font-size: 10.5px;
		font-weight: 500;
	}
	/* What the service said about itself, so a successful test leaves evidence
	   behind instead of a toast that disappears. */
	.probe {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		margin: 0 16px 14px;
		padding: 9px 11px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--muted);
	}
	.probe.good {
		border-color: color-mix(in srgb, var(--good) 32%, var(--line));
		color: var(--good);
	}
	.probe.bad {
		border-color: color-mix(in srgb, var(--bad) 32%, var(--line));
		color: var(--bad);
	}
	.probe :global(svg) {
		flex: none;
		margin-top: 1px;
	}
	.probe p {
		margin: 0;
		font-size: 11.5px;
		line-height: 1.45;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		min-width: 0;
	}
	.chips span {
		padding: 2px 8px;
		border: 1px solid currentcolor;
		border-radius: var(--radius-pill);
		font: 600 10px/1.5 var(--font-mono);
	}
	.secret-facts {
		display: flex;
		flex-wrap: wrap;
		gap: 6px 12px;
		padding: 0 16px 14px;
		color: var(--muted);
		font: 10px/1.4 var(--font-mono);
	}
	.store-warning {
		margin: -2px 16px 14px;
		color: var(--warn-copy);
		font-size: 11px;
	}
	footer {
		display: flex;
		/* Without this the default `stretch` inflates every pill to the height of
		   the tallest wrapped row, turning a button into an oval. */
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 10px;
		padding: 12px 16px;
		border-top: 1px solid var(--line);
		background: color-mix(in srgb, var(--panel2) 42%, transparent);
	}
	.probe-actions,
	.credential-actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
	}
	/* Buttons use the shared .pill vocabulary from workspace.css; only the armed
	   confirm state is specific to this card. */
	.clear.armed {
		border-color: var(--bad);
		background: color-mix(in srgb, var(--bad) 16%, transparent);
		color: var(--bad);
	}
	@media (max-width: 620px) {
		header {
			grid-template-columns: auto minmax(0, 1fr);
		}
		.status {
			grid-column: 2;
			justify-self: start;
		}
		footer {
			align-items: stretch;
			flex-direction: column;
		}
		.credential-actions {
			justify-content: flex-end;
		}
	}
</style>
