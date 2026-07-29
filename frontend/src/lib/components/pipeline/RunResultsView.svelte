<script lang="ts">
	import { onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import ScoreBar from '$lib/components/ScoreBar.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import PosterCandidateTile from '$lib/components/PosterCandidateTile.svelte';
	import PosterStack from '$lib/components/PosterStack.svelte';
	import PosterRankPanel from '$lib/components/PosterRankPanel.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { getRunResults, markOcrFalseAcceptance, markOcrFalseRejection } from '$lib/api/pipeline';
	import { ApiError } from '$lib/api/client';
	import { submitFeedback, undoFeedback } from '$lib/api/feedback';
	import {
		ocrConfidenceLabel,
		ocrInspectorPanel,
		ocrRegionLabel,
		rejectionTag
	} from '$lib/pipeline/ocr-display';
	import { rejectAllCopy } from '$lib/pipeline/review-copy';
	import { toast } from '$lib/toast';
	import { gradientFor } from '$lib/display';
	import type {
		CandidateView,
		OcrLabelRunState,
		RankItem,
		RunResults,
		RunResultsResponse,
		StackView
	} from '$lib/api/types';
	type RunResultsViewData = {
		run: RunResultsResponse | null;
		runId: string;
		debugMode: boolean;
		ocrLabelState: OcrLabelRunState;
		error: string | null;
	};

	let {
		data,
		backHref = '/pipeline/movies',
		backLabel = 'Movie posters',
		onreviewed
	}: {
		data: RunResultsViewData;
		backHref?: string;
		backLabel?: string;
		/** Fired once a review decision is recorded, so a host with its own queue
		 *  (the TV series rail) can drop this run and advance. */
		onreviewed?: (info: {
			runId: string;
			action: 'approve' | 'override' | 'reject_all';
		}) => void | Promise<void>;
	} = $props();

	// svelte-ignore state_referenced_locally
	let run = $state<RunResultsResponse | null>(data.run);

	const results = $derived(run && 'ranked' in run ? (run as RunResults) : null);
	const running = $derived(run != null && !('ranked' in run));
	const autoPick = $derived(results?.auto_pick ?? null);
	const rejectCopy = $derived(
		rejectAllCopy({
			mediaType: results?.media_type,
			seasonNumber: results?.subject?.season_number
		})
	);
	const debugMode = $derived(Boolean(data.debugMode));
	const tileSelectable = $derived(Boolean(debugMode || !results?.reviewed));
	const REQUIRED_OCR_SNAPSHOT_KEYS = [
		'device',
		'workers',
		'detail_passes',
		'max_residual_boxes',
		'max_residual_area_fraction',
		'mode',
		'require_title',
		'accept_no_text_fallback',
		'allow_title',
		'allow_director',
		'allow_studio',
		'allow_rating',
		'allow_tagline',
		'confidence_threshold',
		'strip_confidence_threshold',
		'bottom_confidence_threshold',
		'fuzzy_cutoff',
		'title_proximity_pixels',
		'residual_significant_area_fraction',
		'residual_significant_width_fraction',
		'enhance_retry'
	] as const;
	const hasFullOcrSnapshot = $derived.by(() => {
		const ocr = results?.config_snapshot?.ocr;
		if (!ocr || typeof ocr !== 'object') return false;
		return REQUIRED_OCR_SNAPSHOT_KEYS.every((key) => key in (ocr as Record<string, unknown>));
	});
	const canCaptureOcrLabels = $derived(debugMode && hasFullOcrSnapshot);
	// svelte-ignore state_referenced_locally
	const initialOcrLabels = data.ocrLabelState?.labels ?? {
		false_rejection: [],
		false_acceptance: []
	};
	let ocrLabelState = $state({
		false_rejection: [...initialOcrLabels.false_rejection],
		false_acceptance: [...initialOcrLabels.false_acceptance]
	});
	const falseRejectionSet = $derived(new Set(ocrLabelState.false_rejection));

	const SHORT: Record<string, string> = {
		sha256: 'SHA-256',
		resolution: 'Resolution',
		style: 'Style',
		ocr: 'OCR',
		phash: 'pHash',
		fan_junk: 'Fan-junk',
		errored: 'Errored'
	};

	const stageTabs = $derived.by(() => {
		if (!results) return [];
		const t = [{ id: 'ranked', label: 'Ranked', count: rankedByMethod.length }];
		for (const g of results.rejected_by_stage) {
			if (g.count > 0) t.push({ id: g.stage, label: SHORT[g.stage] ?? g.label, count: g.count });
		}
		return t;
	});

	let activeStage = $state('ranked');

	/** Stack ranking method — recomputed client-side from the archive data. */
	type StackMethod = 'robust' | 'max' | 'mean';
	let stackMethod = $state<StackMethod>('robust');

	/** Re-rank stacks using the selected aggregation method.
	 *  Returns a new ranked array and stacks with updated stack_rank values. */
	const rankedByMethod = $derived.by<CandidateView[]>(() => {
		const r = results?.ranked;
		const s = results?.stacks;
		if (!r?.length || !s?.length) return r ?? [];
		if (stackMethod === 'robust') return r; // default — no change needed

		// Build groups keyed by stack_id.
		const groups: Record<number, CandidateView[]> = {};
		for (const c of r) {
			if (c.stack_id == null) continue;
			const g = groups[c.stack_id] ?? [];
			g.push(c);
			groups[c.stack_id] = g;
		}
		const groupEntries = Object.entries(groups);
		if (groupEntries.length === 0) return r;

		// Compute aggregate score per group using the selected method.
		const scored: { id: number; score: number; members: CandidateView[] }[] = [];
		for (const [id, members] of groupEntries) {
			const scores = members.map((c) => c.final_score ?? 0).sort((a, b) => b - a);
			let agg: number;
			if (stackMethod === 'max') {
				agg = scores[0];
			} else {
				// mean
				agg = scores.reduce((a, b) => a + b, 0) / scores.length;
			}
			scored.push({ id: Number(id), score: agg, members });
		}
		// Sort groups by aggregate score descending, then by size.
		scored.sort((a, b) => b.score - a.score || b.members.length - a.members.length);

		// Reassign stack_rank and stack_score, rebuild the flat ranked list.
		const out: CandidateView[] = [];
		let rank = 0;
		for (const group of scored) {
			rank++;
			for (const c of group.members) {
				out.push({ ...c, stack_rank: rank, stack_score: group.score });
			}
		}
		return out;
	});

	/** Re-rank stacks for the stacks sidebar (same method). */
	const stacksByMethod = $derived.by<StackView[]>(() => {
		const s = results?.stacks;
		if (!s?.length) return [];
		if (stackMethod === 'robust') return s;

		const ranked = rankedByMethod;
		const groups: Record<number, CandidateView[]> = {};
		for (const c of ranked) {
			if (c.stack_id == null) continue;
			const g = groups[c.stack_id] ?? [];
			g.push(c);
			groups[c.stack_id] = g;
		}
		const ordered = Object.entries(groups)
			.map(([id, members]) => {
				const rep = members[0];
				return {
					stack_rank: rep.stack_rank ?? 0,
					stack_id: Number(id),
					label: String(rep.stack_rank ?? ''),
					size: rep.stack_size ?? members.length,
					stack_score: rep.stack_score ?? null,
					representative: rep,
					members
				};
			})
			.sort((a, b) => a.stack_rank - b.stack_rank);
		return ordered;
	});

	/** Persisted toggle: 'flat' = visual card stacks in flat grid; 'sectioned' = Design headers with borders. */
	let viewMode = $state<'flat' | 'sectioned'>(
		(typeof localStorage !== 'undefined' &&
			(localStorage.getItem('marquee:pipeline:stackView') as 'flat' | 'sectioned' | null)) ||
			'flat'
	);
	$effect(() => {
		if (typeof localStorage !== 'undefined') {
			localStorage.setItem('marquee:pipeline:stackView', viewMode);
		}
	});

	/** Which stack_ids are currently expanded (flat view only). */
	let expandedStackIds = $state<number[]>([]);
	let ocrLabelBusy = $state<Record<string, boolean>>({});
	let batchFalseRejectionMode = $state(false);
	let batchFalseRejectionBusy = $state(false);
	let selectedFalseRejections = $state<string[]>([]);

	function toggleStack(stackId: number) {
		expandedStackIds = expandedStackIds.includes(stackId)
			? expandedStackIds.filter((id) => id !== stackId)
			: [...expandedStackIds, stackId];
	}

	/** True when at least one stack is expanded in flat view. */
	const anyExpanded = $derived(expandedStackIds.length > 0);

	function expandAll() {
		if (!results?.stacks) return;
		expandedStackIds = stacksByMethod.map((stack) => stack.stack_id);
	}

	function collapseAll() {
		expandedStackIds = [];
	}

	/** Deterministic palette for expanded stack grouping accents. */
	const STACK_PALETTE = [
		'#6366f1',
		'#f59e0b',
		'#10b981',
		'#ef4444',
		'#8b5cf6',
		'#06b6d4',
		'#f97316',
		'#84cc16'
	];
	function stackColor(stackId: number): string {
		return STACK_PALETTE[Math.abs(stackId) % STACK_PALETTE.length];
	}

	function ocrLabelBusyKey(
		labelKind: 'false_rejection' | 'false_acceptance',
		origFilename: string
	): string {
		return `${labelKind}:${origFilename}`;
	}

	function isOcrLabelBusy(
		labelKind: 'false_rejection' | 'false_acceptance',
		origFilename: string
	): boolean {
		return Boolean(ocrLabelBusy[ocrLabelBusyKey(labelKind, origFilename)]);
	}

	function saveOcrLabels(
		labelKind: 'false_rejection' | 'false_acceptance',
		origFilenames: string[]
	) {
		const next = [...ocrLabelState[labelKind]];
		for (const origFilename of origFilenames) {
			if (!next.includes(origFilename)) {
				next.push(origFilename);
			}
		}
		ocrLabelState = { ...ocrLabelState, [labelKind]: next };
	}

	function toggleFalseRejectionSelection(origFilename: string) {
		selectedFalseRejections = selectedFalseRejections.includes(origFilename)
			? selectedFalseRejections.filter((name) => name !== origFilename)
			: [...selectedFalseRejections, origFilename];
	}

	const currentPosters = $derived.by<CandidateView[]>(() => {
		if (!results) return [];
		const posters =
			activeStage === 'ranked'
				? rankedByMethod
				: (results.rejected_by_stage.find((g) => g.stage === activeStage)?.posters ?? []);
		if (activeStage !== 'ocr' || falseRejectionSet.size === 0) return posters;
		return [...posters].sort(
			(a, b) =>
				Number(falseRejectionSet.has(a.orig_filename)) -
				Number(falseRejectionSet.has(b.orig_filename))
		);
	});

	$effect(() => {
		if (
			(!debugMode || activeStage !== 'ocr') &&
			(batchFalseRejectionMode || selectedFalseRejections.length > 0)
		) {
			batchFalseRejectionMode = false;
			selectedFalseRejections = [];
		}
	});

	/** Group ALL posters that share a stack into arrays (not just consecutive ones).
	 *  Groups appear at the position of their highest-ranked member; within each
	 *  group posters are ordered by stack_pos (A, B, C…).  Only groups of ≥2
	 *  become visual stacks; singles stay as individual tiles. */
	const groupedRanked = $derived.by<Array<CandidateView | CandidateView[]>>(() => {
		const r = rankedByMethod;
		const hasStacks = !!results?.stacks?.length;
		if (!hasStacks) return r;

		// Collect every stack's members into a map, preserving rank order.
		const byStack: Record<number, CandidateView[]> = {};
		for (const c of r) {
			if (c.stack_id != null && (c.stack_size ?? 1) > 1) {
				const group = byStack[c.stack_id] ?? [];
				group.push(c);
				byStack[c.stack_id] = group;
			}
		}
		// Sort each group by stack_pos (A=1, B=2, …).
		for (const group of Object.values(byStack)) {
			group.sort((a, b) => (a.stack_pos ?? 0) - (b.stack_pos ?? 0));
		}

		// Rebuild in original rank order, emitting each stack only once (at its
		// first member's position).
		const seen: number[] = [];
		const out: Array<CandidateView | CandidateView[]> = [];
		for (const c of r) {
			const sid = c.stack_id;
			if (sid != null && (c.stack_size ?? 1) > 1) {
				if (seen.includes(sid)) continue;
				seen.push(sid);
				out.push(byStack[sid]!);
			} else {
				out.push(c);
			}
		}
		return out;
	});

	function contribSegments(c: Record<string, number> | null) {
		if (!c) return [];
		const entries = Object.entries(c).filter(([, v]) => Number.isFinite(v));
		const max = Math.max(1e-6, ...entries.map(([, v]) => Math.abs(v)));
		return entries
			.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
			.slice(0, 8)
			.map(([k, v]) => ({ value: Math.abs(v) / max, label: k, signed: v }));
	}

	async function reload() {
		try {
			run = await getRunResults(fetch, data.runId);
		} catch {
			/* keep the running view; the poll will retry */
		}
	}
	onDestroy(() => {
		if (confirmTimer) clearTimeout(confirmTimer);
	});

	// ── Inspector + feedback (inspect / approve / override / reject) ──────────────
	let inspectedPoster = $state<CandidateView | null>(null);
	let deploy = $state(true);
	let busy = $state(false);
	let scopeActive = $state(false);
	let rejectOpen = $state(false);
	let lastEventId = $state<string | null>(null);
	let confirmingApprove = $state(false);
	let confirmTimer: ReturnType<typeof setTimeout> | null = null;
	let lightboxOpen = $state(false);

	/** The poster the hero inspects — defaults to the auto-pick until the user
	 *  clicks another tile. */
	const inspected = $derived(inspectedPoster ?? autoPick);
	const inspectIsAuto = $derived(
		!!inspected && !!autoPick && inspected.orig_filename === autoPick.orig_filename
	);
	const inspectedRejectionTag = $derived(
		inspected &&
			(inspected.rejection_label ||
				inspected.rejection_explanation ||
				inspected.rejection_reason ||
				inspected.gate_reason)
			? rejectionTag(inspected)
			: null
	);
	const inspectedOcrPanel = $derived(inspected ? ocrInspectorPanel(inspected) : null);
	const inspectDebugLabelKind = $derived.by<'false_rejection' | 'false_acceptance' | null>(() => {
		if (!inspected || !debugMode) return null;
		if (inspected.rank != null) return 'false_acceptance';
		return activeStage === 'ocr' ? 'false_rejection' : null;
	});
	const inspectDebugBusy = $derived.by(() =>
		inspected && inspectDebugLabelKind
			? isOcrLabelBusy(inspectDebugLabelKind, inspected.orig_filename)
			: false
	);
	const inspectAlreadyMarked = $derived.by(() =>
		inspected && inspectDebugLabelKind
			? ocrLabelState[inspectDebugLabelKind].includes(inspected.orig_filename)
			: false
	);

	/** Approve/Override uses a lightweight two-step inline confirm in the hero
	 *  (the modal pick dialog is gone). First click arms; it auto-disarms after 3s. */
	function startConfirm() {
		confirmingApprove = true;
		if (confirmTimer) clearTimeout(confirmTimer);
		confirmTimer = setTimeout(() => (confirmingApprove = false), 3000);
	}
	function cancelConfirm() {
		confirmingApprove = false;
		if (confirmTimer) {
			clearTimeout(confirmTimer);
			confirmTimer = null;
		}
	}

	function handlePosterSelect(candidate: CandidateView) {
		if (batchFalseRejectionMode && activeStage === 'ocr') {
			toggleFalseRejectionSelection(candidate.orig_filename);
			return;
		}
		inspectedPoster = candidate;
		cancelConfirm();
	}

	function selectAllVisibleFalseRejections() {
		selectedFalseRejections = currentPosters
			.filter((candidate) => !falseRejectionSet.has(candidate.orig_filename))
			.map((candidate) => candidate.orig_filename);
	}

	async function confirmPick() {
		if (!inspected || !results) return;
		busy = true;
		try {
			const action = inspectIsAuto ? 'approve' : 'override';
			const res = await submitFeedback(fetch, {
				run_id: results.run_id,
				action,
				selected_filename: inspected.orig_filename,
				deploy
			});
			lastEventId = res.event_id;
			const where = res.deployment_job ? ' · deployment queued' : '';
			toast(`Poster selected${where}`, 'good');
			cancelConfirm();
			await reload();
			await onreviewed?.({ runId: results.run_id, action });
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Selection failed', 'bad');
		} finally {
			busy = false;
		}
	}

	async function confirmReject() {
		if (!results) return;
		busy = true;
		try {
			const res = await submitFeedback(fetch, { run_id: results.run_id, action: 'reject_all' });
			lastEventId = res.event_id;
			toast('Auto-pick rejected — all candidates dismissed', 'info');
			rejectOpen = false;
			await reload();
			await onreviewed?.({ runId: results.run_id, action: 'reject_all' });
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Reject failed', 'bad');
		} finally {
			busy = false;
		}
	}

	async function undoLast() {
		if (!lastEventId) return;
		try {
			await undoFeedback(fetch, lastEventId);
			toast('Reverted', 'good');
			lastEventId = null;
			await reload();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Undo failed', 'bad');
		}
	}

	async function markPoster(
		candidate: CandidateView,
		labelKind: 'false_rejection' | 'false_acceptance'
	) {
		if (!results) return;
		if (!canCaptureOcrLabels) {
			toast('This run predates the OCR snapshot upgrade — re-run it first', 'info');
			return;
		}

		const busyKey = ocrLabelBusyKey(labelKind, candidate.orig_filename);
		ocrLabelBusy = { ...ocrLabelBusy, [busyKey]: true };
		try {
			const response =
				labelKind === 'false_rejection'
					? await markOcrFalseRejection(fetch, {
							run_id: results.run_id,
							orig_filename: candidate.orig_filename
						})
					: await markOcrFalseAcceptance(fetch, {
							run_id: results.run_id,
							orig_filename: candidate.orig_filename
						});
			toast(
				`${labelKind === 'false_rejection' ? 'False rejection' : 'False acceptance'} captured`,
				'good'
			);
			saveOcrLabels(labelKind, [candidate.orig_filename]);
			if (response.missing_artifacts.length > 0) {
				toast(
					`Capture completed with missing artifacts: ${response.missing_artifacts.join(', ')}`,
					'info',
					5000
				);
			}
		} catch (error) {
			toast(describeApiError(error, 'OCR label capture failed'), 'bad');
		} finally {
			ocrLabelBusy = Object.fromEntries(
				Object.entries(ocrLabelBusy).filter(([key]) => key !== busyKey)
			);
		}
	}

	async function markSelectedFalseRejections() {
		if (!results || selectedFalseRejections.length === 0 || batchFalseRejectionBusy) return;
		batchFalseRejectionBusy = true;
		const captured: string[] = [];
		const warnings: string[] = [];
		const failures: Array<{ origFilename: string; detail: string }> = [];
		for (const origFilename of selectedFalseRejections) {
			const busyKey = ocrLabelBusyKey('false_rejection', origFilename);
			ocrLabelBusy = { ...ocrLabelBusy, [busyKey]: true };
			try {
				const response = await markOcrFalseRejection(fetch, {
					run_id: results.run_id,
					orig_filename: origFilename
				});
				captured.push(origFilename);
				if (response.missing_artifacts.length > 0) {
					warnings.push(`${origFilename}: ${response.missing_artifacts.join(', ')}`);
				}
			} catch (error) {
				failures.push({
					origFilename,
					detail: describeApiError(error, `Failed to mark ${origFilename}`)
				});
			} finally {
				ocrLabelBusy = Object.fromEntries(
					Object.entries(ocrLabelBusy).filter(([key]) => key !== busyKey)
				);
			}
		}
		if (captured.length > 0) {
			saveOcrLabels('false_rejection', captured);
			toast(
				`Marked ${captured.length} poster${captured.length === 1 ? '' : 's'} as OCR false rejection`,
				'good'
			);
		}
		if (warnings.length > 0) {
			toast(`Some captures were saved with missing artifacts`, 'info', 5000);
		}
		if (failures.length > 0) {
			toast(
				`Failed to mark ${failures.length} poster${failures.length === 1 ? '' : 's'} as false rejection`,
				'bad',
				6000
			);
			selectedFalseRejections = failures.map((failure) => failure.origFilename);
		} else {
			selectedFalseRejections = [];
			batchFalseRejectionMode = false;
		}
		batchFalseRejectionBusy = false;
	}

	function describeApiError(error: unknown, fallback: string): string {
		if (error instanceof ApiError) {
			const detail =
				error.body &&
				typeof error.body === 'object' &&
				'detail' in error.body &&
				typeof error.body.detail === 'string'
					? error.body.detail
					: null;
			return detail ?? error.message;
		}
		return error instanceof Error ? error.message : fallback;
	}

	// ── Feedback buckets (Favorites/Hate/Neutral → canonical residual evidence) ──
	let rankMode = $state(false);

	/** Rankable units: one per design stack (members move together) when stacks
	 *  exist, else one per ranked poster. Mirrors the active stack method. */
	const rankItems = $derived.by<RankItem[]>(() => {
		if (!results) return [];
		if (stacksByMethod.length) {
			return stacksByMethod.map((st) => ({
				key: String(st.stack_id),
				posterUrl: st.representative.poster_url,
				label: `Design ${st.stack_rank}`,
				score: st.stack_score,
				filenames: st.members.map((m) => m.orig_filename)
			}));
		}
		return rankedByMethod.map((c) => ({
			key: c.orig_filename,
			posterUrl: c.poster_url,
			label: c.rank != null ? `#${c.rank}` : c.orig_filename,
			score: c.final_score,
			filenames: [c.orig_filename]
		}));
	});

	function onRanked(eventId: string) {
		rankMode = false;
		lastEventId = eventId;
		void reload();
	}

	function onWindowKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && lightboxOpen) lightboxOpen = false;
	}
