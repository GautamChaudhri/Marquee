<script lang="ts">
	import { tick } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import Icon from '$lib/components/Icon.svelte';
	import {
		clearIntegrationCredential,
		getSettings,
		putIntegration,
		testIntegration,
		type IntegrationProvider,
		type IntegrationStatusFacts
	} from '$lib/api/system';
	import { syncLibraries } from '$lib/api/library';
	import { ApiError, CONFIGURATION_CONFLICT_MESSAGE } from '$lib/api/client';
	import type { RuntimeSettings } from '$lib/api/types';
	import { toast } from '$lib/toast';

	type ArrProvider = Exclude<IntegrationProvider, 'tmdb'>;
	type StatusTone = 'good' | 'bad' | 'warn';
	type SessionState = 'checking' | 'connected' | 'failed';

	let {
		settings,
		secureContext,
		onSettings
	}: {
		settings: RuntimeSettings;
		secureContext: boolean;
		onSettings: (settings: RuntimeSettings) => void;
	} = $props();

	const details = {
		tmdb: { label: 'TMDB', secretKey: 'TMDB_READ_ACCESS_TOKEN', icon: 'layers' },
		radarr: { label: 'Radarr', secretKey: 'RADARR_API_KEY', urlKey: 'RADARR_URL', icon: 'film' },
		sonarr: { label: 'Sonarr', secretKey: 'SONARR_API_KEY', urlKey: 'SONARR_URL', icon: 'tv' }
	} as const;
	const arrProviders: ArrProvider[] = ['radarr', 'sonarr'];

	let modalOpen = $state(false);
	let modalProvider = $state<IntegrationProvider>('radarr');
	let modalIntent = $state<'add' | 'edit'>('edit');
	let instanceName = $state('');
	let url = $state('');
	let credential = $state('');
	let busy = $state<'test' | 'save' | 'clear' | null>(null);
	let syncing = $state<ArrProvider | null>(null);
	let clearArmed = $state(false);
	let probe = $state<IntegrationStatusFacts | null>(null);
	let probeError = $state<string | null>(null);
	let sessionStates = $state<Partial<Record<IntegrationProvider, SessionState>>>({});
	let nameField = $state<HTMLInputElement | null>(null);
	const checkedConnections = new SvelteMap<IntegrationProvider, string>();

	const current = $derived(details[modalProvider]);
	const tmdbStatus = $derived(statusFor('tmdb'));

	$effect(() => {
		if (modalOpen) tick().then(() => nameField?.focus());
	});

	$effect(() => {
		if (!secureContext) return;
		for (const provider of ['tmdb', 'radarr', 'sonarr'] as IntegrationProvider[]) {
			if (!isConfigured(provider)) continue;
			const fingerprint = `${secretFor(provider)?.generation ?? 0}:${provider === 'tmdb' ? '' : storedUrl(provider)}`;
			if (checkedConnections.get(provider) === fingerprint) continue;
			checkedConnections.set(provider, fingerprint);
			void refreshStoredConnection(provider);
		}
	});

	function secretFor(provider: IntegrationProvider) {
		return settings.secrets[details[provider].secretKey];
	}

	function storedUrl(provider: ArrProvider): string {
		return String(settings.values[details[provider].urlKey] ?? '');
	}

	function displayName(provider: IntegrationProvider): string {
		return settings.integrations[provider].name;
	}

	function isConfigured(provider: IntegrationProvider): boolean {
		if (provider === 'tmdb') return Boolean(secretFor(provider)?.configured);
		return Boolean(storedUrl(provider) && secretFor(provider)?.configured);
	}

	function credentialLabel(provider: IntegrationProvider): string {
		const secret = secretFor(provider);
		if (!secret?.configured) return 'Not configured';
		if (provider === 'tmdb') {
			return secret.source === 'managed' ? 'Custom credential' : 'Marquee built-in';
		}
		return secret.source === 'managed' ? 'Managed key' : 'Deployment key';
	}

	function statusFor(provider: IntegrationProvider): {
		label: string;
		detail: string;
		tone: StatusTone;
	} {
		const state = sessionStates[provider];
		if (state === 'connected') {
			return { label: 'Connected', detail: 'Verified this session', tone: 'good' };
		}
		if (state === 'checking') {
			return { label: 'Checking', detail: 'Verifying saved details', tone: 'warn' };
		}
		if (state === 'failed') {
			return { label: 'Not connected', detail: 'Last connection test failed', tone: 'bad' };
		}
		if (!isConfigured(provider)) {
			return { label: 'Not configured', detail: 'Missing connection details', tone: 'bad' };
		}
		if (!settings.secret_store.writable && secretFor(provider)?.source !== 'environment') {
			return { label: 'Needs attention', detail: 'Credential store is unavailable', tone: 'warn' };
		}
		return { label: 'Awaiting test', detail: 'Saved details have not been verified', tone: 'warn' };
	}

	/** One line per row. The table used to stack a label over its provenance, which
	 * overflowed its column and printed across the neighbouring cell. */
	function credentialText(provider: IntegrationProvider): string {
		const secret = secretFor(provider);
		if (!secret?.configured) return 'Not configured';
		if (provider === 'tmdb') return secret.source === 'managed' ? 'Custom token' : 'Built-in token';
		return secret.source === 'managed' ? 'Encrypted in Marquee' : 'Deployment supplied';
	}

	function message(error: unknown, fallback: string): string {
		return error instanceof Error && error.message ? error.message : fallback;
	}

	function closeModal() {
		credential = '';
		probe = null;
		probeError = null;
		clearArmed = false;
		modalOpen = false;
	}

	function openForm(provider: IntegrationProvider, intent: 'add' | 'edit') {
		modalProvider = provider;
		modalIntent = intent;
		modalOpen = true;
		instanceName = provider === 'tmdb' ? '' : displayName(provider);
		url = provider === 'tmdb' ? '' : storedUrl(provider);
		credential = '';
		probe = null;
		probeError = null;
		clearArmed = false;
	}

	function transportAllowed(): boolean {
		if (secureContext) return true;
		credential = '';
		toast('Use HTTPS or loopback before changing or testing credentials.', 'bad');
		return false;
	}

	const nameProblem = $derived.by(() => {
		if (modalProvider === 'tmdb') return null;
		if (!instanceName.trim()) return 'Give this instance a name.';
		if (instanceName.trim().length > 80) return 'Instance names can be at most 80 characters.';
		return null;
	});

	const urlProblem = $derived.by(() => {
		if (modalProvider === 'tmdb' || !url.trim()) return null;
		let parsed: URL;
		try {
			parsed = new URL(url.trim());
		} catch {
			return 'Enter a full URL, including http:// or https://.';
		}
		if (!['http:', 'https:'].includes(parsed.protocol)) return 'Only http:// and https:// work.';
		if (!parsed.hostname || parsed.username || parsed.password) {
			return 'Use a host URL without embedded credentials.';
		}
		return null;
	});

	function hasChanges(): boolean {
		if (credential) return true;
		if (modalProvider === 'tmdb') return false;
		return (
			instanceName.trim() !== displayName(modalProvider) || url.trim() !== storedUrl(modalProvider)
		);
	}

	function formReady(): boolean {
		if (Boolean(busy) || !secureContext || Boolean(nameProblem) || Boolean(urlProblem))
			return false;
		if (modalProvider !== 'tmdb' && !url.trim()) return false;
		if (!secretFor(modalProvider)?.configured && !credential) return false;
		return hasChanges();
	}

	function testReady(): boolean {
		if (Boolean(busy) || !secureContext || Boolean(nameProblem) || Boolean(urlProblem))
			return false;
		if (modalProvider !== 'tmdb' && !url.trim()) return false;
		return Boolean(secretFor(modalProvider)?.configured || credential);
	}

	function testPayload(provider: IntegrationProvider) {
		return {
			...(provider !== 'tmdb' ? { url: url.trim() } : {}),
			...(credential ? { credential } : {})
		};
	}

	function probeFacts(): string[] {
		if (!probe) return [];
		const facts: string[] = [];
		if (probe.appName) facts.push(probe.appName);
		if (probe.instanceName && probe.instanceName !== probe.appName) facts.push(probe.instanceName);
		if (probe.version) facts.push(`v${probe.version}`);
		if (probe.osName)
			facts.push(probe.osVersion ? `${probe.osName} ${probe.osVersion}` : probe.osName);
		if (probe.isDocker) facts.push('Docker');
		return facts;
	}

	/** Read-only health check for the registry. It uses a stored credential only
	 * inside the server; no credential value is sent back to or retained by the browser. */
	async function refreshStoredConnection(provider: IntegrationProvider) {
		sessionStates[provider] = 'checking';
		try {
			await testIntegration(fetch, provider, {});
			sessionStates[provider] = 'connected';
		} catch {
			sessionStates[provider] = 'failed';
		}
	}

	async function testConnection() {
		if (!transportAllowed() || !testReady()) return;
		busy = 'test';
		probe = null;
		probeError = null;
		try {
			const result = await testIntegration(fetch, modalProvider, testPayload(modalProvider));
			probe = result.status ?? {};
			sessionStates[modalProvider] = 'connected';
			toast(`${details[modalProvider].label} connection verified`, 'good');
		} catch (error) {
			sessionStates[modalProvider] = 'failed';
			probeError = message(error, `${details[modalProvider].label} connection failed`);
			toast(probeError, 'bad');
		} finally {
			// Candidate credentials are intentionally one-use browser state. Saving
			// invokes the server-side test again, so no secret needs to linger here.
			credential = '';
			busy = null;
		}
	}

	async function saveConnection() {
		if (!transportAllowed() || !formReady()) return;
		busy = 'save';
		probeError = null;
		try {
			const payload = {
				expected_version: settings.configuration_version,
				expected_secret_generation: secretFor(modalProvider)?.generation ?? 0,
				...(modalProvider !== 'tmdb' && instanceName.trim() !== displayName(modalProvider)
					? { name: instanceName.trim() }
					: {}),
				...(modalProvider !== 'tmdb' && url.trim() !== storedUrl(modalProvider)
					? { url: url.trim() }
					: {}),
				...(credential ? { credential } : {})
			};
			const result = await putIntegration(fetch, modalProvider, payload);
			probe = result.status ?? {};
			sessionStates[modalProvider] = 'connected';
			onSettings(result.settings);
			toast(`${details[modalProvider].label} connection saved`, 'good');
			closeModal();
		} catch (error) {
			if (error instanceof ApiError && error.status === 409) {
				onSettings(await getSettings(fetch));
				toast(CONFIGURATION_CONFLICT_MESSAGE, 'info');
			} else {
				probeError = message(error, `Could not save ${details[modalProvider].label}`);
				toast(probeError, 'bad');
			}
		} finally {
			credential = '';
			busy = null;
		}
	}

	async function clearCredential() {
		if (!transportAllowed() || busy) return;
		if (!clearArmed) {
			clearArmed = true;
			return;
		}
		busy = 'clear';
		try {
			const result = await clearIntegrationCredential(
				fetch,
				modalProvider,
				secretFor(modalProvider)?.generation ?? 0
			);
			onSettings(result.settings);
			sessionStates[modalProvider] = 'failed';
			clearArmed = false;
			toast(`${details[modalProvider].label} credential cleared`, 'info');
			closeModal();
		} catch (error) {
			toast(message(error, `Could not clear ${details[modalProvider].label} credential`), 'bad');
		} finally {
			busy = null;
		}
	}

	async function runSync(provider: ArrProvider) {
		if (syncing) return;
		syncing = provider;
		try {
			const job = await syncLibraries(fetch);
			toast(
				job.disposition === 'reused'
					? 'A library sync is already running'
					: 'Library sync started for connected services',
				'info'
			);
		} catch (error) {
			toast(message(error, 'Could not start a library sync'), 'bad');
		} finally {
			syncing = null;
		}
	}

	function handleModalKey(event: KeyboardEvent) {
		if (modalOpen && event.key === 'Escape' && !busy) closeModal();
	}
