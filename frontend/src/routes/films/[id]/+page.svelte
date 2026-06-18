<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { posterStatusMeta, letterboxMeta, toneVar } from '$lib/display';
	import { ApiError } from '$lib/api/client';
	import type { LetterboxDetail } from '$lib/api/types';
	import {
		getLetterboxState,
		detectLetterbox,
		applyLetterbox,
		ignoreLetterbox,
		removeLetterbox
	} from '$lib/api/letterbox';
	import { triggerRun } from '$lib/api/pipeline';
	import { subscribe } from '$lib/sse';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import HdrBadge from '$lib/components/HdrBadge.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const movie = $derived(data.movie);
	// svelte-ignore state_referenced_locally
	let tab = $state(data.tab ?? 'poster');

	function setTab(id: string) {
		tab = id;
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('tab', id);
		goto(`/films/${movie?.id}?${sp.toString()}`, {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	}

	const TABS = [
		{ id: 'poster', label: 'Poster' },
		{ id: 'video', label: 'Video · HDR' },
		{ id: 'subtitles', label: 'Subtitles' },
		{ id: 'letterbox', label: 'Letterbox' },
		{ id: 'activity', label: 'Activity' }
	];

	// ── Pipeline ─────────────────────────────────────────────────────────────
	let pipeRunning = $state(false);
	let pipeEvents = $state<string[]>([]);
	let pipeDone = $state(false);
	let pipeError = $state<string | null>(null);
	let pipeRunId = $state<string | null>(null);

	async function startPipeline() {
		if (!movie) return;
		pipeRunning = true;
		pipeEvents = [];
		pipeDone = false;
		pipeError = null;
		pipeRunId = null;
		try {
			const ref = await triggerRun(fetch, movie.id);
			pipeRunId = ref.run_id;
			const unsub = subscribe(ref.events_url, ['message', 'done'], (type, raw) => {
				if (type === 'done') {
					pipeDone = true;
					pipeRunning = false;
					unsub();
				} else {
					const d = raw as Record<string, unknown>;
					const msg = (d?.message ?? d?.stage ?? d?.status ?? JSON.stringify(d)) as string;
					pipeEvents = [...pipeEvents, msg];
				}
			});
		} catch (e) {
			if (e instanceof ApiError && e.status === 409) {
				const detail = (e.body as { detail?: { message?: string; active_run_id?: string } })
					?.detail;
				pipeError = detail?.message ?? 'A run is already in progress';
				if (detail?.active_run_id) pipeRunId = detail.active_run_id;
			} else {
				pipeError = e instanceof Error ? e.message : 'Failed to start pipeline';
			}
			pipeRunning = false;
		}
	}

	// ── Letterbox ─────────────────────────────────────────────────────────────
	let lbState = $state<LetterboxDetail | null | undefined>(undefined);
	let lbLoading = $state(false);
	let lbError = $state<string | null>(null);

	async function loadLb() {
		if (!movie || lbState !== undefined) return;
		lbLoading = true;
		try {
			lbState = await getLetterboxState(fetch, movie.id);
		} catch (e) {
			lbError = e instanceof Error ? e.message : 'Failed to load letterbox state';
			lbState = null;
		} finally {
			lbLoading = false;
		}
	}

	async function lbDetect() {
		if (!movie) return;
		lbLoading = true;
		lbError = null;
		try {
			lbState = await detectLetterbox(fetch, movie.id);
		} catch (e) {
			lbError = e instanceof Error ? e.message : 'Detection failed';
		} finally {
			lbLoading = false;
		}
	}

	async function lbAction(action: 'apply' | 'ignore' | 'remove') {
		if (!movie) return;
		lbLoading = true;
		lbError = null;
		try {
			if (action === 'apply') await applyLetterbox(fetch, movie.id);
			else if (action === 'ignore') await ignoreLetterbox(fetch, movie.id);
			else await removeLetterbox(fetch, movie.id);
			lbState = undefined;
			await loadLb();
		} catch (e) {
			lbError = e instanceof Error ? e.message : 'Action failed';
			lbLoading = false;
		}
	}

	$effect(() => {
		if (tab === 'letterbox') loadLb();
	});

	// ── Subtitle coverage helpers ─────────────────────────────────────────────
	type CoverageEntry = { lang: string; status: string; count: number };

	function subRows(): CoverageEntry[] {
		if (!movie?.subtitle_coverage) return [];
		const cov = movie.subtitle_coverage as Record<string, unknown>;
		return Object.entries(cov)
			.filter(([k]) => k !== 'missing_preferred_languages')
			.map(([lang, val]) => {
				const v = val as Record<string, unknown>;
				return { lang, status: String(v?.status ?? 'unknown'), count: Number(v?.count ?? 0) };
			});
	}

	function missingLangs(): string[] {
		if (!movie?.subtitle_coverage) return [];
		const cov = movie.subtitle_coverage as Record<string, unknown>;
		const ml = cov?.missing_preferred_languages;
		return Array.isArray(ml) ? (ml as string[]) : [];
	}

	function basename(p: string | null | undefined): string {
		if (!p) return '—';
		return p.split('/').pop() ?? p;
	}
</script>

{#if data.error || !movie}
	<div class="errstate">
		<Icon name="film" size={40} stroke={1} />
		<strong>Could not load movie</strong>
		<span>{data.error ?? 'Unknown error'}</span>
		<button onclick={() => goto('/films')}>← Back to Films</button>
	</div>
{:else}
	<div class="crumb">
		<a href="/films">Films</a>
		<span>/</span>
		<span>{movie.title}</span>
	</div>

	<div class="hub">
		<!-- ── Left rail ───────────────────────────────────────── -->
		<aside class="rail">
			<div class="poster-wrap">
				<PosterThumb
					title={movie.title}
					year={movie.year}
					posterStatus={movie.poster_status}
					posterUrl={movie.poster_url}
					hdr={movie.hdr}
				/>
			</div>

			<div class="rail-meta">
				<div class="movie-title">{movie.title}</div>
				<div class="movie-sub">
					{movie.year ?? '—'}{#if movie.genres?.length} · {movie.genres.slice(0, 2).join(', ')}{/if}
				</div>
			</div>

			<div class="chips">
				{#if movie.resolution}
					<div class="chip">
						<span class="chip-label">Resolution</span>
						<span class="chip-val mono">{movie.resolution}</span>
					</div>
				{/if}
				{#if movie.hdr}
					<div class="chip">
						<span class="chip-label">HDR</span>
						<HdrBadge kind={movie.hdr} />
					</div>
				{/if}
				{#if movie.container}
					<div class="chip">
						<span class="chip-label">Container</span>
						<span class="chip-val mono">{movie.container}</span>
					</div>
				{/if}
				<div class="chip">
					<span class="chip-label">Poster</span>
					<span
						class="chip-status"
						style="--c:{toneVar(posterStatusMeta[movie.poster_status].tone)}"
					>
						<StatusDot tone={posterStatusMeta[movie.poster_status].tone} size={6} />
						{posterStatusMeta[movie.poster_status].label}
					</span>
				</div>
				{#if movie.letterbox_status && movie.letterbox_status !== 'none'}
					{@const lm = letterboxMeta(movie.letterbox_status)}
					{#if lm}
						<div class="chip">
							<span class="chip-label">Letterbox</span>
							<span class="chip-status" style="--c:{toneVar(lm.tone)}">
								<StatusDot tone={lm.tone} size={6} />
								{lm.label}
							</span>
						</div>
					{/if}
				{/if}
				{#if movie.media_file_path}
					<div class="chip chip-file">
						<span class="chip-label">File</span>
						<span class="chip-val mono small" title={movie.media_file_path}
							>{basename(movie.media_file_path)}</span
						>
					</div>
				{/if}
			</div>
		</aside>

		<!-- ── Right pane ──────────────────────────────────────── -->
		<div class="pane">
			<div class="tabwrap">
				<TabBar tabs={TABS} active={tab} onSelect={setTab} />
			</div>

			<!-- ═══ POSTER TAB ═══════════════════════════════════ -->
			{#if tab === 'poster'}
				<div class="tab-content">
					<div class="section-label">AI Pipeline</div>

					<div class="poster-status-row">
						<div class="status-card">
							<div class="sc-label">Current status</div>
							<div
								class="sc-val"
								style="--c:{toneVar(posterStatusMeta[movie.poster_status].tone)}"
							>
								<StatusDot tone={posterStatusMeta[movie.poster_status].tone} />
								{posterStatusMeta[movie.poster_status].label}
							</div>
						</div>
						{#if movie.tmdb_id}
							<button class="btn-gold" onclick={startPipeline} disabled={pipeRunning}>
								{#if pipeRunning}
									<span class="spin">⟳</span> Running…
								{:else}
									Run AI pipeline
								{/if}
							</button>
						{:else}
							<div class="info-note">No TMDB ID — sync Radarr first</div>
						{/if}
					</div>

					{#if pipeError}
						<div class="alert-box err">
							{pipeError}{#if pipeRunId}<span class="mono small"> · run {pipeRunId.slice(0, 8)}</span
								>{/if}
						</div>
					{/if}

					{#if pipeRunning || pipeDone || pipeEvents.length > 0}
						<div class="run-log">
							<div class="run-log-head">
								{#if pipeRunning}
									<span class="spin">⟳</span> Pipeline running
								{:else if pipeDone}
									✓ Run complete
								{/if}
								{#if pipeRunId}<span class="mono faint"> · {pipeRunId.slice(0, 8)}</span>{/if}
							</div>
							{#if pipeEvents.length > 0}
								<ul class="event-list">
									{#each pipeEvents.slice(-12) as ev, i (i)}
										<li>{ev}</li>
									{/each}
								</ul>
							{/if}
						</div>
					{/if}

					<div class="placeholder-card">
						<div class="ph-title">Review candidates</div>
						<div class="ph-body">
							Run the pipeline to fetch and score poster candidates. Once complete, the ranked
							results will appear here for approval.
						</div>
					</div>
				</div>

				<!-- ═══ VIDEO · HDR TAB ══════════════════════════════ -->
			{:else if tab === 'video'}
				<div class="tab-content">
					<div class="section-label">Video specs</div>
					<div class="card-grid">
						<div class="info-card">
							<div class="ic-label">Resolution</div>
							<div class="ic-val mono">{movie.resolution ?? '—'}</div>
							{#if movie.video_width && movie.video_height}
								<div class="ic-sub">{movie.video_width} × {movie.video_height}</div>
							{/if}
						</div>
						<div class="info-card">
							<div class="ic-label">Dynamic range</div>
							<div class="ic-val"><HdrBadge kind={movie.hdr} /></div>
							{#if !movie.hdr}<div class="ic-sub muted">Not detected</div>{/if}
						</div>
						<div class="info-card">
							<div class="ic-label">Container</div>
							<div class="ic-val mono">{movie.container ?? '—'}</div>
						</div>
						{#if movie.media_file_path}
							<div class="info-card span2">
								<div class="ic-label">File path</div>
								<div class="ic-val mono small" title={movie.media_file_path}>
									{movie.media_file_path}
								</div>
							</div>
						{/if}
					</div>

					<div class="infobox">
						HDR and resolution come from Radarr's media probe at sync time. Re-sync to refresh after
						a file upgrade.
					</div>

					<div class="placeholder-card">
						<div class="ph-title">Radarr quality profile</div>
						<div class="ph-body">Target HDR tier and upgrade request — coming soon.</div>
					</div>
				</div>

				<!-- ═══ SUBTITLES TAB ════════════════════════════════ -->
			{:else if tab === 'subtitles'}
				<div class="tab-content">
					<div class="section-label">Subtitle coverage</div>

					{#if movie.subtitle_status === 'gap'}
						<div class="alert-box warn">
							Missing preferred language — re-sync or generate subtitles to fill the gap.
						</div>
					{:else if movie.subtitle_status === 'ok'}
						<div class="alert-box good">All preferred languages covered.</div>
					{/if}

					{#if subRows().length > 0}
						<div class="sub-table">
							<div class="sub-row head">
								<span>Language</span><span>Status</span><span>Tracks</span>
							</div>
							{#each subRows() as row (row.lang)}
								<div class="sub-row">
									<span class="mono">{row.lang}</span>
									<span class="cell-status">
										<StatusDot
											tone={row.status === 'ok' || row.status === 'present'
												? 'good'
												: row.status === 'missing'
													? 'bad'
													: 'warn'}
										/>
										{row.status}
									</span>
									<span class="mono muted">{row.count}</span>
								</div>
							{/each}
						</div>
					{:else}
						<div class="empty-state">No subtitle data — re-sync to populate.</div>
					{/if}

					{#if missingLangs().length > 0}
						<div class="missing-langs">Missing: {missingLangs().join(', ')}</div>
					{/if}

					<div class="placeholder-card">
						<div class="ph-title">Track listing & generation</div>
						<div class="ph-body">
							Individual track details and Subgen (faster-whisper) generation — coming soon.
						</div>
					</div>
				</div>

				<!-- ═══ LETTERBOX TAB ════════════════════════════════ -->
			{:else if tab === 'letterbox'}
				<div class="tab-content">
					<div class="section-label">Letterbox detection</div>

					{#if lbLoading && lbState === undefined}
						<div class="empty-state">Loading…</div>
					{:else if lbError}
						<div class="alert-box err">{lbError}</div>
					{:else}
						{@const status = lbState?.status ?? 'none'}
						{@const lm = letterboxMeta(status)}

						<div class="lb-status-row">
							<div class="status-card">
								<div class="sc-label">Current status</div>
								<div class="sc-val" style="--c:{toneVar(lm?.tone ?? 'muted')}">
									<StatusDot tone={lm?.tone ?? 'muted'} />
									{lm?.label ?? 'Not analysed'}
								</div>
								{#if lbState?.aspect_label}
									<div class="sc-sub">{lbState.aspect_label}</div>
								{/if}
								{#if lbState?.recommended_crop_top || lbState?.recommended_crop_bottom}
									<div class="sc-sub mono">
										Crop: ↑{lbState.recommended_crop_top ?? 0}px ↓{lbState.recommended_crop_bottom ??
											0}px
									</div>
								{/if}
								{#if lbState?.confidence}
									<div class="sc-sub">Confidence: {lbState.confidence}</div>
								{/if}
							</div>

							<div class="lb-actions">
								{#if status === 'none' || status === 'not_letterboxed' || !lbState}
									<button class="btn-gold" onclick={lbDetect} disabled={lbLoading}>
										{lbLoading ? '⟳ Detecting…' : 'Detect letterbox'}
									</button>
								{:else if status === 'candidate' || status === 'prefilter_candidate'}
									<button
										class="btn-gold"
										onclick={() => lbAction('apply')}
										disabled={lbLoading}>Apply crop tags</button
									>
									<button class="btn-sec" onclick={() => lbAction('ignore')} disabled={lbLoading}
										>Skip</button
									>
									<button class="btn-ghost" onclick={lbDetect} disabled={lbLoading}
										>Re-detect</button
									>
								{:else if status === 'tagged'}
									<button
										class="btn-sec"
										onclick={() => lbAction('remove')}
										disabled={lbLoading}>Remove tags</button
									>
									<button class="btn-ghost" onclick={lbDetect} disabled={lbLoading}
										>Re-detect</button
									>
								{:else}
									<button class="btn-ghost" onclick={lbDetect} disabled={lbLoading}
										>Re-detect</button
									>
								{/if}
							</div>
						</div>

						{#if lbState?.ineligible_reason}
							<div class="alert-box warn">{lbState.ineligible_reason}</div>
						{/if}
						{#if lbState?.error}
							<div class="alert-box err">Detection error: {lbState.error}</div>
						{/if}
						{#if lbState?.last_detected_at}
							<div class="lb-meta">
								Last detected: <span class="mono"
									>{new Date(lbState.last_detected_at).toLocaleString()}</span
								>
							</div>
						{/if}
					{/if}

					<div class="placeholder-card">
						<div class="ph-title">Before / after preview</div>
						<div class="ph-body">Frame comparison preview available after detection completes.</div>
					</div>
				</div>

				<!-- ═══ ACTIVITY TAB ═════════════════════════════════ -->
			{:else if tab === 'activity'}
				<div class="tab-content">
					<div class="activity-empty">
						<Icon name="activity" size={36} stroke={1} />
						<div class="ae-title">Activity feed</div>
						<div class="ae-body">
							Poster deployments, sync events, and pipeline runs for this movie will appear here.
						</div>
					</div>
				</div>
			{/if}
		</div>
	</div>
{/if}

<style>
	.crumb {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		color: var(--muted);
		margin-bottom: 18px;
	}
	.crumb a {
		color: var(--muted);
		text-decoration: none;
	}
	.crumb a:hover {
		color: var(--text);
	}
	.crumb span:last-child {
		color: var(--text);
	}

	/* ── Hub layout ──────────────────────────────────────── */
	.hub {
		display: grid;
		grid-template-columns: 220px 1fr;
		gap: 28px;
		align-items: start;
	}
	@media (max-width: 680px) {
		.hub {
			grid-template-columns: 1fr;
		}
		.rail {
			position: static !important;
		}
	}

	/* ── Left rail ───────────────────────────────────────── */
	.rail {
		position: sticky;
		top: calc(var(--header-h) + 16px);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.poster-wrap {
		width: 100%;
	}
	.rail-meta {
		padding: 0 2px;
	}
	.movie-title {
		font-size: 14px;
		font-weight: 650;
		line-height: 1.3;
		color: var(--text);
	}
	.movie-sub {
		font-size: 12px;
		color: var(--muted);
		margin-top: 2px;
	}
	.chips {
		display: flex;
		flex-direction: column;
		gap: 0;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		overflow: hidden;
	}
	.chip {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 7px 10px;
		border-bottom: 1px solid var(--line);
		gap: 8px;
	}
	.chip:last-child {
		border-bottom: none;
	}
	.chip-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
		font-weight: 600;
		white-space: nowrap;
	}
	.chip-val {
		font-size: 12px;
		color: var(--text);
		text-align: right;
	}
	.chip-val.small {
		font-size: 10.5px;
	}
	.chip-status {
		display: flex;
		align-items: center;
		gap: 5px;
		font-size: 12px;
		color: var(--c, var(--text));
	}
	.chip-file .chip-val {
		max-width: 120px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* ── Right pane ──────────────────────────────────────── */
	.pane {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0;
	}
	.tabwrap {
		padding-bottom: 14px;
		border-bottom: 1px solid var(--line);
		margin-bottom: 20px;
	}

	/* ── Tab content shared ──────────────────────────────── */
	.tab-content {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.section-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: var(--faint);
		font-weight: 700;
	}

	/* ── Poster tab ──────────────────────────────────────── */
	.poster-status-row {
		display: flex;
		align-items: center;
		gap: 14px;
		flex-wrap: wrap;
	}
	.status-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 12px 14px;
		flex: 1;
		min-width: 160px;
	}
	.sc-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 600;
		margin-bottom: 6px;
	}
	.sc-val {
		display: flex;
		align-items: center;
		gap: 7px;
		font-size: 14px;
		font-weight: 600;
		color: var(--c, var(--text));
	}
	.sc-sub {
		font-size: 11px;
		color: var(--muted);
		margin-top: 4px;
	}

	/* ── Video tab ───────────────────────────────────────── */
	.card-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
		gap: 10px;
	}
	.info-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 12px 14px;
	}
	.info-card.span2 {
		grid-column: 1 / -1;
	}
	.ic-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 600;
		margin-bottom: 5px;
	}
	.ic-val {
		font-size: 18px;
		font-weight: 600;
		color: var(--text);
		line-height: 1.2;
	}
	.ic-val.mono {
		font-size: 15px;
	}
	.ic-val.small {
		font-size: 11px;
		word-break: break-all;
		color: var(--muted);
	}
	.ic-sub {
		font-size: 11.5px;
		color: var(--muted);
		margin-top: 3px;
	}
	.ic-sub.muted {
		color: var(--faint);
	}
	.infobox {
		background: var(--ink2);
		border-left: 3px solid var(--info);
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
		padding: 10px 13px;
		font-size: 12px;
		color: var(--muted);
		line-height: 1.5;
	}

	/* ── Subtitle tab ────────────────────────────────────── */
	.sub-table {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		overflow: hidden;
	}
	.sub-row {
		display: grid;
		grid-template-columns: 1fr 1fr auto;
		gap: 12px;
		padding: 8px 12px;
		border-bottom: 1px solid var(--line);
		font-size: 13px;
		align-items: center;
	}
	.sub-row:last-child {
		border-bottom: none;
	}
	.sub-row.head {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint2);
		font-weight: 700;
		background: var(--ink2);
	}
	.cell-status {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 12px;
		text-transform: capitalize;
		color: var(--muted);
	}
	.missing-langs {
		font-size: 12px;
		color: var(--warn);
		padding: 6px 0;
	}

	/* ── Letterbox tab ───────────────────────────────────── */
	.lb-status-row {
		display: flex;
		align-items: flex-start;
		gap: 14px;
		flex-wrap: wrap;
	}
	.lb-actions {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding-top: 4px;
	}
	.lb-meta {
		font-size: 12px;
		color: var(--faint);
	}

	/* ── Activity tab ────────────────────────────────────── */
	.activity-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 10px;
		padding: 60px 24px;
		color: var(--faint);
		text-align: center;
	}
	.ae-title {
		font-size: 15px;
		font-weight: 600;
		color: var(--muted);
	}
	.ae-body {
		font-size: 13px;
		color: var(--faint);
		max-width: 340px;
		line-height: 1.5;
	}

	/* ── Pipeline run log ────────────────────────────────── */
	.run-log {
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 12px 14px;
		font-size: 12px;
	}
	.run-log-head {
		font-weight: 600;
		color: var(--text);
		margin-bottom: 8px;
	}
	.event-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 3px;
		color: var(--muted);
		font-family: var(--font-mono);
		font-size: 11.5px;
	}
	.event-list li::before {
		content: '›  ';
		color: var(--faint);
	}

	/* ── Placeholder cards ───────────────────────────────── */
	.placeholder-card {
		border: 1px dashed var(--line2);
		border-radius: var(--radius-sm);
		padding: 16px 18px;
		background: transparent;
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
		line-height: 1.5;
	}

	/* ── Alerts ──────────────────────────────────────────── */
	.alert-box {
		padding: 10px 13px;
		border-radius: var(--radius-sm);
		font-size: 12.5px;
		line-height: 1.4;
		border: 1px solid transparent;
	}
	.alert-box.err {
		background: color-mix(in srgb, var(--bad) 10%, transparent);
		border-color: color-mix(in srgb, var(--bad) 30%, transparent);
		color: var(--bad);
	}
	.alert-box.warn {
		background: color-mix(in srgb, var(--warn) 10%, transparent);
		border-color: color-mix(in srgb, var(--warn) 25%, transparent);
		color: var(--warn);
	}
	.alert-box.good {
		background: color-mix(in srgb, var(--good) 10%, transparent);
		border-color: color-mix(in srgb, var(--good) 25%, transparent);
		color: var(--good);
	}
	.info-note {
		font-size: 12px;
		color: var(--faint);
		padding: 6px 0;
	}

	/* ── Error page ──────────────────────────────────────── */
	.errstate {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 10px;
		padding: 80px 24px;
		color: var(--faint);
		text-align: center;
	}
	.errstate strong {
		color: var(--text);
		font-size: 15px;
	}
	.errstate button {
		margin-top: 8px;
		padding: 8px 18px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--text);
		font-size: 13px;
	}

	/* ── Buttons ─────────────────────────────────────────── */
	.btn-gold {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
		white-space: nowrap;
	}
	.btn-gold:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn-sec {
		padding: 8px 16px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 13px;
		white-space: nowrap;
	}
	.btn-ghost {
		padding: 8px 16px;
		border-radius: 8px;
		border: 1px solid transparent;
		background: transparent;
		color: var(--muted);
		font-size: 13px;
		white-space: nowrap;
	}
	.btn-ghost:hover {
		color: var(--text);
		background: var(--panel2);
	}
	.btn-sec:disabled,
	.btn-ghost:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	/* ── Utilities ───────────────────────────────────────── */
	.mono {
		font-family: var(--font-mono);
	}
	.small {
		font-size: 11px;
	}
	.faint {
		color: var(--faint);
	}
	.muted {
		color: var(--muted);
	}
	.empty-state {
		padding: 32px 16px;
		text-align: center;
		font-size: 13px;
		color: var(--faint);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
	}
	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
	.spin {
		display: inline-block;
		animation: spin 1s linear infinite;
	}
</style>
