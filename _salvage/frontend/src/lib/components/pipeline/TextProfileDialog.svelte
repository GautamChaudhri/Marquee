<script lang="ts">
	import {
		DEFAULT_PROFILE_SETTINGS,
		type TextProfile,
		type TextProfileScope,
		type TextProfileSettings
	} from '$lib/api/text-profiles';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import TextProfileEditor from './TextProfileEditor.svelte';
	import { presetSettings, type ProfileDialogMode } from './text-profile-fields';

	let {
		mode,
		scope,
		source = null,
		busy = false,
		onSubmit,
		onCancel
	}: {
		mode: ProfileDialogMode;
		scope: TextProfileScope;
		/** The profile being edited or duplicated; null when creating from scratch. */
		source?: TextProfile | null;
		busy?: boolean;
		onSubmit: (payload: { name: string; settings: TextProfileSettings }) => void;
		onCancel: () => void;
	} = $props();

	function seed(): { name: string; settings: TextProfileSettings } {
		if (mode === 'edit' && source) {
			return { name: source.name, settings: { ...DEFAULT_PROFILE_SETTINGS, ...source.settings } };
		}
		if (mode === 'duplicate' && source) {
			return {
				name: `${source.name} (copy)`,
				settings: { ...DEFAULT_PROFILE_SETTINGS, ...source.settings, mode: 'custom' }
			};
		}
		return { name: '', settings: presetSettings('title_only', scope) };
	}

	// Mounted fresh per open by the parent, so seeding once here is enough.
	const original = seed();
	let draft = $state(seed());

	const trimmedName = $derived(draft.name.trim());
	const dirty = $derived(
		trimmedName !== original.name ||
			JSON.stringify(draft.settings) !== JSON.stringify(original.settings)
	);
	const title = $derived(
		mode === 'create'
			? 'New Text Profile'
			: mode === 'duplicate'
				? `Duplicate ${source?.name ?? 'profile'}`
				: `Edit ${source?.name ?? 'profile'}`
	);
	const confirmLabel = $derived(mode === 'edit' ? 'Save changes' : 'Create profile');

	function submit() {
		if (!trimmedName || busy) return;
		onSubmit({ name: trimmedName, settings: { ...draft.settings, mode: 'custom' } });
	}
</script>

<ConfirmDialog
	open={true}
	{title}
	message={mode === 'duplicate'
		? 'Built-in presets are read-only — this saves a custom copy you can tune.'
		: undefined}
	{confirmLabel}
	cancelLabel="Cancel"
	{busy}
	confirmDisabled={!trimmedName || (mode === 'edit' && !dirty)}
	maxWidth="580px"
	onConfirm={submit}
	{onCancel}
>
	<TextProfileEditor {draft} {scope} {busy} showPresets={mode === 'create'} />
</ConfirmDialog>
