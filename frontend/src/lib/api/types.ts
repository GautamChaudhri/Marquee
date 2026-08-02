/** Handwritten view models for backend responses that are not generated from OpenAPI. */

export interface Paginated<T> {
	total: number;
	page: number;
	page_size: number;
	items: T[];
}

export interface ConfigurationHealth {
	status: string;
	version?: number;
	detail?: string | null;
	[key: string]: unknown;
}

export interface ConfigurationKeyMeta {
	owner: 'database' | 'environment';
	apply_mode: 'next_job' | 'restart';
	sensitivity: 'public' | 'secret';
	scope: 'execution' | 'application';
}

export interface RuntimeSettings {
	configuration_version: number;
	etag: string;
	stale: boolean;
	health: ConfigurationHealth;
	configuration_meta: Record<string, ConfigurationKeyMeta>;
	integrations: {
		tmdb: { configured: boolean };
		radarr: {
			configured: boolean;
			url_configured: boolean;
			api_key_configured: boolean;
			path_mapping_configured: boolean;
		};
		sonarr: {
			configured: boolean;
			url_configured: boolean;
			api_key_configured: boolean;
			path_mapping_configured: boolean;
		};
	};
	app: {
		name: string;
		host: string;
		port: number;
		debug: boolean;
		log_level: string;
		log_format: string;
		cors_origins: string[];
		auth: {
			api_key_configured: boolean;
			allow_local: boolean;
		};
		[key: string]: unknown;
	};
	paths: {
		data_dir: string;
		media_roots: string[];
		radarr_path_prefix_configured: boolean;
		radarr_media_path_configured: boolean;
		sonarr_path_prefix_configured: boolean;
		sonarr_media_path_configured: boolean;
		metrics_disk_path_configured: boolean;
		poster_cache_dir: string;
		poster_staging_dir: string;
	};
	sync: {
		interval_minutes: number;
		cooldown_seconds: number;
		heal_enabled: boolean;
		heal_interval_minutes: number;
		webhook_dry_run: boolean;
	};
	posters: {
		restore_method: 'download' | 'local';
		backup_dir: string;
	};
	poster_formats: {
		movie: string;
		series: string;
		season: string;
	};
	writable: boolean;
	[key: string]: unknown;
}

export type PosterStatus = 'missing' | 'review' | 'approved' | 'deployed';

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
	media_file_id: number | null;
}

export interface MovieDetail extends MovieListItem {
	media_file_path: string | null;
}

export interface PosterSummary {
	has_poster: boolean;
	ai_selected: boolean;
	user_approved: boolean;
	deployed_at: string | null;
}

export type SeasonPosterStatus = 'complete' | 'partial' | 'missing';

export interface SeasonSummary {
	id: number;
	season_number: number;
	episode_count: number | null;
	episode_file_count: number | null;
	poster: PosterSummary;
}

export interface SeriesListItem {
	id: number;
	title: string;
	year: number | null;
	tmdb_id: number | null;
	genres: string[] | null;
	poster: PosterSummary;
	downloaded_seasons: number;
	seasons_with_poster: number;
	season_poster_status: SeasonPosterStatus;
	season_count: number | null;
	/** Downloaded seasons only, sorted numerically by season number. */
	seasons: SeasonSummary[];
}

export interface SeriesDetail extends SeriesListItem {
	tvdb_id: number | null;
	show_text_profile_id: string | null;
	season_text_profile_id: string | null;
}

export interface MovieQuery {
	page?: number;
	page_size?: number;
	q?: string;
	poster_status?: PosterStatus;
	exclude_in_review?: boolean;
	include_unavailable?: boolean;
	sort?: 'title' | 'year' | 'added';
}

export interface PipelineRunRef {
	run_id: string;
	events_url: string;
	results_url: string;
}

export interface SystemMetrics {
	cpu: {
		model: string;
		cores: number | null;
		threads: number | null;
		avg: number;
		perCore: number[];
		freq: number | null;
		load: number | null;
		temp: number | null;
	};
	gpu: {
		model: string;
		util: number;
		memUtil: number;
		vramUsed: number;
		vramTotal: number;
		temp: number;
		power: number | null;
		enc: number | null;
		dec: number | null;
	} | null;
	ram: { used: number; total: number; pct: number };
	disk: {
		used: number | null;
		total: number | null;
		pct: number | null;
		readBytes: number | null;
		writeBytes: number | null;
	};
	net: { bytesSent: number | null; bytesRecv: number | null };
	workers: { active: number; queued: number };
	uptime: string;
}

