// A tiny synthetic canonical backend for hermetic E2E runs. It returns
// well-shaped empty job/activity/system payloads so the SvelteKit app can render
// its shell without the live API, database, or media. It never talks to any
// external service. The SvelteKit API proxy targets this server via
// MARQUEE_API_URL (see playwright.config.ts).
import { createServer } from 'node:http';

const PORT = Number(process.env.SYNTHETIC_BACKEND_PORT ?? 3199);
const DETAIL_JOB_ID = 'detail00000000000000000000000001';
const ONBOARDING_JOB_ID = 'onboarding00000000000000000001';
const ONBOARDING_RUN_ID = 'onboardingreview000000000000001';
const now = '2026-07-16T12:00:00Z';
// Full-body library-poster responses served, read by the caching specs.
let posterBodyServes = 0;
const posterSummary = (hasPoster) => ({
	has_poster: hasPoster,
	ai_selected: false,
	user_approved: hasPoster,
	deployed_at: hasPoster ? now : null,
	version: hasPoster ? 'synthetic0001' : null
});
const seasonSummary = (seriesId, seasonNumber, hasPoster) => ({
	id: seriesId * 100 + seasonNumber,
	season_number: seasonNumber,
	episode_count: seasonNumber === 0 ? 2 : 10,
	episode_file_count: seasonNumber === 0 ? 1 : 8,
	poster: posterSummary(hasPoster)
});
const emptyPipelineMetrics = {
	window_runs: 0,
	by_status: {},
	by_scorer: {},
	distinct_batches: 0,
	duration_seconds: { avg: null, p50: null, p90: null, max: null },
	avg_counts: {},
	total_counts: {},
	avg_stage_seconds: {},
	total_stage_seconds: {}
};
// Two films and two shows with nothing deployed, so the poster workspaces' Run tabs
// have something to select. Deliberately small: the specs assert on counts.
const missingMovies = [
	{
		id: 8101,
		title: 'Synthetic Feature One',
		year: 2019,
		tmdb_id: 8101,
		genres: null,
		container: null,
		video_width: null,
		video_height: null,
		resolution: null,
		poster_status: 'missing',
		review_pending: false,
		poster_url: null,
		media_file_id: null
	},
	{
		id: 8102,
		title: 'Synthetic Feature Two',
		year: 2021,
		tmdb_id: 8102,
		genres: null,
		container: null,
		video_width: null,
		video_height: null,
		resolution: null,
		poster_status: 'missing',
		review_pending: false,
		poster_url: null,
		media_file_id: null
	}
];
const libraryMovies = [
	{
		id: 8301,
		title: 'Midnight Archive',
		year: 1987,
		tmdb_id: 8301,
		genres: ['Thriller', 'Mystery'],
		container: 'mkv',
		video_width: 1920,
		video_height: 1080,
		resolution: '1080p',
		poster_status: 'deployed',
		review_pending: false,
		poster_url: '/api/library/movies/8301/poster?v=synthetic0001',
		media_file_id: 8301
	},
	{
		id: 8302,
		title: 'Paper Moons',
		year: 2004,
		tmdb_id: 8302,
		genres: ['Drama'],
		container: 'mp4',
		video_width: 3840,
		video_height: 2160,
		resolution: '2160p',
		poster_status: 'review',
		review_pending: true,
		poster_url: '/api/library/movies/8302/poster?v=synthetic0001',
		media_file_id: 8302
	},
	{
		id: 8303,
		title: 'Signal House',
		year: 2025,
		tmdb_id: 8303,
		genres: ['Science Fiction'],
		container: 'mkv',
		video_width: 1280,
		video_height: 720,
		resolution: '720p',
		poster_status: 'missing',
		review_pending: false,
		poster_url: null,
		media_file_id: 8303
	},
	{
		id: 8304,
		title: 'Glass Harbor',
		year: 2012,
		tmdb_id: 8304,
		genres: ['Adventure', 'Drama'],
		container: 'mkv',
		video_width: 1920,
		video_height: 1080,
		resolution: '1080p',
		poster_status: 'approved',
		review_pending: false,
		poster_url: '/api/library/movies/8304/poster?v=synthetic0001',
		media_file_id: 8304
	}
];
const librarySeries = [
	{
		id: 9301,
		title: 'Complete Edition',
		year: 2018,
		tmdb_id: 9301,
		genres: ['Drama', 'Mystery'],
		poster: posterSummary(true),
		review_pending: false,
		downloaded_seasons: 3,
		seasons_with_poster: 3,
		season_poster_status: 'complete',
		season_count: 3,
		seasons: [
			seasonSummary(9301, 0, true),
			seasonSummary(9301, 1, true),
			seasonSummary(9301, 2, true)
		]
	},
	{
		id: 9302,
		title: 'Season Gap',
		year: 2020,
		tmdb_id: 9302,
		genres: ['Science Fiction', 'Drama'],
		poster: posterSummary(true),
		review_pending: true,
		downloaded_seasons: 8,
		seasons_with_poster: 3,
		season_poster_status: 'partial',
		season_count: 8,
		seasons: [
			seasonSummary(9302, 1, true),
			seasonSummary(9302, 2, false),
			seasonSummary(9302, 3, true),
			seasonSummary(9302, 4, false),
			seasonSummary(9302, 5, true),
			seasonSummary(9302, 6, false),
			seasonSummary(9302, 7, false),
			seasonSummary(9302, 8, false)
		]
	},
	{
		id: 9303,
		title: 'Series Gap',
		year: 2022,
		tmdb_id: 9303,
		genres: ['Documentary'],
		poster: posterSummary(false),
		review_pending: false,
		downloaded_seasons: 2,
		seasons_with_poster: 2,
		season_poster_status: 'complete',
		season_count: 2,
		seasons: [seasonSummary(9303, 1, true), seasonSummary(9303, 2, true)]
	},
	{
		id: 9304,
		title: 'The Long Combined Gap Case',
		year: 2024,
		tmdb_id: 9304,
		genres: null,
		poster: posterSummary(false),
		review_pending: false,
		downloaded_seasons: 4,
		seasons_with_poster: 0,
		season_poster_status: 'missing',
		season_count: 4,
		seasons: [
			seasonSummary(9304, 1, false),
			seasonSummary(9304, 2, false),
			seasonSummary(9304, 3, false),
			seasonSummary(9304, 4, false)
		]
	}
];
const tvRunQueueItems = [
	{
		series: {
			id: 9201,
			title: 'Synthetic Series One',
			year: 2018,
			tmdb_id: 9201,
			poster_url: null
		},
		show_poster_missing: true,
		missing_seasons: [],
		assets_to_run: [{ media_type: 'series' }],
		no_tmdb: false
	},
	{
		series: {
			id: 9202,
			title: 'Synthetic Series Two',
			year: 2022,
			tmdb_id: 9202,
			poster_url: null
		},
		show_poster_missing: true,
		missing_seasons: [],
		assets_to_run: [{ media_type: 'series' }],
		no_tmdb: false
	}
];
const tvReviewQueueItems = [
	{
		series: { id: 9301, title: 'Synthetic Review Series', year: 2020, tmdb_id: 9301 },
		show_run: {
			run_id: 'synthetic-tv-show-review',
			status: 'completed',
			started_at: now,
			completed_at: now,
			scorer_name: null,
			counts: { ranked: 5 },
			reviewed: false,
			auto_pick_poster_url: null
		},
		season_runs: [
			{
				season_number: 1,
				season_id: 93011,
				run: {
					run_id: 'synthetic-tv-season-review',
					status: 'flagged_manual',
					started_at: now,
					completed_at: now,
					scorer_name: null,
					counts: { ranked: 0 },
					reviewed: false
				},
				auto_pick_poster_url: null,
				flagged_no_candidates: true
			}
		],
		seasons_only: false,
		display_poster_url: null
	}
];
const emptyTvSummary = {
	shows_total: 0,
	shows_with_show_poster: 0,
	shows_missing_show_poster: 0,
	seasons_total: 0,
	seasons_with_poster: 0,
	seasons_missing_poster: 0,
	shows_fully_covered: 0,
	shows_in_review: 0,
	seasons_in_review: 0,
	assets_in_review: 0,
	assets_in_run: 0,
	shows_no_tmdb: 0,
	running_jobs: [],
	last_heal: null,
	heal_schedule: null,
	backups: { count: 0, bytes: 0 }
};

