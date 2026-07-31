<script lang="ts">
	import { OPTIONAL_COLUMNS, type ActivityColumn, type ActivityPreferences } from '../preferences';

	let {
		preferences,
		onChange
	}: {
		preferences: ActivityPreferences;
		onChange: (preferences: ActivityPreferences) => void;
	} = $props();
	const COLUMN_LABELS: Record<ActivityColumn, string> = {
		subject: 'Subject',
		action: 'Action',
		status: 'Status',
		feature: 'Feature',
		trigger: 'Trigger',
		progress: 'Progress',
		impact: 'Impact',
		time: 'Time'
	};

	function toggle(column: ActivityColumn): void {
		const columns = preferences.columns.includes(column)
			? preferences.columns.filter((value) => value !== column)
			: [...preferences.columns, column];
		onChange({ ...preferences, columns });
	}
</script>

<details class="display-options">
	<summary>Display</summary>
	<div class="panel">
		<label>
			<span>Density</span>
			<select
				value={preferences.density}
				onchange={(event) =>
					onChange({
						...preferences,
						density: event.currentTarget.value === 'compact' ? 'compact' : 'comfortable'
					})}
			>
				<option value="comfortable">Comfortable</option>
				<option value="compact">Compact</option>
			</select>
		</label>
		<fieldset>
			<legend>Optional details</legend>
			{#each OPTIONAL_COLUMNS as column (column)}
				<label>
					<input
						type="checkbox"
						checked={preferences.columns.includes(column)}
						onchange={() => toggle(column)}
					/>
					{COLUMN_LABELS[column]}
				</label>
			{/each}
		</fieldset>
		<p>Subject, action, and status are always visible.</p>
	</div>
</details>

<style>
	.display-options {
		position: relative;
	}
	summary {
		min-height: 34px;
		padding: 7px 10px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel);
		color: var(--muted);
		cursor: pointer;
		font-size: 12px;
	}
	.panel {
		position: absolute;
		z-index: 5;
		right: 0;
		top: calc(100% + 6px);
		width: min(280px, calc(100vw - 32px));
		padding: 12px;
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		background: var(--panel2);
		box-shadow: 0 16px 36px var(--shadow);
	}
	.panel > label,
	fieldset label {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		font-size: 12px;
	}
	fieldset {
		display: grid;
		gap: 7px;
		margin: 12px 0 0;
		padding: 10px 0 0;
		border: 0;
		border-top: 1px solid var(--line);
	}
	legend,
	.panel p {
		color: var(--faint2);
		font-size: 10px;
	}
	.panel p {
		margin: 10px 0 0;
	}
</style>
