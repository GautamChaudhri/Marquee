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
}

export interface LetterboxSample {
	minute: number;
	ok: boolean;
	top_bar?: number;
	bottom_bar?: number;
	bar?: number;
	error?: string | null;
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
	total: number;
	completed: number;
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
	created_at: string | null;
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
