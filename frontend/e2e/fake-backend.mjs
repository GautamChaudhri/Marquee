// A small stand-in for the Marquee API, for the end-to-end tests only. It answers the routes
// the web app calls, in the shapes of frontend/openapi.json, and refuses any request whose
// X-Api-Key is not the test key, as the real API does. Jobs created through it advance from
// queued to running to succeeded on their own; the jobs it starts with stay as they are.
//
// Test hooks under /__fake/ need no key: `health`, `last` (what the last /api request
// carried: whether the key matched and whether a cookie came through, never a value), and
// `reset`.
import { createServer } from 'node:http';

const PORT = Number(process.env.FAKE_API_PORT ?? 3199);
const KEY = process.env.FAKE_API_KEY ?? '';
if (!KEY) throw new Error('FAKE_API_KEY must be set');

// A 2x3 grey PNG: the browser draws it scaled into each poster frame.
const IMAGE = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAFElEQVR4nGPMK6hgYGBgYmBgQFAAGawBXM4IhbAAAAAASUVORK5CYII=',
	'base64'
);
const NOW = Date.parse('2026-11-24T12:00:00Z');
const iso = (ms) => new Date(ms).toISOString();

function movieRow(id, title, year, extra = {}) {
	const sha = (id.toString(16).padStart(2, '0') + 'ab').repeat(16);
	return {
		id,
		radarr_id: id,
		tmdb_id: 1000 + id,
		imdb_id: `tt${String(id).padStart(7, '0')}`,
		title,
		sort_title: title.toLowerCase().replace(/^the /, ''),
		year,
		genres: ['Drama'],
		has_file: true,
		file_size_bytes: 20_000_000_000 + id,
		added_at: iso(NOW - id * 86_400_000),
		director: 'A. Director',
		production_companies: ['A Studio'],
		tagline: null,
		poster: {
			file_name: 'poster.jpg',
			sha256: sha,
			size_bytes: 500_000,
			origin: 'library',
			url: `/api/movies/${id}/poster`,
			version: sha.slice(0, 12)
		},
		poster_origin: 'library',
		latest_run_id: null,
		...extra
	};
}

function initialState() {
	const movies = [
		movieRow(1, 'Heat', 1995, { latest_run_id: 11, tagline: 'A Los Angeles crime saga.' }),
		movieRow(2, 'Alien', 1979),
		movieRow(3, 'Arrival', 2016),
		movieRow(4, 'Moon', 2009)
	];
	movies[1].poster.origin = 'marquee';
	movies[1].poster_origin = 'marquee';
	movies[3].poster = null;
	movies[3].poster_origin = null;
	for (let n = 5; n <= 60; n += 1)
		movies.push(movieRow(n, `Film ${String(n).padStart(2, '0')}`, 1950 + n));

	const candidates = [1, 2, 3, 4].map((rank) => ({
		id: 100 + rank,
		source_file: `c${rank}.jpg`,
		sha256: 'c'.repeat(63) + rank,
		stage_reached: 'ranked',
		gate_decision: 'pass',
		rejection_reason: null,
		rank,
		final_score: 0.9 - rank / 100,
		stack: `${rank}A`,
		is_pick: rank === 1,
		image_url: `/api/runs/11/candidates/${100 + rank}/image`
	}));
	candidates.push({
		...candidates[0],
		id: 105,
		source_file: 'x.jpg',
		rank: null,
		final_score: null,
		stack: null,
		is_pick: false,
		rejection_reason: 'text_heavy',
		gate_decision: 'reject'
	});
	const runs = {
		11: {
			id: 11,
			movie_id: 1,
			job_id: 21,
			state: 'completed',
			run_folder: '00000011-heat',
			engine_run_id: 'r11',
			engine_status: 'completed',
			scorer_name: 'weighted',
			ai_model: 'clip-vit-b-32',
			profile_sha256: 'e'.repeat(64),
			counts: {},
			stages: [],
			candidate_count: 5,
			ranked_count: 4,
			error: null,
			started_at: iso(NOW - 3_600_000),
			finished_at: iso(NOW - 3_580_000),
			blind: false,
			reviewed: false,
			candidates
		}
	};
	const deployments = [
		{
			id: 2,
			movie_id: 1,
			action: 'deploy',
			initiated_by: 'owner',
			run_id: 11,
			candidate_id: 102,
			source_file: 'c2.jpg',
			job_id: 23,
			file_name: 'poster.jpg',
			new_sha256: 'b'.repeat(64),
			new_size_bytes: 400_000,
			previous_sha256: 'a'.repeat(64),
			previous_origin: 'marquee',
			backup_file: 'posters/1/' + 'a'.repeat(64) + '.jpg',
			restores_deployment_id: null,
			created_at: iso(NOW - 1_800_000)
		},
		{
			id: 1,
			movie_id: 1,
			action: 'deploy',
			initiated_by: 'owner',
			run_id: 11,
			candidate_id: 101,
			source_file: 'c1.jpg',
			job_id: 22,
			file_name: 'poster.jpg',
			new_sha256: 'a'.repeat(64),
			new_size_bytes: 400_000,
			previous_sha256: 'f'.repeat(64),
			previous_origin: 'library',
			backup_file: 'posters/1/' + 'f'.repeat(64) + '.jpg',
			restores_deployment_id: null,
			created_at: iso(NOW - 2_400_000)
		}
	];
	Object.assign(runs, reviewRuns());
	const jobs = [];
	const events = [];
	const state = {
		movies,
		runs,
		deployments,
		jobs,
		events,
		nextJob: 30,
		nextEvent: 1,
		last: {},
		// What the review routes keep: answers, exemplars, keys used, request bodies received,
		// and requests told to fail once.
		feedback: [],
		nextFeedback: 1,
		exemplars: seedExemplars(movies),
		nextExemplar: 1000,
		keys: {},
		received: {},
		failNext: {},
		systemProblems: false
	};
	addJob(state, 'poster_run', { movie_id: 1 }, { state: 'succeeded', auto: false, finished: true });
	addJob(state, 'library_sync', {}, { state: 'failed', auto: false, error: 'Radarr answered 503' });
	addJob(state, 'poster_run', { movie_id: 3 }, { state: 'queued', auto: false });
	return state;
}

