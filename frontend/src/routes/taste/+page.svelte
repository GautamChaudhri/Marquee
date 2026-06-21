<script lang="ts">
	import { onDestroy } from 'svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { getTasteStatus, retrainTaste, retrainHead, cancelRetrain } from '$lib/api/taste';
	import { trackJob, type JobProgressDetail } from '$lib/jobs';
	import { toast } from '$lib/toast';
	import type { TasteSource, TasteStatus } from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	// svelte-ignore state_referenced_locally
	let status = $state<TasteStatus | null>(data.status);

	async function refresh() {
		try {
			status = await getTasteStatus(fetch);
		} catch {
			/* keep stale */
		}
	}

	// ── Profile rebuild (initial training) ───────────────────────────────────────
	const SOURCES: { id: TasteSource; label: string; hint: string }[] = [
		{ id: 'training_dir', label: 'Training folder', hint: 'Curated data/training/positive' },
		{ id: 'library', label: 'Library posters', hint: 'Every deployed poster' }
	];
	let source = $state<TasteSource>('training_dir');
	let rebuildDetail = $state<JobProgressDetail>({});
	let rebuildStatus = $state('running');
	let rebuilding = $state(false);
	let rebuildJobId = $state<string | null>(null);
	let stopRebuild: (() => void) | null = null;

	async function startRebuild() {
		if (rebuilding) return;
		rebuilding = true;
		rebuildDetail = {};
		rebuildStatus = 'running';
		try {
			const job = await retrainTaste(fetch, source);
			rebuildJobId = job.job_id;
			toast('Taste rebuild queued', 'info');
			stopRebuild?.();
			stopRebuild = trackJob(
				fetch,
				job.job_id,
				{
					onProgress: ({ status: s, detail }) => {
						rebuildStatus = s;
						rebuildDetail = detail;
					},
					onDone: (j) => {
						rebuilding = false;
						toast(`Taste rebuild ${j.status}`, j.status === 'succeeded' ? 'good' : 'bad');
						void refresh();
					}
				},
				{ eventsUrl: job.events_url }
			);
		} catch (e) {
			rebuilding = false;
			toast(e instanceof Error ? e.message : 'Rebuild failed to start', 'bad');
		}
	}
	async function cancelRebuild() {
		try {
			await cancelRetrain(fetch);
			toast('Cancellation requested', 'info');
		} catch {
			toast('Could not cancel', 'bad');
		}
	}

	// ── Key Art Engine (learned head) train ──────────────────────────────────────
	let headDetail = $state<JobProgressDetail>({});
	let headStatus = $state('running');
	let headTraining = $state(false);
	let stopHead: (() => void) | null = null;

	const head = $derived(status?.learned_head);
	const ready = $derived(
		!!head &&
			head.activation.movies.have >= head.activation.movies.need &&
			head.activation.labels.have >= head.activation.labels.need
	);

	async function startHead() {
		if (headTraining) return;
		headTraining = true;
		headDetail = {};
		headStatus = 'running';
		try {
			const job = await retrainHead(fetch);
			toast('Key Art Engine training queued', 'info');
			stopHead?.();
			stopHead = trackJob(
				fetch,
				job.job_id,
				{
					onProgress: ({ status: s, detail }) => {
						headStatus = s;
						headDetail = detail;
					},
					onDone: (j) => {
						headTraining = false;
						toast(`Key Art Engine ${j.status}`, j.status === 'succeeded' ? 'good' : 'bad');
						void refresh();
					}
				},
				{ eventsUrl: job.events_url }
			);
		} catch (e) {
			headTraining = false;
			toast(e instanceof Error ? e.message : 'Training failed to start', 'bad');
		}
	}

	function pct(have: number, need: number): number {
		return need > 0 ? Math.min(100, (have / need) * 100) : 100;
	}
	function fmtDate(iso: string | null): string {
		if (!iso) return 'never';
		const t = new Date(iso);
		return Number.isNaN(t.getTime()) ? 'never' : t.toLocaleDateString();
	}

	onDestroy(() => {
		stopRebuild?.();
		stopHead?.();
	});
