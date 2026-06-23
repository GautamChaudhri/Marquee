<script lang="ts">
	import { getGenerators } from '$lib/api/subtitle-generators';
	import type { SubtitleGenerator } from '$lib/api/types';
	import { putSettings } from '$lib/api/system';
	import { toast } from '$lib/toast';

	let { settings = $bindable() }: { settings: any } = $props();

	// local state for editing
	let enabled = $state(false);
	let scanConcurrency = $state(2);
	let mutationConcurrency = $state(1);
	let generationConcurrency = $state(1);
	let preferredLanguagesStr = $state('en');
	let unknownLanguageAction = $state('keep');
	let protectForced = $state(true);
	let protectLastFullDialogue = $state(true);
	let backupMode = $state('none');
	let externalDeleteMode = $state('quarantine');

	let subgenUrl = $state('');
	let subgenProfileName = $state('Subgen');
	let subgenModelLabel = $state('unknown');
	let subgenMode = $state('transcribe');
	let subgenLocalPathPrefix = $state('');
	let subgenRemotePathPrefix = $state('');
	let subgenCallbackToken = $state('');

	let initialized = $state(false);

	$effect(() => {
		if (settings && !initialized) {
			const sub = settings.subtitles || {};
			const subgen = settings.integrations?.subgen || {};

			enabled = sub.enabled !== false;
			scanConcurrency = sub.scan_concurrency ?? 2;
			mutationConcurrency = sub.mutation_concurrency ?? 1;
			generationConcurrency = sub.generation_concurrency ?? 1;
			preferredLanguagesStr = sub.preferred_languages ? sub.preferred_languages.join(', ') : 'en';
			unknownLanguageAction = sub.unknown_language_action ?? 'keep';
			protectForced = sub.protect_forced !== false;
			protectLastFullDialogue = sub.protect_last_full_dialogue !== false;
			backupMode = sub.backup_mode ?? 'none';
			externalDeleteMode = sub.external_delete_mode ?? 'quarantine';

			subgenUrl = subgen.url ?? '';
			subgenProfileName = subgen.profile_name ?? 'Subgen';
			subgenModelLabel = subgen.model_label ?? 'unknown';
			subgenMode = subgen.mode ?? 'transcribe';
			subgenLocalPathPrefix = subgen.local_path_prefix ?? '';
			subgenRemotePathPrefix = subgen.remote_path_prefix ?? '';
			subgenCallbackToken = ''; // Omit token value for security

			initialized = true;
		}
	});

	// Test connection state
	let testing = $state(false);
	let testResult = $state<{ success: boolean; message: string } | null>(null);

	async function testConnection() {
		testing = true;
		testResult = null;
		try {
			const res = await getGenerators(fetch);
			const subgen = res.generators.find(g => g.type === 'subgen');
			if (subgen && subgen.online) {
				testResult = {
					success: true,
					message: `Connection successful! Subgen v${subgen.version || 'unknown'} online. Model: ${subgen.model || 'unknown'} (${subgen.device || 'CPU'})`
				};
				toast('Subgen connection test successful!', 'good');
			} else {
				testResult = {
					success: false,
					message: 'Connection failed: Subgen is offline or not configured'
				};
				toast('Subgen connection test failed', 'bad');
			}
		} catch (e: any) {
			testResult = {
				success: false,
				message: `Connection error: ${e.message || 'Unknown error'}`
			};
			toast('Subgen connection test failed', 'bad');
		} finally {
			testing = false;
		}
	}

	// Save settings state
	let saving = $state(false);

	async function saveSettings() {
		saving = true;
		try {
			const payload = {
				subtitles: {
					enabled,
					scan_concurrency: scanConcurrency,
					mutation_concurrency: mutationConcurrency,
					generation_concurrency: generationConcurrency,
					preferred_languages: preferredLanguagesStr
						.split(',')
						.map((s) => s.trim())
						.filter(Boolean),
					unknown_language_action: unknownLanguageAction,
					protect_forced: protectForced,
					protect_last_full_dialogue: protectLastFullDialogue,
					backup_mode: backupMode,
					external_delete_mode: externalDeleteMode
				},
				subgen: {
					url: subgenUrl || null,
					profile_name: subgenProfileName,
					model_label: subgenModelLabel,
					mode: subgenMode,
					local_path_prefix: subgenLocalPathPrefix || null,
					remote_path_prefix: subgenRemotePathPrefix || null,
					callback_token: subgenCallbackToken || null
				}
			};

			const response = await putSettings(fetch, payload);
			toast('Subtitle settings saved successfully!', 'good');
			settings = response.settings;
			initialized = false; // trigger re-initialization of local states
		} catch (e: any) {
			toast(e.message || 'Failed to save settings', 'bad');
		} finally {
			saving = false;
		}
	}

	// Interactive naming preview states
	let previewLang = $state('en');
	let previewSdh = $state(true);
	let previewForced = $state(false);
	let previewSubgenTag = $state(true);
	let previewModelName = $state(false);

	let previewFilename = $derived.by(() => {
		const base = 'Interstellar.2014.1080p';
		const parts = [base, previewLang];
		if (previewForced) parts.push('forced');
		if (previewSdh) parts.push('sdh');
		if (previewSubgenTag) parts.push('subgen');
		if (previewModelName) parts.push('medium');
		return parts.join('.') + '.srt';
	});

	// Interactive path translation preview states
	let testPath = $state('/mnt/PLUNDER/Media/Movies/Dune (2021)/Dune.2021.mkv');

	let translatedPath = $derived.by(() => {
		if (subgenLocalPathPrefix && testPath.startsWith(subgenLocalPathPrefix)) {
			return testPath.replace(subgenLocalPathPrefix, subgenRemotePathPrefix || '');
		}
		return 'No match (Prefix mismatch or empty)';
	});