// ── Review: runs to answer ────────────────────────────────────────────────

function candidateRow(runId, id, rank, stack, { sha = true, pick = false } = {}) {
	return {
		id,
		source_file: `r${runId}-${id}.jpg`,
		sha256: sha ? String(id).padStart(64, 'c') : null,
		stage_reached: rank === null ? 'style_gate' : 'ranked',
		gate_decision: rank === null ? 'reject' : 'pass',
		rejection_reason: rank === null ? 'text_heavy' : null,
		rank,
		final_score: rank === null ? null : 0.95 - rank / 50,
		stack,
		is_pick: pick,
		image_url: sha ? `/api/runs/${runId}/candidates/${id}/image` : null
	};
}

function runRow(id, movieId, state, candidates, extra = {}) {
	const ranked = candidates.filter((c) => c.rank !== null).length;
	return {
		id,
		movie_id: movieId,
		job_id: 200 + id,
		state,
		run_folder: `000000${id}-film`,
		engine_run_id: `r${id}`,
		engine_status: ranked ? 'completed' : 'flagged_manual',
		scorer_name: 'weighted',
		ai_model: 'clip-vit-b-32',
		profile_sha256: 'e'.repeat(64),
		counts: {},
		stages: [],
		candidate_count: candidates.length,
		ranked_count: ranked,
		error: null,
		started_at: iso(NOW - (100 - id) * 60_000),
		finished_at: state === 'running' ? null : iso(NOW - (100 - id) * 60_000 + 20_000),
		blind: true,
		reviewed: false,
		policy_deployed: false,
		candidates,
		...extra
	};
}

/** Run 12 (Film 05) has six designs: three stacks of two, one never downloaded, and two
 *  alone. Run 13 (Film 06) has three, and the deploy policy wrote its pick. Run 10 is Film
 *  05's older run, 16 is still running, and 17 ranked nothing. */
function reviewRuns() {
	return {
		10: runRow(10, 5, 'completed', [candidateRow(10, 1001, 1, '1A', { pick: true })]),
		12: runRow(12, 5, 'completed', [
			candidateRow(12, 1201, 1, '1A', { pick: true }),
			candidateRow(12, 1202, 2, '2A'),
			candidateRow(12, 1203, 3, '1B'),
			candidateRow(12, 1204, 4, '3A'),
			candidateRow(12, 1205, 5, '2B'),
			candidateRow(12, 1206, 6, '4A', { sha: false }),
			candidateRow(12, 1207, 7, '5A'),
			candidateRow(12, 1208, 8, '3B'),
			candidateRow(12, 1209, 9, null),
			candidateRow(12, 1210, null, null)
		]),
		13: runRow(
			13,
			6,
			'completed',
			[
				candidateRow(13, 1301, 1, '1A', { pick: true }),
				candidateRow(13, 1302, 2, '2A'),
				candidateRow(13, 1303, 3, '3A')
			],
			{ blind: false, policy_deployed: true }
		),
		16: runRow(16, 7, 'running', []),
		17: runRow(17, 8, 'completed', [candidateRow(17, 1701, null, null)])
	};
}

