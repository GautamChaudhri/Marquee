<script lang="ts">
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import PolicyList from '$lib/components/subtitles/PolicyList.svelte';
	import PolicyEditor from '$lib/components/subtitles/PolicyEditor.svelte';
	import type { SubtitlePolicy } from '$lib/api/types';

	let activePolicy = $state<SubtitlePolicy | null>(null);
	let editorOpen = $state(false);

	function openPolicyEditor(policy: SubtitlePolicy) {
		activePolicy = policy;
		editorOpen = true;
	}

	function closePolicyEditor() {
		activePolicy = null;
		editorOpen = false;
	}
</script>

<svelte:head>
	<title>Language Policies – Marquee</title>
</svelte:head>

<div class="page-container">
	<div class="top-nav">
		<a href="/audio-subs" class="back-link">← Back to Dashboard</a>
	</div>

	<SectionHeader
		title="Audio & Subtitle Cleanup Policies"
		subtitle="Configure rules to automatically clean up unwanted languages and track formats."
	/>

	<div class="policies-wrapper">
		{#if editorOpen}
			<PolicyEditor
				policy={activePolicy || ({} as any)}
				onSave={closePolicyEditor}
				onCancel={closePolicyEditor}
			/>
		{:else}
			<PolicyList onEdit={openPolicyEditor} onAudit={openPolicyEditor} onApply={openPolicyEditor} />
		{/if}
	</div>
</div>

<style>
	.page-container {
		display: flex;
		flex-direction: column;
		gap: 18px;
		height: 100%;
		padding-bottom: 24px;
	}
	.top-nav {
		margin-bottom: 6px;
	}
	.back-link {
		color: var(--muted);
		font-size: 13px;
		text-decoration: none;
		display: inline-flex;
		align-items: center;
		transition: color 0.15s;
	}
	.back-link:hover {
		color: var(--gold);
	}
	.policies-wrapper {
		flex: 1;
		min-height: 0;
	}
</style>
