<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import {
		clearIntegrationCredential,
		getSettings,
		putIntegration,
		testIntegration,
		type IntegrationProvider
	} from '$lib/api/system';
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
	let busy = $state<'test' | 'save' | 'clear' | null>(null);
	let clearArmed = $state(false);

	$effect(() => {
		if (!urlDirty) url = savedUrl;
	});

	function message(error: unknown, fallback: string): string {
		return error instanceof Error && error.message ? error.message : fallback;
	}

	function transportAllowed(): boolean {
		if (secureContext) return true;
		credential = '';
		toast('Use HTTPS or loopback before changing integration credentials.', 'bad');
		return false;
	}

	async function test() {
		if (busy || !transportAllowed()) return;
		busy = 'test';
		try {
			await testIntegration(fetch, provider, {
				...(detail.urlKey ? { url } : {}),
				...(credential ? { credential } : {})
			});
			toast(`${detail.short} connection verified`, 'good');
		} catch (error) {
			toast(message(error, `${detail.short} connection failed`), 'bad');
		} finally {
			credential = '';
			busy = null;
		}
	}

	async function save() {
		if (busy || !transportAllowed() || (!urlDirty && !credential)) return;
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
		if (!transportAllowed()) return;
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
					bind:value={url}
					oninput={() => (urlDirty = true)}
					placeholder="http://service:port"
					spellcheck="false"
					autocomplete="url"
				/>
			</label>
		{/if}
		<label>
			<span>{provider === 'tmdb' ? 'Read access token' : 'API key'}</span>
			<input
				type="password"
				bind:value={credential}
				name={`${provider}-replacement-credential`}
				autocomplete="new-password"
				placeholder={configured ? 'Leave blank to keep current credential' : 'Enter credential'}
				disabled={!settings.secret_store.writable || !secureContext}
			/>
		</label>
	</div>

	<div class="secret-facts">
		<span>Source: {secret?.source ?? 'default'}</span>
		<span>Applies: Next job</span>
		<span>Generation: {secret?.generation ?? 0}</span>
		{#if secret?.updated_at}<span>Rotated: {new Date(secret.updated_at).toLocaleDateString()}</span
			>{/if}
	</div>
	{#if !settings.secret_store.writable}
		<p class="store-warning">
			Credential replacement is unavailable: {settings.secret_store.reason ??
				'keyring not mounted'}.
		</p>
	{:else if !secureContext}
		<p class="store-warning">
			Credential controls are disabled until this page is opened over HTTPS or loopback.
		</p>
	{/if}

	<footer>
		<button
			type="button"
			class="secondary"
			onclick={test}
			disabled={Boolean(busy) || !secureContext}
		>
			{busy === 'test' ? 'Testing…' : 'Test connection'}
		</button>
		<div class="credential-actions">
			{#if configured}
				<button
					type="button"
					class:armed={clearArmed}
					class="clear"
					onclick={clearCredential}
					disabled={Boolean(busy) || !settings.secret_store.writable || !secureContext}
				>
					{busy === 'clear' ? 'Clearing…' : clearArmed ? 'Confirm clear' : 'Clear credential'}
				</button>
			{/if}
			<button
				type="button"
				class="primary"
				onclick={save}
				disabled={Boolean(busy) || !secureContext || (!urlDirty && !credential)}
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
		border-radius: 8px;
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
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
		padding: 9px 10px;
		font: 12px/1.4 var(--font-mono);
	}
	input:disabled {
		opacity: 0.55;
		cursor: not-allowed;
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
		justify-content: space-between;
		gap: 10px;
		padding: 12px 16px;
		border-top: 1px solid var(--line);
		background: color-mix(in srgb, var(--panel2) 42%, transparent);
	}
	.credential-actions {
		display: flex;
		gap: 8px;
	}
	button {
		border-radius: 7px;
		padding: 7px 11px;
		font-size: 11px;
		font-weight: 650;
	}
	button:disabled {
		opacity: 0.46;
		cursor: not-allowed;
	}
	.secondary {
		border: 1px solid var(--line2);
		background: var(--panel2);
	}
	.primary {
		border: 1px solid var(--gold-deep);
		background: var(--gold);
		color: var(--on-gold);
	}
	.clear {
		border: 0;
		background: transparent;
		color: var(--muted);
	}
	.clear.armed {
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
