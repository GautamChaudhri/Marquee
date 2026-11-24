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
	const jobs = [];
	const events = [];
	const state = { movies, runs, deployments, jobs, events, nextJob: 30, nextEvent: 1, last: {} };
	addJob(state, 'poster_run', { movie_id: 1 }, { state: 'succeeded', auto: false, finished: true });
	addJob(state, 'library_sync', {}, { state: 'failed', auto: false, error: 'Radarr answered 503' });
	addJob(state, 'poster_run', { movie_id: 3 }, { state: 'queued', auto: false });
	return state;
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
	poster_deploy: 'media_write'
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

// The `auto` flag is the fake's own; the API has no such field.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const publicJob = ({ auto, ...job }) => job;

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
		return run ? send(res, 200, run) : send(res, 404, { detail: 'run not found' });
	}
	if (path.match(/^\/api\/runs\/\d+\/candidates\/\d+\/image$/) && method === 'GET')
		return send(res, 200, IMAGE);
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
			running: true,
			since: iso(NOW),
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
	req.resume();
	req.on('end', () => route(req, res, url));
}).listen(PORT, '127.0.0.1');