export interface SystemMetricsHistoryPoint {
	ts: string;
	cpu_avg: number | null;
	gpu_util: number | null;
	gpu_mem: number | null;
	gpu_enc: number | null;
	gpu_dec: number | null;
	ram_pct: number | null;
	disk_read_bps: number | null;
	disk_write_bps: number | null;
	net_recv_bps: number | null;
	net_sent_bps: number | null;
	active_jobs: number;
}

export interface JobTimelineItem {
	job_id: string;
	type: string;
	label: string;
	status: string;
	subject: string | null;
	started_at: string | null;
	finished_at: string | null;
}

export interface SystemMetricsHistory {
	window: string;
	start_at: string;
	end_at: string;
	points: SystemMetricsHistoryPoint[];
	jobs: JobTimelineItem[];
}

// ── Background jobs ─────────────────────────────────────────────────────────
/** The `job_summary(job)` dict every enqueue endpoint returns. A superset of the
 *  fields the UI needs to attach a progress bar (`status` + `events_url`). */
export interface JobSummary {
	job_id: string;
	type: string;
	label?: string;
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

export interface LastHeal {
	last_run: string | null;
	checked: number;
	restored: number;
	failed: number;
}

export interface HealScheduleInfo {
	enabled: boolean;
	interval_minutes: number;
	next_run_at: string | null;
}

export interface BackupStats {
	count: number;
	bytes: number;
}

export interface SummaryRunningJob extends JobSummary {
	movie_count: number;
}

export interface PipelineSummary {
	total_movies: number;
	movies_with_poster: number;
	/** Coverage arithmetic: downloaded movies with no poster. */
	movies_missing_poster: number;
	/** The workspace Run tab's own count — missing *and* not awaiting a decision. */
	movies_awaiting_run: number;
	movies_in_review: number;
	movies_in_run: number;
	running_jobs: SummaryRunningJob[];
	last_heal: LastHeal | null;
	heal_schedule: HealScheduleInfo | null;
	backups: BackupStats;
}

// ── Pipeline run results (GET /pipeline/runs/{id}) ──────────────────────────
export interface OcrEvidenceRegion {
	text: string;
	confidence: number | null;
	category: string | null;
	is_title: boolean;
	is_title_fragment: boolean;
	is_significant: boolean;
}

/** Compact, regular-run OCR evidence. The full OCR trace remains DEBUG-only. */
export interface OcrEvidence {
	available: boolean;
	has_text: boolean;
	detected_text: string | null;
	title_matched: boolean;
	regions: OcrEvidenceRegion[];
	error: string | null;
}

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
	rejection_label: string | null;
	rejection_explanation: string | null;
	ocr_evidence: OcrEvidence | null;
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
	media_type?: 'movie' | 'series' | 'season';
	subject?: {
		series_id: number | null;
		season_id: number | null;
		season_number: number | null;
		title: string | null;
	};
	official_pick?: OfficialPick | null;
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

export type OcrLabelKind = 'false_rejection' | 'false_acceptance';

export interface OcrLabelCaptureRequest {
	run_id: string;
	orig_filename: string;
}

export interface OcrLabelCaptureResult {
	status: 'captured';
	label_kind: OcrLabelKind;
	path: string;
	image_copied: boolean;
	log_captured: boolean;
	missing_artifacts: string[];
	metadata: Record<string, unknown>;
}

export interface OcrLabelClearResult {
	status: 'cleared';
	root_path: string;
	deleted_run_dirs: number;
	deleted_capture_dirs: number;
}

export interface OcrLabelRunState {
	run_id: string;
	labels: Record<OcrLabelKind, string[]>;
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
	media_type?: 'movie' | 'series' | 'season';
	season_id?: number | null;
	season_number?: number | null;
}

export interface ReviewQueueItem {
	movie: MovieListItem;
	run: PipelineRunSummary;
	/** Image URL of the run's auto-pick ("1A"); null for runs predating the field. */
	auto_pick_poster_url?: string | null;
	results_url: string;
}

export interface ReviewQueue {
	total: number;
	page: number;
	page_size: number;
	items: ReviewQueueItem[];
}

export interface ReviewQueueAutoApproveError {
	run_id: string;
	movie_id: number;
	title: string | null;
	error: unknown;
}

export interface ReviewQueueAutoApproveResult {
	total: number;
	approved: number;
	skipped_no_auto: number;
	failed: number;
	errors: ReviewQueueAutoApproveError[];
}

export interface MovieRuns {
	movie_id: number;
	runs: PipelineRunSummary[];
}

export interface OfficialPick {
	enabled?: boolean;
	primary_name?: string | null;
	applied?: string | null;
	reason?: string | null;
}

export interface TvPipelineSummary {
	shows_total: number;
	shows_with_show_poster: number;
	shows_missing_show_poster: number;
	seasons_total: number;
	seasons_with_poster: number;
	seasons_missing_poster: number;
	shows_fully_covered: number;
	shows_in_review: number;
	seasons_in_review: number;
	assets_in_review: number;
	assets_in_run: number;
	shows_no_tmdb: number;
	running_jobs: (JobSummary & { asset_count?: number; series_count?: number })[];
	last_heal: LastHeal | null;
	heal_schedule: HealScheduleInfo | null;
	backups: BackupStats;
}

export interface TvRunQueueAsset {
	media_type: 'series' | 'season';
	season_id?: number;
	number?: number;
}

export interface TvRunQueueItem {
	series: {
		id: number;
		title: string;
		year: number | null;
		tmdb_id: number | null;
		poster_url: string | null;
	};
	show_poster_missing: boolean;
	missing_seasons: { season_id: number; number: number; episode_file_count: number | null }[];
	assets_to_run: TvRunQueueAsset[];
	no_tmdb: boolean;
}

export interface TvRunQueue {
	items: TvRunQueueItem[];
	total: number;
}

export interface TvReviewSeasonRun {
	season_number: number;
	season_id: number;
	run: PipelineRunSummary;
	auto_pick_poster_url: string | null;
	flagged_no_candidates: boolean;
	official_pick?: OfficialPick | null;
}

export interface TvReviewGroup {
	series: {
		id: number;
		title: string;
		year: number | null;
		tmdb_id: number | null;
	};
	show_run: (PipelineRunSummary & { auto_pick_poster_url?: string | null }) | null;
	season_runs: TvReviewSeasonRun[];
	seasons_only: boolean;
	display_poster_url: string | null;
}

export interface TvReviewQueue {
	total_series: number;
	page: number;
	page_size: number;
	items: TvReviewGroup[];
}

export interface TvAutoApproveResult {
	total: number;
	approved: number;
	skipped_no_auto: number;
	failed: number;
	errors: { run_id: string; error: unknown }[];
}

export interface SeriesRunsResponse {
	series_id: number;
	runs: PipelineRunSummary[];
}

export interface SeriesArtworkEvent {
	id: number;
	action: string;
	source: string;
	media_type: 'series' | 'season';
	season_id: number | null;
	detail: Record<string, unknown> | null;
	created_at: string | null;
}

export interface SeriesArtworkEventsResponse {
	series_id: number;
	events: SeriesArtworkEvent[];
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

// ── Feedback (pick / approve / reject / rank) ───────────────────────────────
export type FeedbackAction = 'approve' | 'override' | 'reject_all' | 'rank';

export interface FeedbackRequestBody {
	run_id: string;
	action: FeedbackAction;
	selected_filename?: string;
	/** action="rank": final order (best -> worst) of orig_filenames still in
	 *  the orderable list, plus the set thrown into the hate pile. */
	order?: string[];
	hated?: string[];
	deploy?: boolean;
	idempotency_key?: string;
}

export interface FeedbackResult {
	event_id: string;
	labels_written: number;
	exemplar_added: string | null;
	/** action="rank": positive exemplars + hard negatives the event added. */
	favorites_exemplars?: string[];
	negatives_added?: string[];
	remapped_to: string | null;
	gate_override: { reason: string; count_at_current_threshold: number } | null;
	residual: { retrained: boolean; reason?: string } & Record<string, unknown>;
	deployment_job: {
		job_id: string;
		disposition: string;
		phase: string;
		snapshot_url: string;
		detail_url: string;
	} | null;
}

/** One rankable unit in the sortable ranking list — a whole design stack (its
 *  members move together) or a single poster. ``filenames`` is what the rank
 *  payload references. */
export interface RankItem {
	key: string;
	posterUrl: string;
	label: string;
	score: number | null;
	filenames: string[];
}

// ── Taste / bounded residual status (GET /taste/status) ─────────────────────
export interface PublicationAuthorityStatus {
	available: boolean;
	authority: 'ml_active_publications';
}

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
		unique_movies?: number;
		duplicate_groups?: number;
	};
	ranking_residual: {
		active: boolean;
		mode: 'bounded_residual';
		subjects: number;
		pairs: number;
		activation: {
			subjects: { have: number; need: number };
			pairs: { have: number; need: number };
		};
	};
	active_profile?: ManagedArtifactSummary | null;
	active_residual?: ManagedArtifactSummary | null;
	publication_authority?: PublicationAuthorityStatus;
	gate_alerts: { gate: string; overrides: number; threshold?: number | string }[];
	rebuild?: Record<string, unknown>;
}