const presentation = {
	version: 1,
	job_id: DETAIL_JOB_ID,
	job_type: 'poster_pipeline',
	label: 'Poster Pipeline',
	label_key: 'jobs.poster_pipeline.label',
	feature_area: 'ai_posters',
	feature_label: 'Posters',
	presentation_family: 'ai_posters',
	presenter_key: 'jobs.poster_pipeline',
	presenter_version: 1,
	subject: {
		kind: 'movie',
		display_id: 'movie:42',
		display_name: 'Deleted synthetic movie',
		artwork_key: null,
		context: ['English', 'synthetic fixture'],
		snapshot_at: now,
		missing_live_subject: true
	},
	status: {
		label: 'Failed',
		label_key: 'jobs.status.failed',
		phase: 'terminal',
		outcome: 'failed',
		tone: 'negative'
	},
	trigger: { kind: 'manual', label: 'Started manually', initiator: 'Synthetic operator' },
	action: {
		headline: 'Poster selection stopped',
		explanation: 'Review the retained diagnostics.'
	},
	attention: {
		level: 'error',
		reason: 'failed',
		message: 'Poster selection failed',
		remediation: 'Review the failed attempt log.'
	},
	progress: null,
	impact: null,
	allowed_actions: ['retry', 'open_logs', 'open_artifacts', 'open_detail'],
	warnings: [{ code: 'fixture_warning', message: 'Synthetic warning' }],
	failures: [
		{
			code: 'poster_pipeline_failed',
			message: 'Poster pipeline failed',
			remediation: 'Inspect stderr.',
			stage: 'scoring'
		}
	],
	suggested_actions: [],
	evidence: { logs_available: true, artifacts_available: true },
	links: {
		snapshot: `/api/jobs/${DETAIL_JOB_ID}/snapshot`,
		presentation: `/api/jobs/${DETAIL_JOB_ID}/presentation`,
		attempts: `/api/jobs/${DETAIL_JOB_ID}/attempts`,
		events: `/api/jobs/${DETAIL_JOB_ID}/events`,
		children: `/api/jobs/${DETAIL_JOB_ID}/children`,
		artifacts: `/api/jobs/${DETAIL_JOB_ID}/artifacts`,
		request: `/api/jobs/${DETAIL_JOB_ID}/raw/request`,
		plan: `/api/jobs/${DETAIL_JOB_ID}/raw/plan`,
		result: `/api/jobs/${DETAIL_JOB_ID}/raw/result`,
		error: `/api/jobs/${DETAIL_JOB_ID}/raw/error`
	},
	sections: [
		{
			kind: 'facts',
			title: 'Poster analysis',
			facts: [{ label: 'Language', label_key: null, value: { type: 'text', text: 'English' } }]
		},
		{
			kind: 'steps',
			steps: [{ key: 'scoring', label: 'Rank candidates', state: 'failed', at: now }]
		},
		{ kind: 'notice', tone: 'warning', message: 'The source movie is no longer in the library.' }
	]
};

