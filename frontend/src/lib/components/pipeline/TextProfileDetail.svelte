<script lang="ts">
	import type { TextProfile, TextProfileScope } from '$lib/api/text-profiles';
	import Icon from '$lib/components/Icon.svelte';
	import { categoriesFor, describeProfile } from './text-profile-fields';

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

	/** Only the permitted categories are listed — a wall of struck-through
	 *  "not allowed" chips said nothing the allow-list does not already say. */
	const allowed = $derived(categoriesFor(scope).filter((c) => profile.settings[c.key]));
	const areaPercent = $derived(Math.round(profile.settings.max_residual_area_fraction * 100));
	const specs = $derived([
		{ label: 'Residual boxes', value: `≤ ${profile.settings.max_residual_boxes}`, mono: true },
		{ label: 'Residual area', value: `≤ ${areaPercent}%`, mono: true },
		{
			label: 'Title match',
			value: profile.settings.require_title ? 'Required' : 'Optional',
			mono: false
		}
	]);
</script>

<div class="detail">
	<h3 class="detail-head">Profile details</h3>

	<p class="summary">{describeProfile(profile, scope)}</p>

	<div class="columns">
		<section>
			<h4>Allowed text</h4>
			{#if allowed.length}
				<div class="chips">
					{#each allowed as category (category.key)}
						<span class="chip" title={category.hint}>
							<Icon name="check" size={12} />
							{category.label}
						</span>
					{/each}
				</div>
			{:else}
				<p class="none">Nothing — any detected text rejects the poster.</p>
			{/if}
		</section>

		<section class="ruled">
			<h4>Tolerances</h4>
			<dl class="specs">
				{#each specs as spec (spec.label)}
					<div class="spec">
						<dt>{spec.label}</dt>
						<span class="leader" aria-hidden="true"></span>
						<dd class:mono={spec.mono}>{spec.value}</dd>
					</div>
				{/each}
			</dl>
		</section>
	</div>

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
		display: flex;
		flex-direction: column;
		gap: 16px;
		height: 100%;
		padding: 15px 16px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel2);
	}
	.detail-head {
		margin: 0;
		color: var(--faint);
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.09em;
		text-transform: uppercase;
	}
	.summary {
		max-width: 70ch;
		margin: -6px 0 0;
		color: var(--text);
		font-size: 13px;
		line-height: 1.55;
	}
	.columns {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		gap: 24px;
	}
	.ruled {
		padding-left: 24px;
		border-left: 1px solid var(--line);
	}
	@media (max-width: 860px) {
		.columns {
			grid-template-columns: 1fr;
			gap: 16px;
		}
		.ruled {
			padding-left: 0;
			border-left: none;
		}
	}
	section h4 {
		margin: 0 0 8px;
		color: var(--faint);
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.07em;
		text-transform: uppercase;
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
		padding: 4px 10px;
		border: 1px solid color-mix(in srgb, var(--good) 34%, transparent);
		border-radius: 999px;
		background: color-mix(in srgb, var(--good) 10%, transparent);
		color: var(--good);
		font-size: 11.5px;
		font-weight: 550;
	}
	.none {
		margin: 0;
		color: var(--muted);
		font-size: 12.5px;
	}
	/* A spec sheet, not a form: dotted leaders and no field chrome, so the
	   read-only values are never mistaken for editable inputs. */
	.specs {
		margin: 0;
	}
	.spec {
		display: flex;
		align-items: baseline;
		gap: 8px;
		padding: 6px 0;
	}
	.spec + .spec {
		border-top: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
	}
	dt {
		color: var(--muted);
		font-size: 12.5px;
	}
	.leader {
		flex: 1;
		min-width: 12px;
		border-bottom: 1px dotted var(--faint2);
		transform: translateY(-4px);
	}
	dd {
		margin: 0;
		color: var(--text);
		font-size: 12.5px;
		font-weight: 600;
		white-space: nowrap;
	}
	.mono {
		font-family: var(--font-mono);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: 8px;
		margin-top: auto;
		padding-top: 14px;
		border-top: 1px solid var(--line);
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
</style>
