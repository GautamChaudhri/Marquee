<script lang="ts">
	import { breakdown, FAMILIES, signed, type ScorePart } from './score';

	/** Every part of one poster's score by name: how well it did on the part (the bar) and
	 *  what the part added to the score (the number). */
	let { parts }: { parts: readonly ScorePart[] | null | undefined } = $props();

	const rows = $derived(breakdown(parts));
	const families = $derived(FAMILIES.filter((f) => rows.some((row) => row.family === f.key)));
</script>

{#if rows.length === 0}
	<p class="none" data-testid="score-breakdown-none">
		This run kept no score breakdown, so only the score itself is known.
	</p>
{:else}
	<ul class="legend" aria-label="What the colours mean">
		{#each families as family (family.key)}
			<li title={family.about}><span class="swatch {family.key}"></span>{family.label}</li>
		{/each}
	</ul>
	<table class="rows" data-testid="score-breakdown">
		<thead class="sr-only">
			<tr><th>Part</th><th>How it did</th><th>Added to the score</th></tr>
		</thead>
		<tbody>
			{#each rows as row (row.feature)}
				<tr>
					<th scope="row">{row.label}</th>
					<td class="track">
						{#if row.value !== null}
							<span class="meter" title="{Math.round(row.value * 100)} of 100">
								<span class="fill {row.family}" style:width="{(row.value * 100).toFixed(1)}%"
								></span>
							</span>
							<span class="sr-only">{Math.round(row.value * 100)} of 100</span>
						{/if}
					</td>
					<td class="amount" class:minus={row.contribution < 0}>{signed(row.contribution)}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	.none {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
	}
	.legend {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 4px 12px;
		font-size: 11.5px;
		color: var(--muted);
	}
	.legend li {
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}
	.swatch {
		width: 9px;
		height: 9px;
		border-radius: 2px;
	}
	.rows {
		width: 100%;
		border-collapse: collapse;
		font-size: 12.5px;
	}
	th,
	td {
		padding: 3px 0;
		text-align: left;
		font-weight: 400;
	}
	th {
		color: var(--muted);
		white-space: nowrap;
		padding-right: 10px;
		width: 1%;
	}
	.track {
		width: 100%;
	}
	.meter {
		display: block;
		height: 6px;
		border-radius: 3px;
		background: var(--panel2);
		overflow: hidden;
	}
	.fill {
		display: block;
		height: 100%;
		border-radius: 3px;
	}
	.amount {
		padding-left: 10px;
		text-align: right;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.amount.minus {
		color: var(--bad);
	}
	.taste {
		background: var(--gold);
	}
	.look {
		background: var(--info);
	}
	.content {
		background: var(--teal);
	}
	.source {
		background: var(--purple);
	}
	.residual {
		background: var(--magenta);
	}
	.other {
		background: var(--faint);
	}
</style>
