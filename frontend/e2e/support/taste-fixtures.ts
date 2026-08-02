import { expect, type Page } from '@playwright/test';

export const FIXED_TIME = '2026-08-02T12:00:00Z';

type TasteLibrary = 'movies' | 'tv';

export interface TasteMutationRequest {
	method: string;
	path: string;
	library: TasteLibrary;
	body: unknown;
}

const status = (library: TasteLibrary) => ({
	labels: {
		total: library === 'movies' ? 86 : 38,
		movies: library === 'movies' ? 24 : 9,
		subjects: library === 'movies' ? 24 : 9,
		positives: library === 'movies' ? 70 : 31,
		negatives: library === 'movies' ? 16 : 7,
		genres: library === 'movies' ? { Thriller: 8, Drama: 7, 'Science Fiction': 5 } : {},
		genres_available: library === 'movies'
	},
	exemplars: {
		count: library === 'movies' ? 31 : 18,
		negatives: library === 'movies' ? 4 : 2,
		last_rebuild: FIXED_TIME,
		profile_present: true,
		unique_movies: library === 'movies' ? 24 : 9,
		duplicate_groups: 1,
		by_kind: library === 'movies' ? { movie: 31 } : { show: 9, season: 9 }
	},
	ranking_residual: {
		active: true,
		mode: 'bounded_residual',
		subjects: library === 'movies' ? 24 : 9,
		pairs: library === 'movies' ? 96 : 44,
		activation: {
			subjects: { have: library === 'movies' ? 24 : 9, need: 8 },
			pairs: { have: library === 'movies' ? 96 : 44, need: 30 }
		}
	},
	gate_alerts: []
});

const profileSummary = (library: TasteLibrary) => ({
	id: `${library}-profile-active`,
	kind: 'taste_profile',
	status: 'active',
	label: library === 'movies' ? 'Movies profile · generation 3' : 'TV profile · generation 2',
	model_name: 'clip-vit-b-32',
	source_mode: library === 'movies' ? 'canonical evidence' : 'deployed artwork',
	imported_from_active: false,
	created_at: '2026-08-01T08:00:00Z',
	updated_at: '2026-08-01T08:05:00Z',
	trained_at: '2026-08-01T08:04:00Z',
	activated_at: '2026-08-01T08:05:00Z',
	storage_path: `managed/${library}/profile.npz`,
	summary: {
		exemplars: library === 'movies' ? 3 : 4,
		unique_movies: 2,
		unique_subjects: 2,
		total_assets: library === 'movies' ? 3 : 4,
		by_kind: library === 'movies' ? { movie: 3 } : { show: 2, season: 2 },
		negative_exemplars: 1,
		duplicate_groups: 1,
		duplicate_exemplars: 1
	}
});

const profileDetail = (library: TasteLibrary) => ({
	...profileSummary(library),
	movies:
		library === 'movies'
			? [
					{
						movie_id: 3,
						title: 'Heat',
						year: 1995,
						tmdb_id: 949,
						contribution_count: 2,
						subject_key: 'movie:3',
						asset_counts: { movie: 2 },
						asset_summary: '2 posters',
						assets: [
							{
								name: 'heat-primary.jpg',
								label: 'Heat (1995)',
								asset_kind: 'movie',
								season_number: null,
								is_duplicate: true,
								duplicate_count: 2
							},
							{
								name: 'heat-primary-copy.jpg',
								label: 'Heat (1995)',
								asset_kind: 'movie',
								season_number: null,
								is_duplicate: true,
								duplicate_count: 2
							}
						]
					},
					{
						movie_id: 8,
						title: 'Arrival',
						year: 2016,
						tmdb_id: 329865,
						contribution_count: 1,
						subject_key: 'movie:8',
						asset_counts: { movie: 1 },
						asset_summary: '1 poster',
						assets: [
							{
								name: 'arrival.jpg',
								label: 'Arrival (2016)',
								asset_kind: 'movie',
								season_number: null,
								is_duplicate: false,
								duplicate_count: 1
							}
						]
					}
				]
			: [
					{
						movie_id: 12,
						title: 'Dark Matter',
						year: 2024,
						tmdb_id: 220542,
						contribution_count: 3,
						subject_key: 'series:12',
						asset_counts: { show: 1, season: 2 },
						asset_summary: '1 show · 2 seasons',
						assets: [
							{
								name: 'dark-matter-show.jpg',
								label: 'Dark Matter (2024)',
								asset_kind: 'show',
								season_number: null,
								is_duplicate: false,
								duplicate_count: 1
							},
							{
								name: 'dark-matter-s01.jpg',
								label: 'Dark Matter (2024) · Season 1',
								asset_kind: 'season',
								season_number: 1,
								is_duplicate: false,
								duplicate_count: 1
							},
							{
								name: 'dark-matter-s02.jpg',
								label: 'Dark Matter (2024) · Season 2',
								asset_kind: 'season',
								season_number: 2,
								is_duplicate: false,
								duplicate_count: 1
							}
						]
					},
					{
						movie_id: 13,
						title: 'Moon Knight',
						year: 2022,
						tmdb_id: 92749,
						contribution_count: 1,
						subject_key: 'series:13',
						asset_counts: { show: 1 },
						asset_summary: '1 show',
						assets: [
							{
								name: 'moon-knight-show.jpg',
								label: 'Moon Knight (2022)',
								asset_kind: 'show',
								season_number: null,
								is_duplicate: false,
								duplicate_count: 1
							}
						]
					}
				],
	duplicate_groups: [
		{
			title: library === 'movies' ? 'Heat' : 'Dark Matter',
			year: library === 'movies' ? 1995 : 2024,
			asset_kind: library === 'movies' ? 'movie' : 'show',
			season_number: null,
			label: library === 'movies' ? 'Heat (1995)' : 'Dark Matter (2024)',
			count: 2,
			exemplars: ['primary.jpg', 'primary-copy.jpg']
		}
	],
	negative_exemplars: ['negative.jpg']
});