const attempts = [
	{
		origin_job_id: DETAIL_JOB_ID,
		origin_subject: { display_name: 'Deleted synthetic movie' },
		number: 2,
		phase: 'terminal',
		outcome: 'failed',
		admitted_at: now,
		started_at: now,
		stopping_at: null,
		finished_at: now,
		worker_node_id: 'fixture-node',
		worker_build: 'e2e',
		exit_code: 1,
		exit_signal: null,
		failure_class: 'encoder_exit',
		metrics: null,
		error: { message: 'redacted' }
	},
	{
		origin_job_id: DETAIL_JOB_ID,
		origin_subject: { display_name: 'Deleted synthetic movie' },
		number: 1,
		phase: 'terminal',
		outcome: 'failed',
		admitted_at: now,
		started_at: now,
		stopping_at: null,
		finished_at: now,
		worker_node_id: 'fixture-node',
		worker_build: 'e2e',
		exit_code: 1,
		exit_signal: null,
		failure_class: 'encoder_exit',
		metrics: null,
		error: null
	}
];

function json(res, status, body) {
	res.writeHead(status, { 'content-type': 'application/json' });
	res.end(JSON.stringify(body));
}

const onboardingStatus = () => ({
	state: 'collecting',
	active_positive_subjects: 12,
	active_negative_subjects: 3,
	pending_positive_subjects: 0,
	revision: 'a'.repeat(64),
	thresholds: { required: 50, encouraged: 75, strong_target: 100 },
	build_revision: null,
	build_job_id: null,
	profile_generations: {},
	consumer_reloaded: false,
	next_action: 'choose another poster',
	failure: null,
	libraries: {
		movies: {
			active: { generation: null, checksum: null, revision: null, compatible: false },
			desired_revision: null,
			desired_generation: null,
			build: {
				id: null,
				job_id: null,
				state: null,
				revision: null,
				expected_generation: null,
				retry_of: null,
				failure: null
			},
			reload_state: { expected_checksum: null, observed_checksum: null, ready: false },
			residual: { active: false, compatible: false, dormant: false },
			rebuild_due: false,
			update_attention: false
		},
		tv: {
			active: {
				generation: 2,
				checksum: 'b'.repeat(64),
				revision: 'c'.repeat(64),
				compatible: true
			},
			desired_revision: 'c'.repeat(64),
			desired_generation: 2,
			build: {
				id: 'fixture-tv-build',
				job_id: 'fixturetvbuild000000000000000001',
				state: 'succeeded',
				revision: 'c'.repeat(64),
				expected_generation: 2,
				retry_of: null,
				failure: null
			},
			reload_state: {
				expected_checksum: 'b'.repeat(64),
				observed_checksum: 'b'.repeat(64),
				ready: true
			},
			residual: { active: false, compatible: false, dormant: false },
			rebuild_due: false,
			update_attention: false
		}
	},
	initial_profiles_ready: false,
	personalized_scoring_available: false,
	rebuild_due: false,
	residual_dormant: false,
	active_jobs: [],
	review: {
		run_id: ONBOARDING_RUN_ID,
		analysis_job_id: ONBOARDING_JOB_ID,
		url: `/onboarding?review=${ONBOARDING_RUN_ID}`
	}
});

const onboardingReview = () => ({
	version: 1,
	run_id: ONBOARDING_RUN_ID,
	analysis_job_id: ONBOARDING_JOB_ID,
	status: 'completed',
	subject: { kind: 'movie', title: 'Synthetic First Movie', year: 2026 },
	review_revision: 'd'.repeat(64),
	candidates: [
		{
			candidate_id: 'e'.repeat(32),
			image_url: `/api/pipeline/runs/${ONBOARDING_RUN_ID}/posters/synthetic-choice.svg`,
			source: 'Synthetic archive',
			eligibility: {
				status: 'survived_objective_filters',
				ocr: { summary: 'Title text verified.' }
			},
			facts: { width: 1000, height: 1500, language: 'en' }
		}
	],
	rejections: { available: true },
	allowed_actions: { choose: true, hate: true },
	links: {
		activity: `/projection-room?view=queue&job=${ONBOARDING_JOB_ID}`,
		detail: `/projection-room/jobs/${ONBOARDING_JOB_ID}`,
		run: `/api/pipeline/runs/${ONBOARDING_RUN_ID}`
	}
});