export type BatchScope = 'missing' | 'all' | 'selected';
export type PosterBatchMode = 'chunked' | 'all_at_once';
export type PosterBatchOptions = {
	batch_mode: PosterBatchMode;
	chunk_size?: number;
};

// ── Taste map (GET /taste/map) ─────────────────────────────────────────────
export interface TasteMapPoint {
	name: string;
	movie_title: string;
	movie_id: number | null;
	tmdb_id: number | null;
	x: number;
	y: number;
	z: number;
	x2: number;
	y2: number;
	cluster: number | null;
	is_noise: boolean;
	self_knn: number;
	genres: string[] | null;
	year: number | null;
	aesthetic: number | null;
	colorfulness: number | null;
	thumb_url: string | null;
}

export interface TasteMapCluster {
	id: number;
	name: string;
	size: number;
}

export interface TasteMapData {
	projection: { method: string; computed_at: string };
	points: TasteMapPoint[];
	summary: {
		exemplars: number;
		unique_movies: number;
		duplicate_groups: number;
		noise: number;
	};
	clusters: TasteMapCluster[] | null;
	outliers: string[];
	clustering: TasteMapCluster[] | null;
	note: string | null;
}

export interface TasteNeighbor {
	name: string;
	similarity: number;
}

export interface ManagedArtifactMovie {
	movie_id: number | null;
	title: string;
	year: number | null;
	tmdb_id: number | null;
	contribution_count: number;
}