function seedExemplars(movies) {
	return movies.map((movie, i) => ({
		id: i + 1,
		movie_id: movie.id,
		title: movie.title,
		year: movie.year,
		polarity: 'positive',
		weight: 1,
		source: 'seed',
		event_id: null,
		state: i === 1 ? 'superseded' : 'active',
		sha256: String(i + 1).padStart(64, 'd'),
		staged_name: `${movie.title} (${movie.year})`,
		source_file: null,
		replaced_exemplar_id: null,
		created_at: iso(NOW - 86_400_000),
		updated_at: iso(NOW - 86_400_000),
		ended_at: i === 1 ? iso(NOW - 3_600_000) : null,
		ended_by_event_id: null,
		image_url: `/api/taste/exemplars/${i + 1}/image`
	}));
}

/** The run a movie's feedback goes to: its latest completed one. */
function latestCompleted(movieId) {
	const ids = Object.values(state.runs)
		.filter((r) => r.movie_id === movieId && r.state === 'completed')
		.map((r) => r.id);
	return ids.length ? Math.max(...ids) : null;
}

function standing(runId) {
	return state.feedback.some(
		(e) => e.run_id === runId && e.action !== 'revoke' && e.revoked_by === null
	);
}

function pendingReviews() {
	const movieIds = [...new Set(Object.values(state.runs).map((r) => r.movie_id))];
	return movieIds
		.map((movieId) => latestCompleted(movieId))
		.filter((runId) => runId !== null && !standing(runId))
		.map((runId) => {
			const run = state.runs[runId];
			const movie = state.movies.find((m) => m.id === run.movie_id);
			const pick = run.candidates.find((c) => c.is_pick && c.sha256);
			return {
				movie_id: movie.id,
				title: movie.title,
				year: movie.year,
				run_id: run.id,
				ranked: run.ranked_count,
				finished_at: run.finished_at,
				policy_deployed: run.policy_deployed,
				blind: run.blind,
				poster: movie.poster,
				pick_image_url: pick ? pick.image_url : null
			};
		})
		.sort((a, b) => Number(b.policy_deployed) - Number(a.policy_deployed) || b.run_id - a.run_id);
}

function publicRun(run) {
	return { ...run, reviewed: standing(run.id) };
}

function feedbackResult(event, created, ended, reactivated, jobs, status) {
	return {
		status,
		body: {
			event,
			exemplars: { created, ended, reactivated },
			jobs,
			deploy_note: null
		}
	};
}

function newExemplar(event, movie, candidate, polarity, weight) {
	const now = new Date().toISOString();
	const row = {
		id: state.nextExemplar++,
		movie_id: movie.id,
		title: movie.title,
		year: movie.year,
		polarity,
		weight,
		source: 'feedback',
		event_id: event.id,
		state: 'active',
		sha256: candidate.sha256,
		staged_name: polarity === 'positive' ? `${movie.title} (${movie.year})` : null,
		source_file: candidate.source_file,
		replaced_exemplar_id: null,
		created_at: now,
		updated_at: now,
		ended_at: null,
		ended_by_event_id: null,
		image_url: `/api/taste/exemplars/${state.nextExemplar - 1}/image`
	};
	state.exemplars.unshift(row);
	return row;
}

function endExemplar(row, how, eventId) {
	const now = new Date().toISOString();
	Object.assign(row, { state: how, ended_at: now, updated_at: now, ended_by_event_id: eventId });
	return row;
}

