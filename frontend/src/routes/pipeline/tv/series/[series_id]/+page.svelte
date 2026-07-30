<script lang="ts">
	import { goto } from '$app/navigation';
	import RunResultsView from '$lib/components/pipeline/RunResultsView.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { approveTvAuto, resetSeriesPosters } from '$lib/api/pipeline-tv';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let resetOpen = $state(false);
	let resetBusy = $state(false);

	function seasonLabel(number: number | null | undefined): string {
		if (number == null) return 'Show';
		return number === 0 ? 'S00' : `S${String(number).padStart(2, '0')}`;
	}

	function statusLabel(status: string): string {
		if (status === 'completed') return 'Completed';
		if (status === 'flagged_manual') return 'Needs manual review';
		return status.replaceAll('_', ' ');
	}

	type RailItem = {
		key: string;
		label: string;
		runId: string;
		posterUrl: string | null;
		status: string;
		score: number | null;
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
				official: season.official_pick?.applied === 'primary_stack',
				flaggedNoCandidates: season.flagged_no_candidates
			});
		}
		return items;
	});

	/** A decided run leaves the review queue, so the rail entry disappears on the
	 *  next load. Advance to the run after it — or back to the queue when this
	 *  series has nothing left to decide. */
	async function advanceAfterReview() {
		const seriesId = data.series?.id;
		const items = rail;
		const index = items.findIndex((item) => item.runId === data.selectedRunId);
		const next = index >= 0 ? items[index + 1] : undefined;
		if (!next || seriesId == null) {
			await goto('/pipeline/tv?tab=review', { invalidateAll: true });
			return;
		}
		await goto(`/pipeline/tv/series/${seriesId}?run=${next.runId}`, { invalidateAll: true });
	}

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

	async function confirmReset() {
		if (!data.series) return;
		resetBusy = true;
		try {
			await resetSeriesPosters(fetch, data.series.id);
			toast('Reset queued — the show returns to the run queue', 'good');
			resetOpen = false;
			await goto('/pipeline/tv?tab=run', { invalidateAll: true });
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Reset failed', 'bad');
		} finally {
			resetBusy = false;
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
		<div class="header-actions">
			<button class="btn-sec" onclick={() => (resetOpen = true)}>Reset &amp; re-run</button>
			<button class="btn-gold" onclick={approveAll}>Approve all remaining auto-picks</button>
		</div>
	</div>

	<ConfirmDialog
		open={resetOpen}
		title="Reset {data.series.title}?"
		message="Deletes the deployed show and season posters (backups are kept), discards these runs and their stored candidates, and returns the show to the run queue so it can be analysed again."
		confirmLabel="Reset & re-run"
		tone="bad"
		busy={resetBusy}
		onConfirm={confirmReset}
		onCancel={() => (resetOpen = false)}
	/>

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
						{#if item.flaggedNoCandidates}
							<div class="manual-status">
								<StatusDot tone="bad" size={7} />
								<div>
									<span class="manual-title">Needs attention</span>
									<span class="manual-copy">No qualifying poster found</span>
								</div>
							</div>
						{:else}
							<div class="sub-row">
								<StatusDot
									tone={item.status === 'completed'
										? 'good'
										: item.status === 'flagged_manual'
											? 'gold'
											: 'muted'}
									size={6}
								/>
								<span>{statusLabel(item.status)}</span>
								{#if item.score != null}
									<span class="candidate-count mono">{item.score} candidates</span>
								{/if}
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</aside>

		<div class="main">
			{#if data.selectedRunId}
				<!-- Keyed on the run: picking another season in the rail must rebuild the
					 view, which keeps the run and the inspected poster in local state. -->
				{#key data.selectedRunId}
					<RunResultsView
						data={data.runData}
						backHref="/pipeline/tv?tab=review"
						backLabel="TV Posters"
						onreviewed={advanceAfterReview}
					/>
				{/key}
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
	.header-actions {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
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
	.manual-status {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		align-items: start;
		gap: 7px;
		padding: 1px 0;
	}
	.manual-status > div {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.manual-title {
		font-size: 12px;
		font-weight: 700;
		color: var(--bad);
	}
	.manual-copy {
		font-size: 11px;
		line-height: 1.35;
		color: color-mix(in srgb, var(--bad) 72%, var(--muted));
	}
	.candidate-count {
		color: var(--faint);
		font-size: 11px;
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
</style>
