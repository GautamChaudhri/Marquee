<script lang="ts">
	import { goto } from '$app/navigation';
	import RunResultsView from '$lib/components/pipeline/RunResultsView.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { approveTvAuto, useShowPoster } from '$lib/api/pipeline-tv';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	function seasonLabel(number: number | null | undefined): string {
		if (number == null) return 'Show';
		return number === 0 ? 'S00' : `S${String(number).padStart(2, '0')}`;
	}

	type RailItem = {
		key: string;
		label: string;
		runId: string;
		posterUrl: string | null;
		status: string;
		score: number | null;
		seasonId?: number;
		official?: boolean;
		flaggedNoCandidates?: boolean;
	};

	const rail = $derived.by<RailItem[]>(() => {
		const group = data.reviewGroup;
		if (!group) return [];
		const items: RailItem[] = [];
		if (group.show_run) {
			items.push({
				key: 'show',
				label: 'Show',
				runId: group.show_run.run_id,
				posterUrl:
					group.show_run.auto_pick_poster_url ??
					(data.series?.poster.has_poster ? `/api/library/series/${data.series.id}/poster` : null),
				status: group.show_run.status,
				score: group.show_run.counts?.ranked ?? null
			});
		}
		for (const season of group.season_runs) {
			items.push({
				key: `season:${season.season_id}`,
				label: seasonLabel(season.season_number),
				runId: season.run.run_id,
				posterUrl: season.auto_pick_poster_url,
				status: season.run.status,
				score: season.run.counts?.ranked ?? null,
				seasonId: season.season_id,
				official: season.official_pick?.applied === 'primary_stack',
				flaggedNoCandidates: season.flagged_no_candidates
			});
		}
		return items;
	});

	async function approveAll() {
		if (!data.series) return;
		try {
			const result = await approveTvAuto(fetch, { deploy: true, series_id: data.series.id });
			toast(`${result.approved} approved`, result.failed ? 'info' : 'good');
			goto('/pipeline/tv?tab=review');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Approval failed', 'bad');
		}
	}

	async function fallbackToShowPoster(seasonId: number) {
		try {
			await useShowPoster(fetch, seasonId);
			toast('Show poster applied to season', 'good');
			goto(`/pipeline/tv/series/${data.series?.id}`);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Fallback failed', 'bad');
		}
	}
</script>

{#if data.error || !data.series}
	<div class="state error">
		<strong>Could not load series review.</strong>
		<span>{data.error ?? 'Unknown error'}</span>
	</div>
{:else}
	<div class="header">
		<div>
			<h1>{data.series.title}</h1>
			<p>Review show and season poster runs.</p>
		</div>
		<button class="btn-gold" onclick={approveAll}>Approve all remaining auto-picks</button>
	</div>

	<div class="layout">
		<aside class="rail">
			{#each rail as item (item.key)}
				<div
					class="rail-item"
					class:selected={item.runId === data.selectedRunId}
					role="button"
					tabindex="0"
					onclick={() => goto(`/pipeline/tv/series/${data.series?.id}?run=${item.runId}`)}
					onkeydown={(e) =>
						e.key === 'Enter' && goto(`/pipeline/tv/series/${data.series?.id}?run=${item.runId}`)}
				>
					<PosterThumb
						title={`${data.series.title} ${item.label}`}
						posterStatus={item.posterUrl ? 'deployed' : 'missing'}
						posterUrl={item.posterUrl}
					/>
					<div class="rail-meta">
						<div class="title-row">
							<strong>{item.label}</strong>
							{#if item.official}<span class="official">OFFICIAL PICK</span>{/if}
						</div>
						<div class="sub-row">
							<StatusDot
								tone={item.status === 'completed'
									? 'good'
									: item.status === 'flagged_manual'
										? 'gold'
										: 'muted'}
								size={6}
							/>
							<span>{item.status}</span>
							{#if item.score != null}<span class="mono">{item.score}</span>{/if}
						</div>
						{#if item.flaggedNoCandidates && item.seasonId}
							<button
								class="btn-sec small"
								onclick={(e) => {
									e.stopPropagation();
									fallbackToShowPoster(item.seasonId!);
								}}
							>
								Use show poster
							</button>
						{/if}
					</div>
				</div>
			{/each}
		</aside>

		<div class="main">
			{#if data.selectedRunId}
				<RunResultsView
					data={data.runData}
					backHref="/pipeline/tv?tab=review"
					backLabel="TV posters"
				/>
			{:else}
				<div class="state">No active review runs for this series.</div>
			{/if}
		</div>
	</div>
{/if}

<style>
	.header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 16px;
		margin-bottom: 16px;
	}
	h1 {
		margin: 0;
		font-size: 24px;
	}
	p {
		margin: 6px 0 0;
		color: var(--muted);
	}
	.layout {
		display: grid;
		grid-template-columns: 280px minmax(0, 1fr);
		gap: 18px;
	}
	@media (max-width: 980px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
	.rail {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.rail-item,
	.state {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 12px;
	}
	.rail-item {
		display: grid;
		grid-template-columns: 82px minmax(0, 1fr);
		gap: 10px;
		text-align: left;
	}
	.rail-item.selected {
		border-color: color-mix(in srgb, var(--gold) 35%, var(--line));
		box-shadow: 0 0 0 1px color-mix(in srgb, var(--gold) 35%, transparent);
	}
	.rail-meta {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.title-row,
	.sub-row {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.official {
		font-size: 10px;
		font-weight: 700;
		padding: 3px 6px;
		border-radius: 999px;
		background: color-mix(in srgb, var(--gold) 14%, var(--panel2));
		color: var(--gold);
		border: 1px solid color-mix(in srgb, var(--gold) 35%, var(--line2));
	}
	.btn-gold,
	.btn-sec {
		padding: 9px 12px;
		border-radius: 8px;
		font-size: 13px;
	}
	.btn-gold {
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
	}
	.btn-sec {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.small {
		padding: 7px 10px;
		font-size: 12px;
	}
</style>
