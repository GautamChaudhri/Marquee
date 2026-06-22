/** Types mirroring the REAL backend shapes (design/MARQUEE_API.md).
 *  Field names match the API exactly — map to display models in components. */

export interface Paginated<T> {
	total: number;
	page: number;
	page_size: number;
	items: T[];
}

export type PosterStatus = 'missing' | 'review' | 'approved' | 'deployed';
/** Backend currently emits dovi | hdr10 | sdr | null; hdr10p reserved for the badge. */
export type HdrKind = 'dovi' | 'hdr10' | 'hdr10p' | 'sdr';
export type SubtitleStatus = 'ok' | 'gap';

export interface MovieListItem {
	id: number;
	title: string;
	year: number;
	tmdb_id: number | null;
	genres: string[] | null;
	container: string | null;
	video_width: number | null;
	video_height: number | null;
	resolution: string | null;
	poster_status: PosterStatus;
	poster_url: string | null;
	hdr: HdrKind | null;
	letterbox_status: string;
	subtitle_status: SubtitleStatus | null;
	media_file_id: number | null;
	subtitle_coverage: Record<string, unknown> | null;
}

export interface MovieDetail extends MovieListItem {
	media_file_path: string | null;
}

export interface MovieQuery {
	page?: number;
	page_size?: number;
	q?: string;
	poster_status?: PosterStatus;
	hdr?: HdrKind | 'unknown';
	letterbox_status?: string;
	include_unavailable?: boolean;
	sort?: 'title' | 'year' | 'added';
}

export interface PipelineRunRef {
	run_id: string;
	events_url: string;
	results_url: string;
}

export interface LetterboxDetail {
	movie_id: number;
	status: string;
	confidence: string | null;
	eligible: boolean | null;
	ineligible_reason: string | null;
	source_width: number | null;
	source_height: number | null;
	recommended_crop_top: number | null;
	recommended_crop_bottom: number | null;
	aspect_label: string | null;
	applied_crop_top: number | null;
	applied_crop_bottom: number | null;
	detect_method: string | null;
	reviewed: boolean | null;
	last_detected_at: string | null;
	last_applied_at: string | null;
	error: string | null;
	prefilter_bucket: string | null;
	prefilter_reason: string | null;
	prefilter_aspect_ratio?: number | null;
	last_prefiltered_at?: string | null;
	variable_ar?: boolean;
	variable_ar_note?: string | null;
	dolby_vision?: DolbyVisionInfo;
	title?: string;
	year?: number | null;
	samples?: LetterboxSample[];
	preview_minute?: number;
	preview_urls?: { before: string; after: string };
	reencode?: {
		job: MediaJobSnapshot | null;
		artifact: ReencodeArtifact | null;
	} | null;
	detection_job?: {
		job_id: string;
		status: string;
		events_url: string;
	} | null;
}

export interface LetterboxSample {
	minute: number;
	ok: boolean;
	top_bar?: number;
	bottom_bar?: number;
	bar?: number;
	error?: string | null;
	backend?: 'cpu' | 'nvdec';
	elapsed_ms?: number | null;
}

/** One row in a kanban column (from GET /letterbox/candidates items). */
export interface LetterboxColumnItem extends LetterboxDetail {
	title: string;
	year: number | null;
}

export interface LetterboxColumn {
	items: LetterboxColumnItem[];
	total: number;
}

export interface LetterboxStatus {
	enabled: boolean;
	method: string;
	counts: Record<string, number>;
	full_frame?: number;
	binaries: Record<string, boolean>;
	honored_by?: string[];
	not_honored_by?: string[];
	last_scan: string | null;
	batch_active: string | null;
}

export interface LetterboxJobRef {
	job_id: string;
	detector?: string;
	total: number;
	events_url: string;
}

export interface LetterboxAnalyzeSummary {
	candidate: number;
	not_letterboxed: number;
	variable: number;
	errored: number;
	failed: number;
	total: number;
	completed: number;
}

export interface MediaJobSnapshot {
	job_id: string;
	operation: string;
	status: string;
	stage: string | null;
	trigger: string;
	media_file_id: number | null;
	batch_id: string | null;
	progress_done: number;
	progress_total: number;
	plan: ReencodePlan | null;
	result: Record<string, unknown> | null;
	error: { error: string; code?: string | null } | null;
	input_signature: string | null;
	plan_expires_at: string | null;
	confirmed_at: string | null;
	created_at: string | null;
	updated_at: string | null;
}

export interface DolbyVisionInfo {
	present: boolean;
	profile: number | null;
	level: number | null;
	el_present: boolean | null;
	bl_signal_compatibility_id: number | null;
	preservation: { status: string; supported: boolean; reason: string | null };
}