</script>

<SectionHeader title="Key Art Engine" subtitle="Taste profile & learned poster ranker" />

{#if data.error || !status}
	<div class="empty">
		<Icon name="taste" size={34} stroke={1} />
		<strong>Taste status unavailable</strong>
		<span>{data.error ?? 'Could not reach the taste service.'}</span>
	</div>
{:else}
	<!-- ── Status cards ── -->
	<div class="stat-grid">
		<StatCard
			label="Labels"
			value={status.labels.total}
			sub={`${status.labels.positives} pos · ${status.labels.negatives} neg · ${status.labels.movies} movies`}
			tone="gold"
		/>
		<StatCard
			label="Exemplars"
			value={status.exemplars.count}
			sub={`${status.exemplars.negatives} negative · built ${fmtDate(status.exemplars.last_rebuild)}`}
			tone="info"
		/>
		<div class="card engine" class:on={head?.active}>
			<div class="engine-head">
				<span class="label">Key Art Engine</span>
				<span class="engine-state" style="--c:{head?.active ? 'var(--good)' : 'var(--faint)'}">
					<StatusDot tone={head?.active ? 'good' : 'muted'} size={7} />
					{head?.active ? 'Active' : 'Inactive'}
				</span>
			</div>
			<div class="engine-val mono">{head?.n_samples ?? 0}<span class="unit"> labels</span></div>
			{#if head}
				<div class="gauges">
					<div class="gauge">
						<div class="gl">
							<span>Movies</span><span class="mono"
								>{head.activation.movies.have}/{head.activation.movies.need}</span
							>
						</div>
						<ProgressBar
							value={pct(head.activation.movies.have, head.activation.movies.need)}
							tone={head.activation.movies.have >= head.activation.movies.need ? 'good' : 'gold'}
							height={5}
						/>
					</div>
					<div class="gauge">
						<div class="gl">
							<span>Labels</span><span class="mono"
								>{head.activation.labels.have}/{head.activation.labels.need}</span
							>
						</div>
						<ProgressBar
							value={pct(head.activation.labels.have, head.activation.labels.need)}
							tone={head.activation.labels.have >= head.activation.labels.need ? 'good' : 'gold'}
							height={5}
						/>
					</div>
				</div>
			{/if}
		</div>
	</div>

	<!-- ── Genre spread ── -->
	{#if Object.keys(status.labels.genres).length}
		<div class="genres">
			<span class="chip-label">Genres</span>
			{#each Object.entries(status.labels.genres).slice(0, 12) as [g, n] (g)}
				<span class="g-chip">{g}<b>{n}</b></span>
			{/each}
		</div>
	{/if}

	<!-- ── Training cards ── -->
	<div class="train-cols">
		<section class="train-card">
			<h3>Initial training</h3>
			<p class="card-note">
				Rebuild the taste profile (the hand-picked exemplar set the pipeline scores against).
			</p>
			<div class="scope-row">
				{#each SOURCES as s (s.id)}
					<button
						class="scope"
						class:on={source === s.id}
						disabled={rebuilding}
						onclick={() => (source = s.id)}
						title={s.hint}
					>
						{s.label}
						<small>{s.hint}</small>
					</button>
				{/each}
			</div>
			{#if rebuilding || rebuildJobId}
				<RunProgress
					detail={rebuildDetail}
					status={rebuildStatus}
					title="Rebuilding taste profile"
					onCancel={rebuilding ? cancelRebuild : undefined}
				/>
			{/if}
			<button class="btn-gold" onclick={startRebuild} disabled={rebuilding}>
				{rebuilding ? 'Rebuilding…' : 'Rebuild profile'}
			</button>
		</section>

		<section class="train-card">
			<h3>Train the Key Art Engine</h3>
			<p class="card-note">
				Picks accumulate labels automatically. Train the learned ranker from them whenever you want
				to fold in your latest choices.
			</p>
			{#if headTraining}
				<RunProgress detail={headDetail} status={headStatus} title="Training Key Art Engine" />
			{/if}
			{#if !ready}
				<div class="hint">
					Needs more data to activate — keep approving posters to reach the thresholds above.
				</div>
			{/if}
			<button class="btn-gold" onclick={startHead} disabled={headTraining || !ready}>
				{headTraining ? 'Training…' : 'Train Key Art Engine'}
			</button>
		</section>
	</div>

	<!-- ── Gate alerts ── -->
	{#if status.gate_alerts?.length}
		<div class="alert-panel">
			<div class="panel-head">Gate override alerts</div>
			{#each status.gate_alerts as a (a.gate)}
				<div class="alert-row">
					<StatusDot tone="warn" size={6} />
					<span class="ar-gate">{a.gate}</span>
					<span class="ar-note">overridden {a.overrides}× at the current threshold</span>
				</div>
			{/each}
		</div>
	{/if}

	<!-- ── Taste map placeholder ── -->
	<div class="placeholder">
		<div class="ph-title">2D taste map</div>
		<div class="ph-body">
			An interactive projection of the embedding space is coming in a later slice.
		</div>
	</div>
{/if}

<style>
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 8px;
		padding: 70px 24px;
		text-align: center;
		color: var(--faint);
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.empty strong {
		color: var(--text);
		font-size: 15px;
	}

	.stat-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 12px;
		margin-bottom: 16px;
	}
	.card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px 16px;
	}
	.engine.on {
		border-color: color-mix(in srgb, var(--good) 35%, var(--line));
	}
	.engine-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	.label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 600;
	}
	.engine-state {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-size: 11.5px;
		color: var(--c);
	}
	.engine-val {
		font-size: 24px;
		font-weight: 600;
		margin-top: 4px;
		color: var(--text);
	}
	.engine-val .unit {
		font-size: 12px;
		font-family: var(--font-sans);
		color: var(--muted);
	}
	.gauges {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin-top: 12px;
	}
	.gl {
		display: flex;
		justify-content: space-between;
		font-size: 11px;
		color: var(--muted);
		margin-bottom: 4px;
	}

	.genres {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px;
		margin-bottom: 20px;
	}
	.chip-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
		margin-right: 2px;
	}
	.g-chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 3px 9px;
		border-radius: 99px;
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 12px;
		color: var(--muted);
		text-transform: capitalize;
	}
	.g-chip b {
		font-family: var(--font-mono);
		color: var(--text);
	}

	.train-cols {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
		gap: 16px;
		align-items: start;
		margin-bottom: 16px;
	}
	.train-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.train-card h3 {
		margin: 0;
		font-size: 15px;
		font-weight: 650;
	}
	.card-note {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
		line-height: 1.5;
	}
	.scope-row {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.scope {
		flex: 1;
		min-width: 130px;
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 9px 12px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		font-size: 12.5px;
		font-weight: 550;
		text-align: left;
	}
	.scope small {
		font-size: 10.5px;
		color: var(--faint);
		font-weight: 400;
	}
	.scope.on {
		border-color: var(--gold-deep);
		background: var(--gold-soft);
		color: var(--gold);
	}
	.scope.on small {
		color: color-mix(in srgb, var(--gold) 70%, var(--faint));
	}
	.scope:disabled {
		opacity: 0.5;
	}
	.hint {
		font-size: 12px;
		color: var(--warn);
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		border: 1px solid color-mix(in srgb, var(--warn) 22%, transparent);
		border-radius: var(--radius-sm);
		padding: 8px 11px;
		line-height: 1.45;
	}

	.alert-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px 16px;
		margin-bottom: 16px;
	}
	.panel-head {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
		margin-bottom: 10px;
	}
	.alert-row {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12.5px;
		padding: 4px 0;
	}
	.ar-gate {
		font-family: var(--font-mono);
		color: var(--text);
	}
	.ar-note {
		color: var(--muted);
	}

	.placeholder {
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		padding: 22px;
		text-align: center;
	}
	.ph-title {
		font-size: 13px;
		font-weight: 600;
		color: var(--faint);
		margin-bottom: 4px;
	}
	.ph-body {
		font-size: 12px;
		color: var(--faint);
	}

	.btn-gold {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
		align-self: flex-start;
	}
	.btn-gold:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