let settingsVersion = 7;
const settingsValues = {
	APP_NAME: 'Marquee',
	SYNC_INTERVAL_MINUTES: 30,
	WEBHOOK_DRY_RUN: false,
	DATA_DIR: '/app/data',
	MEDIA_ROOTS: [],
	RADARR_URL: 'http://radarr:7878',
	RADARR_INSTANCE_NAME: 'Cinema Rack',
	SONARR_URL: 'http://sonarr:8989',
	SONARR_INSTANCE_NAME: 'Series Rack',
	RADARR_PATH_PREFIX: '/movies',
	RADARR_MEDIA_PATH: '/movies',
	RADARR_PATH_MAPPINGS: null,
	SONARR_PATH_PREFIX: '/tv',
	SONARR_MEDIA_PATH: '/television',
	SONARR_PATH_MAPPINGS: null,
	POSTER_CACHE_DIR: '/app/data/cache/posters',
	POSTER_STAGING_DIR: '/app/data/staging',
	MOVIE_POSTER_FORMAT: 'poster.jpg',
	SERIES_POSTER_FORMAT: 'show.jpg',
	SEASON_POSTER_FORMAT: 'season{season:02d}.jpg',
	HEAL_RECENT_DEPLOY_GRACE_MINUTES: 10,
	POSTER_BACKUP_DIR: '/app/data/poster-backups',
	PREFERRED_LANG: 'en',
	WEIGHT_AESTHETIC: 0.35,
	K_NEIGHBORS: 20,
	KNN_SOFTMAX_TEMP: 0.07,
	LOG_LEVEL: 'INFO',
	JOB_WORKER_CONCURRENCY: 4,
	AUTH_ALLOW_LOCAL: false,
	AUTH_BRUTE_LOCKOUT_ATTEMPTS: 8
};

const settingDefaults = { ...settingsValues, APP_NAME: 'Marquee' };
/**
 * Keys carrying a stored override. The real server derives `sources` from the
 * revision document; resetting removes the key rather than writing its default
 * back, so this set is what makes a key read "custom" instead of "default".
 */
const overriddenKeys = new Set();

const settingEntry = (key, tab, section, level, control, applyMode = 'next_job') => ({
	key,
	title: key
		.toLowerCase()
		.split('_')
		.map((word) => word[0].toUpperCase() + word.slice(1))
		.join(' '),
	description: `Synthetic ${key.toLowerCase().replaceAll('_', ' ')} setting.`,
	owner: ['PREFERRED_LANG', 'WEIGHT_AESTHETIC', 'K_NEIGHBORS', 'KNN_SOFTMAX_TEMP'].includes(key)
		? 'pipeline'
		: 'app',
	scope: 'application',
	storage: 'revision',
	sensitivity: key.includes('DIR') || key.includes('ROOT') ? 'private' : 'public',
	apply_mode: applyMode,
	tab,
	section,
	level,
	control,
	visible: key !== 'MEDIA_ROOTS',
	editable: true
});

const settingsCatalog = Object.fromEntries(
	[
		settingEntry('APP_NAME', 'general', 'Application', 'standard', { kind: 'text' }, 'restart'),
		settingEntry('SYNC_INTERVAL_MINUTES', 'connections', 'Synchronization', 'standard', {
			kind: 'int',
			min: 1,
			max: 1440
		}),
		settingEntry('RADARR_INSTANCE_NAME', 'connections', 'Services', 'standard', { kind: 'str' }),
		settingEntry('SONARR_INSTANCE_NAME', 'connections', 'Services', 'standard', { kind: 'str' }),
		settingEntry('WEBHOOK_DRY_RUN', 'connections', 'Synchronization', 'advanced', {
			kind: 'bool'
		}),
		settingEntry('MEDIA_ROOTS', 'media', 'Library paths', 'standard', { kind: 'list' }),
		settingEntry('RADARR_PATH_MAPPINGS', 'media', 'Library paths', 'standard', {
			kind: 'list'
		}),
		settingEntry('SONARR_PATH_MAPPINGS', 'media', 'Library paths', 'standard', {
			kind: 'list'
		}),
		settingEntry(
			'DATA_DIR',
			'media',
			'Application paths',
			'advanced',
			{
				kind: 'path'
			},
			'restart'
		),
		settingEntry(
			'POSTER_CACHE_DIR',
			'media',
			'Application paths',
			'advanced',
			{
				kind: 'path'
			},
			'restart'
		),
		settingEntry(
			'POSTER_STAGING_DIR',
			'media',
			'Application paths',
			'advanced',
			{
				kind: 'path'
			},
			'restart'
		),
		settingEntry('MOVIE_POSTER_FORMAT', 'posters', 'Poster behavior', 'standard', {
			kind: 'text'
		}),
		settingEntry('HEAL_RECENT_DEPLOY_GRACE_MINUTES', 'posters', 'Storage and Healing', 'advanced', {
			kind: 'int',
			min: 0,
			max: 10080
		}),
		settingEntry(
			'POSTER_BACKUP_DIR',
			'media',
			'Application paths',
			'advanced',
			{
				kind: 'path'
			},
			'restart'
		),
		settingEntry('PREFERRED_LANG', 'pipeline', 'Runtime Defaults', 'standard', {
			kind: 'enum',
			options: ['en', 'fr']
		}),
		settingEntry('WEIGHT_AESTHETIC', 'pipeline', 'Scoring Weights', 'advanced', {
			kind: 'weight',
			min: 0,
			max: 1,
			step: 0.05
		}),
		settingEntry('K_NEIGHBORS', 'taste', 'Taste profile', 'standard', {
			kind: 'int',
			min: 1,
			max: 200
		}),
		settingEntry('KNN_SOFTMAX_TEMP', 'taste', 'Calibration', 'advanced', {
			kind: 'float',
			min: 0.01,
			max: 2,
			step: 0.01
		}),
		settingEntry(
			'LOG_LEVEL',
			'system',
			'Logging',
			'standard',
			{
				kind: 'enum',
				options: ['DEBUG', 'INFO', 'WARNING', 'ERROR']
			},
			'restart'
		),
		settingEntry(
			'JOB_WORKER_CONCURRENCY',
			'system',
			'Workers',
			'advanced',
			{
				kind: 'int',
				min: 1,
				max: 32
			},
			'restart'
		),
		settingEntry('AUTH_ALLOW_LOCAL', 'access', 'Request access', 'standard', {
			kind: 'bool'
		}),
		settingEntry('AUTH_BRUTE_LOCKOUT_ATTEMPTS', 'access', 'Defensive limits', 'advanced', {
			kind: 'int',
			min: 1,
			max: 100
		})
	].map((entry) => [entry.key, entry])
);

