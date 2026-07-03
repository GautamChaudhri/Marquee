<script lang="ts">
	import {
		DEFAULT_PROFILE_SETTINGS,
		createTextProfile,
		deleteTextProfile,
		listTextProfiles,
		setDefaultProfile,
		type TextProfile,
		type TextProfileList
	} from '$lib/api/text-profiles';
	import { toast } from '$lib/toast';

	let { initial = null }: { initial?: TextProfileList | null } = $props();

	// svelte-ignore state_referenced_locally
	let profiles = $state<TextProfile[]>(initial?.profiles ?? []);
	// svelte-ignore state_referenced_locally
	let defaultId = $state<string>(initial?.default_id ?? 'title_only');
	let selectedId = $state<string | null>(null);
	let busy = $state(false);
	let creating = $state(false);
	let newName = $state('');

	const builtins = $derived(profiles.filter((p) => p.builtin));
	const customs = $derived(profiles.filter((p) => !p.builtin));
	const activeProfile = $derived(profiles.find((p) => p.id === defaultId) ?? null);
	const selected = $derived(profiles.find((p) => p.id === selectedId) ?? null);

	function tone(profile: TextProfile): string {
		if (profile.id === 'title_only') return 'blue';
		if (profile.id === 'textless') return 'gray';
		return 'gold';
	}

	function summary(profile: TextProfile): string {
		const s = profile.settings;
		if (s.mode === 'title_only')
			return 'Only the movie title is allowed. All other text (taglines, credits, billing blocks) rejects the poster.';
		if (s.mode === 'textless')
			return 'No text at all. Posters with any detected text are rejected. Use for clean, iconic key art.';
		const allowed = [
			s.allow_title && 'title',
			s.allow_director && 'director',
			s.allow_studio && 'studio',
			s.allow_rating && 'rating',
			s.allow_tagline && 'tagline',
			s.allow_billing && 'billing'
		].filter(Boolean);
		const parts = [
			allowed.length ? `Allows: ${allowed.join(', ')}` : 'Allows no text categories',
			`residual ≤ ${s.max_residual_boxes} boxes / ${Math.round(s.max_residual_area_fraction * 100)}% area`,
			s.require_title ? 'title required' : 'title optional'
		];
		return parts.join(' · ');
	}

	async function refresh() {
		try {
			const data = await listTextProfiles(fetch);
			profiles = data.profiles;
			defaultId = data.default_id;
		} catch {
			/* keep stale list */
		}
	}

	async function activate(id: string) {
		if (id === defaultId || busy) return;
		busy = true;
		try {
			await setDefaultProfile(fetch, id);
			defaultId = id;
			await refresh();
			toast('Default text profile updated — applies on next pipeline run', 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not set default profile', 'bad');
		} finally {
			busy = false;
		}
	}

	async function createProfile() {
		const name = newName.trim();
		if (!name || busy) return;
		busy = true;
		try {
			const profile = await createTextProfile(fetch, {
				name,
				settings: { ...DEFAULT_PROFILE_SETTINGS }
			});
			newName = '';
			creating = false;
			await refresh();
			selectedId = profile.id;
			toast(`Profile “${profile.name}” created`, 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not create profile', 'bad');
		} finally {
			busy = false;
		}
	}

	async function removeProfile(profile: TextProfile) {
		if (busy) return;
		busy = true;
		try {
			await deleteTextProfile(fetch, profile.id);
			if (selectedId === profile.id) selectedId = null;
			await refresh();
			toast(`Profile “${profile.name}” deleted`, 'info');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not delete profile', 'bad');
		} finally {
			busy = false;
		}
	}
</script>

<section class="tp-card">
	<header class="tp-head">
		<div>
			<h2>Poster text profiles</h2>
			<p class="sub">Controls what text is allowed on posters the pipeline accepts.</p>
		</div>
		<div class="active">
			<span class="label">Active</span>
			{#if activeProfile}
				<span class="badge {tone(activeProfile)}">{activeProfile.name}</span>
			{/if}
			<select
				value={defaultId}
				disabled={busy}
				onchange={(e) => activate((e.currentTarget as HTMLSelectElement).value)}
			>
				{#each profiles as profile (profile.id)}
					<option value={profile.id}>{profile.name}</option>
				{/each}
			</select>
		</div>
	</header>

	<div class="tp-body">
		<div class="group">
			<h3>Built-in presets</h3>
			{#each builtins as profile (profile.id)}
				<button
					class="row"
					class:selected={selectedId === profile.id}
					onclick={() => (selectedId = selectedId === profile.id ? null : profile.id)}
				>
					<span class="badge {tone(profile)}">{profile.name}</span>
					{#if profile.id === defaultId}<span class="default-tag">default</span>{/if}
				</button>
			{/each}
		</div>

		<div class="group">
			<h3>Custom profiles</h3>
			{#each customs as profile (profile.id)}
				<div class="row-wrap" class:selected={selectedId === profile.id}>
					<button
						class="row"
						onclick={() => (selectedId = selectedId === profile.id ? null : profile.id)}
					>
						<span class="badge gold">{profile.name}</span>
						{#if profile.id === defaultId}<span class="default-tag">default</span>{/if}
					</button>
					<button
						class="mini danger"
						title="Delete profile"
						disabled={busy}
						onclick={() => removeProfile(profile)}
					>
						✕
					</button>
				</div>
			{:else}
				<p class="empty">No custom profiles yet.</p>
			{/each}

			{#if creating}
				<div class="new-row">
					<input
						placeholder="Profile name"
						bind:value={newName}
						maxlength="64"
						onkeydown={(e) => e.key === 'Enter' && createProfile()}
					/>
					<button class="mini" disabled={busy || !newName.trim()} onclick={createProfile}>
						Create
					</button>
					<button class="mini" onclick={() => (creating = false)}>Cancel</button>
				</div>
			{:else}
				<button class="new-btn" onclick={() => (creating = true)}>+ New profile</button>
			{/if}
		</div>
	</div>

	{#if selected}
		<div class="detail">
			<div class="detail-head">
				<span class="badge {tone(selected)}">{selected.name}</span>
				{#if selected.builtin}<span class="readonly-tag">built-in · read-only</span>{/if}
			</div>
			<p class="summary">{summary(selected)}</p>
		</div>
	{/if}
</section>

<style>
	.tp-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		padding: 16px;
		margin-bottom: 18px;
	}
	.tp-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 12px;
		flex-wrap: wrap;
	}
	.tp-head h2 {
		margin: 0;
		font-size: 15px;
	}
	.sub {
		margin: 4px 0 0;
		color: var(--muted);
		font-size: 12px;
	}
	.active {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.active .label {
		color: var(--faint);
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.active select {
		border: 1px solid var(--line2);
		background: var(--ink2);
		color: var(--text);
		border-radius: 7px;
		padding: 6px 9px;
		font-size: 13px;
	}
	.badge {
		display: inline-block;
		padding: 2px 9px;
		border-radius: 999px;
		font-size: 12px;
		font-weight: 600;
		border: 1px solid transparent;
	}
	.badge.blue {
		color: #7fb5ff;
		border-color: rgba(127, 181, 255, 0.35);
		background: rgba(127, 181, 255, 0.08);
	}
	.badge.gray {
		color: var(--muted);
		border-color: var(--line2);
		background: var(--panel2);
	}
	.badge.gold {
		color: var(--gold);
		border-color: color-mix(in srgb, var(--gold) 40%, transparent);
		background: color-mix(in srgb, var(--gold) 8%, transparent);
	}
	.tp-body {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 16px;
		margin-top: 14px;
	}
	@media (max-width: 680px) {
		.tp-body {
			grid-template-columns: 1fr;
		}
	}
	.group h3 {
		margin: 0 0 8px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
	}
	.row {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		text-align: left;
		background: none;
		border: 1px solid transparent;
		border-radius: 8px;
		padding: 7px 8px;
		cursor: pointer;
	}
	.row:hover {
		background: var(--panel2);
	}
	.row.selected,
	.row-wrap.selected .row {
		border-color: var(--line2);
		background: var(--panel2);
	}
	.row-wrap {
		display: flex;
		align-items: center;
		gap: 6px;
		border-radius: 8px;
	}
	.row-wrap .row {
		flex: 1;
	}
	.default-tag {
		font-size: 10px;
		color: var(--good);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.readonly-tag {
		font-size: 11px;
		color: var(--faint);
	}
	.mini {
		border: 1px solid var(--line2);
		background: var(--ink2);
		color: var(--text);
		border-radius: 7px;
		padding: 4px 9px;
		font-size: 12px;
		cursor: pointer;
	}
	.mini.danger {
		color: var(--bad);
	}
	.mini:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.empty {
		color: var(--faint);
		font-size: 12px;
		margin: 2px 0 8px;
	}
	.new-btn {
		border: 1px dashed var(--line2);
		background: none;
		color: var(--muted);
		border-radius: 8px;
		padding: 7px 10px;
		font-size: 12px;
		cursor: pointer;
		width: 100%;
	}
	.new-btn:hover {
		color: var(--text);
		border-color: var(--line);
	}
	.new-row {
		display: flex;
		gap: 6px;
		margin-top: 6px;
	}
	.new-row input {
		flex: 1;
		border: 1px solid var(--line2);
		background: var(--ink2);
		color: var(--text);
		border-radius: 7px;
		padding: 6px 9px;
		font-size: 13px;
	}
	.detail {
		margin-top: 14px;
		border-top: 1px solid var(--line);
		padding-top: 12px;
	}
	.detail-head {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.summary {
		margin: 8px 0 0;
		color: var(--muted);
		font-size: 12.5px;
		line-height: 1.5;
	}
</style>
