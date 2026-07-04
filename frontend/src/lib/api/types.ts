/** Types mirroring the REAL backend shapes (design/MARQUEE_API.md).
 *  Field names match the API exactly — map to display models in components. */

export interface Paginated<T> {
	total: number;
	page: number;
	page_size: number;
	items: T[];
}

export interface RuntimeSettings {
	integrations: {
		subgen?: {
			configured?: boolean;
			url_configured?: boolean;
			callback_token_configured?: boolean;
			url?: string | null;
			profile_name?: string | null;
			model_label?: string | null;
			mode?: string | null;
			local_path_prefix?: string | null;
			remote_path_prefix?: string | null;
			[key: string]: unknown;
		};
		[key: string]: unknown;
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
	paths: Record<string, unknown>;
	sync: Record<string, unknown>;
	letterbox: Record<string, unknown>;
	posters?: {
		restore_method?: 'download' | 'local';
		backup_dir?: string;
		[key: string]: unknown;
	};
	subtitles: {
		enabled?: boolean;
		scan_concurrency?: number;
		mutation_concurrency?: number;
		generation_concurrency?: number;
		preferred_languages?: string[];
		preferred_audio_languages?: string[] | null;
		preferred_subtitle_languages?: string[] | null;
		effective_preferred_audio_languages?: string[];
		effective_preferred_subtitle_languages?: string[];
		unknown_language_action?: string;
		protect_forced?: boolean;
		protect_last_full_dialogue?: boolean;
		backup_mode?: string;
		external_delete_mode?: string;
		[key: string]: unknown;
	};
	poster_formats: {
		movie?: string;
		series?: string;
		season?: string;
		[key: string]: unknown;
	};
	writable: boolean;
	[key: string]: unknown;
}

export type PosterStatus = 'missing' | 'review' | 'approved' | 'deployed';
export type HdrKind = 'hdr' | 'hdr10' | 'hdr10p' | 'dovi' | 'dovi_no_fallback' | 'sdr';
export type HdrPreferenceChoice =
	| 'sdr'
	| 'hdr'
	| 'hdr10'
	| 'hdr10p'
	| 'dovi_no_fallback'
	| 'dovi_fallback';
export type SubtitleStatus = 'ok' | 'gap';
export type RadarrOverlayStatus =
	| 'below_target'
	| 'meets_target'
	| 'exceeds_target'
	| 'no_hdr_target';

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
	hdr_tags: HdrKind[];
	letterbox_status: string;
	subtitle_status: SubtitleStatus | null;
	media_file_id: number | null;
	subtitle_coverage: Record<string, unknown> | null;
	preferred_languages?: PreferredLanguageState;
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

export interface SeriesListItem {
	id: number;
	title: string;
	year: number | null;
	tmdb_id: number | null;
	poster: PosterSummary;
	downloaded_seasons: number;
	seasons_with_poster: number;
	season_poster_status: SeasonPosterStatus;
	season_count: number | null;
}

export interface SeasonSummary {
	id: number;
	season_number: number;
	episode_count: number | null;
	episode_file_count: number | null;
	poster: PosterSummary;
}

export interface SeriesDetail extends SeriesListItem {
	tvdb_id: number | null;
	show_text_profile_id: string | null;
	season_text_profile_id: string | null;
	seasons: SeasonSummary[];
}

export interface MovieQuery {
	page?: number;
	page_size?: number;
	q?: string;
	poster_status?: PosterStatus;
	hdr?: HdrKind | 'unknown';
	letterbox_status?: string;
	exclude_in_review?: boolean;
	include_unavailable?: boolean;
	sort?: 'title' | 'year' | 'added';
}

export interface RadarrOverlayProfile {
	id: number;
	name: string;
	cutoff_format_score: number | null;
}

export interface RadarrOverlayProfilePreference {
	profile_id: number;
	profile_name: string;
	profile_targets: HdrKind[];
	available_preference_targets: HdrPreferenceChoice[];
	meet_target: HdrPreferenceChoice | null;
	exceed_target: HdrPreferenceChoice | null;
	excluded_targets: HdrPreferenceChoice[];
}

export interface RadarrOverlayItem extends MovieListItem {
	dovi_no_fallback: boolean;
	dovi_status?: 'unknown' | 'analyzed' | 'not_dovi' | 'error' | null;
	dovi_profile?: number | null;
	dovi_el_type?: DoviElType;
	dovi_bl_signal_compatibility_id?: number | null;
	profile_id: number | null;
	profile_name: string | null;
	cf_score: number | null;
	cf_cutoff: number | null;
	cutoff_met: boolean | null;
	profile_targets: HdrKind[];
	available_preference_targets: HdrPreferenceChoice[];
	meet_target: HdrPreferenceChoice | null;
	exceed_target: HdrPreferenceChoice | null;
	preference_status: RadarrOverlayStatus;
}

export interface RadarrOverlayQuery {
	page?: number;
	page_size?: number;
	hdr?: HdrKind | 'unknown';
	hdr_tags?: string[];
	cf_score_min?: number;
	cf_score_max?: number;
	profile_id?: number;
	preference_status?: RadarrOverlayStatus;
	dovi_no_fallback?: boolean;
	sort_by?: 'title' | 'year' | 'cf_score' | 'preference_status';
	sort_dir?: 'asc' | 'desc';
}

export interface RadarrOverlayResponse extends Paginated<RadarrOverlayItem> {
	distribution: Record<
		'hdr' | 'hdr10' | 'hdr10p' | 'dovi' | 'dovi_no_fallback' | 'sdr' | 'unknown',
		number
	>;
	distribution_order: string[];
	profiles: RadarrOverlayProfile[];
	profile_preferences: RadarrOverlayProfilePreference[];
	applied_filters: Record<string, unknown>;
}

export type DoviElType = 'FEL' | 'MEL' | null;

export interface DoviConversion {
	eligible: boolean | 'lossy';
	target: string | null;
	kind: 'p5_to_p81' | 'p7_strip_el' | null;
	reason: string;
}

export interface DoviState {
	status: 'unknown' | 'analyzing' | 'analyzed' | 'not_dovi' | 'error';
	profile: number | null;
	level: number | null;
	el_present: boolean | null;
	el_type: DoviElType;
	bl_signal_compatibility_id: number | null;
	source_codec: string | null;
	rpu_summary: string | null;
	error_reason: string | null;
	conversion: DoviConversion;
	last_analyzed_at: string | null;
}

export interface HdrMovieDetail {
	movie: {
		id: number;
		title: string;
		year: number;
		tmdb_id: number | null;
		radarr_id: number | null;
		movie_file_path: string | null;
		container: string | null;
		resolution: string | null;
		has_hdr: boolean | null;
		has_dv: boolean | null;
		hdr_type_raw: string | null;
		quality_profile_id: number | null;
	};
	profile_name: string | null;
	hdr_tags: HdrKind[];
	hdr_bucket: string;
	dovi: DoviState | null;
	binaries: { dovi_tool: boolean; ffmpeg: boolean; ffprobe: boolean };
	analysis_job: import('./jobs').JobSnapshot | null;
	conversion_job: import('./jobs').JobSnapshot | null;
}

// ── TV HDR (design/plans/06 §5–6, 07) ──────────────────────────────────────

/** Sonarr preference-summary shape is byte-identical to the Radarr one. */
export type SonarrOverlayProfilePreference = RadarrOverlayProfilePreference;

export type ShowStatus =
	| 'exceeds_target'
	| 'meets_target'
	| 'gaps'
	| 'below_target'
	| 'no_hdr_target'
	| 'unknown';
export type ShowUniformity = 'uniform' | 'uniform_by_season' | 'mixed';
export type SeasonUniformity = 'uniform' | 'mixed';
export type EpisodePreferenceStatus = RadarrOverlayStatus | 'unknown';

export interface SeasonRollup {
	uniformity: SeasonUniformity;
	uniform_tags: HdrKind[] | null;
	union_tags: HdrKind[];
	distribution: Record<string, number>;
	status_counts: Record<string, number>;
	episodes_total: number;
	episodes_known: number;
	episodes_unknown: number;
}

export interface ShowRollup {
	status: ShowStatus;
	uniformity: ShowUniformity;
	union_tags: HdrKind[];
	uniform_tags: HdrKind[] | null;
	distribution: Record<string, number>;
	status_counts: Record<string, number>;
	episodes_total: number;
	episodes_known: number;
	episodes_unknown: number;
	meeting_fraction: { met: number; of: number };
}

export interface HdrTvListItem {
	id: number;
	title: string;
	year: number | null;
	poster_available: boolean;
	profile_id: number | null;
	profile_name: string | null;
	profile_targets: HdrKind[];
	meet_target: HdrPreferenceChoice | null;
	exceed_target: HdrPreferenceChoice | null;
	rollup: ShowRollup;
	seasons_count: number;
	episodes_total: number;
}

export interface HdrTvQuery {
	page?: number;
	page_size?: number;
	hdr_tags?: string[];
	preference_status?: ShowStatus;
	uniformity?: ShowUniformity;
	profile_id?: number;
	dovi_no_fallback?: boolean;
	sort_by?: 'title' | 'status' | 'coverage';
	sort_dir?: 'asc' | 'desc';
}

export interface HdrTvListResponse extends Paginated<HdrTvListItem> {
	distribution: Record<'hdr' | 'hdr10' | 'hdr10p' | 'dovi' | 'dovi_no_fallback' | 'sdr', number>;
	distribution_order: string[];
	profiles: RadarrOverlayProfile[];
	profile_preferences: SonarrOverlayProfilePreference[];
	applied_filters: Record<string, unknown>;
}

export interface EpisodeHdrItem {
	id: number;
	episode_number: number;
	title: string | null;
	hdr_type_raw: string | null;
	hdr_tags: HdrKind[];
	bucket: string;
	resolution: string | null;
	preference_status: EpisodePreferenceStatus;
	dovi: DoviState | null;
}

export interface HdrTvSeason {
	season_number: number;
	is_specials: boolean;
	rollup: SeasonRollup;
	episodes: EpisodeHdrItem[];
}

export interface HdrTvDetail {
	series: {
		id: number;
		title: string;
		year: number | null;
		tvdb_id: number | null;
		tmdb_id: number | null;
		quality_profile_id: number | null;
	};
	profile: {
		id: number | null;
		name: string | null;
		targets: HdrKind[];
		meet_target: HdrPreferenceChoice | null;
		exceed_target: HdrPreferenceChoice | null;
		excluded_targets: HdrPreferenceChoice[];
	};
	rollup: ShowRollup;
	seasons: HdrTvSeason[];
	analysis_jobs: import('./jobs').JobListItem[];
	binaries: { ffprobe: boolean };
}

export interface HdrSummaryDistribution {
	sdr: number;
	hdr: number;
	hdr10: number;
	hdr10p: number;
	dovi: number;
	dovi_no_fallback: number;
}

export interface HdrWorstOffender {
	series_id: number;
	title: string;
	year: number | null;
	status: 'gaps' | 'below_target';
	below_count: number;
	unknown_count: number;
	episodes_total: number;
	meeting_fraction: { met: number; of: number };
}

export interface HdrSummary {
	movies: {
		total: number;
		distribution: HdrSummaryDistribution;
		status_counts: Record<RadarrOverlayStatus, number>;
		dovi_analysis: { analyzed: number; total_dovi: number };
	};
	tv: {
		shows_total: number;
		episodes_total: number;
		episodes_unknown: number;
		episode_distribution: HdrSummaryDistribution;
		show_status_counts: Record<ShowStatus, number>;
		uniformity_counts: Record<ShowUniformity, number>;
		dovi_analysis: { analyzed: number; total_dovi: number };
	};
	worst_offenders: HdrWorstOffender[];
	insights: {
		dovi_no_fallback: { movies: number; episodes: number; shows_affected: number };
		unanalyzed_dovi: { movies: number; episodes: number };
		no_hdr_target: { movies: number; shows: number };
		four_k_sdr: { movies: number; shows_affected: number; episodes: number };
	};
	profile_preferences: {
		radarr: RadarrOverlayProfilePreference[];
		sonarr: SonarrOverlayProfilePreference[];
	};
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
	sample_previews?: LetterboxSamplePreview[];
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

export interface LetterboxSamplePreview {
	minute: number;
	ok: boolean;
	url: string | null;
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

export interface BatchReencodeSettings {
	quality_profile?: 'speed' | 'balanced' | 'quality' | null;
	encoder?: string | null;
	quality?: number | null;
	preset?: string | null;
	codec?: 'preserve' | 'hevc' | 'h264' | null;
	allow_cpu?: boolean | null;
	crop_top_override?: number | null;
	crop_bottom_override?: number | null;
}

export interface BatchReencodeResponse {
	job_ids: string[];
	count: number;
	skipped: Array<{ movie_id: number; code?: string | null; reason: string }>;
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
	movies_missing_poster: number;
	movies_in_review: number;
	movies_in_run: number;
	running_jobs: SummaryRunningJob[];
	last_heal: LastHeal | null;
	heal_schedule: HealScheduleInfo | null;
	backups: BackupStats;
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
	head: { retrained: boolean; reason?: string } & Record<string, unknown>;
	deployed_to: string | null;
	deploy_error: string | null;
}

// ── Onboarding (cold-start "Rank Test") ─────────────────────────────────────
export interface OnboardingStatus {
	profile_present: boolean;
	head_active: boolean;
	taste_test_available: boolean;
	needs_onboarding: boolean;
	ranked: number;
	min: number;
	goal: number;
	max: number;
	can_complete: boolean;
	at_goal: boolean;
	at_max: boolean;
	complete: boolean;
	path: 'library' | 'taste_test' | null;
	started_at: string | null;
}

export interface TasteTestPoster {
	file: string;
	url: string;
}

export interface TasteTestMovie {
	id: string;
	title: string | null;
	year: number | null;
	genres: string[];
	posters: TasteTestPoster[];
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

// ── Taste / Key Art Engine status (GET /taste/status) ───────────────────────
export interface ArtifactRegistryStatus {
	available: boolean;
	reason?: 'missing_schema';
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
	learned_head: {
		active: boolean;
		/** Training mode: 'pairwise' counts within-movie preference pairs;
		 *  'pointwise' counts approve/override labels. Drives the unit label. */
		mode?: 'pairwise' | 'pointwise';
		n_samples: number;
		activation: {
			movies: { have: number; need: number };
			labels: { have: number; need: number };
		};
	};
	active_profile?: ManagedArtifactSummary | null;
	active_head?: ManagedArtifactSummary | null;
	artifact_registry?: ArtifactRegistryStatus;
	gate_alerts: { gate: string; overrides: number; threshold?: number | string }[];
	rebuild?: Record<string, unknown>;
}

export type TasteSource = 'training_dir' | 'library';

export type BatchScope = 'missing' | 'all' | 'selected';

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

export interface ManagedArtifactMovie {
	movie_id: number | null;
	title: string;
	year: number | null;
	tmdb_id: number | null;
	contribution_count: number;
}

export interface ManagedArtifactSummary {
	id: string;
	kind: 'taste_profile' | 'learned_head';
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

export interface ManagedHeadDetail extends ManagedArtifactSummary {
	movies: ManagedArtifactMovie[];
}

export interface ManagedProfilesResponse {
	profiles: ManagedArtifactSummary[];
	artifact_registry?: ArtifactRegistryStatus;
}

export interface ManagedHeadsResponse {
	heads: ManagedArtifactSummary[];
	artifact_registry?: ArtifactRegistryStatus;
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
	thumb_url: string;
}

// ── Subtitle Inventory ──
export interface SubtitleTrack {
	id: string;
	source: 'embedded' | 'external';
	stream_index: number | null;
	tool_track_id: number | null;
	external_path: string | null;
	codec: string | null;
	codec_label?: string;
	kind: 'text' | 'bitmap' | 'teletext' | 'unknown';
	kind_label?: string;
	language_raw: string | null;
	language_tag: string;
	language_source: 'metadata' | 'filename' | 'user' | 'unknown';
	title: string | null;
	is_default: boolean;
	is_forced: boolean;
	is_sdh: boolean;
	is_commentary: boolean;
	is_generated: boolean;
	size_bytes: number | null;
	per_track_actions: {
		remove: TrackAction;
		embed: TrackAction;
		extract: TrackAction;
	};
}

export interface TrackAction {
	available: boolean;
	reason: string | null;
}

export interface AudioStreamInfo {
	index: number;
	language: string;
	language_raw?: string | null;
	language_tag: string;
	language_source?: 'metadata' | 'filename' | 'user' | 'unknown';
	channels: number;
	channel_layout?: string | null;
	channel_label?: string | null;
	codec: string | null;
	codec_long_name?: string | null;
	profile?: string | null;
	format_label?: string | null;
	title?: string | null;
	tool_track_id?: number | null;
	disposition?: Record<string, unknown>;
	is_default?: boolean;
	is_forced?: boolean;
	is_sdh?: boolean;
	is_commentary?: boolean;
}

export interface ContainerCapabilities {
	can_remove: boolean;
	can_embed_text: boolean;
	can_embed_bitmap: boolean;
	can_edit_metadata: boolean;
}

export interface SubtitleCoverage {
	audio_languages: string[];
	audio_channels_by_language?: Record<string, string[]>;
	full_dialogue_languages: string[];
	forced_only_languages: string[];
	sdh_languages: string[];
	commentary_present: boolean;
	external_present: boolean;
	embedded_present: boolean;
	generated_present: boolean;
	unknown_present: boolean;
	preferred_audio_languages?: string[];
	preferred_subtitle_languages?: string[];
	missing_preferred_audio_languages?: string[];
	missing_preferred_languages: string[];
	audio_status?: 'ok' | 'gap';
	subtitle_status?: 'ok' | 'gap';
	status?: 'ok' | 'gap';
	track_count: number;
	preferences?: PreferredLanguageState;
}

export interface PreferredLanguageState {
	shared: string[];
	audio: string[];
	subtitles: string[];
	override: boolean;
	override_audio: string[] | null;
	override_subtitles: string[] | null;
}

export interface SubtitleInventory {
	inventory_id: string;
	file_path: string;
	container: string;
	duration_seconds: number;
	tracks: SubtitleTrack[];
	coverage: SubtitleCoverage;
	capabilities: ContainerCapabilities;
	audio_streams: AudioStreamInfo[];
	file_signature: string;
	scanned_at: string;
}

// ── Plans ──
export interface TrackEdit {
	track_id?: string;
	stream_type?: 'audio' | 'subtitle';
	stream_index?: number;
	audio_stream_index?: number;
	language_tag?: string | null;
	title?: string | null;
	is_default?: boolean;
	is_forced?: boolean;
	is_sdh?: boolean;
	is_commentary?: boolean;
	field?: string;
	value?: unknown;
}

export interface SubtitlePlanRequest {
	operation:
		| 'audio_remove'
		| 'subtitle_remove'
		| 'subtitle_embed'
		| 'subtitle_metadata'
		| 'track_remove'
		| 'audio_reorder';
	track_ids: string[];
	audio_stream_indices?: number[];
	audio_stream_order?: number[];
	edits?: TrackEdit[];
	backup?: boolean;
	allow_break?: boolean;
}

export interface SubtitlePlan {
	job_id: string;
	status: string;
	operation: string;
	before: Record<string, unknown>;
	after: Record<string, unknown>;
	warnings: unknown[];
	storage: {
		source_bytes?: number;
		estimated_temp_bytes?: number;
		free_bytes?: number;
		backup_requested?: boolean;
		estimated_bytes?: number;
		available_bytes?: number;
	};
	plan_expires_at?: string;
}

export interface MediaJob {
	job_id: string;
	status: string;
	operation: string;
	label?: string;
	media_file_id: string | number | null;
	created_at: string;
	updated_at: string;
	started_at: string | null;
	completed_at: string | null;
	result: Record<string, unknown> | null;
	error: Record<string, unknown> | string | null;
	progress: {
		stage: string;
		percent: number;
		message: string;
	} | null;
	events_url: string;
	backup_id: string | null;
	plan: Record<string, unknown> | null;
}

// ── Generators ──
export interface SubtitleGenerator {
	name: string;
	type: string;
	url: string;
	online: boolean;
	version: string | null;
	model: string | null;
	device: string | null;
	capabilities: {
		language_hint: boolean;
		translate: boolean;
		concurrent: number;
	};
}

export interface GenerationRequest {
	generator_id?: string | null;
	language_hint?: string | null;
	output: 'external' | 'embedded';
}

// ── Policies ──
export interface SubtitlePolicy {
	id: number;
	name: string;
	enabled: boolean;
	revision: number;
	mode: 'allowlist' | 'blocklist';
	languages: string[];
	unknown_action: 'keep' | 'review' | 'remove';
	target_source?: 'embedded' | 'external' | 'both';
	protect_forced: boolean;
	protect_default: boolean;
	protect_last_full_dialogue: boolean;
	include_external: boolean;
	auto_apply: boolean;
	audit_only: boolean;
	hardlink_action: 'block' | 'allow_break';
	backup_mode: 'none' | 'keep_original';
	created_at: string;
	updated_at: string;
}

export interface PolicyAuditResultItem {
	movie_id: number;
	media_file_id: string;
	removals: number;
	protected: number;
	review_required: number;
	warnings: string[];
	coverage_before: SubtitleCoverage;
	coverage_after: SubtitleCoverage;
}

export interface PolicyAuditResult {
	policy_id: number;
	total_removals: number;
	items: PolicyAuditResultItem[];
}

// ── Audio & Subtitles TV & Subgen Types ──
export type AudioSubStatus = 'ok' | 'audio_gap' | 'subtitle_gap' | 'both_gap' | 'unknown';

export interface AudioSubsSummary {
	movies: {
		total: number;
		audio_ok: number;
		audio_gap: number;
		subtitle_ok: number;
		subtitle_gap: number;
		both_gap: number;
		unknown: number;
		forced_coverage: number;
		sdh_coverage: number;
		unknown_language_tracks: number;
		generated_tracks: number;
	};
	tv: {
		audio_ok: number;
		audio_gap: number;
		subtitle_ok: number;
		subtitle_gap: number;
		both_gap: number;
		unknown: number;
		forced_coverage: number;
		sdh_coverage: number;
		show_status_counts: Record<string, number>;
		uniformity_counts: Record<string, number>;
		dub_coverage_highlights: Array<{
			series_id: number;
			title: string;
			missing_audio_languages: string[];
			coverage: { ok: number; of: number };
		}>;
	};
	preferred: {
		audio: string[];
		subtitles: string[];
		shared: string[];
	};
	policies: {
		active_count: number;
		last_audit_summary: null | Record<string, unknown>;
	};
	generator: SubtitleGenerator[];
	deep_scan: {
		enabled: boolean;
		hour: number;
		last_run_at: string | null;
		pending_file_count: number;
	};
}

export interface TvShowRollup {
	episodes_total: number;
	episodes_counted: number;
	status_counts: Record<string, number>;
	missing_languages: string[];
	missing_audio_languages: string[];
	missing_subtitle_languages: string[];
	dub_coverage: { ok: number; of: number };
	subtitle_coverage: { ok: number; of: number };
	status: AudioSubStatus;
	uniformity: ShowUniformity;
}

export interface AudioSubsTvItem {
	series_id: number;
	title: string;
	year: number;
	rollup: TvShowRollup;
	missing_languages: string[];
	dub_coverage: { ok: number; of: number };
	uniformity: ShowUniformity;
	episode_fraction: string;
	active_scan_job_ids: string[];
	active_generation_job_ids: string[];
}

export interface AudioSubsTvIndex {
	total: number;
	items: AudioSubsTvItem[];
	applied_filters: {
		status: string | null;
		uniformity: string | null;
		missing_language: string | null;
		q: string | null;
		sort_by: 'title' | 'status' | 'coverage';
	};
}

export interface TvEpisodeCoverage {
	episode_id: number;
	code: string;
	title: string;
	audio_languages: string[];
	subtitle_languages: string[];
	forced_languages: string[];
	sdh_languages: string[];
	tier: 'synced' | 'probed';
	status: AudioSubStatus;
	media_file_id: number | null;
}

export interface TvSeasonDetail {
	season_number: number;
	rollup: TvShowRollup;
	episodes: TvEpisodeCoverage[];
	active_scan_job_ids: string[];
	active_generation_job_ids: string[];
}

export interface AudioSubsTvDetail {
	series: {
		id: number;
		title: string;
		year: number;
	};
	preferred_audio_languages: string[];
	preferred_subtitle_languages: string[];
	rollup: TvShowRollup;
	seasons: TvSeasonDetail[];
}

export interface SubgenGpuHardware {
	index: number;
	name: string;
	vram_total: number;
	vram_free: number;
}

export interface SubgenHardwareResponse {
	hardware: {
		gpus: SubgenGpuHardware[];
		cpu_count: number;
		ram_total: number;
	};
	models: Record<
		string,
		{
			supported: boolean;
			reason?: string;
			vram_estimate_gb?: number;
			verdict?: 'supported' | 'vram_low' | 'too_big' | 'unsupported';
			note?: string;
		}
	>;
	catalog: Array<{
		id: string;
		params: string;
		vram_fp16_gb: number | null;
		vram_int8_gb: number | null;
		multilingual: boolean | null;
		can_translate: boolean | null;
		notes: string;
		cpu_ram_int8_gb?: number | null;
	}>;
	recommendation: {
		model: string;
		device: 'cpu' | 'cuda';
		gpu_index: number | null;
		compute_type: string;
		reason: string;
	} | null;
}

export interface SubgenSettings {
	deployment?: 'disabled' | 'external' | 'embedded';
	url?: string | null;
	profile_name?: string | null;
	model_label?: string | null;
	mode?: 'transcribe' | 'translate';
	local_path_prefix?: string | null;
	remote_path_prefix?: string | null;
	callback_token?: string | null;
	whisper_model?: string | null;
	embedded_port?: number | null;
	transcribe_device?: 'auto' | 'cpu' | 'cuda';
	gpu_index?: number | null;
	compute_type?: string | null;
	concurrent_transcriptions?: number | null;
	whisper_threads?: number | null;
	model_path?: string | null;
	naming_type?: 'ISO_639_1' | 'ISO_639_2_T' | 'ISO_639_2_B' | 'NAME' | 'NATIVE';
	name_includes_subgen?: boolean;
	name_includes_model?: boolean;
}