export interface ManagedArtifactSummary {
	id: string;
	kind: 'taste_profile' | 'ranking_residual';
	status: 'active' | 'archived';
	label: string;
	model_name: string | null;
	source_mode: string | null;
	imported_from_active: boolean;
	created_at: string | null;
	updated_at: string | null;
	trained_at: string | null;
	activated_at: string | null;
	storage_path: string;
	summary: Record<string, unknown> & {
		exemplars?: number;
		unique_movies?: number;
		negative_exemplars?: number;
		duplicate_groups?: number;
		duplicate_exemplars?: number;
		source_mode?: string;
		mode?: string;
		sample_count?: number;
		train_accuracy?: number;
		top_features?: { name: string; weight: number }[];
		alpha?: number;
		delta_max?: number;
		evaluation?: {
			baseline_accuracy: number;
			residual_accuracy: number;
			improvement: number;
			pair_count: number;
			subject_count: number;
		};
	};
}

export interface ManagedProfileDetail extends ManagedArtifactSummary {
	movies: ManagedArtifactMovie[];
	duplicate_groups: {
		title: string;
		year: number | null;
		count: number;
		exemplars: string[];
	}[];
	negative_exemplars: string[];
}

export interface ManagedResidualDetail extends ManagedArtifactSummary {
	movies: ManagedArtifactMovie[];
}

export interface ManagedProfilesResponse {
	profiles: ManagedArtifactSummary[];
	publication_authority?: PublicationAuthorityStatus;
}

export interface ManagedResidualsResponse {
	residuals: ManagedArtifactSummary[];
	publication_authority?: PublicationAuthorityStatus;
}

export interface ManagedExemplarRow {
	name: string;
	title: string;
	year: number | null;
	movie_id: number | null;
	movie_title: string;
	tmdb_id: number | null;
	is_duplicate: boolean;
	duplicate_count: number;
	exists_in_training_dir: boolean;
	thumb_url: string | null;
}