/** A planned permanent re-encode (POST /letterbox/movies/{id}/reencode-plan). */
export interface ReencodePlan {
	job_id: string;
	status: string;
	expires_at: string;
	method: string;
	crop: { top: number; bottom: number; output_height: number };
	source: {
		path: string;
		size_bytes: number;
		codec: string | null;
		width: number;
		height: number;
		pix_fmt: string | null;
		color_transfer: string | null;
		color_primaries: string | null;
		color_space: string | null;
		has_hdr: boolean;
		has_dovi: boolean;
		dovi_profile: number | null;
	};
	encoder: {
		codec: string;
		encoder: string;
		family: string;
		quality: number;
		preset: string | null;
		available_encoders: string[];
		used_cpu_fallback: boolean;
	};
	acceleration?: {
		enabled: boolean;
		mode: 'nvidia_zero_copy' | 'cpu_decode_crop' | 'cpu_decode_crop_fallback';
		decoder: string | null;
		reason: string | null;
	};
	hdr: { status: string };
	// Flattened preservation fields + profile/level (see build_plan).
	dovi: {
		status: string;
		supported: boolean;
		reason: string | null;
		profile: number | null;
		level: number | null;
		el_present: boolean | null;
	};
	storage: {
		estimated_temp_bytes: number;
		free_bytes: number;
		original_preserved_by_default: boolean;
		replace_original_after_review: boolean;
	};
	warnings: ReencodeWarning[];
	confirmation_required: boolean;
	input_signature: string;
}

export interface ReencodeWarning {
	code: string;
	message: string;
	requires_confirmation: boolean;
}

/** Overrides sent to the re-encode plan endpoint. */
export interface ReencodeOptions {
	top?: number | null;
	bottom?: number | null;
	allow_cpu_fallback?: boolean | null;
	encoder?: string | null;
	quality?: number | null;
	preset?: string | null;
	codec?: string | null;
}

export interface ReencodeArtifact {
	id: number;
	job_id: string | null;
	movie_id: number;
	status: string;
	original_path: string;
	candidate_path: string | null;
	saved_original_path: string | null;
	original_size_bytes: number | null;
	candidate_size_bytes: number | null;
	encoder: string | null;
	encoder_family: string | null;
	codec: string | null;
	crop_top: number;
	crop_bottom: number;
	hdr_status: string | null;
	dovi_status: string | null;
	detail?: {
		plan?: ReencodePlan | null;
		warnings?: ReencodeWarning[] | null;
		execution?: { acceleration?: ReencodePlan['acceleration'] | null } | null;
	} | null;
	created_at: string | null;
	updated_at: string | null;
}

export interface ReencodeArtifactList {
	summary: {
		counts: Record<string, number>;
		candidate_bytes: number;
		saved_original_bytes: number;
		total_bytes: number;
	};
	items: ReencodeArtifact[];
}

export interface SystemMetrics {
	cpu: {
		model: string;
		cores: number | null;
		threads: number | null;
		avg: number;
		freq: number | null;
		load: number | null;
		temp: number | null;
	};
	gpu: {
		model: string;
		util: number;
		vramUsed: number;
		vramTotal: number;
		temp: number;
		power: number | null;
		enc: number | null;
	} | null;
	ram: { used: number; total: number; pct: number };
	disk: { used: number | null; total: number | null; pct: number | null };
	workers: { active: number; queued: number };
	uptime: string;
}

// ── Background jobs ─────────────────────────────────────────────────────────
/** The `job_summary(job)` dict every enqueue endpoint returns. A superset of the
 *  fields the UI needs to attach a progress bar (`status` + `events_url`). */
export interface JobSummary {
	job_id: string;
	type: string;
	status: string;
	stage: string | null;
	progress: Record<string, unknown> | null;
	subject: { type: string; id: string } | null;
	cancel_requested: boolean;
	events_url: string;
	status_url: string;
	movie_count?: number; // batch runs only
	[k: string]: unknown;
}

// ── Pipeline run results (GET /pipeline/runs/{id}) ──────────────────────────
/** One candidate, shaped by `api/results.py::_candidate_view`. Ranked survivors
 *  carry `rank`/`final_score`/`contributions`; rejects carry a reason. */
export interface CandidateView {
	orig_filename: string;
	rank: number | null;
	final_score: number | null;
	poster_url: string;
	contributions: Record<string, number> | null;
	raw_features: Record<string, number> | null;
	normalized_features: Record<string, number> | null;
	gate_decision: string | null;
	gate_reason: string | null;
	stage_reached: string | null;
	rejection_reason: string | null;
	rejection_explanation: string | null;
	dedup_kept: string | null;
	/** Stack layer: which design group this poster belongs to and its place
	 * within it. Null when stacking is off or the run predates the layer. */
	stack_id: number | null;
	stack_rank: number | null;
	stack_pos: number | null;
	stack_label: string | null;
	stack_size: number | null;
	stack_score: number | null;
	/** Present on `auto_pick` only — top human-readable contribution lines. */
	explanations?: string[];
}

/** One design group: a representative poster + its ranked variants (A,B,C…). */
export interface StackView {
	stack_rank: number;
	stack_id: number;
	label: string;
	size: number;
	stack_score: number | null;
	representative: CandidateView;
	members: CandidateView[];
}

/** One per-stage rejection group — drives the results-page stage tabs. */
export interface RejectedStageGroup {
	stage: string;
	label: string;
	count: number;
	posters: CandidateView[];
}