</script>

<div class="subtitle-settings mq-rise">
	<div class="env-notice">
		<span class="icon">⚙️</span>
		<div class="content">
			<strong>Subtitles Configuration Overrides</strong>
			<p>Settings below override the defaults in your <code>.env</code> file. Changes are applied immediately to the active pipeline. Saved settings are persisted to <code>data/subtitle_overrides.json</code>.</p>
		</div>
	</div>

	{#if settings}
	<div class="settings-grid">
		<!-- Section 1: connection & status -->
		<div class="settings-section">
			<h3>AI Subgen Connection</h3>
			<div class="setting-row">
				<label for="enabled-chk" class="lbl">Enabled:</label>
				<label class="toggle">
					<input type="checkbox" id="enabled-chk" bind:checked={enabled} />
					<span class="toggle-track"></span>
				</label>
			</div>
			<div class="setting-row">
				<label for="subgen-url" class="lbl">Subgen URL:</label>
				<input type="text" id="subgen-url" class="str-input wide" bind:value={subgenUrl} placeholder="e.g. http://localhost:9000" />
			</div>
			<div class="setting-row">
				<label for="profile-name" class="lbl">Profile Name:</label>
				<input type="text" id="profile-name" class="str-input" bind:value={subgenProfileName} />
			</div>
			<div class="setting-row">
				<label for="model-label" class="lbl">Model Label:</label>
				<input type="text" id="model-label" class="str-input" bind:value={subgenModelLabel} />
			</div>
			<div class="setting-row">
				<label for="subgen-mode" class="lbl">Translation Mode:</label>
				<select id="subgen-mode" class="enum-select" bind:value={subgenMode}>
					<option value="transcribe">Transcribe</option>
					<option value="translate">Translate</option>
				</select>
			</div>
			<div class="setting-row">
				<label for="callback-token" class="lbl">Callback Token:</label>
				<input type="password" id="callback-token" class="str-input" bind:value={subgenCallbackToken} placeholder={settings?.integrations?.subgen?.callback_token_configured ? "••••••••" : "Not set"} />
			</div>

			<div class="test-conn-area">
				<button class="btn secondary btn-sm" onclick={testConnection} disabled={testing}>
					{testing ? 'Testing...' : 'Test Connection'}
				</button>
				{#if testResult}
					<div class="test-result" class:success={testResult.success}>
						{testResult.message}
					</div>
				{/if}
			</div>
		</div>

		<!-- Section 2: Preferred languages & concurrency -->
		<div class="settings-section">
			<h3>Preferences & Concurrency</h3>
			<div class="setting-row">
				<label for="preferred-langs" class="lbl">Preferred Languages:</label>
				<input type="text" id="preferred-langs" class="str-input" bind:value={preferredLanguagesStr} placeholder="e.g. en, es" />
			</div>
			<div class="setting-row">
				<label for="scan-concurrency" class="lbl">Scan Concurrency:</label>
				<input type="number" id="scan-concurrency" class="str-input" bind:value={scanConcurrency} min="1" step="1" />
			</div>
			<div class="setting-row">
				<label for="mutation-concurrency" class="lbl">Mutation Concurrency:</label>
				<input type="number" id="mutation-concurrency" class="str-input" bind:value={mutationConcurrency} min="1" step="1" />
			</div>
			<div class="setting-row">
				<label for="generation-concurrency" class="lbl">Generation Concurrency:</label>
				<input type="number" id="generation-concurrency" class="str-input" bind:value={generationConcurrency} min="1" step="1" />
			</div>
			<div class="setting-row">
				<label for="unknown-lang-action" class="lbl">Unknown Lang Action:</label>
				<select id="unknown-lang-action" class="enum-select" bind:value={unknownLanguageAction}>
					<option value="keep">Keep</option>
					<option value="review">Review</option>
					<option value="remove">Remove</option>
				</select>
			</div>
		</div>

		<!-- Section 3: Safety Rules -->
		<div class="settings-section">
			<h3>Mutation Safety</h3>
			<div class="setting-row">
				<label for="backup-mode" class="lbl">Backup Mode:</label>
				<select id="backup-mode" class="enum-select" bind:value={backupMode}>
					<option value="none">None (Delete original)</option>
					<option value="keep_original">Keep Original Backup</option>
				</select>
			</div>
			<div class="setting-row">
				<label for="external-delete-mode" class="lbl">External Delete Mode:</label>
				<select id="external-delete-mode" class="enum-select" bind:value={externalDeleteMode}>
					<option value="quarantine">Quarantine</option>
					<option value="delete">Delete</option>
				</select>
			</div>
			<div class="setting-row">
				<label for="protect-forced-chk" class="lbl">Protect Forced:</label>
				<label class="toggle">
					<input type="checkbox" id="protect-forced-chk" bind:checked={protectForced} />
					<span class="toggle-track"></span>
				</label>
			</div>
			<div class="setting-row">
				<label for="protect-last-full-dialogue-chk" class="lbl">Protect Last Full Dialogue:</label>
				<label class="toggle">
					<input type="checkbox" id="protect-last-full-dialogue-chk" bind:checked={protectLastFullDialogue} />
					<span class="toggle-track"></span>
				</label>
			</div>
		</div>

		<!-- Section 4: Interactive Filename Style Preview -->
		<div class="settings-section preview-section">
			<h3>Sidecar Filename Style</h3>
			<div class="preview-controls">
				<div class="ctrl">
					<label for="p-lang-sel">Language:</label>
					<select id="p-lang-sel" bind:value={previewLang}>
						<option value="en">English (en)</option>
						<option value="es">Spanish (es)</option>
						<option value="ja">Japanese (ja)</option>
						<option value="eng">English 3-letter (eng)</option>
					</select>
				</div>
				<div class="chk-row">
					<input type="checkbox" id="p-sdh-chk" bind:checked={previewSdh} />
					<label for="p-sdh-chk">SDH tag</label>
				</div>
				<div class="chk-row">
					<input type="checkbox" id="p-forced-chk" bind:checked={previewForced} />
					<label for="p-forced-chk">Forced tag</label>
				</div>
				<div class="chk-row">
					<input type="checkbox" id="p-subgen-chk" bind:checked={previewSubgenTag} />
					<label for="p-subgen-chk">Subgen tag</label>
				</div>
				<div class="chk-row">
					<input type="checkbox" id="p-model-chk" bind:checked={previewModelName} />
					<label for="p-model-chk">Model name</label>
				</div>
			</div>
			<div class="preview-output">
				<span class="lbl">Generated Filename:</span>
				<code class="val">{previewFilename}</code>
			</div>
		</div>

		<!-- Section 5: Interactive Path Translation Preview -->
		<div class="settings-section preview-section">
			<h3>Remote Path Mapping</h3>
			<div class="path-inputs">
				<div class="field">
					<label for="l-prefix">Local Path Prefix (Marquee)</label>
					<input type="text" id="l-prefix" bind:value={subgenLocalPathPrefix} placeholder="e.g. /mnt/PLUNDER/Media/Movies" />
				</div>
				<div class="field">
					<label for="r-prefix">Remote Path Prefix (Subgen)</label>
					<input type="text" id="r-prefix" bind:value={subgenRemotePathPrefix} placeholder="e.g. /movies" />
				</div>
				<div class="field">
					<label for="t-path">Test Input File</label>
					<input type="text" id="t-path" bind:value={testPath} />
				</div>
			</div>
			<div class="preview-output mt-10">
				<span class="lbl">Path Sent to Subgen:</span>
				<code class="val">{translatedPath}</code>
			</div>
		</div>
	</div>

	<!-- Save settings action bar -->
	<div class="save-bar mq-rise">
		<button class="save-btn" onclick={saveSettings} disabled={saving}>
			{#if saving}
				<span class="spin">⟳</span> Saving…
			{:else}
				Save Subtitle Settings
			{/if}
		</button>
		<span class="save-hint">Applied immediately to the running instance and saved to disk.</span>
	</div>
	{/if}
</div>

<style>
	.subtitle-settings {
		color: var(--text);
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.env-notice {
		display: flex;
		gap: 12px;
		padding: 14px;
		background: rgba(121, 192, 255, 0.05);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		font-size: 13px;
	}
	.env-notice .icon {
		font-size: 16px;
	}
	.env-notice strong {
		color: var(--gold);
		display: block;
		margin-bottom: 4px;
	}
	.env-notice p {
		margin: 0;
		color: var(--muted);
		line-height: 1.4;
	}
	.settings-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
		gap: 20px;
	}
	.settings-section {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.settings-section h3 {
		margin-top: 0;
		margin-bottom: 8px;
		font-size: 14px;
		font-weight: 600;
		border-bottom: 1px solid var(--line);
		padding-bottom: 8px;
	}
	.setting-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 13px;
		min-height: 32px;
	}
	.setting-row .lbl {
		color: var(--muted);
	}
	.test-conn-area {
		margin-top: 8px;
		border-top: 1px dashed var(--line);
		padding-top: 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.test-result {
		font-size: 12px;
		color: var(--bad);
		background: rgba(239, 83, 80, 0.05);
		border: 1px solid rgba(239, 83, 80, 0.2);
		padding: 8px;
		border-radius: 4px;
		line-height: 1.4;
	}
	.test-result.success {
		color: var(--good);
		background: rgba(86, 211, 100, 0.05);
		border-color: rgba(86, 211, 100, 0.2);
	}

	/* Inputs styling matching main settings page */
	.str-input {
		padding: 5px 10px;
		border-radius: 6px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12.5px;
		width: 140px;
		outline: none;
	}
	.str-input:focus {
		border-color: var(--gold-deep);
	}
	.str-input.wide {
		width: 220px;
	}

	.enum-select {
		padding: 5px 28px 5px 10px;
		border-radius: 6px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12.5px;
		font-family: inherit;
		min-width: 140px;
		appearance: none;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238a909f'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 8px center;
		outline: none;
	}
	.enum-select:focus {
		border-color: var(--gold-deep);
	}

	/* Toggle */
	.toggle {
		position: relative;
		display: inline-flex;
		align-items: center;
		cursor: pointer;
	}
	.toggle input {
		position: absolute;
		opacity: 0;
		width: 0;
		height: 0;
	}
	.toggle-track {
		width: 36px;
		height: 20px;
		border-radius: 10px;
		background: var(--panel2);
		border: 1px solid var(--line2);
		transition: background 0.15s;
		position: relative;
	}
	.toggle-track::after {
		content: '';
		position: absolute;
		top: 2px;
		left: 2px;
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: var(--text);
		transition: transform 0.15s;
	}
	.toggle input:checked + .toggle-track {
		background: var(--gold-deep);
		border-color: var(--gold);
	}
	.toggle input:checked + .toggle-track::after {
		transform: translateX(16px);
		background: var(--on-gold);
	}

	/* Save bar */
	.save-bar {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-top: 10px;
		padding: 14px 16px;
		border-radius: var(--radius);
		background: var(--panel);
		border: 1px solid var(--line);
	}
	.save-btn {
		padding: 9px 22px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.save-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.save-hint {
		font-size: 12px;
		color: var(--muted);
		flex: 1;
	}
	.spin {
		display: inline-block;
		animation: spin 1s linear infinite;
	}
	@keyframes spin {
		from { transform: rotate(0deg); }
		to { transform: rotate(360deg); }
	}

	/* Interactive previews */
	.preview-section {
		grid-column: span 1;
	}
	@media (min-width: 800px) {
		.preview-section {
			grid-column: span 2;
		}
	}
	.preview-controls {
		display: flex;
		gap: 14px;
		align-items: center;
		flex-wrap: wrap;
		font-size: 13px;
	}
	.preview-controls .ctrl {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.preview-controls select {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: 4px;
		padding: 4px 8px;
		color: var(--text);
	}
	.chk-row {
		display: flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
	}
	.chk-row input {
		width: 14px;
		height: 14px;
		accent-color: var(--gold);
	}
	.preview-output {
		background: var(--panel2);
		border: 1px solid var(--line);
		padding: 10px;
		border-radius: 6px;
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 12.5px;
	}
	.preview-output .lbl {
		color: var(--muted);
	}
	.preview-output .val {
		color: var(--gold);
		font-family: var(--font-mono);
		font-weight: 600;
	}

	.path-inputs {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
		gap: 12px;
	}
	.path-inputs .field {
		display: flex;
		flex-direction: column;
		gap: 5px;
	}
	.path-inputs label {
		font-size: 11px;
		color: var(--faint2);
		text-transform: uppercase;
		font-weight: 700;
	}
	.path-inputs input {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: 5px;
		padding: 6px 10px;
		font-size: 13px;
		color: var(--text);
		outline: none;
	}
	.mt-10 {
		margin-top: 10px;
	}

	/* Buttons */
	.btn {
		font-size: 12px;
		font-weight: 600;
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
		transition: background-color 0.15s;
	}
	.btn.secondary {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
		width: max-content;
	}
	.btn.secondary:hover {
		background: var(--panel);
	}
	.btn-sm {
		padding: 5px 10px;
	}
</style>