const residualSummary = (library: TasteLibrary) => ({
	id: `${library}-residual-active`,
	kind: 'ranking_residual',
	status: 'active',
	label: library === 'movies' ? 'Movies residual · generation 2' : 'TV residual · generation 1',
	model_name: null,
	source_mode: 'canonical evidence',
	imported_from_active: false,
	created_at: '2026-08-01T09:00:00Z',
	updated_at: '2026-08-01T09:05:00Z',
	trained_at: '2026-08-01T09:04:00Z',
	activated_at: '2026-08-01T09:05:00Z',
	storage_path: `managed/${library}/residual.npz`,
	summary: {
		alpha: 0.35,
		delta_max: 0.8,
		top_features: [
			{ name: 'knn_sim', weight: 0.42 },
			{ name: 'official_family', weight: 0.21 }
		],
		evaluation: {
			baseline_accuracy: 0.68,
			residual_accuracy: 0.76,
			improvement: 0.08,
			pair_count: library === 'movies' ? 96 : 44,
			subject_count: library === 'movies' ? 24 : 9
		}
	}
});

const jobResponse = (jobId: string) => ({
	job_id: jobId,
	disposition: 'created',
	idempotent: false,
	phase: 'queued',
	snapshot_url: `/api/jobs/${jobId}/snapshot`,
	detail_url: `/projection-room/jobs/${jobId}`,
	activity_url: `/projection-room?view=queue&job=${jobId}`,
	active_conflict: null
});

function jobRow(jobId: string, label: string) {
	return {
		version: 1,
		job_id: jobId,
		job_type: 'taste_rebuild',
		label,
		label_key: 'jobs.taste_rebuild.label',
		feature_area: 'ml_taste',
		feature_label: 'Taste',
		presentation_family: 'ml_taste',
		subject: {
			kind: 'model',
			display_id: `taste:${jobId}`,
			display_name: label,
			artwork_key: null,
			context: ['Key Art Engine'],
			snapshot_at: FIXED_TIME,
			missing_live_subject: false,
			monogram: 'KA'
		},
		action_headline: label,
		status: {
			label: 'Queued',
			label_key: 'jobs.status.queued',
			phase: 'queued',
			outcome: null,
			tone: 'queued'
		},
		attention: { level: 'normal', reason: 'none', message: null, remediation: null },
		trigger: { kind: 'manual', label: 'Started manually', initiator: 'Operator' },
		progress: null,
		work_items: null,
		contained_work: null,
		priority: 0,
		fence_token: 1,
		execution_class: 'gpu',
		queue_rank: 1,
		is_parent: false,
		allowed_actions: ['cancel', 'open_detail'],
		links: {
			detail: `/projection-room/jobs/${jobId}`,
			presentation: `/api/jobs/${jobId}/presentation`,
			snapshot: `/api/jobs/${jobId}/snapshot`
		},
		parent_id: null,
		root_id: jobId,
		retry_of_job_id: null,
		created_at: FIXED_TIME,
		eligible_at: null,
		started_at: null,
		terminal_at: null,
		duration_seconds: null,
		impact: null,
		evidence: { artifacts_available: false, logs_available: false }
	};
}