/** POST /api/runs/{id}/feedback, as the real API answers it (a smaller rule set). */
function recordFeedback(run, body) {
	const used = state.keys[body.idempotency_key];
	if (used) return { status: 200, body: used };
	const movie = state.movies.find((m) => m.id === run.movie_id);
	if (latestCompleted(movie.id) !== run.id)
		return {
			status: 409,
			body: { detail: `run ${run.id} is not the movie's latest completed run` }
		};
	const byId = new Map(run.candidates.map((c) => [c.id, c]));
	const pick = run.candidates.find((c) => c.is_pick);
	let action = body.action;
	let chosen = null;
	if (action === 'override') chosen = byId.get(body.candidate_id);
	if (action === 'override' && chosen?.id === pick?.id) action = 'approval';
	if (action === 'approval') chosen = pick;
	if (action === 'rank') chosen = byId.get(body.order?.[0]);
	const shown = (body.shown ?? []).map((id) => byId.get(id));
	if (shown.some((c) => !c))
		return { status: 404, body: { detail: 'not a candidate of this run' } };
	let hated = (body.hated ?? []).map((id) => byId.get(id));
	if (action === 'reject' && hated.length === 0) hated = shown.filter((c) => c.sha256);
	for (const c of [chosen, ...hated].filter(Boolean)) {
		if (!c.sha256)
			return {
				status: 409,
				body: { detail: `candidate ${c.id} has no downloaded file; it cannot be chosen or hated` }
			};
	}
	const event = {
		id: state.nextFeedback++,
		movie_id: movie.id,
		run_id: run.id,
		action,
		chosen_source_file: chosen?.source_file ?? null,
		chosen_sha256: chosen?.sha256 ?? null,
		pick_source_file: pick?.source_file ?? null,
		profile_sha256: run.profile_sha256,
		scorer_name: 'weighted',
		blind: run.blind,
		exposed: shown.map((c) => ({
			candidate_id: c.source_file,
			sha256: c.sha256,
			baseline_rank: c.rank,
			baseline_score: c.final_score,
			stack: c.stack
		})),
		context: {
			order: (body.order ?? []).map((id) => byId.get(id)?.source_file),
			hated: hated.map((c) => c.source_file),
			selected_candidate: chosen?.source_file ?? null
		},
		revokes_event_id: null,
		revoked_by: null,
		created_at: new Date().toISOString()
	};
	state.feedback.push(event);
	const created = [];
	const ended = [];
	const active = state.exemplars.find(
		(x) => x.movie_id === movie.id && x.polarity === 'positive' && x.state === 'active'
	);
	if (chosen && (action !== 'approval' || !active)) {
		if (active) ended.push(endExemplar(active, 'superseded', event.id));
		created.push(newExemplar(event, movie, chosen, 'positive', action === 'approval' ? 0.75 : 1));
	}
	for (const c of hated.filter((c) => c.rank !== null && c.rank <= 10).slice(0, 8)) {
		created.push(newExemplar(event, movie, c, 'negative', 1));
	}
	const jobs = { deploy: null, rebuild: null };
	if (chosen && action !== 'reject' && body.deploy !== false) {
		jobs.deploy = addJob(state, 'poster_deploy', {
			candidate_id: chosen.id,
			initiated_by: 'owner',
			movie_id: movie.id,
			event_id: event.id
		}).id;
	}
	if (created.length || ended.length) jobs.rebuild = addJob(state, 'taste_rebuild', {}).id;
	const result = feedbackResult(event, created, ended, [], jobs, 201);
	state.keys[body.idempotency_key] = result.body;
	return result;
}

function revokeFeedback(event) {
	const key = `revoke:${event.id}`;
	if (state.keys[key]) return { status: 200, body: state.keys[key] };
	if (event.action === 'revoke')
		return { status: 409, body: { detail: `event ${event.id} is a revoke; it cannot be revoked` } };
	if (event.revoked_by !== null)
		return {
			status: 409,
			body: { detail: `event ${event.id} was already revoked by event ${event.revoked_by}` }
		};
	const revoke = {
		...event,
		id: state.nextFeedback++,
		action: 'revoke',
		chosen_source_file: null,
		chosen_sha256: null,
		exposed: [],
		context: {},
		revokes_event_id: event.id,
		revoked_by: null,
		created_at: new Date().toISOString()
	};
	state.feedback.push(revoke);
	event.revoked_by = revoke.id;
	const ended = state.exemplars
		.filter((x) => x.event_id === event.id && x.state === 'active')
		.map((x) => endExemplar(x, 'revoked', revoke.id));
	const reactivated = state.exemplars
		.filter((x) => x.ended_by_event_id === event.id && x.state === 'superseded')
		.map((x) => Object.assign(x, { state: 'active', ended_at: null, ended_by_event_id: null }));
	const rebuild = addJob(state, 'taste_rebuild', {}).id;
	const result = feedbackResult(revoke, [], ended, reactivated, { deploy: null, rebuild }, 201);
	state.keys[key] = result.body;
	return result;
}

function tasteStatus() {
	const counts = {};
	for (const x of state.exemplars) {
		counts[x.state] ??= {};
		counts[x.state][x.polarity] = (counts[x.state][x.polarity] ?? 0) + 1;
	}
	const tally = () => ({ reviewed: 0, agreed: 0, top3: 0, percent: null });
	const blind = tally();
	const notBlind = tally();
	const seen = new Set();
	for (const e of state.feedback) {
		if (e.action === 'revoke' || e.revoked_by !== null || seen.has(e.run_id)) continue;
		seen.add(e.run_id);
		const t = e.blind ? blind : notBlind;
		t.reviewed += 1;
		if (e.action !== 'reject' && e.chosen_source_file === e.pick_source_file) t.agreed += 1;
		const rank = e.exposed.find((x) => x.candidate_id === e.chosen_source_file)?.baseline_rank;
		if (e.action !== 'reject' && rank !== undefined && rank <= 3) t.top3 += 1;
	}
	const active = counts.active ?? {};
	return {
		exemplars: counts,
		profile: {
			id: 2,
			sha256: '670b7c07'.padEnd(64, '0'),
			source: 'rebuild',
			revision: 'f'.repeat(64),
			positives: active.positive ?? 0,
			negatives: active.negative ?? 0,
			created_at: iso(NOW - 7_200_000)
		},
		revision: state.feedback.length ? 'a'.repeat(64) : 'f'.repeat(64),
		rebuild_due: state.feedback.length > 0,
		agreement: { blind, by_profile: {}, by_month: {}, not_blind: notBlind, unknown: tally() },
		residual: {
			subjects: seen.size,
			pairs: seen.size * 3,
			min_subjects: 25,
			min_pairs: 200,
			trained: false
		},
		pending_reviews: pendingReviews().length
	};
}

