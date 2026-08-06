<script lang="ts">
	import {
		createTextProfile,
		deleteTextProfile,
		listTextProfiles,
		setDefaultProfile,
		type ScopedTextProfileList,
		type TextProfileScope,
		updateTextProfile,
		type TextProfile,
		type TextProfileSettings
	} from '$lib/api/text-profiles';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import TextProfileDetail from '$lib/components/pipeline/TextProfileDetail.svelte';
	import TextProfileDialog from '$lib/components/pipeline/TextProfileDialog.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import { toast } from '$lib/toast';
	import { profileTone, type ProfileDialogMode } from './text-profile-fields';

	let {
		initial = null,
		flat = false
	}: {
		initial?: ScopedTextProfileList | null;
		/** Drop the outer card so a parent can supply one shared border. */
		flat?: boolean;
	} = $props();

	const EMPTY_SCOPE = { profiles: [] as TextProfile[], default_id: 'title_only' };
	let activeScope = $state<TextProfileScope>('movie');
	// svelte-ignore state_referenced_locally
	let scoped = $state<Record<TextProfileScope, { profiles: TextProfile[]; default_id: string }>>(
		initial?.scopes ?? { movie: EMPTY_SCOPE, show: EMPTY_SCOPE, season: EMPTY_SCOPE }
	);
	/** null means "follow the scope default" — the detail pane is never empty. */
	let selectedId = $state<string | null>(null);
	let busy = $state(false);
	let dialog = $state<{ mode: ProfileDialogMode; source: TextProfile | null } | null>(null);
	let pendingDelete = $state<TextProfile | null>(null);

	const scopeData = $derived(scoped[activeScope] ?? EMPTY_SCOPE);
	const profiles = $derived(scopeData.profiles);
	const defaultId = $derived(scopeData.default_id);
	const builtins = $derived(profiles.filter((p) => p.builtin));
	const customs = $derived(profiles.filter((p) => !p.builtin));
	const selected = $derived(
		profiles.find((p) => p.id === selectedId) ?? profiles.find((p) => p.id === defaultId) ?? null
	);
	const tabs = [
		{ id: 'movie', label: 'Movie' },
		{ id: 'show', label: 'Show' },
		{ id: 'season', label: 'Season' }
	];

	async function refresh() {
		try {
			const data = await listTextProfiles(fetch);
			scoped = data.scopes;
		} catch {
			/* keep stale list */
		}
	}

	async function activate(id: string) {
		if (id === defaultId || busy) return;
		busy = true;
		try {
			await setDefaultProfile(fetch, activeScope, id);
			await refresh();
			toast('Default text profile updated — applies on next pipeline run', 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not set default profile', 'bad');
		} finally {
			busy = false;
		}
	}

	async function submitDialog(payload: { name: string; settings: TextProfileSettings }) {
		if (!dialog || busy) return;
		const { mode, source } = dialog;
		busy = true;
		try {
			if (mode === 'edit' && source) {
				await updateTextProfile(fetch, activeScope, source.id, payload);
				await refresh();
				selectedId = source.id;
				toast('Profile saved — applies on next pipeline run', 'good');
			} else {
				const profile = await createTextProfile(fetch, activeScope, payload);
				await refresh();
				selectedId = profile.id;
				toast(`Profile “${profile.name}” created`, 'good');
			}
			dialog = null;
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not save profile', 'bad');
		} finally {
			busy = false;
		}
	}

	async function confirmDelete() {
		const profile = pendingDelete;
		if (!profile || busy) return;
		busy = true;
		try {
			await deleteTextProfile(fetch, activeScope, profile.id);
			if (selectedId === profile.id) selectedId = null;
			await refresh();
			pendingDelete = null;
			toast(`Profile “${profile.name}” deleted`, 'info');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not delete profile', 'bad');
		} finally {
			busy = false;
		}
	}
</script>

<section class="tp-card" class:flat>
	<header class="tp-head">
		<div>
			<h2>Poster Text Profiles</h2>
			<p class="sub">Controls what text is allowed on posters the pipeline accepts.</p>
		</div>
		<div class="scope-tabs">
			<TabBar
				{tabs}
				active={activeScope}
				onSelect={(id: string) => {
					activeScope = id as TextProfileScope;
					selectedId = null;
				}}
			/>
		</div>
	</header>

	<div class="tp-body">
		<div class="list">
			<div class="group">
				<h3>Built-in Presets</h3>
				{#each builtins as profile (profile.id)}
					<button
						class="row"
						class:selected={selected?.id === profile.id}
						onclick={() => (selectedId = profile.id)}
					>
						<span class="badge {profileTone(profile)}">{profile.name}</span>
						{#if profile.id === defaultId}<span class="default-tag">active</span>{/if}
					</button>
				{/each}
			</div>

			<div class="group">
				<h3>Custom Profiles</h3>
				{#each customs as profile (profile.id)}
					<button
						class="row"
						class:selected={selected?.id === profile.id}
						onclick={() => (selectedId = profile.id)}
					>
						<span class="badge gold">{profile.name}</span>
						{#if profile.id === defaultId}<span class="default-tag">active</span>{/if}
					</button>
				{:else}
					<p class="empty">No custom profiles yet.</p>
				{/each}
			</div>

			<button class="new-btn" onclick={() => (dialog = { mode: 'create', source: null })}>
				+ New profile
			</button>
		</div>

		{#if selected}
			{@const sel = selected}
			<TextProfileDetail
				profile={sel}
				scope={activeScope}
				isDefault={sel.id === defaultId}
				{busy}
				onActivate={() => activate(sel.id)}
				onEdit={() => (dialog = { mode: 'edit', source: sel })}
				onDuplicate={() => (dialog = { mode: 'duplicate', source: sel })}
				onDelete={() => (pendingDelete = sel)}
			/>
		{:else}
			<p class="empty">No profiles in this scope.</p>
		{/if}
	</div>
</section>

{#if dialog}
	<TextProfileDialog
		mode={dialog.mode}
		scope={activeScope}
		source={dialog.source}
		{busy}
		onSubmit={submitDialog}
		onCancel={() => {
			if (!busy) dialog = null;
		}}
	/>
{/if}

<ConfirmDialog
	open={pendingDelete !== null}
	title="Delete Text Profile"
	message={pendingDelete
		? `Delete “${pendingDelete.name}”? Titles using it fall back to the scope default.`
		: ''}
	confirmLabel="Delete"
	tone="bad"
	{busy}
	onConfirm={confirmDelete}
	onCancel={() => {
		if (!busy) pendingDelete = null;
	}}
/>

<style>
	.tp-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		margin-bottom: 18px;
		overflow: hidden;
	}
	.tp-card.flat {
		border: 0;
		border-radius: 0;
		background: none;
		margin-bottom: 0;
	}
	.tp-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
		padding: 13px 16px;
		border-bottom: 1px solid var(--line);
	}
	.tp-head h2 {
		margin: 0;
		font-size: 13px;
		font-weight: 680;
	}
	.sub {
		margin: 2px 0 0;
		color: var(--muted);
		font-size: 11.5px;
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
		color: var(--info);
		border-color: color-mix(in srgb, var(--info) 35%, transparent);
		background: color-mix(in srgb, var(--info) 10%, transparent);
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
		grid-template-columns: 280px minmax(0, 1fr);
		gap: 16px;
		padding: 16px;
	}
	@media (max-width: 980px) {
		.tp-body {
			grid-template-columns: 1fr;
		}
	}
	.list {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.group h3 {
		margin: 0 0 6px;
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
	.row.selected {
		border-color: color-mix(in srgb, var(--gold) 30%, var(--line2));
		background: var(--panel2);
	}
	.default-tag {
		margin-left: auto;
		font-size: 10px;
		color: var(--good);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 700;
	}
	.empty {
		color: var(--faint);
		font-size: 12px;
		margin: 2px 0 0;
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
</style>
