<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import SliderControl from './SliderControl.svelte';
	import type { SettingsCatalogEntry } from '$lib/api/types';

	let {
		entry,
		value,
		source,
		defaultValue,
		dirty = false,
		disabled = false,
		pathStatus,
		onChange,
		onReset
	}: {
		entry: SettingsCatalogEntry;
		value: unknown;
		source: string;
		defaultValue?: unknown;
		dirty?: boolean;
		disabled?: boolean;
		pathStatus?: { path: string; exists: boolean; readable: boolean; writable: boolean };
		onChange: (value: unknown) => void;
		onReset: () => void;
	} = $props();

	const inputId = $derived(`setting-${entry.key.toLowerCase().replaceAll('_', '-')}`);
	const locked = $derived(disabled || !entry.editable || entry.storage === 'deployment');

	/**
	 * A numeric knob only earns a slider when the catalog gives it real bounds.
	 * A range input with no min/max silently becomes 0–100, which would quietly
	 * mangle any knob whose true range is wider or finer.
	 */
	const bounded = $derived.by(() => {
		if (!['weight', 'float', 'int'].includes(entry.control.kind)) return null;
		const { min, max } = entry.control;
		if (typeof min !== 'number' || typeof max !== 'number' || max <= min) return null;
		return { min, max, step: entry.control.step ?? (entry.control.kind === 'int' ? 1 : 0.01) };
	});

	/**
	 * Most pipeline knobs document themselves in source comments rather than
	 * Field(description=...), so the old fallback rendered the de-cased key —
	 * "k neighbors" under a "K Neighbors" label. Say nothing instead of that.
	 */
	const description = $derived.by(() => {
		const text = entry.description ?? entry.control.help ?? '';
		const trimmed = text.trim();
		if (!trimmed) return null;
		const normalize = (value: string) =>
			value
				.toLowerCase()
				.replaceAll(/[\s_]+/g, ' ')
				.trim();
		return normalize(trimmed) === normalize(entry.title) ? null : trimmed;
	});

	// Only a stored override can be dropped. A key already sitting on its default
	// has nothing to reset, so offering the action there would be a no-op button.
	const overridden = $derived(source === 'custom' || source === 'revision');
	const resettable = $derived(
		entry.storage === 'revision' && defaultValue !== undefined && (dirty || overridden)
	);

	function numberValue(raw: string): number {
		return entry.control.kind === 'int' ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
	}

	function listValue(raw: string): string[] {
		return raw
			.split('\n')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function optionLabel(option: string | number | boolean): string {
		return String(option)
			.replaceAll('_', ' ')
			.replace(/\b\w/g, (letter) => letter.toUpperCase());
	}

	/**
	 * Badges only earn their place when they say something the row does not already
	 * show. "Default" labels the status quo, "Custom" duplicates the reset button
	 * that appears alongside it, and `next_job` is the apply mode of nearly every
	 * key in the catalog, so it printed on nearly every row while carrying no
	 * information. What survives is the genuinely exceptional: a key that needs a
	 * restart or one still being read from a deprecated environment variable.
	 */
	const badges = $derived.by(() => {
		const marks: { text: string; tone?: 'warn' }[] = [];
		if (entry.apply_mode === 'restart') marks.push({ text: 'Restart required', tone: 'warn' });
		if (source === 'environment') marks.push({ text: 'Legacy environment' });
		return marks;
	});
</script>

<div class:dirty class:locked class="setting-row">
	<div class="setting-copy">
		<label for={inputId}>{entry.title}</label>
		{#if description}<p>{description}</p>{/if}
		{#if badges.length}
			<div class="setting-meta">
				{#each badges as badge (badge.text)}
					<span class:warn={badge.tone === 'warn'}>{badge.text}</span>
				{/each}
			</div>
		{/if}
	</div>

	<div class="setting-control">
		{#if entry.storage === 'deployment' && value === undefined}
			<span class="deployment-value">Configured before startup</span>
		{:else if entry.control.kind === 'bool'}
			<label class="switch" aria-label={entry.title}>
				<input
					id={inputId}
					type="checkbox"
					checked={Boolean(value)}
					disabled={locked}
					onchange={(event) => onChange((event.currentTarget as HTMLInputElement).checked)}
				/>
				<span></span>
			</label>
		{:else if entry.control.kind === 'enum' && entry.control.options}
			<select
				id={inputId}
				value={String(value ?? '')}
				disabled={locked}
				onchange={(event) => onChange((event.currentTarget as HTMLSelectElement).value)}
			>
				{#each entry.control.options as option (String(option))}
					<option value={String(option)}>{optionLabel(option)}</option>
				{/each}
			</select>
		{:else if bounded}
			<SliderControl
				id={inputId}
				label={entry.title}
				value={Number(value ?? 0)}
				min={bounded.min}
				max={bounded.max}
				step={bounded.step}
				integer={entry.control.kind === 'int'}
				disabled={locked}
				{onChange}
			/>
		{:else if ['weight', 'float', 'int'].includes(entry.control.kind)}
			<input
				id={inputId}
				type="number"
				value={Number(value ?? 0)}
				min={entry.control.min}
				max={entry.control.max}
				step={entry.control.step ?? (entry.control.kind === 'int' ? 1 : 0.1)}
				disabled={locked}
				oninput={(event) => onChange(numberValue((event.currentTarget as HTMLInputElement).value))}
			/>
		{:else if entry.control.kind === 'list'}
			<textarea
				id={inputId}
				rows="3"
				value={Array.isArray(value) ? value.join('\n') : ''}
				disabled={locked}
				oninput={(event) => onChange(listValue((event.currentTarget as HTMLTextAreaElement).value))}
			></textarea>
		{:else}
			<input
				id={inputId}
				type="text"
				value={String(value ?? '')}
				disabled={locked}
				spellcheck="false"
				oninput={(event) => onChange((event.currentTarget as HTMLInputElement).value)}
			/>
		{/if}
		{#if resettable}
			<button
				type="button"
				class="reset"
				{disabled}
				onclick={onReset}
				title="Drop the stored override and use the server default"
			>
				<Icon name="refresh" size={12} />
				Reset
			</button>
		{/if}
		{#if pathStatus}
			<output class="path-health" aria-label={`Current accessibility for ${entry.title}`}>
				<span class:good={pathStatus.readable} class:bad={!pathStatus.readable}
					>{pathStatus.readable
						? 'Readable'
						: pathStatus.exists
							? 'Not readable'
							: 'Unavailable'}</span
				>
				<span class:good={pathStatus.writable} class:bad={!pathStatus.writable}
					>{pathStatus.writable ? 'Writable' : 'Not writable'}</span
				>
			</output>
		{/if}
	</div>
</div>

<style>
	.setting-row {
		display: grid;
		grid-template-columns: minmax(200px, 1fr) minmax(240px, 0.9fr);
		gap: 24px;
		align-items: center;
		padding: 16px 18px;
		border-bottom: 1px solid var(--line);
		transition: background 120ms ease;
	}
	.setting-row:last-child {
		border-bottom: 0;
	}
	.setting-row.dirty {
		background: color-mix(in srgb, var(--gold) 6%, transparent);
	}
	.setting-row.locked {
		background: color-mix(in srgb, var(--panel2) 45%, transparent);
	}
	.setting-copy {
		min-width: 0;
	}
	.setting-copy label {
		color: var(--text);
		font-size: 13px;
		font-weight: 650;
	}
	.setting-copy p {
		margin: 4px 0 0;
		color: var(--muted);
		font-size: 11.5px;
		line-height: 1.45;
	}
	.setting-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		margin-top: 8px;
	}
	.setting-meta span {
		padding: 2px 6px;
		border: 1px solid var(--line2);
		border-radius: 999px;
		color: var(--muted);
		font: 600 9px/1.2 var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.setting-meta span.warn {
		color: var(--warn-copy);
		border-color: color-mix(in srgb, var(--warn) 32%, var(--line2));
	}
	.setting-control {
		display: flex;
		justify-content: flex-end;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		min-width: 0;
	}
	.path-health {
		display: flex;
		flex: 1 0 100%;
		justify-content: flex-end;
		gap: 6px;
		margin: -1px 0 0;
	}
	.path-health span {
		border: 1px solid var(--line2);
		border-radius: var(--radius-pill);
		padding: 3px 6px;
		color: var(--muted);
		font: 650 9px/1.2 var(--font-mono);
		letter-spacing: 0.03em;
		text-transform: uppercase;
	}
	.path-health span.good {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 32%, var(--line2));
	}
	.path-health span.bad {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 30%, var(--line2));
	}
	/* A slider needs the whole column to be worth dragging, so it pushes Reset
	   onto its own line rather than competing with it for width. */
	.setting-control :global(.slider-control) {
		flex: 1 1 200px;
	}
	.setting-control input[type='text'],
	.setting-control input[type='number'],
	.setting-control select,
	.setting-control textarea {
		width: min(100%, 300px);
		min-width: 0;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--text);
		font: 12px/1.4 var(--font-mono);
		padding: 8px 10px;
	}
	.setting-control textarea {
		resize: vertical;
	}
	.setting-control :is(input, select, textarea):focus-visible,
	.reset:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	.setting-control :is(input, select, textarea):disabled {
		opacity: 0.58;
		cursor: not-allowed;
	}
	.deployment-value {
		color: var(--muted);
		font: 11px/1.4 var(--font-mono);
		text-align: right;
	}
	/* With the Custom badge gone this button is the only sign that a row has moved
	   off its default, so it has to read as a control rather than as fine print. */
	.reset {
		display: inline-flex;
		flex: 0 0 auto;
		align-items: center;
		gap: 5px;
		padding: 5px 10px;
		border: 1px solid var(--gold-deep);
		border-radius: var(--radius-pill);
		background: color-mix(in srgb, var(--gold) 9%, transparent);
		color: var(--gold-copy);
		font-size: 11px;
		font-weight: 650;
	}
	.reset:hover:not(:disabled) {
		background: color-mix(in srgb, var(--gold) 18%, transparent);
	}
	.reset:disabled {
		cursor: not-allowed;
		opacity: 0.5;
	}
	.switch {
		position: relative;
		display: inline-flex;
	}
	.switch input {
		position: absolute;
		opacity: 0;
	}
	.switch span {
		display: block;
		width: 40px;
		height: 22px;
		border-radius: 999px;
		background: var(--faint2);
		transition: background 120ms ease;
	}
	.switch span::after {
		content: '';
		position: absolute;
		top: 2px;
		left: 2px;
		width: 18px;
		height: 18px;
		border-radius: 50%;
		background: var(--text);
		transition: transform 120ms ease;
	}
	.switch input:checked + span {
		background: var(--gold-deep);
	}
	.switch input:checked + span::after {
		transform: translateX(18px);
	}
	.switch input:focus-visible + span {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	@media (max-width: 720px) {
		.setting-row {
			grid-template-columns: 1fr;
			gap: 12px;
		}
		.setting-control {
			justify-content: flex-start;
		}
		.path-health {
			justify-content: flex-start;
		}
		.setting-control input[type='text'],
		.setting-control input[type='number'],
		.setting-control select,
		.setting-control textarea {
			width: 100%;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.setting-row,
		.switch span,
		.switch span::after {
			transition: none;
		}
	}
</style>