</script>

<svelte:window onkeydown={handleModalKey} />

<section class="connection-registry" aria-labelledby="connection-registry-title">
	<header class="registry-head">
		<h3 id="connection-registry-title">Connected Services</h3>
	</header>

	<div class="table-scroll">
		<table>
			<colgroup>
				<col />
				<col />
				<col class="flex-column" />
				<col />
				<col />
				<col />
				<col />
			</colgroup>
			<thead>
				<tr>
					<th scope="col">Service</th>
					<th scope="col">Instance</th>
					<th scope="col">Endpoint</th>
					<th scope="col">Credential</th>
					<th scope="col">Connection</th>
					<th scope="col">Sync</th>
					<th scope="col" class="actions-head">Actions</th>
				</tr>
			</thead>
			<tbody>
				{#each arrProviders as provider (provider)}
					{@const detail = details[provider]}
					{@const status = statusFor(provider)}
					<tr>
						<td>
							<div class="service-cell">
								<span class="service-icon"><Icon name={detail.icon} size={16} /></span>
								<b>{detail.label}</b>
							</div>
						</td>
						<td>
							{#if isConfigured(provider)}
								<b class="instance-name" title={displayName(provider)}>{displayName(provider)}</b>
							{:else}
								<span class="muted">—</span>
							{/if}
						</td>
						<td>
							{#if storedUrl(provider)}
								<code title={storedUrl(provider)}>{storedUrl(provider)}</code>
							{:else}
								<span class="muted">—</span>
							{/if}
						</td>
						<td>{credentialText(provider)}</td>
						<td>
							<div class="connection-state" data-tone={status.tone} title={status.detail}>
								<i aria-hidden="true"></i>
								<b>{status.label}</b>
							</div>
						</td>
						<td>
							{#if isConfigured(provider)}
								<span>Every {settings.sync.interval_minutes} min</span>
							{:else}
								<span class="muted">—</span>
							{/if}
						</td>
						<td class="actions-cell">
							{#if isConfigured(provider)}
								<button type="button" class="pill quiet" onclick={() => openForm(provider, 'edit')}>
									Edit
								</button>
								<button
									type="button"
									class="pill ghost"
									onclick={() => runSync(provider)}
									disabled={Boolean(syncing)}
								>
									{syncing === provider ? 'Starting…' : 'Sync'}
								</button>
							{:else}
								<button type="button" class="pill quiet" onclick={() => openForm(provider, 'add')}>
									Configure
								</button>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>

<!-- Metadata providers are built in: no instance, no sync cadence, and nothing to
     add or remove, so they get their own table rather than distorting the one above. -->
<section class="connection-registry" aria-labelledby="metadata-registry-title">
	<header class="registry-head">
		<h3 id="metadata-registry-title">Metadata Providers</h3>
	</header>

	<div class="table-scroll">
		<table>
			<colgroup>
				<col />
				<col class="flex-column" />
				<col />
				<col />
				<col />
			</colgroup>
			<thead>
				<tr>
					<th scope="col">Service</th>
					<th scope="col">Endpoint</th>
					<th scope="col">Credential</th>
					<th scope="col">Connection</th>
					<th scope="col" class="actions-head">Actions</th>
				</tr>
			</thead>
			<tbody>
				<tr>
					<td>
						<div class="service-cell">
							<span class="service-icon tmdb"><Icon name="layers" size={16} /></span>
							<b class="instance-name">{displayName('tmdb')}</b>
							<span class="built-in-mark">Marquee built-in</span>
						</div>
					</td>
					<td><code>api.themoviedb.org</code></td>
					<td>{credentialText('tmdb')}</td>
					<td>
						<div class="connection-state" data-tone={tmdbStatus.tone} title={tmdbStatus.detail}>
							<i aria-hidden="true"></i>
							<b>{tmdbStatus.label}</b>
						</div>
					</td>
					<td class="actions-cell">
						<button type="button" class="pill quiet" onclick={() => openForm('tmdb', 'edit')}>
							Edit credential
						</button>
					</td>
				</tr>
			</tbody>
		</table>
	</div>
</section>

{#if modalOpen}
	<div
		class="modal-backdrop"
		role="button"
		tabindex="-1"
		aria-label="Dismiss connection dialog"
		onclick={() => !busy && closeModal()}
		onkeydown={(event) => event.key === 'Enter' && !busy && closeModal()}
	>
		<div
			class="connection-modal"
			role="dialog"
			tabindex="-1"
			aria-modal="true"
			aria-labelledby="connection-modal-title"
			onclick={(event) => event.stopPropagation()}
			onkeydown={(event) => event.stopPropagation()}
		>
			<header>
				<div>
					<span class="registry-kicker"
						>{modalIntent === 'add' ? 'New connection' : 'Connection settings'}</span
					>
					<h3 id="connection-modal-title">
						{modalIntent === 'add' ? `Add ${current.label}` : `Edit ${current.label}`}
					</h3>
				</div>
				<button
					type="button"
					class="close"
					aria-label="Close"
					onclick={closeModal}
					disabled={Boolean(busy)}>×</button
				>
			</header>

			<form
				onsubmit={(event) => {
					event.preventDefault();
					saveConnection();
				}}
			>
				{#if modalProvider === 'tmdb'}
					<div class="built-in-panel">
						<Icon name="layers" size={17} />
						<div>
							<b>{credentialLabel('tmdb')}</b>
							<p>
								{secretFor('tmdb')?.source === 'managed'
									? 'A custom token replaces Marquee’s built-in credential.'
									: 'Marquee’s built-in credential is active. Add a custom token only when you need to use your own TMDB account.'}
							</p>
						</div>
					</div>
				{:else}
					<label>
						<span>Instance name</span>
						<input
							bind:this={nameField}
							bind:value={instanceName}
							maxlength="80"
							autocomplete="off"
							aria-invalid={Boolean(nameProblem)}
						/>
						{#if nameProblem}<small class="field-error">{nameProblem}</small>{/if}
					</label>
					<label>
						<span>Service URL</span>
						<input
							type="url"
							bind:value={url}
							placeholder="http://service:port"
							spellcheck="false"
							autocomplete="url"
							aria-invalid={Boolean(urlProblem)}
						/>
						{#if urlProblem}<small class="field-error">{urlProblem}</small>{/if}
					</label>
				{/if}
				<label>
					<span>{modalProvider === 'tmdb' ? 'Custom read access token' : 'API key'}</span>
					<input
						type="password"
						bind:value={credential}
						name={`${modalProvider}-replacement-credential`}
						autocomplete="new-password"
						placeholder={secretFor(modalProvider)?.configured
							? 'Leave blank to keep current credential'
							: 'Enter credential'}
						disabled={!settings.secret_store.writable || !secureContext}
					/>
					<small
						>Blank leaves the current credential unchanged. This value is never displayed after
						submission.</small
					>
				</label>

				{#if probe || probeError}
					<div class:bad={Boolean(probeError)} class="probe-result" role="status">
						<Icon name={probeError ? 'alert' : 'check'} size={15} />
						<div>
							<b>{probeError ? 'Connection check failed' : 'Connection verified'}</b>
							<p>
								{(probeError ?? probeFacts().join(' · ')) || 'Service responded successfully.'}
							</p>
						</div>
					</div>
				{/if}

				{#if !settings.secret_store.writable}
					<p class="security-note">
						<Icon name="alert" size={14} /> Credential replacement is unavailable: {settings
							.secret_store.reason ?? 'the settings keyring is not mounted'}.
					</p>
				{:else if !secureContext}
					<p class="security-note">
						<Icon name="alert" size={14} /> Credential controls require HTTPS or loopback.
					</p>
				{/if}

				<footer class="modal-actions">
					<div>
						{#if secretFor(modalProvider)?.configured}
							<button
								type="button"
								class:armed={clearArmed}
								class="pill danger"
								onclick={clearCredential}
								disabled={Boolean(busy) || !settings.secret_store.writable || !secureContext}
							>
								{busy === 'clear' ? 'Clearing…' : clearArmed ? 'Confirm clear' : 'Clear credential'}
							</button>
						{/if}
					</div>
					<div>
						<button type="button" class="pill ghost" onclick={closeModal} disabled={Boolean(busy)}
							>Cancel</button
						>
						<button
							type="button"
							class="pill quiet"
							onclick={testConnection}
							disabled={!testReady()}>{busy === 'test' ? 'Testing…' : 'Test connection'}</button
						>
						<button type="submit" class="pill primary" disabled={!formReady()}
							>{busy === 'save'
								? 'Saving…'
								: modalIntent === 'add'
									? 'Test & add'
									: 'Test & save'}</button
						>
					</div>
				</footer>
			</form>
		</div>
	</div>
{/if}

<style>
	.connection-registry {
		margin-bottom: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		overflow: hidden;
	}
	.registry-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 20px;
		padding: 16px 18px;
		border-bottom: 1px solid var(--line);
	}
	.registry-head h3,
	.connection-modal h3 {
		margin: 3px 0 0;
		color: var(--text);
		font-size: 15px;
		font-weight: 680;
		letter-spacing: -0.01em;
	}
	.registry-kicker {
		color: var(--muted);
		font: 650 9px/1.2 var(--font-mono);
		letter-spacing: 0.08em;
		text-transform: uppercase;
	}
	.table-scroll {
		overflow-x: auto;
		overscroll-behavior-x: contain;
	}
	/* Deliberately auto layout. The previous `table-layout: fixed` with percentage
	   columns sized tracks independently of their content, so a cell whose text was
	   wider than its percentage printed straight over its neighbour. Auto layout
	   sizes every track to fit, which makes that overlap impossible; anything wider
	   than the viewport becomes horizontal scroll in .table-scroll instead. */
	table {
		width: 100%;
		min-width: 640px;
		border-collapse: collapse;
		font-size: 11.5px;
	}
	/* Absorbs the slack so the actions column stays hard right. */
	.flex-column {
		width: 100%;
	}
	th {
		padding: 10px 14px;
		border-bottom: 1px solid var(--line);
		background: color-mix(in srgb, var(--panel2) 45%, var(--panel));
		color: var(--muted);
		font: 650 9px/1.2 var(--font-mono);
		letter-spacing: 0.06em;
		text-align: left;
		text-transform: uppercase;
		white-space: nowrap;
	}
	td {
		padding: 13px 14px;
		border-bottom: 1px solid var(--line);
		color: var(--text);
		vertical-align: middle;
		white-space: nowrap;
	}
	tbody tr {
		transition: background 0.12s;
	}
	tbody tr:hover {
		background: color-mix(in srgb, var(--panel2) 34%, transparent);
	}
	.service-cell,
	.connection-state {
		display: flex;
		align-items: center;
		gap: 9px;
	}
	.service-cell b,
	.connection-state b,
	.probe-result b,
	.built-in-panel b {
		font-size: 12px;
		font-weight: 680;
	}
	/* A user-supplied instance name is unbounded, so cap it rather than let it
	   push the rest of the row into a scroll. */
	.instance-name {
		display: block;
		overflow: hidden;
		max-width: 190px;
		text-overflow: ellipsis;
	}
	.service-icon {
		display: grid;
		flex: 0 0 auto;
		place-items: center;
		width: 30px;
		height: 30px;
		border: 1px solid color-mix(in srgb, var(--info) 30%, var(--line));
		border-radius: var(--radius-sm);
		color: var(--info);
		background: color-mix(in srgb, var(--info) 7%, var(--panel2));
	}
	.service-icon.tmdb {
		border-color: color-mix(in srgb, var(--gold) 35%, var(--line));
		color: var(--gold);
		background: color-mix(in srgb, var(--gold) 8%, var(--panel2));
	}
	.built-in-mark {
		flex: 0 0 auto;
		padding: 2px 6px;
		border: 1px solid color-mix(in srgb, var(--gold) 34%, var(--line));
		border-radius: var(--radius-pill);
		color: var(--gold-copy, var(--gold));
		font: 600 9px/1.2 var(--font-mono);
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}
	code {
		display: block;
		max-width: 260px;
		overflow: hidden;
		color: var(--text);
		font: 10.5px/1.4 var(--font-mono);
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.muted {
		color: var(--muted);
	}
	.connection-state {
		gap: 8px;
	}
	.connection-state i {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--warn);
		box-shadow: 0 0 0 3px color-mix(in srgb, var(--warn) 12%, transparent);
	}
	.connection-state[data-tone='good'] i {
		background: var(--good);
		box-shadow: 0 0 0 3px color-mix(in srgb, var(--good) 12%, transparent);
	}
	.connection-state[data-tone='bad'] i {
		background: var(--bad);
		box-shadow: 0 0 0 3px color-mix(in srgb, var(--bad) 12%, transparent);
	}
	.connection-state[data-tone='good'] b {
		color: var(--good);
	}
	.connection-state[data-tone='bad'] b {
		color: var(--bad);
	}
	.connection-state[data-tone='warn'] b {
		color: var(--warn-copy);
	}
	.actions-head,
	.actions-cell {
		text-align: right;
	}
	/* The global .pill is 13px against an 11.5px table, which made two of them
	   wider than the column they sit in. */
	.actions-cell .pill {
		padding: 6px 11px;
		font-size: 11.5px;
	}
	.actions-cell .pill + .pill {
		margin-left: 6px;
	}
	.modal-backdrop {
		position: fixed;
		inset: 0;
		z-index: 200;
		display: grid;
		place-items: start center;
		padding: min(12vh, 120px) 16px 20px;
		background: color-mix(in srgb, var(--ink) 65%, transparent);
		backdrop-filter: blur(5px);
	}
	.connection-modal {
		width: min(100%, 560px);
		max-height: calc(100vh - 40px);
		overflow-y: auto;
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		background: var(--panel);
		box-shadow: 0 26px 64px var(--shadow);
	}
	.connection-modal > header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 16px;
		padding: 18px 20px 14px;
		border-bottom: 1px solid var(--line);
	}
	.close {
		display: grid;
		place-items: center;
		width: 30px;
		height: 30px;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--muted);
		font-size: 20px;
		line-height: 1;
	}
	.close:hover:not(:disabled) {
		color: var(--text);
	}
	form {
		display: grid;
		gap: 14px;
		padding: 18px 20px 0;
	}
	form > label {
		display: grid;
		gap: 6px;
		color: var(--text);
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
	input:focus-visible {
		outline: 2px solid color-mix(in srgb, var(--gold) 65%, transparent);
		outline-offset: 1px;
	}
	input:disabled {
		cursor: not-allowed;
		opacity: 0.55;
	}
	form > label small {
		color: var(--muted);
		font-size: 10.5px;
		font-weight: 500;
		line-height: 1.4;
	}
	.field-error {
		color: var(--bad) !important;
	}
	.built-in-panel,
	.probe-result,
	.security-note {
		display: flex;
		align-items: flex-start;
		gap: 9px;
		margin: 0;
		padding: 11px 12px;
		border: 1px solid color-mix(in srgb, var(--gold) 28%, var(--line));
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--gold) 6%, var(--panel2));
		color: var(--gold-copy, var(--gold));
	}
	.built-in-panel p,
	.probe-result p {
		margin: 4px 0 0;
		color: var(--muted);
		font-size: 10.5px;
		line-height: 1.45;
	}
	.probe-result {
		border-color: color-mix(in srgb, var(--good) 30%, var(--line));
		background: color-mix(in srgb, var(--good) 6%, var(--panel2));
		color: var(--good);
	}
	.probe-result.bad {
		border-color: color-mix(in srgb, var(--bad) 34%, var(--line));
		background: color-mix(in srgb, var(--bad) 7%, var(--panel2));
		color: var(--bad);
	}
	.security-note {
		border-color: color-mix(in srgb, var(--warn) 35%, var(--line));
		background: color-mix(in srgb, var(--warn) 7%, var(--panel2));
		color: var(--warn-copy);
		font-size: 10.5px;
		line-height: 1.45;
	}
	.modal-actions {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin: 4px -20px 0;
		padding: 12px 20px;
		border-top: 1px solid var(--line);
		background: color-mix(in srgb, var(--panel2) 35%, transparent);
	}
	.modal-actions > div {
		display: flex;
		align-items: center;
		gap: 7px;
	}
	.pill.danger.armed {
		border-color: var(--bad);
		background: color-mix(in srgb, var(--bad) 15%, transparent);
		color: var(--bad);
	}
	@media (max-width: 700px) {
		.modal-backdrop {
			padding: 8px;
		}
		.connection-modal {
			max-height: calc(100vh - 16px);
		}
		.modal-actions,
		.modal-actions > div {
			align-items: stretch;
			flex-direction: column;
		}
		.modal-actions > div:last-child {
			width: 100%;
		}
		.modal-actions .pill {
			justify-content: center;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		tbody tr {
			transition: none;
		}
	}
</style>
