<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { invalidateAll } from '$app/navigation';
	import { analyzeMovieDovi } from '$lib/api/radarr-overlay';
	import { trackJob } from '$lib/jobs';
	import HdrBadge from '$lib/components/HdrBadge.svelte';
	import { toast } from '$lib/toast';
	import type { HdrKind } from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let detail = $derived(data.detail);
	let movie = $derived(data.detail?.movie ?? null);
	let dovi = $derived(data.detail?.dovi ?? null);

	let analyzing = $state(false);
	let analyzePercent = $state(0);
	let analyzeMessage = $state('');
	let stopTracking: (() => void) | null = null;

	const jobKey = $derived(movie ? `marquee:hdr:activeJob:${movie.id}` : null);

	function storeJob(id: string | null) {
		if (!browser || !jobKey) return;
		if (id) localStorage.setItem(jobKey, id);
		else localStorage.removeItem(jobKey);
	}

	function attach(jobId: string) {
		storeJob(jobId);
		stopTracking?.();
		analyzing = true;
		stopTracking = trackJob(
			fetch,
			jobId,
			{
				onProgress: ({ detail: d }) => {
					const p = d as { percent?: number; message?: string; stage?: string };
					if (typeof p.percent === 'number') analyzePercent = p.percent;
					if (typeof p.message === 'string') analyzeMessage = p.message;
					else if (typeof p.stage === 'string') analyzeMessage = p.stage;
				},
				onDone: async (job) => {
					stopTracking = null;
					analyzing = false;
					analyzePercent = 100;
					storeJob(null);
					if (job.status === 'succeeded') {
						toast('Dolby Vision analysis complete', 'good');
					} else {
						toast(`Analysis ${job.status}`, 'bad');
					}
					await invalidateAll();
				},
				onError: () => toast('Analysis progress stream interrupted', 'bad')
			},
			{ eventsUrl: `/api/jobs/${jobId}/events` }
		);
	}

	async function runAnalyze() {
		if (analyzing || !movie) return;
		analyzing = true;
		analyzePercent = 0;
		analyzeMessage = 'Queuing analysis…';
		try {
			const job = await analyzeMovieDovi(fetch, movie.id);
			attach(job.job_id);
		} catch (e) {
			analyzing = false;
			toast(e instanceof Error ? e.message : 'Could not start analysis', 'bad');
		}
	}

	onMount(() => {
		const active = detail?.analysis_job?.job_id ?? null;
		if (active) {
			attach(active);
			return;
		}
		if (browser && jobKey) {
			const stored = localStorage.getItem(jobKey);
			if (stored) attach(stored);
		}
	});

	onDestroy(() => stopTracking?.());

	const PROFILE_NOTE: Record<number, string> = {
		5: 'Profile 5 — IPT-PQ-C2 color. Shows green/purple tints on non-DV hardware.',
		7: 'Profile 7 — dual-layer (BL + EL), typically from UHD Blu-ray rips.',
		8: 'Profile 8 — single-layer with an HDR10/HLG/SDR-compatible base layer.'
	};

	function elTone(elType: string | null): string {
		if (elType === 'FEL') return 'var(--bad)';
		if (elType === 'MEL') return 'var(--good)';
		return 'var(--faint)';
	}
</script>

