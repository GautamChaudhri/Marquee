<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { buildFunnel, dropLabel } from '$lib/pipeline/candidate-funnel';
	import type { PipelineMetrics } from '$lib/api/types';

	let {
		movie = null,
		tv = null,
		activeProfile
	}: {
		movie?: PipelineMetrics | null;
		tv?: PipelineMetrics | null;
		/** Named in the text-gate row so the cause sits next to the effect. */
		activeProfile?: string;
	} = $props();

	const funnel = $derived(buildFunnel([movie, tv]));
	const survivalPct = $derived(funnel ? Math.round((funnel.ranked / funnel.head) * 100) : 0);
</script>

<section class="funnel-card">
	<header>
		<div>
			<h2>Candidate funnel</h2>
			<p>
				{#if funnel}
					How {funnel.head.toLocaleString()} candidates across {funnel.windowRuns} run{funnel.windowRuns ===
					1
						? ''
						: 's'} became {funnel.ranked.toLocaleString()}.
				{:else}
					What each gate removes, once runs have happened.
				{/if}
			</p>
		</div>
		{#if funnel}
			<div class="headline">
				<b>{survivalPct}%</b>
				<small>survived</small>
			</div>
		{/if}
	</header>

	{#if funnel}
		<ol class="stages">
			{#each funnel.stages as stage (stage.key)}
				<li class:text-gate={stage.isTextGate}>
					<div class="row">
						<span class="name" title={stage.hint}>
							{stage.label}
							{#if stage.isTextGate && activeProfile}
								<em>{activeProfile}</em>
							{/if}
						</span>
						<span class="count">{stage.count.toLocaleString()}</span>
						<span class="drop">{dropLabel(stage, funnel.head)}</span>
					</div>
					<div class="track">
						<div class="fill" style="width:{Math.max(stage.fraction * 100, 0.6)}%"></div>
					</div>
				</li>
			{/each}
		</ol>
		<footer>
			<span><b>{funnel.gated.toLocaleString()}</b> rejected across all gates</span>
		</footer>
	{:else}
		<div class="empty">
			<span class="empty-icon"><Icon name="layers" size={18} /></span>
			<div>
				<b>No runs recorded yet</b>
				<small>
					Run the pipeline and this fills in — including how much the active text profile rejects.
				</small>
			</div>
		</div>
	{/if}
</section>

<style>
	.funnel-card {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		margin-bottom: 18px;
		overflow: hidden;
	}
	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		padding: 13px 16px;
		border-bottom: 1px solid var(--line);
	}
	h2 {
		margin: 0;
		font-size: 13px;
		font-weight: 680;
	}
	header p {
		margin: 2px 0 0;
		color: var(--muted);
		font-size: 11.5px;
	}
	.headline {
		display: flex;
		align-items: baseline;
		gap: 5px;
		flex: none;
	}
	.headline b {
		color: var(--gold-copy);
		font: 600 19px/1 var(--font-mono);
	}
	.headline small {
		color: var(--faint);
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.stages {
		display: grid;
		gap: 11px;
		margin: 0;
		padding: 15px 16px;
		list-style: none;
	}
	.row {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin-bottom: 5px;
	}
	.name {
		flex: 1;
		min-width: 0;
		color: var(--muted);
		font-size: 12px;
	}
	.name em {
		margin-left: 6px;
		padding: 1px 7px;
		border: 1px solid color-mix(in srgb, var(--info) 32%, transparent);
		border-radius: 999px;
		background: color-mix(in srgb, var(--info) 9%, transparent);
		color: var(--info);
		font-size: 10.5px;
		font-style: normal;
	}
	.count {
		color: var(--text);
		font: 600 12.5px/1 var(--font-mono);
	}
	.drop {
		width: 44px;
		color: var(--faint);
		font: 500 11px/1 var(--font-mono);
		text-align: right;
	}
	.track {
		height: 7px;
		border-radius: 4px;
		background: var(--ink2);
		overflow: hidden;
	}
	.fill {
		height: 100%;
		border-radius: 4px;
		background: linear-gradient(
			90deg,
			var(--teal),
			color-mix(in srgb, var(--teal) 55%, var(--info))
		);
		transition: width 0.3s ease;
	}
	/* The text gate is what the profile box above configures, so it reads as the
	   subject of this panel rather than one row among six. */
	.text-gate .fill {
		background: linear-gradient(90deg, var(--gold), var(--gold-deep));
	}
	.text-gate .name {
		color: var(--text);
		font-weight: 600;
	}
	.text-gate .drop {
		color: var(--gold-copy);
	}
	footer {
		padding: 10px 16px;
		border-top: 1px solid var(--line);
		background: var(--panel2);
		color: var(--muted);
		font-size: 11.5px;
	}
	footer b {
		color: var(--text);
		font-family: var(--font-mono);
	}
	.empty {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 20px 16px;
	}
	.empty-icon {
		display: grid;
		place-items: center;
		width: 36px;
		height: 36px;
		flex: none;
		border: 1px dashed var(--line2);
		border-radius: 9px;
		color: var(--faint);
	}
	.empty div {
		display: grid;
		gap: 2px;
	}
	.empty b {
		font-size: 12.5px;
		font-weight: 650;
	}
	.empty small {
		color: var(--muted);
		font-size: 11.5px;
	}
</style>
