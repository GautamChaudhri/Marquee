<script lang="ts">
	import type { SettingsCatalogEntry } from '$lib/api/types';

	let {
		entry,
		value,
		source,
		defaultValue,
		dirty = false,
		disabled = false,
		onChange,
		onReset
	}: {
		entry: SettingsCatalogEntry;
		value: unknown;
		source: string;
		defaultValue?: unknown;
		dirty?: boolean;
		disabled?: boolean;
		onChange: (value: unknown) => void;
		onReset: () => void;
	} = $props();

	const inputId = $derived(`setting-${entry.key.toLowerCase().replaceAll('_', '-')}`);
	const locked = $derived(disabled || !entry.editable || entry.storage === 'deployment');

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

	function sourceLabel(raw: string): string {
		if (entry.storage === 'deployment') return 'Deployment';
		if (raw === 'custom' || raw === 'revision') return 'Custom';
		if (raw === 'environment') return 'Legacy environment';
		return 'Default';
	}

	function applyLabel(): string {
		return {
			hot: 'Live',
			next_job: 'Next job',
			restart: 'Restart required',
			deployment: 'Deployment only'
		}[entry.apply_mode];
	}
</script>

<div class:dirty class:locked class="setting-row">
	<div class="setting-copy">
		<label for={inputId}>{entry.title}</label>
		<p>{entry.description ?? entry.control.help ?? entry.key.replaceAll('_', ' ').toLowerCase()}</p>
		<div class="setting-meta">
			<span class:custom={source === 'custom' || source === 'revision'}>{sourceLabel(source)}</span>
			<span class:restart={entry.apply_mode === 'restart'}>
				{applyLabel()}
			</span>
			{#if entry.sensitivity === 'private'}<span>private</span>{/if}
		</div>
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
		{#if entry.editable && defaultValue !== undefined}
			<button type="button" class="reset" {disabled} onclick={onReset}>Reset</button>
		{/if}
	</div>
</div>

<style>
	.setting-row {
		display: grid;
		grid-template-columns: minmax(220px, 1fr) minmax(180px, 0.8fr);
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
	.setting-meta span.custom {
		color: var(--gold-copy);
		border-color: var(--gold-deep);
	}
	.setting-meta span.restart {
		color: var(--warn-copy);
	}
	.setting-control {
		display: flex;
		justify-content: flex-end;
		align-items: center;
		gap: 8px;
	}
	.setting-control input[type='text'],
	.setting-control input[type='number'],
	.setting-control select,
	.setting-control textarea {
		width: min(100%, 300px);
		border: 1px solid var(--line2);
		border-radius: 7px;
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
	.reset {
		border: 0;
		background: transparent;
		color: var(--muted);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.reset:hover:not(:disabled) {
		color: var(--gold);
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
