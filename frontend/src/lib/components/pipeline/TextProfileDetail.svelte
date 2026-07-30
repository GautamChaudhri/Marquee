<script lang="ts">
	import type { TextProfile, TextProfileScope } from '$lib/api/text-profiles';
	import Icon from '$lib/components/Icon.svelte';
	import { categoriesFor, describeProfile, modeLabel, profileTone } from './text-profile-fields';

	let {
		profile,
		scope,
		isDefault = false,
		busy = false,
		onActivate,
		onEdit,
		onDuplicate,
		onDelete
	}: {
		profile: TextProfile;
		scope: TextProfileScope;
		isDefault?: boolean;
		busy?: boolean;
		onActivate: () => void;
		onEdit: () => void;
		onDuplicate: () => void;
		onDelete: () => void;
	} = $props();

	const categories = $derived(categoriesFor(scope));
	const areaPercent = $derived(Math.round(profile.settings.max_residual_area_fraction * 100));
</script>

<div class="detail">
	<div class="head">
		<span class="badge {profileTone(profile)}">{profile.name}</span>
		<span class="tag">{profile.builtin ? 'built-in · read-only' : 'custom'}</span>
		{#if profile.settings.mode !== 'custom'}
			<span class="tag mode">{modeLabel(profile)}</span>
		{/if}
		{#if isDefault}<span class="tag active">active</span>{/if}
	</div>

	<p class="summary">{describeProfile(profile, scope)}</p>

	<div class="section-label">Allowed text</div>
	<div class="chips">
		{#each categories as category (category.key)}
			<span class="chip" class:on={profile.settings[category.key]} title={category.hint}>
				<Icon name={profile.settings[category.key] ? 'check' : 'x'} size={12} />
				{category.label}
			</span>
		{/each}
	</div>

	<div class="section-label">Tolerances</div>
	<dl class="specs">
		<div>
			<dt>Residual boxes</dt>
			<dd class="mono">≤ {profile.settings.max_residual_boxes}</dd>
		</div>
		<div>
			<dt>Residual area</dt>
			<dd class="mono">≤ {areaPercent}%</dd>
		</div>
		<div>
			<dt>Title match</dt>
			<dd>{profile.settings.require_title ? 'required' : 'optional'}</dd>
		</div>
	</dl>

	<div class="actions">
		<button class="btn" disabled={busy || isDefault} onclick={onActivate}>
			{isDefault ? 'Active' : 'Set as active'}
		</button>
		{#if profile.builtin}
			<button class="btn primary" disabled={busy} onclick={onDuplicate}>Duplicate & edit</button>
		{:else}
			<button class="btn primary" disabled={busy} onclick={onEdit}>Edit</button>
			<button class="btn danger" disabled={busy} onclick={onDelete}>Delete</button>
		{/if}
	</div>
</div>

<style>
	.detail {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel2);
		padding: 14px 15px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		height: 100%;
	}
	.head {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
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
		background: var(--panel);
	}
	.badge.gold {
		color: var(--gold);
		border-color: color-mix(in srgb, var(--gold) 40%, transparent);
		background: color-mix(in srgb, var(--gold) 8%, transparent);
	}
	.tag {
		font-size: 11px;
		color: var(--faint);
	}
	.tag.mode {
		border: 1px solid var(--line2);
		border-radius: 999px;
		padding: 1px 7px;
	}
	.tag.active {
		color: var(--good);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-size: 10px;
		font-weight: 700;
	}
	.summary {
		margin: 0;
		color: var(--muted);
		font-size: 12.5px;
		line-height: 1.5;
	}
	.section-label {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: var(--faint);
		font-weight: 700;
		margin-top: 2px;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 4px 9px;
		border-radius: 999px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--faint);
		font-size: 11.5px;
	}
	.chip.on {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 38%, transparent);
		background: color-mix(in srgb, var(--good) 10%, transparent);
	}
	.specs {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 8px;
		margin: 0;
	}
	.specs > div {
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--panel);
		padding: 7px 9px;
	}
	dt {
		font-size: 10.5px;
		color: var(--faint);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	dd {
		margin: 3px 0 0;
		font-size: 13px;
		color: var(--text);
	}
	.mono {
		font-family: var(--font-mono);
	}
	.actions {
		display: flex;
		gap: 8px;
		margin-top: auto;
		padding-top: 4px;
		flex-wrap: wrap;
	}
	.btn {
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--text);
		border-radius: 8px;
		padding: 7px 13px;
		font-size: 12.5px;
		cursor: pointer;
	}
	.btn:hover:not(:disabled) {
		border-color: var(--line);
		background: var(--ink2);
	}
	.btn.primary {
		border-color: var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-weight: 600;
	}
	.btn.danger {
		border-color: color-mix(in srgb, var(--bad) 45%, transparent);
		background: color-mix(in srgb, var(--bad) 10%, var(--panel));
		color: var(--bad);
	}
	.btn:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.actions .btn:first-child {
		margin-right: auto;
	}
</style>