function feedbackList(query) {
	const movieId = query.get('movie_id');
	const items = [...state.feedback]
		.filter((e) => movieId === null || e.movie_id === Number(movieId))
		.sort((a, b) => b.id - a.id)
		.map((e) => {
			const movie = state.movies.find((m) => m.id === e.movie_id);
			return {
				id: e.id,
				movie_id: e.movie_id,
				title: movie.title,
				year: movie.year,
				run_id: e.run_id,
				action: e.action,
				chosen_source_file: e.chosen_source_file,
				pick_source_file: e.pick_source_file,
				blind: e.blind,
				shown: e.exposed.length,
				context: e.context,
				revokes_event_id: e.revokes_event_id,
				revoked_by: e.revoked_by,
				created_at: e.created_at
			};
		});
	return page(items, query);
}

function systemInfo() {
	const problems = state.systemProblems;
	return {
		version: '0.1.0',
		environment: 'development',
		schema: { current: problems ? '0004' : '0005', head: '0005', at_head: !problems },
		settings: {
			deploy_policy: 'off',
			run_new_movies: false,
			sync_interval_minutes: 360,
			movie_roots: ['/srv/media/movies/1080p', '/srv/media/movies/4K'],
			data_dir: '/srv/marquee/data'
		},
		library: {
			movies: state.movies.length,
			movies_with_file: state.movies.length,
			posters: { library: 58, marquee: 1 },
			runs: { completed: 5, running: 1 },
			deployments: state.deployments.length,
			exemplars: tasteStatus().exemplars,
			preference_events: state.feedback.length
		},
		models: {
			manifest: 'found',
			files: [
				{
					item: 'clip',
					path: 'models/clip-vit-b-32.onnx',
					kind: 'file',
					present: true,
					size_bytes: 351_000_000,
					expected_size_bytes: 351_000_000
				},
				{
					item: 'dinov2',
					path: 'models/dinov2-vits14.onnx',
					kind: 'file',
					present: !problems,
					size_bytes: problems ? null : 88_000_000,
					expected_size_bytes: 88_000_000
				},
				{
					item: 'ocr',
					path: 'models/paddlex',
					kind: 'folder',
					present: true,
					size_bytes: 138_000_000,
					expected_size_bytes: 138_000_000
				}
			]
		},
		last_sync: {
			job_id: 31,
			state: 'failed',
			created_at: iso(NOW - 600_000),
			started_at: iso(NOW - 590_000),
			finished_at: iso(NOW - 580_000)
		},
		storage: {
			runs: 741_000_000,
			backups: 2_000_000,
			cache: 520_000_000,
			ml: 161_000_000,
			measured_at: iso(NOW)
		}
	};
}

function addEvent(state, jobId, type, message = null) {
	state.events.push({
		id: state.nextEvent++,
		job_id: jobId,
		at: new Date().toISOString(),
		type,
		message,
		data: null
	});
}

const CLASSES = {
	library_sync: 'network',
	poster_run: 'gpu',
	poster_restore: 'media_write',
	poster_deploy: 'media_write',
	taste_rebuild: 'gpu'
};

function addJob(
	state,
	kind,
	payload,
	{ state: jobState = 'queued', auto = true, error = null, finished = false } = {}
) {
	const at = new Date().toISOString();
	const job = {
		id: state.nextJob++,
		kind,
		job_class: CLASSES[kind] ?? 'network',
		state: jobState,
		priority: 0,
		payload,
		result: jobState === 'succeeded' ? {} : null,
		error,
		progress: null,
		attempt: jobState === 'queued' ? 0 : 1,
		max_attempts: 3,
		run_after: at,
		dedupe_key: null,
		parent_id: null,
		cancel_requested: false,
		created_at: at,
		updated_at: at,
		started_at: jobState === 'queued' ? null : at,
		finished_at: finished || jobState === 'failed' || jobState === 'succeeded' ? at : null,
		auto
	};
	state.jobs.unshift(job);
	addEvent(state, job.id, 'queued');
	if (jobState !== 'queued') addEvent(state, job.id, 'started');
	if (jobState === 'succeeded') addEvent(state, job.id, 'succeeded');
	if (jobState === 'failed') addEvent(state, job.id, 'failed', error);
	return job;
}