const settingsDocument = () => ({
	configuration_version: settingsVersion,
	etag: `synthetic-settings-${settingsVersion}`,
	stale: false,
	health: { status: 'valid' },
	catalog: settingsCatalog,
	values: settingsValues,
	defaults: settingDefaults,
	sources: Object.fromEntries(
		Object.keys(settingsCatalog).map((key) => [key, overriddenKeys.has(key) ? 'custom' : 'default'])
	),
	secrets: Object.fromEntries(
		['TMDB_READ_ACCESS_TOKEN', 'RADARR_API_KEY', 'SONARR_API_KEY'].map((key) => [
			key,
			{
				configured: true,
				source: key === 'TMDB_READ_ACCESS_TOKEN' ? 'environment' : 'managed',
				generation: 2,
				updated_at: now
			}
		])
	),
	integrations: {
		tmdb: { configured: true, name: 'The Movie Database' },
		radarr: {
			configured: true,
			name: settingsValues.RADARR_INSTANCE_NAME,
			url_configured: true,
			api_key_configured: true,
			path_mapping_configured: true
		},
		sonarr: {
			configured: true,
			name: settingsValues.SONARR_INSTANCE_NAME,
			url_configured: true,
			api_key_configured: true,
			path_mapping_configured: true
		}
	},
	secret_store: { writable: true, reason: null },
	deployment: {
		version: '0.0.0-e2e',
		environment: 'test',
		process_role: 'api',
		host: '0.0.0.0',
		port: 3165,
		debug: false,
		database_configured: true,
		api_key_configured: true,
		keyring_configured: true,
		mounts: [
			{ path: '/movies', exists: true, readable: true, writable: true },
			{ path: '/television', exists: true, readable: true, writable: true },
			{ path: '/app/data', exists: true, readable: true, writable: true },
			{ path: '/app/data/cache/posters', exists: true, readable: true, writable: true },
			{ path: '/app/data/staging', exists: true, readable: true, writable: true },
			{ path: '/app/data/poster-backups', exists: true, readable: true, writable: true }
		]
	},
	sync: {
		interval_minutes: settingsValues.SYNC_INTERVAL_MINUTES,
		cooldown_seconds: 30,
		heal_enabled: true,
		heal_interval_minutes: 30,
		webhook_dry_run: settingsValues.WEBHOOK_DRY_RUN
	},
	writable: true
});

/** Drop overrides so the shipped default becomes effective again. */
function applyRemovals(keys) {
	for (const key of keys) {
		if (!overriddenKeys.delete(key)) continue;
		settingsValues[key] = settingDefaults[key];
	}
}