export async function installTasteFixtures(page: Page) {
	const requests: TasteMutationRequest[] = [];
	const jobs: ReturnType<typeof jobRow>[] = [];
	let sequence = 0;

	await page.route('**/api/jobs?*', (route) =>
		route.fulfill({ json: { view: 'queue', items: jobs, next_cursor: null, limit: 20 } })
	);
	await page.route(/\/api\/jobs\/[^/]+\/snapshot$/, (route) => {
		const jobId = new URL(route.request().url()).pathname.split('/').at(-2) ?? 'taste-job';
		return route.fulfill({
			json: {
				version: 1,
				job_id: jobId,
				type: 'taste_rebuild',
				label: jobs.find((job) => job.job_id === jobId)?.label ?? 'Taste work',
				phase: 'queued',
				outcome: null,
				desired_state: 'run',
				fence_token: 1,
				execution_class: 'gpu',
				progress_sequence: 0,
				progress: null,
				status: {
					label: 'Queued',
					label_key: 'jobs.status.queued',
					phase: 'queued',
					outcome: null,
					tone: 'queued'
				},
				attention: { level: 'normal', reason: 'none', message: null, remediation: null },
				priority: 0,
				allowed_actions: ['cancel', 'open_detail'],
				links: {
					detail: `/projection-room/jobs/${jobId}`,
					presentation: `/api/jobs/${jobId}/presentation`,
					snapshot: `/api/jobs/${jobId}/snapshot`
				},
				configuration_version: 1,
				eligible_at: null,
				created_at: FIXED_TIME,
				started_at: null,
				terminal_at: null,
				parent_id: null,
				root_id: jobId,
				retry_of_job_id: null,
				updated_at: FIXED_TIME,
				last_event_id: 0
			}
		});
	});

	await page.route(/\/api\/taste(?:\/|$)/, async (route) => {
		const request = route.request();
		const url = new URL(request.url());
		const path = url.pathname;
		const library: TasteLibrary = url.searchParams.get('library') === 'tv' ? 'tv' : 'movies';
		if (request.method() === 'POST') {
			let body: unknown;
			try {
				body = request.postDataJSON();
			} catch {
				body = null;
			}
			const bodyLibrary =
				body && typeof body === 'object' && 'library' in body && body.library === 'tv'
					? 'tv'
					: library;
			requests.push({ method: request.method(), path, library: bodyLibrary, body });
			sequence += 1;
			const jobId = `tastefixture${String(sequence).padStart(20, '0')}`;
			const label = path.endsWith('/enrich')
				? 'Enrich taste metadata'
				: path.endsWith('/map/rebuild')
					? 'Rebuild taste map'
					: path.includes('/residual/')
						? 'Train bounded residual'
						: 'Rebuild taste profile';
			jobs.splice(0, jobs.length, jobRow(jobId, label));
			return route.fulfill({ status: 202, json: jobResponse(jobId) });
		}

		if (path === '/api/taste/status') return route.fulfill({ json: status(library) });
		if (path === '/api/taste/profiles') {
			return route.fulfill({ json: { library, profiles: [profileSummary(library)] } });
		}
		if (path === `/api/taste/profiles/${library}-profile-active`) {
			return route.fulfill({ json: profileDetail(library) });
		}
		if (path === '/api/taste/residuals') {
			return route.fulfill({ json: { residuals: [residualSummary(library)] } });
		}
		if (path === `/api/taste/residuals/${library}-residual-active`) {
			return route.fulfill({ json: { ...residualSummary(library), movies: [] } });
		}
		if (path === '/api/taste/map') {
			return route.fulfill({
				json: {
					projection: { method: 'pca', computed_at: FIXED_TIME },
					points: [],
					summary: { exemplars: 0, unique_movies: 0, duplicate_groups: 0, noise: 0 },
					clusters: [],
					outliers: [],
					clustering: [],
					note: 'Deterministic empty projection for browser certification.'
				}
			});
		}
		return route.fulfill({ status: 404, json: { detail: `Unhandled taste fixture: ${path}` } });
	});

	return {
		requests,
		jobs,
		async expectLastRequest(path: string, library: TasteLibrary) {
			await expect.poll(() => requests.at(-1)?.path).toBe(path);
			expect(requests.at(-1)?.library).toBe(library);
			return requests.at(-1)!;
		}
	};
}