let state = initialState();

// Jobs made through the API move on by themselves, like a worker would run them.
setInterval(() => {
	for (const job of state.jobs) {
		if (!job.auto) continue;
		const now = new Date().toISOString();
		if (job.state === 'queued') {
			Object.assign(job, {
				state: 'running',
				attempt: job.attempt + 1,
				started_at: now,
				updated_at: now,
				progress: { current: 1, total: 2, message: 'working' }
			});
			addEvent(state, job.id, 'started');
		} else if (job.state === 'running') {
			Object.assign(job, {
				state: 'succeeded',
				finished_at: now,
				updated_at: now,
				progress: null,
				result: { done: true }
			});
			addEvent(state, job.id, 'succeeded');
			if (job.kind === 'poster_run') {
				const movie = state.movies.find((m) => m.id === job.payload.movie_id);
				if (movie) movie.latest_run_id = 11;
			}
		}
	}
}, 700);

// The `auto` flag is the fake's own; the API has no such field. `subject` is the movie the
// payload names, as the API gives it.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const publicJob = ({ auto, ...job }) => {
	const movie = state.movies.find((m) => m.id === job.payload?.movie_id);
	return {
		...job,
		subject: movie ? { movie_id: movie.id, title: movie.title, year: movie.year } : null
	};
};

function send(res, status, body, headers = {}) {
	if (Buffer.isBuffer(body)) {
		res.writeHead(status, {
			'content-type': 'image/png',
			'content-length': body.length,
			...headers
		});
		res.end(body);
		return;
	}
	const text = body === null ? '' : JSON.stringify(body);
	res.writeHead(status, { 'content-type': 'application/json', ...headers });
	res.end(text);
}

const page = (items, query, defaultSize = 50) => {
	const pageNo = Number(query.get('page') ?? 1);
	const size = Number(query.get('page_size') ?? defaultSize);
	return {
		total: items.length,
		page: pageNo,
		page_size: size,
		items: items.slice((pageNo - 1) * size, pageNo * size)
	};
};

function listMovies(query) {
	let items = [...state.movies];
	const q = query.get('q');
	if (q) items = items.filter((m) => m.title.toLowerCase().includes(q.toLowerCase()));
	const hasPoster = query.get('has_poster');
	if (hasPoster !== null)
		items = items.filter((m) => (m.poster !== null) === (hasPoster === 'true'));
	const origin = query.get('origin');
	if (origin) items = items.filter((m) => m.poster_origin === origin);
	const hasRun = query.get('has_run');
	if (hasRun !== null)
		items = items.filter((m) => (m.latest_run_id !== null) === (hasRun === 'true'));
	const sort = query.get('sort') ?? 'title';
	items.sort((a, b) =>
		sort === 'year'
			? (b.year ?? 0) - (a.year ?? 0)
			: sort === 'added'
				? b.added_at.localeCompare(a.added_at)
				: a.sort_title.localeCompare(b.sort_title)
	);
	return page(items, query);
}

