<script lang="ts">
	import { createPolicy, updatePolicy, auditPolicy, applyPolicy } from '$lib/api/subtitle-policies';
	import { listMovies } from '$lib/api/library';
	import type { SubtitlePolicy } from '$lib/api/types';
	import { toast } from '$lib/toast';

	let {
		policy,
		onSave,
		onCancel,
		onJob
	}: {
		policy: SubtitlePolicy;
		onSave: () => void;
		onCancel: () => void;
		onJob: (jobId: string) => void;
	} = $props();

	let isNew = $derived(!policy.id);

	// Form states
	// svelte-ignore state_referenced_locally
	let name = $state(policy.name || '');
	// svelte-ignore state_referenced_locally
	let enabled = $state(policy.enabled !== false);
	// svelte-ignore state_referenced_locally
	let mode = $state<'allowlist' | 'blocklist'>(policy.mode || 'allowlist');
	// svelte-ignore state_referenced_locally
	let languagesText = $state(policy.languages ? policy.languages.join(', ') : 'en');
	// svelte-ignore state_referenced_locally
	let unknownAction = $state<'keep' | 'review' | 'remove'>(policy.unknown_action || 'review');

	// svelte-ignore state_referenced_locally
	let protectForced = $state(policy.protect_forced !== false);
	// svelte-ignore state_referenced_locally
	let protectDefault = $state(policy.protect_default !== false);
	// svelte-ignore state_referenced_locally
	let protectLastFullDialogue = $state(policy.protect_last_full_dialogue !== false);
	// svelte-ignore state_referenced_locally
	let includeExternal = $state(policy.include_external !== false);
	// svelte-ignore state_referenced_locally
	let autoApply = $state(!!policy.auto_apply);
	// svelte-ignore state_referenced_locally
	let auditOnly = $state(!!policy.audit_only);

	// svelte-ignore state_referenced_locally
	let hardlinkAction = $state<'block' | 'allow_break'>(policy.hardlink_action || 'block');
	// svelte-ignore state_referenced_locally
	let backupMode = $state<'none' | 'keep_original'>(policy.backup_mode || 'keep_original');

	// Audit & Apply state
	let auditing = $state(false);
	let applying = $state(false);

	async function handleSave() {
		if (!name.trim()) {
			toast('Policy name is required', 'info');
			return;
		}

		const body = {
			name,
			enabled,
			mode,
			languages: languagesText
				.split(',')
				.map((s) => s.trim().toLowerCase())
				.filter(Boolean),
			unknown_action: unknownAction,
			protect_forced: protectForced,
			protect_default: protectDefault,
			protect_last_full_dialogue: protectLastFullDialogue,
			include_external: includeExternal,
			auto_apply: autoApply,
			audit_only: auditOnly,
			hardlink_action: hardlinkAction,
			backup_mode: backupMode
		};

		try {
			if (isNew) {
				await createPolicy(fetch, body);
				toast('Policy created successfully', 'good');
			} else {
				await updatePolicy(fetch, policy.id, body);
				toast('Policy updated successfully', 'good');
			}
			onSave();
		} catch (e: any) {
			toast(`Failed to save: ${e.message}`, 'bad');
		}
	}

	// Audits policy on the first 10 movies in the library
	async function runAudit() {
		auditing = true;
		try {
			// We need a saved policy to audit, so save/create a temporary policy or use current id
			if (isNew) {
				toast('Please save the policy first before running an audit.', 'info');
				auditing = false;
				return;
			}

			const res = await auditPolicy(fetch, policy.id, 'movies');
			onJob(res.job_id);
			toast('Audit dry-run queued', 'good');
		} catch (e: unknown) {
			toast(`Audit failed: ${e instanceof Error ? e.message : 'Unknown error'}`, 'bad');
		} finally {
			auditing = false;
		}
	}

	// Apply policy to the library
	async function runApply() {
		if (isNew) {
			toast('Please save the policy first', 'info');
			return;
		}
		if (
			!confirm(
				'Are you sure you want to apply this policy now? This will queue batch subtitle removal jobs.'
			)
		)
			return;

		applying = true;
		try {
			const libraryRes = await listMovies(fetch, { page_size: 100 });
			const movieIds = libraryRes.items.map((m) => m.id);

			const res = await applyPolicy(fetch, policy.id, movieIds);
			onJob(res.job_id);
			toast('Policy batch queued', 'good');
		} catch (e: unknown) {
			toast(`Failed to apply: ${e instanceof Error ? e.message : 'Unknown error'}`, 'bad');
		} finally {
			applying = false;
		}
	}
