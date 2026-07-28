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

const presentation = {
	version: 1,
	job_id: DETAIL_JOB_ID,
	job_type: 'poster_pipeline',
	label: 'Poster Pipeline',
	label_key: 'jobs.poster_pipeline.label',
	feature_area: 'ai_posters',
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

const server = createServer((req, res) => {
	const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
	const path = url.pathname;
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