function route(req, res, url) {
	const { pathname: path, searchParams: query } = url;
	const method = req.method;
	let m;
	if (method === 'GET' && path === '/api/movies') return send(res, 200, listMovies(query));
	if ((m = path.match(/^\/api\/movies\/(\d+)$/)) && method === 'GET') {
		const movie = state.movies.find((row) => row.id === Number(m[1]));
		return movie ? send(res, 200, movie) : send(res, 404, { detail: 'movie not found' });
	}
	if ((m = path.match(/^\/api\/movies\/(\d+)\/poster$/)) && method === 'GET') {
		const movie = state.movies.find((row) => row.id === Number(m[1]));
		if (!movie?.poster) return send(res, 404, { detail: 'no poster' });
		const width = query.get('width');
		if (width !== null && !['160', '320', '640'].includes(width)) {
			return send(res, 422, {
				detail: [{ loc: ['query', 'width'], msg: 'Input should be 160, 320 or 640' }]
			});
		}
		const etag = `"${movie.poster.sha256}${width ? `-${width}` : ''}"`;
		const cache =
			query.get('v') === movie.poster.version
				? 'private, max-age=31536000, immutable'
				: 'private, no-cache';
		if (req.headers['if-none-match'] === etag)
			return send(res, 304, null, { etag, 'cache-control': cache });
		return send(res, 200, IMAGE, { etag, 'cache-control': cache });
	}
	if ((m = path.match(/^\/api\/movies\/(\d+)\/run$/)) && method === 'POST') {
		const movieId = Number(m[1]);
		if (movieId === 4)
			return send(res, 409, { detail: `movie 4 (Moon) has no TMDB details yet; sync it first` });
		const active = state.jobs.find(
			(j) =>
				j.kind === 'poster_run' &&
				j.payload.movie_id === movieId &&
				['queued', 'running'].includes(j.state)
		);
		if (active) return send(res, 200, publicJob(active));
		return send(res, 202, publicJob(addJob(state, 'poster_run', { movie_id: movieId })));
	}
	if ((m = path.match(/^\/api\/runs\/(\d+)$/)) && method === 'GET') {
		const run = state.runs[Number(m[1])];
		return run ? send(res, 200, publicRun(run)) : send(res, 404, { detail: 'run not found' });
	}
	if (path.match(/^\/api\/runs\/\d+\/candidates\/\d+\/image$/) && method === 'GET')
		return send(res, 200, IMAGE);
	if (path === '/api/runs' && method === 'GET') {
		const movieId = query.get('movie_id');
		const wanted = query.get('state');
		const items = Object.values(state.runs)
			.filter((r) => movieId === null || r.movie_id === Number(movieId))
			.filter((r) => wanted === null || r.state === wanted)
			.sort((a, b) => b.id - a.id)
			// eslint-disable-next-line @typescript-eslint/no-unused-vars
			.map(({ candidates, blind, reviewed, policy_deployed, ...run }) => run);
		return send(res, 200, page(items, query));
	}
	if (path === '/api/review' && method === 'GET')
		return send(res, 200, { items: pendingReviews() });
	if ((m = path.match(/^\/api\/runs\/(\d+)\/feedback$/))) {
		const run = state.runs[Number(m[1])];
		if (!run) return send(res, 404, { detail: 'run not found' });
		if (method === 'GET') {
			const own = state.feedback.filter((e) => e.run_id === run.id);
			return send(res, 200, { items: own.sort((a, b) => a.id - b.id) });
		}
		if (method === 'POST') {
			const result = recordFeedback(run, req.body ?? {});
			return send(res, result.status, result.body);
		}
	}
	if ((m = path.match(/^\/api\/feedback\/(\d+)\/revoke$/)) && method === 'POST') {
		const event = state.feedback.find((e) => e.id === Number(m[1]));
		if (!event) return send(res, 404, { detail: 'no such event' });
		const result = revokeFeedback(event);
		return send(res, result.status, result.body);
	}
	if (path === '/api/feedback' && method === 'GET') return send(res, 200, feedbackList(query));
	if (path === '/api/taste' && method === 'GET') return send(res, 200, tasteStatus());
	if (path === '/api/taste/rebuild' && method === 'POST') {
		const active = state.jobs.find(
			(j) => j.kind === 'taste_rebuild' && ['queued', 'running'].includes(j.state)
		);
		if (active) return send(res, 200, publicJob(active));
		return send(res, 202, publicJob(addJob(state, 'taste_rebuild', { force: false })));
	}
	if (path === '/api/taste/exemplars' && method === 'GET') {
		let items = state.exemplars;
		if (query.get('polarity')) items = items.filter((x) => x.polarity === query.get('polarity'));
		if (query.get('state')) items = items.filter((x) => x.state === query.get('state'));
		return send(res, 200, page(items, query));
	}
	if (path.match(/^\/api\/taste\/exemplars\/\d+\/image$/) && method === 'GET')
		return send(res, 200, IMAGE);
	if (path === '/api/system' && method === 'GET') return send(res, 200, systemInfo());
	if (path === '/api/deployments' && method === 'GET') {
		const movieId = query.get('movie_id');
		const items = state.deployments.filter(
			(d) => movieId === null || d.movie_id === Number(movieId)
		);
		return send(res, 200, page(items, query));
	}
	if ((m = path.match(/^\/api\/deployments\/(\d+)\/restore$/)) && method === 'POST') {
		const deployment = state.deployments.find((d) => d.id === Number(m[1]));
		if (!deployment) return send(res, 404, { detail: 'deployment not found' });
		return send(
			res,
			202,
			publicJob(
				addJob(state, 'poster_restore', {
					deployment_id: deployment.id,
					movie_id: deployment.movie_id
				})
			)
		);
	}
	if (path === '/api/sync' && method === 'POST') {
		const active = state.jobs.find(
			(j) => j.kind === 'library_sync' && ['queued', 'running'].includes(j.state)
		);
		if (active) return send(res, 200, publicJob(active));
		return send(res, 202, publicJob(addJob(state, 'library_sync', {})));
	}
	if (path === '/api/worker' && method === 'GET') {
		const count = (s) => state.jobs.filter((j) => j.state === s).length;
		return send(res, 200, {
			running: !state.systemProblems,
			since: state.systemProblems ? null : iso(NOW),
			queued: count('queued'),
			running_jobs: count('running'),
			failed: count('failed')
		});
	}
	if (path === '/api/jobs/events' && method === 'GET') {
		const after = Number(query.get('after') ?? 0);
		const limit = Number(query.get('limit') ?? 100);
		const items = state.events.filter((e) => e.id > after).slice(0, limit);
		return send(res, 200, { items, next_after: items.length ? items[items.length - 1].id : after });
	}
	if (path === '/api/jobs' && method === 'GET') {
		let items = state.jobs;
		if (query.get('state')) items = items.filter((j) => j.state === query.get('state'));
		if (query.get('kind')) items = items.filter((j) => j.kind === query.get('kind'));
		const result = page(items, query);
		return send(res, 200, { ...result, items: result.items.map(publicJob) });
	}
	if ((m = path.match(/^\/api\/jobs\/(\d+)(\/events|\/cancel|\/retry)?$/))) {
		const job = state.jobs.find((j) => j.id === Number(m[1]));
		if (!job) return send(res, 404, { detail: 'job not found' });
		const now = new Date().toISOString();
		if (!m[2] && method === 'GET') return send(res, 200, publicJob(job));
		if (m[2] === '/events' && method === 'GET') {
			return send(res, 200, { items: state.events.filter((e) => e.job_id === job.id) });
		}
		if (m[2] === '/cancel' && method === 'POST') {
			if (job.state !== 'queued')
				return send(res, 409, {
					detail: `job ${job.id} is ${job.state}; only a queued or running job`
				});
			Object.assign(job, { state: 'cancelled', finished_at: now, updated_at: now });
			addEvent(state, job.id, 'cancelled');
			return send(res, 200, publicJob(job));
		}
		if (m[2] === '/retry' && method === 'POST') {
			if (!['failed', 'cancelled'].includes(job.state))
				return send(res, 409, {
					detail: `job ${job.id} is ${job.state}; only a failed or cancelled job`
				});
			Object.assign(job, {
				state: 'queued',
				error: null,
				attempt: 0,
				finished_at: null,
				started_at: null,
				updated_at: now,
				auto: true
			});
			addEvent(state, job.id, 'retried');
			return send(res, 200, publicJob(job));
		}
	}
	return send(res, 404, { detail: 'Not Found' });
}