</script>

<div class="policy-editor-panel mq-rise">
	<div class="editor-header">
		<h4>{isNew ? 'Create New Subtitle Policy' : `Edit Policy: ${policy.name}`}</h4>
		<button class="btn-close" onclick={onCancel}>✕</button>
	</div>

	<div class="editor-grid">
		<div class="form-pane">
			<div class="form-row">
				<div class="field">
					<label for="p-name">Policy Name</label>
					<input
						type="text"
						id="p-name"
						placeholder="e.g. Keep English & Spanish"
						bind:value={name}
					/>
				</div>
				<div class="checkbox-field flex-end">
					<input type="checkbox" id="p-enabled" bind:checked={enabled} />
					<label for="p-enabled">Enabled</label>
				</div>
			</div>

			<div class="form-row split">
				<div class="field">
					<div class="field-label">Classification Mode</div>
					<div class="radio-group" role="group" aria-label="Classification Mode">
						<label class="radio-lbl">
							<input type="radio" value="allowlist" bind:group={mode} />
							<span>Allowlist (Keep selected languages)</span>
						</label>
						<label class="radio-lbl">
							<input type="radio" value="blocklist" bind:group={mode} />
							<span>Blocklist (Remove selected languages)</span>
						</label>
					</div>
				</div>

				<div class="field">
					<label for="p-langs">Languages (comma-separated codes)</label>
					<input
						type="text"
						id="p-langs"
						placeholder="e.g. en, es, ja"
						bind:value={languagesText}
					/>
					<small class="help">Use 2-letter (ISO 639-1) or 3-letter (ISO 639-2) codes.</small>
				</div>
			</div>

			<div class="form-row split">
				<div class="field">
					<label for="p-unknown">Unknown Language Action</label>
					<select id="p-unknown" bind:value={unknownAction}>
						<option value="keep">Keep Track</option>
						<option value="review">Flag for Review</option>
						<option value="remove">Remove Track</option>
					</select>
				</div>
				<div class="field">
					<label for="p-hardlink">Hardlink Action</label>
					<select id="p-hardlink" bind:value={hardlinkAction}>
						<option value="block">Block Remux (Safest)</option>
						<option value="allow_break">Allow Break Link (Duplicate File)</option>
					</select>
				</div>
			</div>

			<div class="form-row split">
				<div class="field">
					<label for="p-backup">Backup Mode</label>
					<select id="p-backup" bind:value={backupMode}>
						<option value="none">No Backup (Permanent deletion)</option>
						<option value="keep_original">Keep Original Backup File</option>
					</select>
				</div>
				<div class="field">
					<!-- Empty grid cell to keep split aligned -->
				</div>
			</div>

			<div class="safety-options">
				<h5>Safety Exceptions & Scope</h5>
				<div class="options-grid">
					<div class="checkbox-field">
						<input type="checkbox" id="p-forced" bind:checked={protectForced} />
						<label for="p-forced">Protect Forced Tracks</label>
					</div>
					<div class="checkbox-field">
						<input type="checkbox" id="p-default" bind:checked={protectDefault} />
						<label for="p-default">Protect Default Tracks</label>
					</div>
					<div class="checkbox-field">
						<input type="checkbox" id="p-last" bind:checked={protectLastFullDialogue} />
						<label for="p-last">Protect Last Full Dialogue Track</label>
					</div>
					<div class="checkbox-field">
						<input type="checkbox" id="p-ext" bind:checked={includeExternal} />
						<label for="p-ext">Include External Sidecars in Evaluation</label>
					</div>
					<div class="checkbox-field">
						<input type="checkbox" id="p-auto" bind:checked={autoApply} />
						<label for="p-auto">Auto-Apply on Sync</label>
					</div>
					<div class="checkbox-field">
						<input type="checkbox" id="p-audit" bind:checked={auditOnly} />
						<label for="p-audit">Audit Only (Do not apply deletion)</label>
					</div>
				</div>
			</div>

			<div class="editor-foot">
				{#if !isNew}
					<button class="btn secondary" onclick={runAudit} disabled={auditing || applying}>
						{auditing ? 'Auditing...' : '🔍 Audit (Dry-Run)'}
					</button>
					<button class="btn secondary" onclick={runApply} disabled={auditing || applying}>
						{applying ? 'Applying...' : '⚡ Apply Policy Now'}
					</button>
				{/if}
				<div class="right-btns">
					<button class="btn secondary" onclick={onCancel} disabled={auditing || applying}
						>Cancel</button
					>
					<button class="btn primary" onclick={handleSave} disabled={auditing || applying}
						>Save Policy</button
					>
				</div>
			</div>
		</div>

		<!-- Audit Output Pane -->
		<div class="audit-pane">
			<h5>Audit Dry-Run Result</h5>
			{#if auditing}
				<div class="audit-loading">Running dry-run audit against library...</div>
			{:else}
				<div class="audit-placeholder">
					Save changes and click "Audit" to evaluate this cleanup policy against your library
					without making any actual filesystem writes.
				</div>
			{/if}
		</div>
	</div>
</div>

<style>
	.policy-editor-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 18px;
		color: var(--text);
		margin-top: 16px;
	}
	.editor-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		border-bottom: 1px solid var(--line);
		padding-bottom: 10px;
		margin-bottom: 16px;
	}
	.editor-header h4 {
		margin: 0;
		font-size: 15px;
		font-weight: 600;
	}
	.btn-close {
		background: transparent;
		border: none;
		color: var(--muted);
		font-size: 16px;
		cursor: pointer;
	}
	.editor-grid {
		display: grid;
		grid-template-columns: 1fr 340px;
		gap: 20px;
	}
	@media (max-width: 900px) {
		.editor-grid {
			grid-template-columns: 1fr;
		}
	}
	.form-pane {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.form-row {
		display: flex;
		gap: 14px;
		align-items: flex-start;
	}
	.form-row.split > .field {
		flex: 1;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 5px;
	}
	.field label {
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--faint2);
	}
	.field input[type='text'],
	.field select {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 9px;
		font-size: 13px;
		color: var(--text);
		outline: none;
	}
	.field input[type='text']:focus,
	.field select:focus {
		border-color: var(--gold);
	}
	.checkbox-field {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		cursor: pointer;
	}
	.checkbox-field.flex-end {
		margin-bottom: 10px;
		align-self: flex-end;
	}
	.checkbox-field input {
		width: 16px;
		height: 16px;
		accent-color: var(--gold);
		cursor: pointer;
	}
	.checkbox-field label {
		cursor: pointer;
	}
	.radio-group {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin-top: 4px;
	}
	.radio-lbl {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		cursor: pointer;
	}
	.radio-lbl input {
		accent-color: var(--gold);
	}
	.help {
		font-size: 11px;
		color: var(--muted);
	}

	.safety-options {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 14px;
		margin-top: 8px;
	}
	.safety-options h5 {
		margin-top: 0;
		margin-bottom: 12px;
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		color: var(--faint2);
	}
	.options-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
		gap: 12px;
	}

	.editor-foot {
		display: flex;
		justify-content: space-between;
		border-top: 1px solid var(--line);
		padding-top: 14px;
		margin-top: 10px;
	}
	.right-btns {
		display: flex;
		gap: 8px;
	}

	/* Audit Output Pane */
	.audit-pane {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
		display: flex;
		flex-direction: column;
		height: max-content;
		max-height: 480px;
	}
	.audit-pane h5 {
		margin-top: 0;
		margin-bottom: 12px;
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		color: var(--faint2);
		border-bottom: 1px solid var(--line);
		padding-bottom: 6px;
	}
	.audit-placeholder {
		font-size: 12.5px;
		color: var(--muted);
		text-align: center;
		padding: 40px 10px;
		line-height: 1.4;
	}
	.audit-loading {
		font-size: 13px;
		color: var(--muted);
		text-align: center;
		padding: 32px 0;
	}
	/* Buttons */
	.btn {
		font-size: 13px;
		font-weight: 600;
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
		transition: background-color 0.15s;
	}
	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--on-gold);
	}
	.btn.primary:hover:not(:disabled) {
		background: var(--gold-deep);
	}
	.btn.secondary {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
	}
	.btn.secondary:hover:not(:disabled) {
		background: var(--panel);
	}
</style>
