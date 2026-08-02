<script lang="ts">
	// Controlled on purpose: the Activity row keeps one flag, a workspace panel
	// keeps a set of them, and neither wants this button owning the truth.
	let {
		label,
		controls,
		open = false,
		onToggle
	}: {
		label: string;
		/** Id of the panel this button reveals. */
		controls: string;
		open?: boolean;
		onToggle: (open: boolean) => void;
	} = $props();
</script>

<button
	type="button"
	class="roster-toggle"
	class:open
	aria-expanded={open}
	aria-controls={controls}
	onclick={() => onToggle(!open)}
>
	<span class="chevron" aria-hidden="true"></span>
	{label}
</button>

<style>
	.roster-toggle {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		min-height: 32px;
		padding: 6px 9px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
		font-size: 11px;
	}
	.roster-toggle:hover {
		border-color: var(--gold);
	}
	/* An explicit chevron: the default disclosure triangle was easy to miss and gave no
	   hover affordance. It points down once the panel below is showing. */
	.chevron {
		width: 0;
		height: 0;
		flex: none;
		border-top: 4px solid transparent;
		border-bottom: 4px solid transparent;
		border-left: 6px solid var(--muted);
		transition: transform 0.15s ease;
	}
	.roster-toggle.open .chevron {
		transform: rotate(90deg);
	}
</style>