<section class="page">
	<a class="back" href="/hdr">← HDR/DoVi Management</a>

	{#if data.error || !detail || !movie}
		<div class="error">{data.error ?? 'Movie not found.'}</div>
	{:else}
		<div class="header mq-rise">
			<div class="title-row">
				<h1>{movie.title} <span class="year">({movie.year})</span></h1>
				{#if movie.container}<span class="badge">{movie.container}</span>{/if}
				{#if movie.resolution}<span class="badge muted">{movie.resolution}</span>{/if}
			</div>
			{#if movie.movie_file_path}
				<p class="path mono" title={movie.movie_file_path}>{movie.movie_file_path}</p>
			{/if}
		</div>

		{#if !detail.binaries.ffprobe || !detail.binaries.dovi_tool}
			<div class="banner warn">
				{#if !detail.binaries.ffprobe}
					<strong>ffprobe not found on PATH.</strong> DoVi analysis is unavailable until it is installed.
				{:else}
					<strong>dovi_tool not found on PATH.</strong> Profiles can be read, but FEL/MEL detection
					and remediation need dovi_tool.
				{/if}
			</div>
		{/if}

		{#if analyzing}
			<div class="banner">
				<div class="bar"><span style={`width:${Math.max(4, analyzePercent)}%`}></span></div>
				<small>{analyzeMessage}</small>
			</div>
		{/if}

		<div class="grid">
			<!-- HDR overview -->
			<div class="card">
				<h2>HDR</h2>
				<div class="kv">
					<span class="lbl">Profiles</span>
					<span class="val"><HdrBadge kinds={detail.hdr_tags as HdrKind[]} /></span>
				</div>
				<div class="kv">
					<span class="lbl">Bucket</span>
					<span class="val">{detail.hdr_bucket}</span>
				</div>
				<div class="kv">
					<span class="lbl">Radarr descriptor</span>
					<span class="val mono">{movie.hdr_type_raw ?? '—'}</span>
				</div>
				<div class="kv">
					<span class="lbl">Quality profile</span>
					<span class="val">{detail.profile_name ?? '—'}</span>
				</div>
			</div>

			<!-- Dolby Vision -->
			<div class="card">
				<div class="card-head">
					<h2>Dolby Vision</h2>
					{#if detail.binaries.ffprobe}
						<button class="analyze" onclick={runAnalyze} disabled={analyzing}>
							{dovi ? 'Re-analyze' : 'Analyze DoVi'}
						</button>
					{/if}
				</div>

				{#if !dovi}
					<p class="empty">
						{#if movie.has_dv}
							Not analyzed yet. Run analysis to read the exact profile and (for profile 7) FEL vs
							MEL.
						{:else}
							Radarr did not flag this title as Dolby Vision. You can still analyze it to confirm.
						{/if}
					</p>
				{:else if dovi.status === 'not_dovi'}
					<p class="empty">No Dolby Vision stream found in this file.</p>
				{:else if dovi.status === 'error'}
					<p class="empty bad">Analysis failed: {dovi.error_reason ?? 'unknown error'}</p>
				{:else}
					<div class="kv">
						<span class="lbl">Profile</span>
						<span class="val"><strong>{dovi.profile ?? '—'}</strong></span>
					</div>
					{#if dovi.profile != null && PROFILE_NOTE[dovi.profile]}
						<p class="note">{PROFILE_NOTE[dovi.profile]}</p>
					{/if}
					<div class="kv">
						<span class="lbl">Level</span>
						<span class="val">{dovi.level ?? '—'}</span>
					</div>
					<div class="kv">
						<span class="lbl">Enhancement layer</span>
						<span class="val">
							{#if dovi.el_present}
								<span class="chip" style={`--tone:${elTone(dovi.el_type)}`}>
									{dovi.el_type ?? 'present (type unknown)'}
								</span>
								{#if dovi.el_type === 'FEL'}
									<small class="bad"> · can force real-time transcoding</small>
								{:else if dovi.el_type === 'MEL'}
									<small class="good"> · minimal, safe to drop</small>
								{/if}
							{:else}
								<span class="val muted">none (single-layer)</span>
							{/if}
						</span>
					</div>
					<div class="kv">
						<span class="lbl">BL signal compatibility</span>
						<span class="val">{dovi.bl_signal_compatibility_id ?? '—'}</span>
					</div>
					<div class="kv">
						<span class="lbl">Codec</span>
						<span class="val mono">{dovi.source_codec ?? '—'}</span>
					</div>
					<div class="kv">
						<span class="lbl">Last analyzed</span>
						<span class="val">
							{dovi.last_analyzed_at
								? new Date(dovi.last_analyzed_at).toLocaleString()
								: '—'}
						</span>
					</div>
					{#if dovi.rpu_summary}
						<details class="fold">
							<summary>dovi_tool RPU summary</summary>
							<pre>{dovi.rpu_summary}</pre>
						</details>
					{/if}
				{/if}
			</div>

			<!-- Remediation (Phase 2) -->
			{#if dovi && dovi.status === 'analyzed' && dovi.conversion}
				<div class="card span">
					<h2>Remediation</h2>
					<p class="note">{dovi.conversion.reason}</p>
					{#if dovi.conversion.eligible}
						<div class="actions">
							<button class="convert" disabled title="Coming soon">
								{dovi.conversion.kind === 'p5_to_p81'
									? 'Convert to Profile 8.1 (fix green tint)'
									: 'Strip EL → Profile 8.1'}
							</button>
							<span class="soon"
								>Coming soon{dovi.conversion.eligible === 'lossy' ? ' · lossy step' : ''}</span
							>
						</div>
					{:else}
						<p class="empty">No single-layer conversion applies to this stream.</p>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
</section>

<style>
	.page {
		max-width: 1100px;
		margin: 0 auto;
		padding: 24px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.back {
		color: var(--faint);
		font-size: 13px;
		text-decoration: none;
	}
	.back:hover {
		color: var(--ink);
	}
	.error {
		padding: 16px;
		border: 1px solid var(--bad);
		border-radius: var(--radius);
		background: color-mix(in srgb, var(--bad) 12%, transparent);
	}
	.header {
		padding: 18px 20px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.title-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.title-row h1 {
		margin: 0;
		font-size: 24px;
	}
	.year {
		color: var(--faint);
		font-weight: 400;
	}
	.badge {
		padding: 3px 9px;
		border: 1px solid var(--line);
		border-radius: 999px;
		font-size: 12px;
		text-transform: uppercase;
	}
	.badge.muted,
	.muted {
		color: var(--faint);
	}
	.path {
		margin: 10px 0 0;
		color: var(--faint);
		font-size: 12px;
		word-break: break-all;
	}
	.mono {
		font-family: var(--mono, ui-monospace, monospace);
	}
	.banner {
		padding: 12px 16px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.banner.warn {
		border-color: var(--gold);
		background: color-mix(in srgb, var(--gold) 10%, transparent);
	}
	.bar {
		height: 6px;
		border-radius: 999px;
		background: var(--line);
		overflow: hidden;
	}
	.bar span {
		display: block;
		height: 100%;
		background: var(--gold);
		transition: width 0.3s ease;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 16px;
	}
	.card {
		padding: 18px 20px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.card.span {
		grid-column: 1 / -1;
	}
	.card h2 {
		margin: 0 0 14px;
		font-size: 16px;
	}
	.card-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 14px;
	}
	.card-head h2 {
		margin: 0;
	}
	.kv {
		display: grid;
		grid-template-columns: 160px 1fr;
		gap: 10px;
		padding: 7px 0;
		border-top: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
		align-items: center;
	}
	.kv:first-of-type {
		border-top: none;
	}
	.lbl {
		color: var(--faint);
		font-size: 12px;
		text-transform: uppercase;
	}
	.note {
		margin: 8px 0 0;
		color: var(--faint);
		font-size: 13px;
	}
	.empty {
		color: var(--faint);
		font-size: 14px;
		margin: 4px 0 0;
	}
	.bad {
		color: var(--bad);
	}
	.good {
		color: var(--good);
	}
	.chip {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 999px;
		border: 1px solid var(--tone);
		color: var(--tone);
		font-size: 12px;
		font-weight: 600;
	}
	.analyze {
		padding: 7px 14px;
		border: 1px solid var(--gold);
		border-radius: var(--radius);
		background: color-mix(in srgb, var(--gold) 14%, transparent);
		color: var(--gold);
		font-weight: 600;
		cursor: pointer;
	}
	.analyze:disabled {
		opacity: 0.6;
		cursor: progress;
	}
	.fold {
		margin-top: 12px;
	}
	.fold summary {
		cursor: pointer;
		font-size: 13px;
		color: var(--faint);
	}
	.fold pre {
		margin-top: 8px;
		padding: 12px;
		border-radius: var(--radius);
		background: var(--bg, #0000001a);
		overflow-x: auto;
		font-size: 12px;
		white-space: pre-wrap;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-top: 12px;
		flex-wrap: wrap;
	}
	.convert {
		padding: 9px 16px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		color: var(--ink);
		font-weight: 600;
		cursor: not-allowed;
		opacity: 0.7;
	}
	.soon {
		font-size: 12px;
		color: var(--faint);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	@media (max-width: 760px) {
		.grid {
			grid-template-columns: 1fr;
		}
		.kv {
			grid-template-columns: 1fr;
			gap: 2px;
		}
	}
</style>
