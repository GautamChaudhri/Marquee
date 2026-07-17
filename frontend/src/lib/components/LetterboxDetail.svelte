<script lang="ts">
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import { confirmMutation } from '$lib/activity/client';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import { aspectRatio, confidenceTone, letterboxMeta, toneVar } from '$lib/display';
	import { toast } from '$lib/toast';
	import type { LetterboxDetail, ReencodeArtifact, ReencodePlan } from '$lib/api/types';
	import {
		getLetterboxState,
		detectLetterbox,
		applyLetterbox,
		ignoreLetterbox,
		markNotLetterboxed,
		removeLetterbox,
		confirmLetterbox,
		createReencodePlan,
		listReencodeArtifacts,
		replaceOriginal,
		restoreOriginal,
		deleteArtifact
	} from '$lib/api/letterbox';
	import {
		CPU_PRESETS,
		KNOWN_ENCODERS,
		NVENC_PRESETS,
		PROFILE_META,
		PROFILE_SETTINGS,
		prettyEncoder,
		profileFamilyKey,
		type QualityProfile
	} from '$lib/letterbox/encodeSettings';
	import StatusDot from './StatusDot.svelte';
	import Icon from './Icon.svelte';
	import LetterboxFrame from './LetterboxFrame.svelte';
	import { pairKey, pairColorMap, agreeCount } from '$lib/letterbox-samples';

	function fmtBytes(n: number | null | undefined): string {
		if (!n) return '—';
		const gb = n / 1e9;
		return gb >= 1 ? `${gb.toFixed(2)} GB` : `${(n / 1e6).toFixed(0)} MB`;
	}

	let {
		movieId,
		onChanged,
		onAnalyzeAll,
		analyzing = false
	}: {
		movieId: number | null;
		onChanged: () => void;
		onAnalyzeAll: () => void;
		analyzing?: boolean;
	} = $props();

	let detail = $state<LetterboxDetail | null>(null);
	let loading = $state(false);
	let busy = $state(false);
	let loadError = $state<string | null>(null);
	let showConf = $state(false);
	let previewMinute = $state<number | null>(null);

	let detecting = $state(false);
	let initiatedJobIds = $state<string[]>([]);
	let scopeActive = $state(false);
	const actionBusy = $derived(busy || scopeActive);

	function resetReencodeState() {
		detecting = false;
		method = 'quick';
		settingsMode = 'simple';
		selectedProfile = 'balanced';
		plan = null;
		jobId = null;
		planError = null;
		planLoading = false;
		artifact = null;
		encoding = false;
		setEncoder = 'auto';
		setQuality = null;
		setPreset = '';
		setCodec = 'preserve';
		setAllowCpu = true;
		cropTopOverride = null;
		cropBottomOverride = null;
	}

	let lastId = $state<number | null>(null);
	$effect(() => {
		if (movieId === lastId) return;
		lastId = movieId;
		detail = null;
		loadError = null;
		showConf = false;
		previewMinute = null;
		resetReencodeState();
		if (movieId == null) return;
		loading = true;
		getLetterboxState(fetch, movieId)
			.then((d) => {
				detail = d;
				hydrateReencodeState(d);
			})
			.catch((e) => (loadError = e instanceof Error ? e.message : 'Failed to load'))
			.finally(() => (loading = false));
	});

	const stage = $derived.by(() => {
		const s = detail?.status;
		if (!s) return 'none';
		if (s.startsWith('prefilter')) return 'candidate';
		if (s === 'candidate') return 'detected';
		if (s === 'tagged') return detail?.reviewed ? 'processed' : 'preview';
		if (s === 'reencoded') return 'reencoded';
		if (s === 'not_letterboxed' || s === 'variable_unsafe' || s === 'skipped') return 'clean';
		return 'other';
	});

	const meta = $derived(detail ? letterboxMeta(detail.status) : null);

	const cropLabel = $derived.by(() => {
		const t = detail?.recommended_crop_top ?? 0;
		const b = detail?.recommended_crop_bottom ?? 0;
		return `${t} / ${b} px`;
	});

	const afterHeight = $derived.by(() => {
		const h = detail?.source_height;
		const t = detail?.recommended_crop_top ?? 0;
		const b = detail?.recommended_crop_bottom ?? 0;
		return h ? h - t - b : null;
	});

	const afterAR = $derived.by(() => {
		const w = detail?.source_width;
		const ah = afterHeight;
		return w && ah ? (w / ah).toFixed(2) + ':1' : null;
	});

	// Tray accent for the selected movie's stage → colors the panel's top border.
	const panelAccent = $derived(
		(
			{
				candidate: 'var(--warn)',
				detected: 'var(--info)',
				preview: 'var(--dovi)',
				clean: 'var(--bad)',
				processed: 'var(--good)',
				reencoded: 'var(--good)'
			} as Record<string, string>
		)[stage] ?? 'var(--muted)'
	);

	// Both aspect ratios for a variable-AR film, parsed from the detector's note
	// (format owned by consensus(): "...alternates between 1.90:1 (4 samples),
	// 2.39:1 (8 samples)...").
	const variableRatios = $derived.by(() => {
		if (!detail?.variable_ar) return null;
		const found = detail.variable_ar_note?.match(/\d+\.\d{2}:1/g) ?? [];
		const unique = [...new Set(found)];
		return unique.length >= 2 ? unique.join(' / ') : (afterAR ?? null);
	});

	// Override preview URLs when the user clicks a sample frame row. A clicked
	// row requests the *exact* minute (no brightness substitution) so it always
	// shows the frame the user actually selected — see preview_path() in
	// letterbox_preview.py for why the cache keys must stay separate.
	const activeMinute = $derived(previewMinute ?? detail?.preview_minute ?? 5);
	const exactPreview = $derived(previewMinute != null);
	const previewSuffix = $derived(exactPreview ? '&exact=true' : '');
	const beforeUrl = $derived(
		detail && movieId != null
			? `/api/letterbox/movies/${movieId}/preview?mode=before&minute=${activeMinute}${previewSuffix}`
			: null
	);
	const afterUrl = $derived(
		detail && movieId != null
			? `/api/letterbox/movies/${movieId}/preview?mode=after&minute=${activeMinute}${previewSuffix}`
			: null
	);

	function bindJob(result: unknown) {
		if (typeof result !== 'object' || result === null || !('job_id' in result)) return false;
		const jobId = (result as { job_id?: unknown }).job_id;
		if (typeof jobId !== 'string') return false;
		initiatedJobIds = initiatedJobIds.includes(jobId)
			? initiatedJobIds
			: [...initiatedJobIds, jobId];
		return true;
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		detecting = false;
		encoding = false;
		busy = false;
		if (snapshot.type === 'letterbox_reencode' && snapshot.status.outcome === 'succeeded' && id) {
			const list = await listReencodeArtifacts(fetch, { movie_id: id });
			artifact =
				list.items.find((a) => a.status === 'candidate_ready' || a.status === 'kept') ?? null;
		}
		toast(
			`${snapshot.label} ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		lastId = null;
		onChanged();
	}

	async function startDetection() {
		if (id == null || busy || detecting) return;
		busy = true;
		try {
			detecting = true;
			bindJob(await detectLetterbox(fetch, id));
			toast('Analysis queued', 'info');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not queue analysis', 'bad');
		} finally {
			busy = false;
		}
	}

	async function startThoroughDetection() {
		if (id == null || busy || detecting) return;
		busy = true;
		try {
			detecting = true;
			bindJob(await detectLetterbox(fetch, id, { thorough: true }));
			toast('Thorough analysis queued', 'info');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not queue thorough analysis', 'bad');
		} finally {
			busy = false;
		}
	}

	async function startReprocess() {
		if (id == null || busy || detecting) return;
		busy = true;
		try {
			bindJob(await removeLetterbox(fetch, id));
			detecting = true;
			bindJob(await detectLetterbox(fetch, id));
			toast('Reprocess queued', 'info');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not queue reprocess', 'bad');
		} finally {
			busy = false;
		}
	}

	async function run(fn: () => Promise<unknown>, okMsg: string) {
		if (movieId == null || busy) return;
		busy = true;
		try {
			const result = await fn();
			toast(okMsg, 'good');
			if (!bindJob(result)) {
				lastId = null;
				onChanged();
			}
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Action failed', 'bad');
		} finally {
			busy = false;
		}
	}

	const id = $derived(movieId);
	const detectedAtLabel = $derived(
		detail?.last_detected_at ? new Date(detail.last_detected_at).toLocaleString() : null
	);

	// ── Permanent re-encode flow ────────────────────────────────────────────────
	type Method = 'quick' | 'permanent';
	let method = $state<Method>('quick');
	let settingsMode = $state<'simple' | 'advanced'>('simple');
	let selectedProfile = $state<QualityProfile>('balanced');

	// User-editable settings (null/'' = use plan default).
	let setEncoder = $state('auto');
	let setQuality = $state<number | null>(null);
	let setPreset = $state('');
	let setCodec = $state('preserve');
	let setAllowCpu = $state(true);
	let cropTopOverride = $state<number | null>(null);
	let cropBottomOverride = $state<number | null>(null);

	let plan = $state<ReencodePlan | null>(null);
	// The canonical media-job id, sourced from the job snapshot on hydration or
	// the plan response on fresh planning. Never read job_id off `plan` — the
	// stored plan_json doesn't carry it, so a hydrated plan's job_id is undefined.
	let jobId = $state<string | null>(null);
	let planError = $state<string | null>(null);
	let planLoading = $state(false);
	let encoding = $state(false);
	let artifact = $state<ReencodeArtifact | null>(null);

	const reencodeMode = $derived.by(() => {
		const artifactStatus = detail?.reencode?.artifact?.status;
		if (plan && !encoding) return 'planned';
		if (encoding) return 'encoding';
		if (artifactStatus === 'candidate_ready' || artifactStatus === 'kept') return 'ready';
		return null;
	});

	const presetOptions = $derived(
		plan?.encoder.family === 'nvidia'
			? NVENC_PRESETS
			: plan?.encoder.family === 'cpu'
				? CPU_PRESETS
				: []
	);
	const encoderOptions = $derived(
		plan?.encoder.available_encoders.filter((e) => KNOWN_ENCODERS.includes(e)) ?? []
	);

	function hydrateReencodeState(snapshot: LetterboxDetail | null) {
		plan = null;
		jobId = null;
		planError = null;
		planLoading = false;
		artifact = null;
		encoding = false;

		const reencode = snapshot?.reencode;
		if (!reencode) {
			method = 'quick';
			return;
		}

		method = 'permanent';
		if (reencode.artifact?.status === 'candidate_ready' || reencode.artifact?.status === 'kept') {
			artifact = reencode.artifact;
		}
	}

	function getProfileOverrides(): { quality?: number; preset?: string | null } {
		if (!plan) return {};
		const key = profileFamilyKey(plan.encoder.family, plan.encoder.encoder);
		const settings = PROFILE_SETTINGS[selectedProfile]?.[key];
		return settings ? { quality: settings.quality, preset: settings.preset } : {};
	}

	function selectProfile(p: QualityProfile) {
		selectedProfile = p;
		if (plan) loadPlan();
	}

	async function loadPlan() {
		if (id == null || reencodeMode !== null) return;
		planLoading = true;
		planError = null;
		try {
			const profileOv = settingsMode === 'simple' ? getProfileOverrides() : {};
			plan = await createReencodePlan(fetch, id, {
				top: cropTopOverride,
				bottom: cropBottomOverride,
				allow_cpu_fallback: settingsMode === 'advanced' ? setAllowCpu : true,
				encoder: setEncoder === 'auto' ? null : setEncoder,
				quality: settingsMode === 'advanced' ? setQuality : (profileOv.quality ?? null),
				preset: settingsMode === 'advanced' ? setPreset || null : (profileOv.preset ?? null),
				codec: setCodec
			});
			jobId = plan.job_id;
		} catch (e) {
			plan = null;
			jobId = null;
			const body = (e as { body?: { detail?: { message?: string } } })?.body;
			planError =
				body?.detail?.message ?? (e instanceof Error ? e.message : 'Could not plan re-encode');
		} finally {
			planLoading = false;
		}
	}

	function selectMethod(m: Method) {
		if (reencodeMode !== null) return;
		method = m;
		if (m === 'permanent' && !plan && !planLoading) loadPlan();
	}

	async function startEncode() {
		if (!plan || !jobId || id == null) return;
		const confirmId = jobId;
		encoding = true;
		artifact = null;
		try {
			bindJob(
				await confirmMutation(fetch, confirmId, plan.plan_version, plan.configuration_version)
			);
		} catch (e) {
			encoding = false;
			toast(e instanceof Error ? e.message : 'Could not start encode', 'bad');
			return;
		}
		onChanged();
	}

	async function doReplace() {
		if (!artifact || busy) return;
		busy = true;
		try {
			bindJob(await replaceOriginal(fetch, artifact.id));
			toast('Publication queued', 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Replace failed', 'bad');
		} finally {
			busy = false;
		}
	}

	async function doDiscard() {
		if (!artifact || busy) return;
		busy = true;
		try {
			bindJob(await deleteArtifact(fetch, artifact.id));
			toast('Candidate discard queued', 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Discard failed', 'bad');
		} finally {
			busy = false;
		}
	}
</script>

<div class="panel" style="--panel-accent:{panelAccent}">
	{#if movieId != null}
		<FeatureActivityPanel
			scopeKey={`feature:letterbox:movie:${movieId}`}
			query={{
				feature_area: 'letterbox',
				types: ['letterbox_detect', 'letterbox_apply', 'letterbox_remove', 'letterbox_reencode'],
				subject_kind: 'movie',
				subject_reference: [String(movieId)]
			}}
			jobIds={initiatedJobIds}
			bind:active={scopeActive}
			heading="Movie letterbox activity"
			onSettled={handleJobSettled}
		/>
	{/if}
	{#if movieId == null}
		<div class="empty">
			<Icon name="letterbox" size={32} stroke={1} />
			<span>Select a film to inspect it.</span>
		</div>
	{:else if loading}
		<div class="empty">Loading…</div>
	{:else if loadError || !detail}
		<div class="empty err">{loadError ?? 'No detail available.'}</div>
	{:else if stage === 'reencoded'}
		<!-- ── Re-encode complete: no preview to show, show the actual result ── -->
		<div class="grid">
			<div class="meta">
				<div class="badge" style="--c:{toneVar(meta?.tone ?? 'muted')}; margin-bottom:10px;">
					<StatusDot tone={meta?.tone ?? 'muted'} size={7} />
					{meta?.label ?? detail.status}
				</div>
				<h3>{detail.title ?? `Movie ${detail.movie_id}`}</h3>
				<div class="year">
					{detail.year ?? '—'}{#if detail.source_height}
						· {detail.source_height}p{/if}
				</div>
			</div>
			<div class="actions">
				<div class="alabel">Re-encode complete</div>
				{#if detail.reencode?.artifact}
					{@const a = detail.reencode.artifact}
					<div class="applied-card">
						<dl class="enc-summary">
							<dt>Encoder</dt>
							<dd class="mono">{prettyEncoder(a.encoder ?? '')} · {a.codec ?? '—'}</dd>
							<dt>Crop T / B</dt>
							<dd class="mono">{a.crop_top ?? 0} / {a.crop_bottom ?? 0} px</dd>
							<dt>Size</dt>
							<dd class="mono">
								{fmtBytes(a.candidate_size_bytes)}
								<span class="crop-note">
									(was {fmtBytes(
										a.original_size_bytes
									)}{#if a.original_size_bytes && a.candidate_size_bytes}
										· {Math.round((1 - a.candidate_size_bytes / a.original_size_bytes) * 100)}%
										smaller{/if})
								</span>
							</dd>
							{#if a.hdr_status}
								<dt>HDR</dt>
								<dd class="mono">{a.hdr_status}</dd>
							{/if}
							{#if a.dovi_status}
								<dt>Dolby Vision</dt>
								<dd class="mono">{a.dovi_status}</dd>
							{/if}
							{#if a.detail?.execution?.acceleration}
								<dt>Pipeline</dt>
								<dd class="mono">
									{a.detail.execution.acceleration.enabled
										? 'NVIDIA NVDEC → GPU crop → NVENC'
										: `CPU decode/crop${a.detail.execution.acceleration.reason ? ` · ${a.detail.execution.acceleration.reason}` : ''}`}
								</dd>
							{/if}
							{#if a.updated_at}
								<dt>Completed</dt>
								<dd class="mono">{new Date(a.updated_at).toLocaleString()}</dd>
							{/if}
						</dl>
					</div>
					<button
						class="btn-sec"
						disabled={actionBusy}
						onclick={() => run(() => restoreOriginal(fetch, a.id), 'Restore queued')}
					>
						Restore original →
					</button>
					<div class="note good">
						The original is preserved as a checksummed canonical artifact; restore is a separate
						confirmed job.
					</div>
				{:else}
					<div class="note">No re-encode record found for this file.</div>
				{/if}
			</div>
		</div>
	{:else if stage === 'detected' || stage === 'preview' || stage === 'processed'}
		<!-- ── 2-row grid: before row + after row, actions span both ── -->
		<div class="grid-det">
			<!-- Row 1, Col 1: badge + title + year + BEFORE dims/AR -->
			<div class="meta-top">
				<div class="badge-row">
					<div class="badge" style="--c:{toneVar(meta?.tone ?? 'muted')}">
						<StatusDot tone={meta?.tone ?? 'muted'} size={7} />
						{meta?.label ?? detail.status}
					</div>
				</div>
				<h3>{detail.title ?? `Movie ${detail.movie_id}`}</h3>
				<div class="year">
					{detail.year ?? '—'}{#if detail.source_height}
						· {detail.source_height}p{/if}
				</div>
				<dl>
					{#if detail.source_width && detail.source_height}
						<dt>Dimensions</dt>
						<dd class="mono">{detail.source_width}×{detail.source_height}</dd>
					{/if}
					{#if aspectRatio(detail.source_width, detail.source_height)}
						<dt>Aspect ratio</dt>
						<dd class="mono">{aspectRatio(detail.source_width, detail.source_height)}:1</dd>
					{/if}
				</dl>
			</div>

			<!-- Row 1, Col 2: before image -->
			<div class="frame-cell before">
				{#if stage === 'processed'}
					<LetterboxFrame
						src={null}
						alt="before crop"
						placeholder="Previews cleared after confirmation"
					/>
				{:else}
					<LetterboxFrame src={beforeUrl} alt="before crop" placeholder="No preview" />
				{/if}
			</div>

			<!-- Row 3, spans both columns: actions -->
			<div class="actions det-actions">
				<div class="alabel">Actions</div>
				{#if reencodeMode !== null || stage === 'detected'}
					{#if reencodeMode === null}
						<button
							class="fix-card"
							class:active={method === 'quick'}
							onclick={() => selectMethod('quick')}
						>
							<div class="fix-head">
								⚡ Quick · Crop Tag
								{#if method === 'quick'}<span class="fix-on">Active</span>{/if}
							</div>
							<div class="fix-body">MKV pixel-crop tag. Instant & reversible. No quality loss.</div>
						</button>
						<button
							class="fix-card"
							class:active={method === 'permanent'}
							onclick={() => selectMethod('permanent')}
						>
							<div class="fix-head">
								🛠 Permanent · Re-encode
								{#if method === 'permanent'}<span class="fix-on">Active</span>{/if}
							</div>
							<div class="fix-body">
								FFmpeg re-encode. Works on all clients. Higher quality cost; original is preserved.
							</div>
						</button>
					{/if}

					{#if reencodeMode === null && method === 'quick'}
						<button
							class="btn-gold"
							disabled={actionBusy}
							onclick={() => run(() => applyLetterbox(fetch, id!), 'Crop tag applied')}
						>
							Apply crop tag →
						</button>
						<button
							class="btn-sec"
							disabled={busy}
							onclick={() => run(() => markNotLetterboxed(fetch, id!), 'Marked as cleared')}
						>
							Set as cleared
						</button>
						<button
							class="btn-ghost"
							disabled={busy}
							onclick={() => run(() => ignoreLetterbox(fetch, id!), 'Skipped')}
						>
							Skip
						</button>
					{:else if artifact}
						<!-- Encode finished: candidate ready for review -->
						<div class="applied-card">
							<div class="alabel">Candidate ready</div>
							<dl class="enc-summary">
								<dt>Encoder</dt>
								<dd class="mono">
									{prettyEncoder(artifact.encoder ?? '')} · {artifact.codec ?? '—'}
								</dd>
								<dt>Size</dt>
								<dd class="mono">
									{fmtBytes(artifact.candidate_size_bytes)}
									<span class="crop-note">(was {fmtBytes(artifact.original_size_bytes)})</span>
								</dd>
								{#if artifact.dovi_status}
									<dt>Dolby Vision</dt>
									<dd class="mono">{artifact.dovi_status}</dd>
								{/if}
								{#if artifact.detail?.execution?.acceleration}
									<dt>Pipeline</dt>
									<dd class="mono">
										{artifact.detail.execution.acceleration.enabled
											? 'NVIDIA NVDEC → GPU crop → NVENC'
											: `CPU decode/crop${artifact.detail.execution.acceleration.reason ? ` · ${artifact.detail.execution.acceleration.reason}` : ''}`}
									</dd>
								{/if}
							</dl>
						</div>
						<button class="btn-gold" disabled={actionBusy} onclick={doReplace}>
							Replace original →
						</button>
						<button class="btn-ghost" disabled={actionBusy} onclick={doDiscard}
							>Discard candidate</button
						>
						<div class="note good">
							The original is preserved under <span class="mono">.marquee/backups</span> after replacement
							— reversible later.
						</div>
					{:else if encoding}
						<div class="applied-card">
							<div class="alabel">Encoding in progress</div>
							<div class="note">Live progress and available actions are shown above.</div>
						</div>
					{:else if reencodeMode === 'planned'}
						<div class="applied-card">
							<div class="alabel">Re-encode planned</div>
							<dl class="enc-summary">
								<dt>Encoder</dt>
								<dd class="mono">
									{prettyEncoder(plan?.encoder.encoder ?? '')} · {plan?.encoder.family ?? '—'}
								</dd>
								<dt>Crop</dt>
								<dd class="mono">
									{plan?.crop.top ?? 0}:{plan?.crop.bottom ?? 0}
									<span class="crop-note"
										>→ {plan?.crop.output_height ?? detail.source_height ?? '—'}p</span
									>
								</dd>
								<dt>Temp size</dt>
								<dd class="mono">
									{fmtBytes(plan?.storage.estimated_temp_bytes ?? null)}
									<span class="crop-note">/ {fmtBytes(plan?.storage.free_bytes ?? null)} free</span>
								</dd>
							</dl>
							<div class="note {plan?.acceleration?.enabled ? 'good' : ''}">
								{plan?.acceleration?.enabled
									? `NVIDIA NVDEC → GPU crop → NVENC (${plan?.acceleration?.decoder})`
									: `CPU decode/crop · ${plan?.acceleration?.reason ?? 'NVIDIA acceleration unavailable.'}`}
							</div>
						</div>
						{#if planError}
							<div class="note err">{planError}</div>
						{/if}
						{#each plan?.warnings ?? [] as w (w.code)}
							<div class="note {w.requires_confirmation ? 'warn' : ''}">{w.message}</div>
						{/each}
						<button class="btn-gold" disabled={actionBusy} onclick={startEncode}>
							Confirm & encode →
						</button>
						<div class="note">
							This plan is already saved. Confirm it to start the queued re-encode job, or discard
							it to choose a different method.
						</div>
					{:else if planLoading}
						<div class="note">Planning re-encode…</div>
					{:else if planError}
						<div class="note err">{planError}</div>
						<button class="btn-sec" onclick={loadPlan}>Retry plan</button>
					{:else if plan}
						<div class="settings">
							<div class="note {plan.acceleration?.enabled ? 'good' : ''}">
								{plan.acceleration?.enabled
									? `NVIDIA NVDEC \u2192 GPU crop \u2192 NVENC (${plan.acceleration?.decoder})`
									: `CPU decode/crop \u00b7 ${plan.acceleration?.reason ?? 'NVIDIA acceleration unavailable.'}`}
							</div>
							<label class="field">
								<span>Encoder</span>
								<select bind:value={setEncoder} onchange={loadPlan}>
									<option value="auto">Auto \u00b7 {prettyEncoder(plan.encoder.encoder)}</option>
									{#each encoderOptions as enc (enc)}
										<option value={enc}>{prettyEncoder(enc)}</option>
									{/each}
								</select>
							</label>
							<label class="field">
								<span>Target codec</span>
								<select bind:value={setCodec} onchange={loadPlan}>
									<option value="preserve">Preserve ({plan.source.codec ?? '\u2014'})</option>
									<option value="hevc">HEVC (H.265)</option>
									<option value="h264">H.264</option>
								</select>
							</label>

							{#if settingsMode === 'simple'}
								<div class="profile-cards">
									{#each ['speed', 'balanced', 'quality'] as const as p (p)}
										{@const pmeta = PROFILE_META[p]}
										{@const key = profileFamilyKey(plan.encoder.family, plan.encoder.encoder)}
										{@const vals = PROFILE_SETTINGS[p][key]}
										<button
											class="profile-card"
											class:active={selectedProfile === p}
											onclick={() => selectProfile(p)}
										>
											<div class="profile-head">
												<span class="profile-icon">{pmeta.icon}</span>
												{pmeta.label}
												{#if selectedProfile === p}<span class="fix-on">Selected</span>{/if}
											</div>
											<div class="profile-desc">{pmeta.description}</div>
											{#if vals}
												<div class="profile-vals mono">
													{plan.encoder.family === 'nvidia'
														? 'CQ'
														: plan.encoder.family === 'cpu'
															? 'CRF'
															: 'Quality'}
													{vals.quality}{#if vals.preset}
														\u00b7 {vals.preset}{/if}
												</div>
											{/if}
										</button>
									{/each}
								</div>
								<button class="adv-toggle" onclick={() => (settingsMode = 'advanced')}>
									\u2699 Advanced
								</button>
							{:else}
								<label class="field">
									<span>Quality (CQ/CRF) \u00b7 lower = better</span>
									<input
										type="number"
										min="0"
										max="51"
										placeholder={String(plan.encoder.quality)}
										bind:value={setQuality}
										onchange={loadPlan}
									/>
								</label>
								{#if presetOptions.length > 0}
									<label class="field">
										<span>Preset (speed \u2194 quality)</span>
										<select bind:value={setPreset} onchange={loadPlan}>
											<option value="">Default ({plan.encoder.preset ?? 'auto'})</option>
											{#each presetOptions as pr (pr)}
												<option value={pr}>{pr}</option>
											{/each}
										</select>
									</label>
								{/if}
								<div class="field-row">
									<label class="field">
										<span>Crop top</span>
										<input
											type="number"
											min="0"
											placeholder={String(detail.recommended_crop_top ?? 0)}
											bind:value={cropTopOverride}
											onchange={loadPlan}
										/>
									</label>
									<label class="field">
										<span>Crop bottom</span>
										<input
											type="number"
											min="0"
											placeholder={String(detail.recommended_crop_bottom ?? 0)}
											bind:value={cropBottomOverride}
											onchange={loadPlan}
										/>
									</label>
								</div>
								<label class="field checkbox">
									<input type="checkbox" bind:checked={setAllowCpu} onchange={loadPlan} />
									<span>Allow CPU encoding fallback</span>
								</label>
								<dl class="enc-summary">
									<dt>Resolved encoder</dt>
									<dd class="mono">
										{prettyEncoder(plan.encoder.encoder)} \u00b7 {plan.encoder.family}
									</dd>
									<dt>HDR</dt>
									<dd class="mono">{plan.hdr.status}</dd>
									<dt>Dolby Vision</dt>
									<dd class="mono">
										{plan.dovi.status}{#if plan.dovi.reason}
											\u00b7 {plan.dovi.reason}{/if}
									</dd>
									<dt>Est. temp size</dt>
									<dd class="mono">
										{fmtBytes(plan.storage.estimated_temp_bytes)}
										<span class="crop-note">/ {fmtBytes(plan.storage.free_bytes)} free</span>
									</dd>
								</dl>
								<button class="adv-toggle" onclick={() => (settingsMode = 'simple')}>
									\u2190 Simple
								</button>
							{/if}
						</div>

						{#each plan.warnings as w (w.code)}
							<div class="note {w.requires_confirmation ? 'warn' : ''}">{w.message}</div>
						{/each}

						<button class="btn-gold" disabled={actionBusy} onclick={startEncode}>
							Confirm & encode →
						</button>
						<button class="btn-ghost" onclick={() => selectMethod('quick')}>Cancel</button>
					{/if}
				{:else if stage === 'preview'}
					<div class="applied-card">
						<div class="alabel">MKV pixel-crop value</div>
						<div class="mono gold big">
							{detail.applied_crop_top ??
								detail.recommended_crop_top ??
								0}:{detail.applied_crop_bottom ?? detail.recommended_crop_bottom ?? 0}:0:0
						</div>
					</div>
					<button
						class="btn-gold"
						disabled={busy}
						onclick={() => run(() => confirmLetterbox(fetch, id!), 'Confirmed & finished')}
					>
						<Icon name="refresh" size={14} /> Confirm & finish
					</button>
					<button
						class="btn-sec"
						disabled={busy}
						onclick={() => run(() => removeLetterbox(fetch, id!), 'Tag removed · reverted')}
					>
						Remove tag · revert
					</button>
					<button class="btn-ghost" disabled={actionBusy || detecting} onclick={startReprocess}>
						Reprocess with new settings
					</button>
					<div class="note good">
						Reversible. Remove the tag at any time with zero quality impact.
					</div>
				{:else if stage === 'processed'}
					<div class="applied-card">
						<div class="alabel">Applied crop</div>
						<div class="mono gold big">
							{detail.applied_crop_top ?? 0}:{detail.applied_crop_bottom ?? 0}:0:0
						</div>
					</div>
					<button
						class="btn-sec"
						disabled={busy}
						onclick={() => run(() => removeLetterbox(fetch, id!), 'Tag removed')}
					>
						Remove tag
					</button>
					<button class="btn-ghost" disabled={actionBusy || detecting} onclick={startReprocess}>
						Reprocess
					</button>
				{/if}
			</div>

			<!-- Row 2, Col 1: AFTER dims/AR/crop/confidence/method — aligns with after image -->
			<div class="meta-bot">
				<dl>
					{#if detail.source_width && afterHeight}
						<dt>Dimensions</dt>
						<dd class="mono">{detail.source_width}×{afterHeight}</dd>
					{/if}
					{#if detail.variable_ar && variableRatios}
						<dt class="dt-variable">Variable aspect ratio</dt>
						<dd class="mono variable">{variableRatios}</dd>
					{:else if afterAR}
						<dt>Aspect ratio</dt>
						<dd class="mono">{afterAR}</dd>
					{/if}
					<dt>Crop T / B</dt>
					<dd class="mono gold">
						{cropLabel}{#if detail.variable_ar}<span class="crop-note">
								· defaulting to smaller crop</span
							>{/if}
					</dd>
					{#if detail.confidence && detail.confidence !== 'none'}
						<dt>Confidence</dt>
						<dd>
							<button
								class="conf-btn"
								style="color:{confidenceTone(detail.confidence)}"
								onclick={() => (showConf = !showConf)}
							>
								{detail.confidence}
								<span class="caret">{showConf ? '▲' : '▼'}</span>
							</button>
						</dd>
					{/if}
				</dl>
				{#if showConf}
					<div class="conf-expand">
						{#if detail.samples && detail.samples.length > 0}
							{@const { agreeCount: nAgree, totalOk } = agreeCount(detail.samples)}
							{@const colorMap = pairColorMap(detail.samples)}
							<div class="ce-summary">
								{#if nAgree === totalOk && totalOk > 0}
									All {totalOk} agree
								{:else}
									{nAgree}/{detail.samples.length} agree
								{/if}
								<span class="ce-hint">· click to preview that frame</span>
							</div>
							{#each detail.samples as s (s.minute)}
								{@const key = s.ok ? pairKey(s) : null}
								{@const barColor =
									key != null ? (colorMap.get(key) ?? 'var(--faint)') : 'var(--faint)'}
								{@const isActive = s.minute === activeMinute}
								<button
									class="ce-row"
									class:ce-active={isActive}
									onclick={() => (previewMinute = s.minute)}
									title="Preview frame at {s.minute} min"
								>
									<span class="mono ce-min">{s.minute}min</span>
									{#if s.ok}
										<span class="mono ce-val">{s.top_bar ?? '?'}/{s.bottom_bar ?? '?'} px</span>
										{#if s.backend || s.elapsed_ms != null}
											<span class="ce-hint">{s.backend ?? 'cpu'} · {s.elapsed_ms ?? '?'}ms</span>
										{/if}
										<span class="ce-dot" style="color:{barColor}">●</span>
									{:else}
										<span class="ce-err">{s.error ?? 'failed'}</span>
										<span class="ce-dot" style="color:var(--bad)">✕</span>
									{/if}
								</button>
							{/each}
						{:else}
							<div class="ce-summary">No sample data available.</div>
						{/if}
					</div>
				{/if}
				{#if detail.detect_method}
					<div class="method-tag mono">{detail.detect_method}</div>
				{/if}
			</div>

			<!-- Row 2, Col 2: after image — defines the row's height -->
			<div class="frame-cell after">
				{#if stage === 'processed'}
					<LetterboxFrame
						src={null}
						alt="after crop"
						tone="after"
						placeholder="Previews cleared after confirmation"
					/>
				{:else}
					<LetterboxFrame src={afterUrl} alt="after crop" tone="after" placeholder="No preview" />
				{/if}
			</div>
		</div>
	{:else}
		<!-- ── Single-row grid for candidate / clean / other ── -->
		<div class="grid">
			<div class="meta">
				<div class="badge" style="--c:{toneVar(meta?.tone ?? 'muted')}; margin-bottom:10px;">
					<StatusDot tone={meta?.tone ?? 'muted'} size={7} />
					{meta?.label ?? detail.status}
				</div>
				<h3>{detail.title ?? `Movie ${detail.movie_id}`}</h3>
				<div class="year">
					{detail.year ?? '—'}{#if detail.source_height}
						· {detail.source_height}p{/if}
				</div>
				<dl>
					{#if detail.source_width && detail.source_height}
						<dt>Dimensions</dt>
						<dd class="mono">{detail.source_width}×{detail.source_height}</dd>
					{/if}
					{#if detail.variable_ar && variableRatios}
						<dt class="dt-variable">Variable aspect ratio</dt>
						<dd class="mono variable">{variableRatios}</dd>
					{:else if aspectRatio(detail.source_width, detail.source_height)}
						<dt>Aspect ratio</dt>
						<dd class="mono">{aspectRatio(detail.source_width, detail.source_height)}:1</dd>
					{/if}
				</dl>
				{#if detail.ineligible_reason}
					<div class="note warn">{detail.ineligible_reason}</div>
				{/if}
				{#if detail.error}
					<div class="note err">{detail.error}</div>
				{/if}
			</div>

			<div class="preview">
				{#if stage === 'candidate'}
					<div class="ptitle">Suspected frame · unanalyzed</div>
					<div class="frame unanalyzed">
						<span class="ph">? awaiting frame analysis</span>
					</div>
					<div class="note">
						Resolution scan flagged this file. Run frame analysis to confirm and measure exact crop
						values before applying any fix.
					</div>
				{:else if stage === 'clean'}
					<LetterboxFrame
						src={beforeUrl}
						alt="cleared frame"
						label="Cleared — sample frame"
						placeholder="No preview available"
					/>
				{:else if detail.preview_urls}
					<img class="frame" src={detail.preview_urls.before} alt="before crop" loading="lazy" />
					<img class="frame good" src={detail.preview_urls.after} alt="after crop" loading="lazy" />
				{:else}
					<div class="frame unanalyzed"><span class="ph">No preview available</span></div>
				{/if}
			</div>

			<div class="actions">
				<div class="alabel">Actions</div>
				{#if stage === 'candidate'}
					<button class="btn-gold" onclick={onAnalyzeAll} disabled={analyzing}>
						<Icon name="refresh" size={15} /> Analyze all candidates
					</button>
					<button class="btn-sec" disabled={actionBusy || detecting} onclick={startDetection}>
						{detecting ? 'Analyzing…' : 'Analyze this film only'}
					</button>
					<button
						class="btn-ghost"
						disabled={busy}
						onclick={() => run(() => ignoreLetterbox(fetch, id!), 'Marked as not letterboxed')}
					>
						Skip · mark as not LB
					</button>
				{:else if stage === 'clean'}
					<div class="note">
						No fix needed.{#if detail.status === 'variable_unsafe'}
							Variable aspect ratio — unsafe to crop.{/if}
					</div>
					{#if detail.variable_ar_note}
						<div class="note">{detail.variable_ar_note}</div>
					{/if}
					{#if detail.detect_method || detectedAtLabel}
						<div class="note">
							{#if detail.detect_method}<span class="mono">{detail.detect_method}</span>{/if}
							{#if detail.detect_method && detectedAtLabel}
								·
							{/if}
							{#if detectedAtLabel}last analyzed {detectedAtLabel}{/if}
						</div>
					{/if}
					<button class="btn-sec" disabled={actionBusy || detecting} onclick={startDetection}>
						{detecting ? 'Analyzing…' : 'Re-detect'}
					</button>
					<button
						class="btn-gold"
						disabled={actionBusy || detecting}
						onclick={startThoroughDetection}
					>
						{detecting ? 'Analyzing…' : 'Re-analyze (thorough)'}
					</button>
				{:else}
					<button class="btn-sec" disabled={actionBusy || detecting} onclick={startDetection}>
						{detecting ? 'Analyzing…' : 'Re-detect'}
					</button>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	/* Rendered flush inside the fixed inspector pane — the pane provides the
	   surface + scroll; the panel keeps only a stage-color top accent. */
	.panel {
		border: none;
		border-top: 2px solid var(--panel-accent, var(--line));
		border-radius: 0;
		background: transparent;
		padding: 16px 14px 20px;
		margin-bottom: 0;
	}
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 10px;
		padding: 48px 24px;
		color: var(--faint);
		font-size: 13px;
		text-align: center;
	}
	.empty.err {
		color: var(--bad);
	}

	/* Vertical inspector layout (candidate / clean / other) */
	.grid {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	/* Two-row grid for detected / preview / processed: meta sits to the left of
	   its image, shrinking the previews instead of stacking everything full-width.
	   Actions span both columns in a third row at the bottom. */
	.grid-det {
		display: grid;
		grid-template-columns: 200px 1fr;
		gap: 14px 20px;
	}
	.meta-top {
		grid-column: 1;
		grid-row: 1;
	}
	.frame-cell {
		grid-column: 2;
		min-width: 0;
	}
	.frame-cell.before {
		grid-row: 1;
	}
	.meta-bot {
		grid-column: 1;
		grid-row: 2;
	}
	.frame-cell.after {
		grid-row: 2;
	}
	.det-actions {
		grid-column: 1 / -1;
		grid-row: 3;
	}

	/* meta shared */
	.badge-row {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 10px;
	}
	.badge {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--c, var(--muted));
		padding: 3px 8px;
		border: 1px solid var(--line2);
		border-radius: 99px;
	}
	h3 {
		margin: 0;
		font-size: 17px;
		font-weight: 650;
	}
	.year {
		font-size: 12px;
		color: var(--muted);
		margin-top: 2px;
		margin-bottom: 14px;
	}
	.method-tag {
		display: inline-block;
		font-size: 10.5px;
		color: var(--faint);
		border: 1px solid var(--line);
		border-radius: 4px;
		padding: 2px 7px;
		margin-top: 10px;
	}
	.conf-btn {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		background: transparent;
		border: none;
		padding: 0;
		font-size: 13px;
		font-weight: 600;
		text-transform: capitalize;
		cursor: pointer;
	}
	.caret {
		font-size: 9px;
		opacity: 0.6;
	}
	.conf-expand {
		margin-top: 8px;
		padding: 8px 10px;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.ce-summary {
		font-size: 10.5px;
		color: var(--muted);
		margin-bottom: 4px;
	}
	.ce-hint {
		color: var(--faint);
		font-size: 10px;
	}
	.ce-row {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 11px;
		width: 100%;
		padding: 3px 4px;
		border-radius: 4px;
		border: none;
		background: transparent;
		text-align: left;
		cursor: pointer;
		color: var(--text);
	}
	.ce-row:hover {
		background: var(--panel2);
	}
	.ce-active {
		background: var(--ink2);
		outline: 1px solid var(--line2);
	}
	.ce-min {
		color: var(--faint);
		min-width: 36px;
	}
	.ce-val {
		color: var(--text);
		flex: 1;
	}
	.ce-dot {
		font-size: 11px;
		flex-shrink: 0;
	}
	.ce-err {
		font-size: 10px;
		color: var(--bad);
		flex: 1;
	}
	dl {
		display: grid;
		grid-template-columns: 1fr;
		gap: 0;
		margin: 0;
	}
	dt {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
		font-weight: 600;
		margin-top: 8px;
	}
	dd {
		margin: 2px 0 0;
		font-size: 13px;
		color: var(--text);
	}
	dd.gold {
		color: var(--gold);
	}
	dt.dt-variable {
		color: var(--gold);
	}
	dd.variable {
		color: var(--gold);
		font-weight: 600;
	}
	.crop-note {
		font-size: 10.5px;
		font-weight: 400;
		color: var(--muted);
		text-transform: none;
		letter-spacing: 0;
	}

	/* preview (used by single-row layout) */
	.preview {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.ptitle {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.frame {
		width: 100%;
		aspect-ratio: 16 / 9;
		object-fit: cover;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: linear-gradient(150deg, #1a1410, #0a0806);
	}
	.frame.good {
		border-color: color-mix(in srgb, var(--good) 50%, var(--line2));
		/* After-crop image has a wider AR than 16:9 — let its natural height show */
		aspect-ratio: auto;
		height: auto;
		object-fit: initial;
	}
	.frame.unanalyzed {
		display: flex;
		align-items: center;
		justify-content: center;
		border-style: dashed;
		background: repeating-linear-gradient(
			45deg,
			var(--ink2),
			var(--ink2) 10px,
			var(--panel) 10px,
			var(--panel) 20px
		);
	}
	.ph {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--gold);
	}
	/* actions */
	.actions {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.alabel {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.fix-card {
		display: block;
		width: 100%;
		text-align: left;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		padding: 10px 12px;
		background: transparent;
		color: inherit;
		cursor: pointer;
	}
	.fix-card:hover {
		border-color: var(--faint);
	}
	.fix-card.active {
		border-color: var(--gold);
		background: var(--gold-soft);
	}
	.fix-head {
		font-size: 12.5px;
		font-weight: 600;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.fix-on {
		font-size: 10px;
		color: var(--on-gold);
		background: var(--gold);
		padding: 1px 6px;
		border-radius: 99px;
		margin-left: auto;
	}
	.fix-body {
		font-size: 11px;
		color: var(--muted);
		margin-top: 4px;
		line-height: 1.4;
	}
	.settings {
		display: flex;
		flex-direction: column;
		gap: 9px;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		padding: 11px 12px;
		background: var(--ink2);
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 11px;
		color: var(--muted);
	}
	.field input[type='number'],
	.field select {
		font-size: 12.5px;
		padding: 5px 7px;
		border: 1px solid var(--line2);
		border-radius: 6px;
		background: var(--panel);
		color: var(--text);
		font-family: var(--font-mono);
	}
	.field-row {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px;
	}
	.field.checkbox {
		flex-direction: row;
		align-items: center;
		gap: 7px;
	}
	.adv-toggle {
		align-self: flex-start;
		background: transparent;
		border: none;
		color: var(--info);
		font-size: 11.5px;
		font-weight: 600;
		cursor: pointer;
		padding: 2px 0;
	}
	.profile-cards {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.profile-card {
		display: block;
		width: 100%;
		text-align: left;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		padding: 10px 12px;
		background: transparent;
		color: inherit;
		cursor: pointer;
		transition:
			border-color 0.15s,
			background 0.15s;
	}
	.profile-card:hover {
		border-color: var(--faint);
	}
	.profile-card.active {
		border-color: var(--gold);
		background: var(--gold-soft);
	}
	.profile-head {
		font-size: 12.5px;
		font-weight: 600;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.profile-icon {
		font-size: 14px;
	}
	.profile-desc {
		font-size: 11px;
		color: var(--muted);
		margin-top: 3px;
		line-height: 1.4;
	}
	.profile-vals {
		font-size: 10.5px;
		color: var(--faint);
		margin-top: 4px;
	}
	.enc-summary {
		margin-top: 2px;
		padding-top: 8px;
		border-top: 1px solid var(--line2);
	}
	.applied-card {
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		padding: 10px 12px;
	}
	.big {
		font-size: 16px;
		margin-top: 3px;
	}
	.gold {
		color: var(--gold);
	}
	.mono {
		font-family: var(--font-mono);
	}

	/* notes */
	.note {
		font-size: 11.5px;
		color: var(--muted);
		line-height: 1.5;
		padding: 9px 11px;
		background: var(--ink2);
		border-left: 3px solid var(--line2);
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
	}
	.note.good {
		border-left-color: var(--good);
		color: var(--good);
	}
	.note.warn {
		border-left-color: var(--warn);
		color: var(--warn);
	}
	.note.err {
		border-left-color: var(--bad);
		color: var(--bad);
	}

	/* buttons */
	.btn-gold,
	.btn-sec,
	.btn-ghost {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 7px;
		padding: 9px 14px;
		border-radius: 8px;
		font-size: 13px;
		font-weight: 600;
		white-space: nowrap;
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
		font-weight: 500;
	}
	.btn-ghost {
		border: 1px solid transparent;
		background: transparent;
		color: var(--muted);
		font-weight: 500;
	}
	.btn-ghost:hover {
		color: var(--text);
		background: var(--panel2);
	}
	.btn-gold:disabled,
	.btn-sec:disabled,
	.btn-ghost:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