const server = createServer((req, res) => {
	const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
	const path = url.pathname;
	if (req.method === 'POST' && path === '/api/settings/paths/test') {
		let body = '';
		req.setEncoding('utf8');
		req.on('data', (chunk) => (body += chunk));
		req.on('end', () => {
			const payload = JSON.parse(body || '{}');
			const checked = (mapping) => ({
				configured: true,
				prefix: mapping.arr_path,
				target: {
					path: mapping.marquee_path,
					exists: true,
					directory: true,
					readable: true,
					writable: true
				}
			});
			const pathMappings = {
				radarr: (payload.radarr_mappings ?? []).map(checked),
				sonarr: (payload.sonarr_mappings ?? []).map(checked)
			};
			const empty = { configured: false, prefix: null, target: null };
			json(res, 200, {
				ok: true,
				mutated: false,
				mappings: {
					radarr: pathMappings.radarr[0] ?? empty,
					sonarr: pathMappings.sonarr[0] ?? empty
				},
				path_mappings: pathMappings,
				media_roots: (payload.media_roots ?? []).map((mediaPath) => ({
					path: mediaPath,
					exists: true,
					directory: true,
					readable: true,
					writable: true
				}))
			});
		});
		return;
	}
	if (req.method === 'PUT' && path === '/api/settings/config') {
		let body = '';
		req.setEncoding('utf8');
		req.on('data', (chunk) => (body += chunk));
		req.on('end', () => {
			const payload = JSON.parse(body || '{}');
			const values = payload.values ?? {};
			Object.assign(settingsValues, values);
			for (const key of Object.keys(values)) overriddenKeys.add(key);
			applyRemovals(payload.removals ?? []);
			settingsVersion += 1;
			json(res, 200, {
				configuration_version: settingsVersion,
				etag: `synthetic-settings-${settingsVersion}`,
				changed: true,
				applied: [...Object.keys(values), ...(payload.removals ?? [])],
				settings: settingsDocument()
			});
		});
		return;
	}
	if (req.method === 'POST' && path === '/api/settings/config/reset') {
		let body = '';
		req.setEncoding('utf8');
		req.on('data', (chunk) => (body += chunk));
		req.on('end', () => {
			const payload = JSON.parse(body || '{}');
			const scope = payload.scope ?? 'all';
			const candidates =
				scope === 'keys'
					? (payload.keys ?? [])
					: Object.keys(settingsCatalog).filter((key) => {
							if (scope === 'all') return true;
							if (scope === 'tab') return settingsCatalog[key].tab === payload.tab;
							return settingsCatalog[key].section === payload.section;
						});
			const applied = candidates.filter((key) => overriddenKeys.has(key));
			applyRemovals(applied);
			if (applied.length) settingsVersion += 1;
			json(res, 200, {
				configuration_version: settingsVersion,
				etag: `synthetic-settings-${settingsVersion}`,
				changed: applied.length > 0,
				applied,
				settings: settingsDocument()
			});
		});
		return;
	}
	const integration = path.match(
		/^\/api\/settings\/integrations\/(tmdb|radarr|sonarr)(?:\/(test|credential))?$/
	);
	if (integration && req.method !== 'GET') {
		let body = '';
		req.setEncoding('utf8');
		req.on('data', (chunk) => (body += chunk));
		req.on('end', () => {
			const [, provider, action] = integration;
			const payload = JSON.parse(body || '{}');
			if (req.method === 'POST' && action === 'test') {
				json(res, 200, {
					ok: true,
					provider,
					status: { appName: provider === 'tmdb' ? 'TMDB' : provider, version: 'synthetic' }
				});
				return;
			}
			if (req.method === 'PUT' && !action) {
				if (provider === 'radarr') {
					if (payload.name) settingsValues.RADARR_INSTANCE_NAME = payload.name;
					if (payload.url) settingsValues.RADARR_URL = payload.url;
				}
				if (provider === 'sonarr') {
					if (payload.name) settingsValues.SONARR_INSTANCE_NAME = payload.name;
					if (payload.url) settingsValues.SONARR_URL = payload.url;
				}
				settingsVersion += 1;
				json(res, 200, {
					configuration_version: settingsVersion,
					etag: `synthetic-settings-${settingsVersion}`,
					changed: true,
					status: { appName: provider === 'tmdb' ? 'TMDB' : provider, version: 'synthetic' },
					settings: settingsDocument()
				});
				return;
			}
			if (req.method === 'DELETE' && action === 'credential') {
				json(res, 200, { cleared: true, settings: settingsDocument() });
				return;
			}
			json(res, 405, { detail: 'Method not allowed' });
		});
		return;
	}
	if (
		req.method === 'POST' &&
		(path === '/api/onboarding/choose' || path === '/api/onboarding/hate')
	) {
		const decision = path.endsWith('/choose') ? 'choose' : 'hate';
		json(res, 200, {
			decision,
			event_id: `synthetic-${decision}-event`,
			exemplar_id: `synthetic-${decision}-exemplar`,
			deployment_job_id: decision === 'choose' ? 'syntheticdeployment000000000000001' : null,
			disposition: 'created',
			status: onboardingStatus()
		});
		return;
	}
	if (req.method === 'POST' && path === '/api/onboarding/start') {
		json(res, 200, {
			subject: { kind: 'movie', id: 42, title: 'Synthetic First Movie', year: 2026 },
			analysis_job: {
				job_id: ONBOARDING_JOB_ID,
				disposition: 'created',
				phase: 'queued',
				snapshot_url: `/api/jobs/${ONBOARDING_JOB_ID}/snapshot`,
				detail_url: `/projection-room/jobs/${ONBOARDING_JOB_ID}`
			},
			status: onboardingStatus()
		});
		return;
	}
	if (req.method === 'POST' && path === '/api/onboarding/complete') {
		json(res, 200, { revision: 'a'.repeat(64), build_jobs: [], status: onboardingStatus() });
		return;
	}
	if (req.method !== 'GET') {
		json(res, 200, {});
		return;
	}
	if (path === '/api/onboarding/status') {
		json(res, 200, onboardingStatus());
		return;
	}
	if (path === '/api/settings') {
		json(res, 200, settingsDocument());
		return;
	}
	if (path === `/api/onboarding/runs/${ONBOARDING_RUN_ID}/review`) {
		json(res, 200, onboardingReview());
		return;
	}
	if (path === `/api/pipeline/runs/${ONBOARDING_RUN_ID}/posters/synthetic-choice.svg`) {
		res.writeHead(200, { 'content-type': 'image/svg+xml' });
		res.end(
			'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1500"><rect width="100%" height="100%" fill="#263248"/><text x="500" y="750" text-anchor="middle" fill="#f5c96c" font-size="56">Synthetic poster</text></svg>'
		);
		return;
	}
	// Observability for the caching specs: a poster fetched from the browser
	// cache never reaches this server, so this counter is the ground truth for
	// "the revisit re-downloaded nothing".
	if (path === '/__test/poster-body-serves') {
		json(res, 200, { count: posterBodyServes });
		return;
	}
	const libraryPoster = path.match(/^\/api\/library\/(movies|series|seasons)\/(\d+)\/poster$/);
	if (libraryPoster) {
		const [, kind, id] = libraryPoster;
		const label = kind === 'movies' ? 'FILM' : kind === 'series' ? 'SHOW' : `S${id.slice(-2)}`;
		const accent = kind === 'series' ? '#5eead4' : '#fbbf24';
		// Mirror the real caching policy: URLs presenting the current version
		// token are immutable, everything else revalidates via ETag, and a
		// matching If-None-Match gets a bodiless 304.
		const version = url.searchParams.get('v');
		const cacheControl =
			version === 'synthetic0001' ? 'public, max-age=31536000, immutable' : 'public, no-cache';
		if ((req.headers['if-none-match'] ?? '').includes('"synthetic0001"')) {
			res.writeHead(304, { etag: '"synthetic0001"', 'cache-control': cacheControl });
			res.end();
			return;
		}
		posterBodyServes += 1;
		res.writeHead(200, {
			'content-type': 'image/svg+xml',
			etag: '"synthetic0001"',
			'cache-control': cacheControl
		});
		res.end(
			`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="960"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#17243d"/><stop offset="1" stop-color="#080d16"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/><circle cx="320" cy="390" r="150" fill="none" stroke="${accent}" stroke-width="10" opacity=".72"/><text x="320" y="420" text-anchor="middle" fill="${accent}" font-family="system-ui" font-size="72" font-weight="700">${label}</text><text x="320" y="850" text-anchor="middle" fill="#f5f5f4" font-family="system-ui" font-size="28">Marquee fixture</text></svg>`
		);
		return;
	}
	if (path === '/api/pipeline/run-queue') {
		json(res, 200, {
			total: missingMovies.length,
			page: 1,
			page_size: 60,
			items: missingMovies
		});
		return;
	}
	if (path === '/api/library/movies') {
		const query = (url.searchParams.get('q') ?? '').trim().toLowerCase();
		const posterStatus = url.searchParams.get('poster_status');
		const artworkStatus = url.searchParams.get('artwork_status');
		const sort = url.searchParams.get('sort') ?? 'title';
		const page = Math.max(1, Number(url.searchParams.get('page') ?? 1));
		const pageSize = Math.max(1, Number(url.searchParams.get('page_size') ?? 60));
		// Preserve the two-item missing-poster queue fixture while exercising the
		// same search, filter, sort, and pagination contract as the real endpoint.
		const sourceMovies = posterStatus === 'missing' ? missingMovies : libraryMovies;
		let items = sourceMovies.filter(
			(movie) =>
				(!query || movie.title.toLowerCase().includes(query)) &&
				(!posterStatus || movie.poster_status === posterStatus) &&
				(!artworkStatus ||
					(artworkStatus === 'review' && movie.review_pending) ||
					(artworkStatus === 'deployed' &&
						!movie.review_pending &&
						movie.poster_status !== 'missing') ||
					(artworkStatus === 'missing' &&
						!movie.review_pending &&
						movie.poster_status === 'missing'))
		);
		items = items.toSorted((left, right) =>
			sort === 'year'
				? (right.year ?? 0) - (left.year ?? 0) || left.title.localeCompare(right.title)
				: left.title.localeCompare(right.title)
		);
		const total = items.length;
		items = items.slice((page - 1) * pageSize, page * pageSize);
		json(res, 200, {
			total,
			page,
			page_size: pageSize,
			review_pending_total: libraryMovies.filter((movie) => movie.review_pending).length,
			items
		});
		return;
	}
	if (path === '/api/library/series') {
		json(res, 200, {
			total: librarySeries.length,
			page: 1,
			page_size: Number(url.searchParams.get('page_size') ?? 200),
			review_pending_total: librarySeries.filter((series) => series.review_pending).length,
			items: librarySeries
		});
		return;
	}
	if (path === '/api/pipeline/review-queue') {
		json(res, 200, { total: 0, page: 1, page_size: 60, items: [] });
		return;
	}
	if (path === '/api/pipeline/cache') {
		json(res, 200, {
			sizes_bytes: { runs_work: 0, staging: 0, embeddings: 0, archives: 0 },
			clearable_bytes: 0,
			total_bytes: 0
		});
		return;
	}
	if (path === '/api/pipeline/metrics' || path === '/api/pipeline/tv/metrics') {
		json(res, 200, emptyPipelineMetrics);
		return;
	}
	if (path === '/api/pipeline/tv/summary') {
		json(res, 200, emptyTvSummary);
		return;
	}
	if (path === '/api/pipeline/tv/run-queue') {
		json(res, 200, { total: tvRunQueueItems.length, items: tvRunQueueItems });
		return;
	}
	if (path === '/api/pipeline/tv/review-queue') {
		json(res, 200, {
			total_series: tvReviewQueueItems.length,
			page: 1,
			page_size: 200,
			items: tvReviewQueueItems
		});
		return;
	}

	if (path === '/api/jobs') {
		const view = url.searchParams.get('view') ?? 'queue';
		json(res, 200, { items: [], limit: 200, next_cursor: null, view });
		return;
	}
	if (path === '/api/jobs/attention') {
		json(res, 200, {
			running: 0,
			waiting_held: 0,
			retrying: 0,
			needs_attention: 0,
			warning: 0,
			error: 0,
			highest_severity: 'normal'
		});
		return;
	}
	if (path === `/api/jobs/${DETAIL_JOB_ID}/presentation`) {
		json(res, 200, presentation);
		return;
	}
	if (path === `/api/jobs/${DETAIL_JOB_ID}/events`) {
		json(res, 200, {
			items: [
				{
					id: 1,
					event_key: 'attempt.failed',
					state: 'failed',
					stage: 'encode',
					message: 'Encoder stopped',
					detail: null,
					canonical_version: 4,
					created_at: now
				}
			],
			limit: 100,
			next_cursor: null
		});
		return;
	}
	if (path === `/api/jobs/${DETAIL_JOB_ID}/attempts`) {
		json(res, 200, { items: attempts, limit: 50, next_cursor: null });
		return;
	}
	if (path.match(new RegExp(`^/api/jobs/${DETAIL_JOB_ID}/attempts/\\d+/logs$`))) {
		json(res, 200, {
			items: [
				{
					version: 1,
					cursor: 1,
					timestamp: now,
					level: 'error',
					source: 'stderr',
					stage: 'encode',
					message: 'secret=[REDACTED] synthetic failure'
				}
			],
			limit: 200,
			next_cursor: null,
			last_cursor: 1,
			freshness: 'sealed',
			sealed: true,
			truncated: true,
			opened_at: now,
			closed_at: now,
			expires_at: '2026-08-16T12:00:00Z',
			byte_count: 48,
			stored_byte_count: 48,
			compression: 'gzip'
		});
		return;
	}
	if (path === `/api/jobs/${DETAIL_JOB_ID}/artifacts`) {
		json(res, 200, {
			items: [
				{
					id: 1,
					attempt_id: 2,
					kind: 'diagnostic',
					name: 'stderr.txt',
					content_type: 'text/plain',
					size_bytes: 48,
					checksum: 'synthetic',
					retention_class: 'diagnostic',
					status: 'available',
					created_at: now,
					expires_at: '2026-08-16T12:00:00Z',
					virtual: false,
					available: true,
					download_url: `/api/jobs/${DETAIL_JOB_ID}/artifacts/1/download`
				}
			],
			limit: 50,
			next_cursor: null
		});
		return;
	}
	if (path.startsWith(`/api/jobs/${DETAIL_JOB_ID}/raw/`)) {
		json(res, 200, {
			kind: path.split('/').at(-1),
			api_key: '[REDACTED]',
			note: 'synthetic diagnostic document'
		});
		return;
	}
	if (path === '/api/system/metrics/history') {
		const now = new Date().toISOString();
		json(res, 200, {
			window: '1h',
			start_at: now,
			end_at: now,
			points: [
				{
					ts: now,
					cpu_avg: 18.5,
					gpu_util: null,
					gpu_mem: null,
					gpu_enc: null,
					gpu_dec: null,
					ram_pct: 42.5,
					disk_read_bps: 1024,
					disk_write_bps: 2048,
					net_recv_bps: 4096,
					net_sent_bps: 512,
					active_jobs: 1
				}
			],
			jobs: [],
			jobs_truncated: false
		});
		return;
	}
	if (path === '/api/system/operations') {
		const now = new Date().toISOString();
		json(res, 200, {
			version: 1,
			generated_at: now,
			node: {
				cpu_model: 'Synthetic CPU',
				cpu_percent: 18.5,
				cpu_temperature_c: 44,
				ram_percent: 42.5,
				ram_used_bytes: 4294967296,
				ram_total_bytes: 8589934592,
				gpu_model: null,
				gpu_percent: null,
				gpu_memory_percent: null,
				network_received_bytes: 4096,
				network_sent_bytes: 512,
				uptime: '2h 15m'
			},
			workers: {
				active: 1,
				queued: 2,
				supervisor_available: true,
				listener_healthy: true
			},
			transport: { picked: 1, held_failed: 0, oldest_eligible_age_seconds: 3.5 },
			database: {
				observed_connections: 4,
				connection_roles: { api: 2, worker: 2 },
				pool_size: 5,
				pool_checked_in: 4,
				pool_checked_out: 1,
				pool_overflow: 0,
				budget: { configured: 8, maximum: 20, within_budget: true }
			},
			events: { listener_healthy: true, source: 'postgres', last_observed_event_at: now },
			storage: {
				poster_cache_items: 12,
				poster_cache_bytes: 1048576,
				disk_percent: 31.25,
				disk_used_bytes: 1073741824,
				disk_total_bytes: 34359738368
			},
			contracts: [
				{
					component: 'jobs',
					expected_version: 1,
					durability: 'durable',
					catalog_fingerprint: 'synthetic'
				}
			]
		});
		return;
	}
	// Everything else the shell may probe: a benign empty document.
	json(res, 200, {});
});

server.listen(PORT, '127.0.0.1', () => {
	console.log(`synthetic backend listening on http://127.0.0.1:${PORT}`);
});