</script>

<svelte:window onkeydown={onWindowKeydown} />

<div class="crumb">
	<a href={backHref}>{backLabel}</a><span>/</span><span>Run {data.runId.slice(0, 8)}</span>
</div>

{#if data.error || !run}
	<div class="errstate">
		<Icon name="pipeline" size={40} stroke={1} />
		<strong>Could not load run</strong>
		<span>{data.error ?? 'Unknown error'}</span>
		<button onclick={() => goto(backHref)}>← Back to {backLabel}</button>
	</div>
{:else if running}
	<div class="running-wrap">
		<h2>{results?.movie?.title ?? 'Pipeline run'}</h2>
		<FeatureActivityPanel
			scopeKey={`feature:pipeline:run:${data.runId}`}
			query={{
				feature_area: 'ai_posters',
				types: ['poster_pipeline'],
				correlation_id: data.runId
			}}
			jobIds={[data.runId]}
			bind:active={scopeActive}
			heading="Poster selection activity"
			onSettled={reload}
		/>
		<p class="run-hint">Live progress — results appear here as soon as the run finishes.</p>
	</div>
{:else if results}
	<!-- ── Header / inspector hero ── -->
	<div class="hero">
		<div
			class="hero-poster"
			style="--c0:{gradientFor(results.movie.title ?? '?')[0]}; --c1:{gradientFor(
				results.movie.title ?? '?'
			)[1]}"
		>
			{#if inspected}
				<img src={inspected.poster_url} alt="Inspected poster" />
				<button
					class="maximize-btn"
					title="Maximize"
					aria-label="Maximize poster"
					onclick={() => (lightboxOpen = true)}
				>
					<Icon name="maximize" size={16} />
				</button>
			{:else}
				<div class="no-pick">No rankable candidate</div>
			{/if}
		</div>
		<div class="hero-body">
			<div class="hero-top">
				<div>
					<div class="hero-title">{results.movie.title ?? 'Unknown'}</div>
					<div class="hero-sub">
						<span class="scorer-tag">{results.scorer ?? 'scored'}</span>
						{#if results.reviewed}
							<span class="reviewed"><StatusDot tone="good" size={6} /> Reviewed</span>
							{#if lastEventId}<button class="undo" onclick={undoLast}>Undo</button>{/if}
						{/if}
					</div>
				</div>
			</div>

			{#if inspected}
				<div class="auto-line">
					{#if inspectIsAuto}
						<span class="auto-badge">AUTO-PICK</span>
					{:else}
						<span class="auto-badge inspect">INSPECTING</span>
					{/if}
					{#if inspected.final_score != null}<span class="mono score"
							>{inspected.final_score.toFixed(3)}</span
						>{/if}
				</div>

				<div class="hero-meta">
					{#if inspected.rank != null}
						<span class="meta-chip">Rank {inspected.rank}</span>
					{/if}
					{#if inspected.stack_rank != null}
						<span class="meta-chip"
							>Design {inspected.stack_rank}{#if inspected.stack_label}
								· {inspected.stack_label}{/if}{#if inspected.stack_size && inspected.stack_size > 1}
								(of {inspected.stack_size}){/if}</span
						>
					{/if}
					{#if inspected.rank == null && (inspectedRejectionTag || inspected.stage_reached)}
						<span class="meta-chip low" title={inspected.rejection_explanation ?? undefined}>
							{inspectedRejectionTag ?? `Stopped at ${inspected.stage_reached}`}
						</span>
					{/if}
				</div>

				{#if inspectedOcrPanel?.kind === 'text'}
					{@const evidence = inspectedOcrPanel.evidence}
					<section class="ocr-evidence" aria-label="OCR captured text">
						<div class="ocr-evidence-head">
							<div>
								<span class="ocr-evidence-title">OCR captured</span>
								<span class="ocr-evidence-summary"
									>{evidence.regions.length} detected region{evidence.regions.length === 1
										? ''
										: 's'}</span
								>
							</div>
							<span class="ocr-evidence-state"
								>{evidence.title_matched ? 'Title matched' : 'Title not matched'}</span
							>
						</div>
						<div class="ocr-transcript">
							<span class="ocr-evidence-label">OCR read</span>
							<p class="mono">{evidence.detected_text}</p>
						</div>
						{#if evidence.regions.length > 0}
							<div class="ocr-regions">
								<span class="ocr-evidence-label">Detected regions</span>
								<ul>
									{#each evidence.regions as region, index (`${region.text}:${index}`)}
										{@const confidence = ocrConfidenceLabel(region.confidence)}
										<li>
											<span class="ocr-region-kind">{ocrRegionLabel(region)}</span>
											<code>{region.text}</code>
											{#if confidence}<span class="ocr-confidence mono">{confidence}</span>{/if}
											{#if region.is_significant}
												<span class="ocr-significant">Counts toward rejection</span>
											{/if}
										</li>
									{/each}
								</ul>
							</div>
						{/if}
					</section>
				{:else if inspectedOcrPanel?.kind === 'error'}
					<section class="ocr-evidence error" aria-label="OCR error">
						<span class="ocr-evidence-title">OCR error</span>
						<p class="ocr-error-message mono">{inspectedOcrPanel.message}</p>
					</section>
				{:else if inspectedOcrPanel?.kind === 'unavailable'}
					<section class="ocr-evidence unavailable" aria-label="OCR details unavailable">
						<span class="ocr-evidence-title">OCR details unavailable</span>
						<p>{inspectedOcrPanel.message}</p>
					</section>
				{/if}

				{#if inspected.explanations?.length}
					<ul class="explain">
						{#each inspected.explanations.slice(0, 5) as line, i (i)}<li>{line}</li>{/each}
					</ul>
				{/if}
				{#if inspected.contributions}
					<div class="contrib">
						<ScoreBar showLabels segments={contribSegments(inspected.contributions)} />
					</div>
				{/if}

				{#if !results.reviewed}
					<label class="toggle">
						<input type="checkbox" bind:checked={deploy} />
						Deploy to the movie folder now
					</label>
				{/if}

				{#if debugMode && inspectDebugLabelKind && !hasFullOcrSnapshot}
					<p class="pick-debug-note">
						Debug OCR labeling is disabled for this run because its archived OCR snapshot is
						incomplete. Re-run the movie after the snapshot upgrade first.
					</p>
				{:else if debugMode && inspectDebugLabelKind}
					<div class="pick-debug-row">
						<button
							class="pick-debug-btn"
							class:fr={inspectDebugLabelKind === 'false_rejection'}
							class:fa={inspectDebugLabelKind === 'false_acceptance'}
							disabled={inspectDebugBusy}
							onclick={() => inspected && markPoster(inspected, inspectDebugLabelKind)}
						>
							{#if inspectDebugBusy}
								Capturing…
							{:else if inspectDebugLabelKind === 'false_rejection'}
								{inspectAlreadyMarked
									? 'Refresh OCR false rejection capture'
									: 'Mark as OCR false rejection'}
							{:else}
								{inspectAlreadyMarked
									? 'Refresh OCR false acceptance capture'
									: 'Mark as OCR false acceptance'}
							{/if}
						</button>
						<p class="pick-debug-note">
							{inspectAlreadyMarked ? 'Already captured. ' : ''}Saves the poster image and OCR
							diagnostics under <code>data/debug/ocr-labels</code>.
						</p>
					</div>
				{/if}
			{/if}

			<div class="hero-actions">
				{#if confirmingApprove}
					<button class="btn-gold" onclick={confirmPick} disabled={busy}>
						{busy ? 'Working…' : 'Confirm'}
					</button>
					<button class="btn-ghost" onclick={cancelConfirm} disabled={busy}>Cancel</button>
				{:else}
					<button class="btn-gold" onclick={startConfirm} disabled={!inspected || results.reviewed}>
						{inspectIsAuto ? 'Approve auto-pick' : 'Override with this pick'}
					</button>
				{/if}
				<button class="btn-ghost" onclick={() => (rejectOpen = true)} disabled={results.reviewed}>
					Reject all
				</button>
				<button
					class="btn-ghost"
					onclick={() => (rankMode = !rankMode)}
					disabled={results.reviewed || !results.ranked.length}
				>
					{rankMode ? 'Exit ranking' : 'Rank by taste'}
				</button>
			</div>
		</div>
	</div>

	{#if rankMode}
		<PosterRankPanel
			runId={results.run_id}
			items={rankItems}
			disabled={results.reviewed}
			onsubmitted={onRanked}
			oncancel={() => (rankMode = false)}
		/>
	{:else}
		<!-- ── Stage tabs ── -->
		<div class="tabwrap">
			<TabBar tabs={stageTabs} active={activeStage} onSelect={(id) => (activeStage = id)} />
		</div>

		<p class="grid-hint">
			{#if activeStage === 'ranked'}
				{#if stacksByMethod.length}
					<span class="hint-left">
						{#if viewMode === 'flat'}
							Stacked posters share a design — click the stack to fan out all variants. Click any
							poster to choose it.
						{:else}
							Posters are grouped into <strong>stacks</strong> of the same design — variants differ only
							in title position, text, or crop. Designs are ranked by their best few variants, so the
							auto-pick (1A) may not be the single highest-scored poster. Click any poster to choose it.
						{/if}
					</span>
					<span class="hint-toggle">
						<select class="method-select" bind:value={stackMethod} title="Stack ranking method">
							<option value="robust">Robust (top-3 mean)</option>
							<option value="max">Max (best variant)</option>
							<option value="mean">Mean (all variants)</option>
						</select>
						<button
							class="view-btn"
							class:active={viewMode === 'flat'}
							onclick={() => (viewMode = 'flat')}
							title="Flat grid with visual card stacks"
						>
							<svg
								width="14"
								height="14"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="2"
								stroke-linecap="round"
								stroke-linejoin="round"
								><rect x="3" y="3" width="7" height="7" /><rect
									x="14"
									y="3"
									width="7"
									height="7"
								/><rect x="3" y="14" width="7" height="7" /><rect
									x="14"
									y="14"
									width="7"
									height="7"
								/></svg
							>
							Flat
						</button>
						<button
							class="view-btn"
							class:active={viewMode === 'sectioned'}
							onclick={() => (viewMode = 'sectioned')}
							title="Sectioned stacks with Design headers"
						>
							<svg
								width="14"
								height="14"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="2"
								stroke-linecap="round"
								stroke-linejoin="round"
								><rect x="3" y="3" width="18" height="8" rx="1" /><rect
									x="3"
									y="13"
									width="18"
									height="8"
									rx="1"
								/></svg
							>
							Sectioned
						</button>
						{#if viewMode === 'flat'}
							<button
								class="view-btn"
								onclick={() => (anyExpanded ? collapseAll() : expandAll())}
								title={anyExpanded ? 'Collapse all stacks' : 'Expand all stacks'}
							>
								<svg
									width="14"
									height="14"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="2"
									stroke-linecap="round"
									stroke-linejoin="round"
								>
									{#if anyExpanded}
										<polyline points="15 18 9 12 15 6" />
									{:else}
										<polyline points="9 18 15 12 9 6" />
									{/if}
								</svg>
								{anyExpanded ? 'Collapse all' : 'Expand all'}
							</button>
						{/if}
					</span>
				{:else}
					Click any poster to set it as the chosen one — it deploys to the movie folder and trains
					the Key Art Engine.
				{/if}
			{:else}
				Rejected at this stage. Click to override and choose it anyway.
			{/if}
		</p>
		{#if debugMode && !hasFullOcrSnapshot}
			<div class="dev-hint">
				Debug OCR labeling is disabled for this run because its archived OCR snapshot is incomplete.
				Re-run the movie after the snapshot upgrade to capture false rejections or false
				acceptances.
			</div>
		{/if}
		{#if debugMode && activeStage === 'ocr' && hasFullOcrSnapshot}
			<div class="ocr-dev-tools">
				{#if batchFalseRejectionMode}
					<button
						class="view-btn active"
						onclick={markSelectedFalseRejections}
						disabled={selectedFalseRejections.length === 0 || batchFalseRejectionBusy}
					>
						{batchFalseRejectionBusy
							? 'Capturing…'
							: `Mark selected false rejection (${selectedFalseRejections.length})`}
					</button>
					<button
						class="view-btn"
						onclick={selectAllVisibleFalseRejections}
						disabled={batchFalseRejectionBusy}
					>
						Select all visible
					</button>
					<button
						class="view-btn"
						onclick={() => (selectedFalseRejections = [])}
						disabled={selectedFalseRejections.length === 0 || batchFalseRejectionBusy}
					>
						Clear selection
					</button>
					<button
						class="view-btn"
						onclick={() => {
							batchFalseRejectionMode = false;
							selectedFalseRejections = [];
						}}
						disabled={batchFalseRejectionBusy}
					>
						Cancel
					</button>
				{:else}
					<button class="view-btn" onclick={() => (batchFalseRejectionMode = true)}>
						Select multiple false rejections
					</button>
				{/if}
			</div>
		{/if}

		{#if activeStage === 'ranked' && results.stacks?.length}
			{#if viewMode === 'flat'}
				<div class="poster-grid">
					{#each groupedRanked as item (Array.isArray(item) ? (item as CandidateView[])[0].orig_filename : (item as CandidateView).orig_filename)}
						{#if Array.isArray(item)}
							{@const group = item as CandidateView[]}
							{@const sid = group[0].stack_id ?? 0}
							{#if expandedStackIds.includes(sid)}
								{#each group as c (c.orig_filename)}
									<PosterCandidateTile
										candidate={c}
										kind="ranked"
										selectable={tileSelectable}
										inspected={inspected?.orig_filename === c.orig_filename}
										onSelect={handlePosterSelect}
										accent={stackColor(sid)}
										onCollapse={c.stack_pos === 1 ? () => toggleStack(sid) : undefined}
									/>
								{/each}
							{:else}
								<PosterStack
									members={group}
									selectable={tileSelectable}
									inspected={inspected?.orig_filename === group[0].orig_filename}
									onSelect={handlePosterSelect}
									onToggle={() => toggleStack(sid)}
								/>
							{/if}
						{:else}
							<PosterCandidateTile
								candidate={item as CandidateView}
								kind="ranked"
								selectable={tileSelectable}
								inspected={inspected?.orig_filename === (item as CandidateView).orig_filename}
								onSelect={handlePosterSelect}
							/>
						{/if}
					{/each}
				</div>
			{:else}
				<div class="stacks">
					{#each stacksByMethod as st (st.stack_id)}
						<section class="stack">
							<div class="stack-head">
								<span class="stack-name">Design {st.stack_rank}</span>
								{#if st.stack_score != null}
									<span class="stack-score mono">{st.stack_score.toFixed(3)}</span>
								{/if}
								<span class="stack-size">{st.size} variant{st.size === 1 ? '' : 's'}</span>
							</div>
							<div class="poster-grid">
								{#each st.members as c (c.orig_filename)}
									<PosterCandidateTile
										candidate={c}
										kind="ranked"
										selectable={tileSelectable}
										inspected={inspected?.orig_filename === c.orig_filename}
										onSelect={handlePosterSelect}
									/>
								{/each}
							</div>
						</section>
					{/each}
				</div>
			{/if}
		{:else if currentPosters.length === 0}
			<div class="empty-tab">No posters in this group.</div>
		{:else}
			<div class="poster-grid">
				{#each currentPosters as c (c.orig_filename)}
					<PosterCandidateTile
						candidate={c}
						kind={activeStage === 'ranked' ? 'ranked' : 'rejected'}
						selectable={tileSelectable}
						inspected={inspected?.orig_filename === c.orig_filename}
						onSelect={handlePosterSelect}
						selected={batchFalseRejectionMode &&
							activeStage === 'ocr' &&
							selectedFalseRejections.includes(c.orig_filename)}
						badgeText={activeStage === 'ocr' && falseRejectionSet.has(c.orig_filename)
							? 'False rejection'
							: null}
					/>
				{/each}
			</div>
		{/if}
	{/if}
{/if}

<!-- ── Maximized poster lightbox ── -->
{#if lightboxOpen && inspected}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="lightbox" onclick={() => (lightboxOpen = false)}>
		<button class="lightbox-close" aria-label="Close" onclick={() => (lightboxOpen = false)}>
			<Icon name="x" size={22} />
		</button>
		<img src={inspected.poster_url} alt="Maximized poster" />
	</div>
{/if}

<!-- ── Reject-all confirm ── -->
<ConfirmDialog
	open={rejectOpen}
	title={rejectCopy.title}
	message={rejectCopy.message}
	confirmLabel="Reject all"
	tone="bad"
	{busy}
	onConfirm={confirmReject}
	onCancel={() => (rejectOpen = false)}
/>

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

	.running-wrap {
		display: flex;
		flex-direction: column;
		gap: 14px;
		max-width: 560px;
	}
	.running-wrap h2 {
		margin: 0;
		font-size: 18px;
		font-weight: 650;
	}
	.run-hint {
		margin: 0;
		font-size: 12.5px;
		color: var(--faint);
	}

	/* ── Hero ── */
	.hero {
		display: grid;
		grid-template-columns: 168px 1fr;
		gap: 22px;
		align-items: start;
		margin-bottom: 24px;
	}
	@media (max-width: 620px) {
		.hero {
			grid-template-columns: 1fr;
		}
	}
	.hero-poster {
		aspect-ratio: 2 / 3;
		border-radius: var(--radius);
		overflow: hidden;
		border: 1px solid var(--line2);
		background: linear-gradient(165deg, var(--c0), var(--c1));
		box-shadow: 0 8px 26px var(--shadow);
		position: relative;
	}
	.hero-poster img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.no-pick {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		font-size: 12px;
		color: var(--faint);
		text-align: center;
		padding: 12px;
	}
	.hero-body {
		display: flex;
		flex-direction: column;
		gap: 12px;
		min-width: 0;
	}
	.hero-title {
		font-size: 20px;
		font-weight: 700;
		letter-spacing: -0.01em;
	}
	.hero-sub {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-top: 4px;
		font-size: 12px;
		color: var(--muted);
	}
	.scorer-tag {
		font-family: var(--font-mono);
		padding: 1px 7px;
		border-radius: 6px;
		background: var(--panel2);
		border: 1px solid var(--line);
	}
	.reviewed {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		color: var(--good);
	}
	.undo {
		background: none;
		border: none;
		color: var(--info);
		font-size: 12px;
		text-decoration: underline;
		padding: 0;
	}
	.auto-line {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.auto-badge {
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.07em;
		padding: 2px 7px;
		border-radius: 6px;
		background: var(--gold);
		color: var(--on-gold);
	}
	.auto-badge.inspect {
		background: color-mix(in srgb, var(--info) 20%, var(--panel2));
		color: var(--info);
	}
	.score {
		font-size: 14px;
		color: var(--gold);
	}
	.hero-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.meta-chip {
		font-size: 11px;
		padding: 2px 8px;
		border-radius: 999px;
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--muted);
	}
	.meta-chip.low {
		color: var(--low);
		border-color: color-mix(in srgb, var(--low) 40%, transparent);
	}
	.ocr-evidence {
		display: flex;
		flex-direction: column;
		gap: 10px;
		max-width: 640px;
		padding: 11px 12px;
		border: 1px solid color-mix(in srgb, var(--info) 35%, var(--line2));
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--info) 5%, var(--panel));
	}
	.ocr-evidence.error {
		border-color: color-mix(in srgb, var(--bad) 45%, var(--line2));
		background: color-mix(in srgb, var(--bad) 7%, var(--panel));
	}
	.ocr-evidence.unavailable {
		border-color: color-mix(in srgb, var(--warn) 35%, var(--line2));
		background: color-mix(in srgb, var(--warn) 6%, var(--panel));
	}
	.ocr-evidence-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 10px;
	}
	.ocr-evidence-head > div {
		display: flex;
		align-items: baseline;
		gap: 8px;
		min-width: 0;
	}
	.ocr-evidence-title {
		font-size: 12px;
		font-weight: 700;
		color: var(--text);
	}
	.ocr-evidence-summary,
	.ocr-evidence-label,
	.ocr-evidence.unavailable p {
		font-size: 11px;
		color: var(--faint);
	}
	.ocr-evidence-state {
		flex-shrink: 0;
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--info);
	}
	.ocr-transcript {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.ocr-transcript p,
	.ocr-error-message,
	.ocr-evidence.unavailable p {
		margin: 0;
		font-size: 12px;
		line-height: 1.5;
		white-space: pre-wrap;
		word-break: break-word;
	}
	.ocr-transcript p {
		padding: 7px 8px;
		border-radius: 6px;
		background: color-mix(in srgb, var(--ink) 28%, var(--panel2));
		color: var(--text);
	}
	.ocr-error-message {
		color: var(--bad);
	}
	.ocr-regions {
		display: flex;
		flex-direction: column;
		gap: 5px;
	}
	.ocr-regions ul {
		display: flex;
		flex-direction: column;
		gap: 4px;
		max-height: 190px;
		margin: 0;
		padding: 0;
		overflow: auto;
		list-style: none;
	}
	.ocr-regions li {
		display: grid;
		grid-template-columns: minmax(80px, 110px) minmax(0, 1fr) auto auto;
		align-items: baseline;
		gap: 7px;
		padding: 5px 7px;
		border-radius: 6px;
		background: color-mix(in srgb, var(--panel2) 82%, transparent);
		font-size: 11px;
	}
	.ocr-region-kind {
		color: var(--muted);
		font-weight: 650;
	}
	.ocr-regions code {
		min-width: 0;
		overflow-wrap: anywhere;
		color: var(--text);
		font-family: var(--font-mono);
		font-size: 10.5px;
	}
	.ocr-confidence {
		color: var(--faint);
		font-size: 10px;
	}
	.ocr-significant {
		color: var(--warn);
		font-size: 10px;
		white-space: nowrap;
	}
	@media (max-width: 620px) {
		.ocr-evidence-head,
		.ocr-evidence-head > div {
			align-items: flex-start;
			flex-direction: column;
			gap: 3px;
		}
		.ocr-regions li {
			grid-template-columns: minmax(76px, 100px) minmax(0, 1fr) auto;
		}
		.ocr-significant {
			grid-column: 2 / -1;
		}
	}
	.explain {
		margin: 0;
		padding-left: 16px;
		display: flex;
		flex-direction: column;
		gap: 3px;
		font-size: 12.5px;
		color: var(--muted);
		line-height: 1.4;
	}
	.contrib {
		max-width: 320px;
	}
	.hero-actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		margin-top: 2px;
	}
	.maximize-btn {
		position: absolute;
		top: 8px;
		right: 8px;
		z-index: 2;
		width: 30px;
		height: 30px;
		display: grid;
		place-items: center;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: color-mix(in srgb, var(--ink) 55%, transparent);
		color: var(--text);
		cursor: pointer;
		opacity: 0.65;
		transition: opacity 0.14s ease;
		backdrop-filter: blur(3px);
	}
	.hero-poster:hover .maximize-btn,
	.maximize-btn:focus-visible {
		opacity: 1;
	}

	/* ── Maximized poster lightbox ── */
	.lightbox {
		position: fixed;
		inset: 0;
		z-index: 300;
		background: rgba(0, 0, 0, 0.92);
		display: grid;
		place-items: center;
		padding: 4vh 4vw;
		cursor: zoom-out;
	}
	.lightbox img {
		max-width: 90vw;
		max-height: 90vh;
		object-fit: contain;
		border-radius: var(--radius-sm);
		box-shadow: 0 12px 48px rgba(0, 0, 0, 0.6);
	}
	.lightbox-close {
		position: fixed;
		top: 18px;
		right: 18px;
		width: 40px;
		height: 40px;
		display: grid;
		place-items: center;
		border-radius: 999px;
		border: 1px solid var(--line2);
		background: color-mix(in srgb, var(--ink) 60%, transparent);
		color: var(--text);
		cursor: pointer;
	}

	/* ── Tabs + grid ── */
	.tabwrap {
		padding-bottom: 12px;
		border-bottom: 1px solid var(--line);
		margin-bottom: 12px;
	}
	.grid-hint {
		margin: 0 0 14px;
		font-size: 12px;
		color: var(--faint);
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.hint-left {
		flex: 1;
		min-width: 0;
	}
	.hint-toggle {
		display: flex;
		gap: 2px;
		flex-shrink: 0;
	}
	.view-btn {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		padding: 3px 8px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--panel);
		color: var(--muted);
		font-size: 11px;
		font-weight: 550;
		cursor: pointer;
		transition:
			color 0.12s ease,
			border-color 0.12s ease,
			background 0.12s ease;
	}
	.view-btn:hover {
		color: var(--text);
		border-color: var(--line2);
	}
	.view-btn.active {
		color: var(--text);
		border-color: var(--gold-deep);
		background: var(--panel2);
	}
	.method-select {
		padding: 3px 20px 3px 8px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--panel);
		color: var(--muted);
		font-size: 11px;
		font-weight: 550;
		cursor: pointer;
		appearance: none;
		-webkit-appearance: none;
		-moz-appearance: none;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%23999' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 5px center;
		transition:
			color 0.12s ease,
			border-color 0.12s ease;
	}
	.method-select:hover {
		color: var(--text);
		border-color: var(--line2);
	}
	.poster-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
		gap: 14px;
	}
	.dev-hint {
		margin: 0 0 14px;
		padding: 10px 12px;
		border-radius: var(--radius-sm);
		border: 1px solid color-mix(in srgb, var(--warn) 35%, var(--line2));
		background: color-mix(in srgb, var(--warn) 8%, var(--panel));
		color: var(--muted);
		font-size: 12px;
		line-height: 1.45;
	}
	.ocr-dev-tools {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin: 0 0 14px;
	}
	.stacks {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.stack {
		padding: 12px 12px 14px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
	}
	.stack-head {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin-bottom: 10px;
	}
	.stack-name {
		font-size: 13px;
		font-weight: 650;
		color: var(--text);
	}
	.stack-score {
		font-size: 12px;
		color: var(--gold);
	}
	.stack-size {
		font-size: 11.5px;
		color: var(--faint);
		margin-left: auto;
	}
	.empty-tab {
		padding: 40px;
		text-align: center;
		color: var(--faint);
		font-size: 13px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
	}

	/* ── Debug OCR labeling (hosted in the hero) ── */
	.pick-debug-row {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin-top: 4px;
	}
	.pick-debug-btn {
		align-self: flex-start;
		padding: 8px 12px;
		border-radius: 999px;
		border: 1px solid transparent;
		font-size: 12px;
		font-weight: 700;
	}
	.pick-debug-btn.fr {
		background: color-mix(in srgb, var(--warn) 22%, var(--panel2));
		border-color: color-mix(in srgb, var(--warn) 45%, transparent);
		color: var(--text);
	}
	.pick-debug-btn.fa {
		background: color-mix(in srgb, var(--bad) 18%, var(--panel2));
		border-color: color-mix(in srgb, var(--bad) 45%, transparent);
		color: var(--text);
	}
	.pick-debug-btn:disabled {
		opacity: 0.55;
		cursor: wait;
	}
	.pick-debug-note {
		margin: 0;
		font-size: 11.5px;
		color: var(--faint);
		line-height: 1.45;
	}
	.pick-debug-note :global(code) {
		font-size: 11px;
		padding: 1px 4px;
		border-radius: 4px;
		background: var(--panel2);
		font-family: var(--font-mono);
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12.5px;
		color: var(--text);
	}

	/* ── Buttons / error ── */
	.btn-gold {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
	}
	.btn-ghost {
		padding: 9px 16px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: transparent;
		color: var(--muted);
		font-size: 13px;
	}
	.btn-ghost:hover:not(:disabled) {
		color: var(--bad);
	}
	.btn-gold:disabled,
	.btn-ghost:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.mono {
		font-family: var(--font-mono);
	}
	.errstate {
		display: flex;
		flex-direction: column;
		align-items: center;
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
</style>