createServer((req, res) => {
	const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
	if (url.pathname === '/__fake/health') return send(res, 200, { ok: true });
	if (url.pathname === '/__fake/last') {
		return send(res, 200, state.last[url.searchParams.get('path') ?? ''] ?? {});
	}
	if (url.pathname === '/__fake/reset' && req.method === 'POST') {
		state = initialState();
		if (url.searchParams.get('review') === 'none') {
			for (const id of Object.keys(state.runs)) if (Number(id) !== 11) delete state.runs[id];
			state.runs[11].state = 'running';
		}
		state.systemProblems = url.searchParams.get('problems') === '1';
		return send(res, 200, { ok: true });
	}
	// Every JSON body the fake received for a path, oldest first.
	if (url.pathname === '/__fake/received') {
		return send(res, 200, state.received[url.searchParams.get('path') ?? ''] ?? []);
	}
	// The next request with this method to a path is received, then answered with this status
	// and no effect.
	if (url.pathname === '/__fake/fail-next' && req.method === 'POST') {
		const key = `${url.searchParams.get('method') ?? 'POST'} ${url.searchParams.get('path') ?? ''}`;
		state.failNext[key] = Number(url.searchParams.get('status'));
		return send(res, 200, { ok: true });
	}
	if (!url.pathname.startsWith('/api/')) return send(res, 404, { detail: 'Not Found' });
	state.last[url.pathname] = {
		method: req.method,
		path: url.pathname,
		key_matched: req.headers['x-api-key'] === KEY,
		cookie_forwarded: 'cookie' in req.headers,
		authorization_forwarded: 'authorization' in req.headers
	};
	if (req.headers['x-api-key'] !== KEY)
		return send(res, 401, { detail: 'a valid API key is required' });
	const chunks = [];
	req.on('data', (chunk) => chunks.push(chunk));
	req.on('end', () => {
		const raw = Buffer.concat(chunks).toString('utf8');
		if (raw) {
			try {
				req.body = JSON.parse(raw);
			} catch {
				return send(res, 422, { detail: 'the body is not JSON' });
			}
			(state.received[url.pathname] ??= []).push(req.body);
		}
		const failKey = `${req.method} ${url.pathname}`;
		const failWith = state.failNext[failKey];
		if (failWith) {
			delete state.failNext[failKey];
			return send(res, failWith, { detail: 'the fake failed this request on purpose' });
		}
		return route(req, res, url);
	});
}).listen(PORT, '127.0.0.1');