export interface RunResults {
	run_id: string;
	movie: { id: number | null; title: string | null; tmdb_id: number | null };
	status: string;
	scorer: string | null;
	reviewed: boolean;
	auto_pick: CandidateView | null;
	ranked: CandidateView[];
	/** Ranked survivors grouped by design. Empty when stacking is off. */
	stacks: StackView[];
	rejected: {
		gate: CandidateView[];
		ocr: CandidateView[];
		dedup: CandidateView[];
		errored: CandidateView[];
	};
	rejected_by_stage: RejectedStageGroup[];
	rejection_summary: Record<string, number>;
	suggestion: unknown;
	counts: Record<string, number>;
	stage_timings_s: Record<string, number>;
	config_snapshot?: Record<string, unknown>;
}

/** Returned by `GET /pipeline/runs/{id}` while the run is still executing. */
export interface RunningRun {
	run_id: string;
	status: 'running';
	events_url: string;
}

export type RunResultsResponse = RunResults | RunningRun;

export function isRunningRun(r: RunResultsResponse): r is RunningRun {
	return r.status === 'running' && !('ranked' in r);
}

// ── Review queue + run history ──────────────────────────────────────────────
export interface PipelineRunSummary {
	run_id: string;
	status: string;
	started_at: string | null;
	completed_at: string | null;
	scorer_name: string | null;
	counts: Record<string, number> | null;
	reviewed: boolean;
}

export interface ReviewQueueItem {
	movie: MovieListItem;
	run: PipelineRunSummary;
	results_url: string;
}

export interface ReviewQueue {
	total: number;
	page: number;
	page_size: number;
	items: ReviewQueueItem[];
}

export interface MovieRuns {
	movie_id: number;
	runs: PipelineRunSummary[];
}

// ── Metrics + cache ─────────────────────────────────────────────────────────
export interface PipelineMetrics {
	window_runs: number;
	by_status: Record<string, number>;
	by_scorer: Record<string, number>;
	distinct_batches: number;
	duration_seconds: {
		avg: number | null;
		p50: number | null;
		p90: number | null;
		max: number | null;
	};
	avg_counts: Record<string, number>;
	total_counts: Record<string, number>;
	avg_stage_seconds: Record<string, number>;
	total_stage_seconds: Record<string, number>;
}

export interface CacheSizes {
	sizes_bytes: { runs_work: number; staging: number; embeddings: number; archives: number };
	clearable_bytes: number;
	total_bytes: number;
}

// ── Feedback (pick / approve / reject) ──────────────────────────────────────
export type FeedbackAction = 'approve' | 'override' | 'reject_all';

export interface FeedbackRequestBody {
	run_id: string;
	action: FeedbackAction;
	selected_filename?: string;
	deploy?: boolean;
}

export interface FeedbackResult {
	event_id: string;
	labels_written: number;
	exemplar_added: string | null;
	remapped_to: string | null;
	gate_override: { reason: string; count_at_current_threshold: number } | null;
	head: { retrained: boolean; reason?: string } & Record<string, unknown>;
	deployed_to: string | null;
	deploy_error: string | null;
}

// ── Taste / Key Art Engine status (GET /taste/status) ───────────────────────
export interface TasteStatus {
	labels: {
		total: number;
		movies: number;
		positives: number;
		negatives: number;
		genres: Record<string, number>;
	};
	exemplars: {
		count: number;
		negatives: number;
		last_rebuild: string | null;
		profile_present: boolean;
	};
	learned_head: {
		active: boolean;
		n_samples: number;
		activation: {
			movies: { have: number; need: number };
			labels: { have: number; need: number };
		};
	};
	gate_alerts: { gate: string; overrides: number; threshold?: number | string }[];
	rebuild?: Record<string, unknown>;
}

export type TasteSource = 'training_dir' | 'library';

export type BatchScope = 'missing' | 'all' | 'selected';

// ── Taste map (GET /taste/map) ─────────────────────────────────────────────
export interface TasteMapPoint {
	name: string;
	x: number;
	y: number;
	z: number;
	x2: number;
	y2: number;
	cluster: number | null;
	self_knn: number;
	genres: string[] | null;
	year: number | null;
	aesthetic: number | null;
	colorfulness: number | null;
	thumb_url: string;
}

export interface TasteMapCluster {
	id: number;
	name: string;
	size: number;
}

export interface TasteMapData {
	projection: { method: string; computed_at: string };
	points: TasteMapPoint[];
	clusters: TasteMapCluster[] | null;
	outliers: string[];
	clustering: TasteMapCluster[] | null;
	note: string | null;
}

export interface TasteMapCandidate {
	orig_filename: string;
	rank: number | null;
	final_score: number | null;
	x: number;
	y: number;
	z: number;
	knn_sim: number;
	neighbors: { name: string; similarity: number }[];
}

export interface TasteMapCandidateOverlay {
	run_id: string;
	candidates: TasteMapCandidate[];
}

export interface TasteNeighbor {
	name: string;
	similarity: number;
}
