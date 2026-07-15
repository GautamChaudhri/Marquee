import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockSubtitleInventory } from './mock';
import type {
	MediaJob,
	PreferredLanguageState,
	SubtitleInventory,
	SubtitlePlanRequest,
	SubtitlePlan,
	MutationTrackEntry,
	MutationTrackSelector,
	AudioSubsSummary,
	AudioSubsTvIndex,
	AudioSubsTvDetail,
	AudioSubsTvItem
} from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getInventory(fetch: Fetch, mediaFileId: number): Promise<SubtitleInventory> {
	if (useMocks()) return Promise.resolve(mockSubtitleInventory(mediaFileId));
	return apiGet<SubtitleInventory>(fetch, `/media-files/${mediaFileId}/subtitles`);
}

export function scanSubtitles(fetch: Fetch, mediaFileId: number): Promise<SubtitleInventory> {
	if (useMocks()) return Promise.resolve(mockSubtitleInventory(mediaFileId));
	return apiSend<SubtitleInventory>(fetch, 'POST', `/media-files/${mediaFileId}/subtitles/scan`);
}

export function previewTrack(fetch: Fetch, mediaFileId: number, trackId: string): Promise<string> {
	if (useMocks())
		return Promise.resolve(
			'1\n00:00:01,000 --> 00:00:04,000\n[Mock Preview] Subtitle content line.'
		);
	return apiGet<string>(fetch, `/media-files/${mediaFileId}/subtitles/${trackId}/preview`);
}

export function inspectMovie(
	fetch: Fetch,
	movieId: number
): Promise<{
	movie_id: number;
	title: string;
	media_file_id: number;
	path_present: boolean;
	inventory: SubtitleInventory;
	active_job: MediaJob | null;
	preferred_languages?: PreferredLanguageState;
}> {
	if (useMocks()) {
		return Promise.resolve({
			movie_id: movieId,
			title: 'Mock Movie',
			media_file_id: movieId,
			path_present: true,
			inventory: mockSubtitleInventory(movieId),
			active_job: null,
			preferred_languages: {
				shared: ['en'],
				audio: ['en'],
				subtitles: ['en'],
				override: false,
				override_audio: null,
				override_subtitles: null
			}
		});
	}
	return apiSend<{
		movie_id: number;
		title: string;
		media_file_id: number;
		path_present: boolean;
		inventory: SubtitleInventory;
		active_job: MediaJob | null;
		preferred_languages?: PreferredLanguageState;
	}>(fetch, 'POST', `/movies/${movieId}/subtitles/inspect`);
}

export function updateMovieSubtitlePreferences(
	fetch: Fetch,
	movieId: number,
	request: {
		preferred_audio_languages?: string[] | null;
		preferred_subtitle_languages?: string[] | null;
		use_global?: boolean;
	}
): Promise<{ movie_id: number; preferred_languages: PreferredLanguageState }> {
	if (useMocks()) {
		return Promise.resolve({
			movie_id: movieId,
			preferred_languages: {
				shared: ['en'],
				audio: request.preferred_audio_languages || ['en'],
				subtitles: request.preferred_subtitle_languages || ['en'],
				override: !request.use_global,
				override_audio: request.use_global ? null : request.preferred_audio_languages || ['en'],
				override_subtitles: request.use_global
					? null
					: request.preferred_subtitle_languages || ['en']
			}
		});
	}
	return apiSend<{ movie_id: number; preferred_languages: PreferredLanguageState }>(
		fetch,
		'PUT',
		`/movies/${movieId}/subtitles/preferences`,
		request
	);
}

export function createPlan(
	fetch: Fetch,
	mediaFileId: number,
	request: SubtitlePlanRequest
): Promise<SubtitlePlan> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-plan-${Date.now()}`,
			phase: 'planned',
			disposition: 'created',
			operation: request.operation,
			plan_version: '0'.repeat(64),
			input_signature: 'mock-signature',
			configuration_version: 1,
			plan_expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
			snapshot_url: `/api/jobs/job-mock-plan/snapshot`,
			detail_url: `/api/jobs/job-mock-plan/presentation`,
			confirmation_url: `/api/jobs/job-mock-plan/mutation-confirmation`
		});
	}
	return getInventory(fetch, mediaFileId).then((inventory) => {
		const mutation = inventory.mutation_inventory;
		if (!mutation) throw new Error('The server did not return a canonical track inventory.');
		const selector = (entry: MutationTrackEntry): MutationTrackSelector => ({
			track_key: entry.track_key,
			facts: entry.facts,
			inventory_signature: mutation.signature,
			stream_index_hint: entry.stream_index,
			tool_track_id_hint: entry.tool_track_id
		});
		const byStream = (kind: 'audio' | 'subtitle', index: number) => {
			const matches = mutation.tracks.filter(
				(entry) => entry.facts.kind === kind && entry.stream_index === index
			);
			if (matches.length !== 1) throw new Error('The selected track is stale or ambiguous.');
			return selector(matches[0]);
		};
		const subtitleSelectors = request.track_ids.map((id) => {
			const track = inventory.tracks.find((candidate) => candidate.id === id);
			if (!track || track.stream_index == null || track.source !== 'embedded') {
				throw new Error('Only a current embedded track can be selected for this mutation.');
			}
			return byStream('subtitle', track.stream_index);
		});
		const audioSelectors = (request.audio_stream_indices ?? []).map((index) =>
			byStream('audio', index)
		);
		let canonicalRequest: Record<string, unknown>;
		if (
			request.operation === 'audio_remove' ||
			request.operation === 'subtitle_remove' ||
			request.operation === 'track_remove'
		) {
			canonicalRequest = {
				media_file_id: mediaFileId,
				selectors: [...subtitleSelectors, ...audioSelectors]
			};
		} else if (request.operation === 'audio_reorder') {
			canonicalRequest = {
				media_file_id: mediaFileId,
				ordered_selectors: (request.audio_stream_order ?? []).map((index) =>
					byStream('audio', index)
				)
			};
		} else if (request.operation === 'subtitle_metadata') {
			canonicalRequest = {
				media_file_id: mediaFileId,
				edits: (request.edits ?? []).map((edit) => {
					const track = inventory.tracks.find((candidate) => candidate.id === edit.track_id);
					if (!track || track.stream_index == null || track.source !== 'embedded') {
						throw new Error('Subtitle metadata requires a current embedded subtitle track.');
					}
					return {
						selector: byStream('subtitle', track.stream_index),
						language_tag: edit.language_tag,
						title: edit.title,
						is_default: edit.is_default,
						is_forced: edit.is_forced,
						is_hearing_impaired: edit.is_sdh
					};
				})
			};
		} else {
			throw new Error('This action requires a managed subtitle artifact.');
		}
		return apiSend<SubtitlePlan>(
			fetch,
			'POST',
			`/media-files/${mediaFileId}/subtitle-plans`,
			{ operation: request.operation, request: canonicalRequest },
			{ 'Idempotency-Key': `${request.operation}:${crypto.randomUUID()}` }
		);
	});
}

export function extractTrack(
	fetch: Fetch,
	mediaFileId: number,
	trackId: string
): Promise<SubtitlePlan> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-extract-${Date.now()}`,
			phase: 'planned',
			disposition: 'created',
			operation: 'subtitle_extract',
			plan_version: '0'.repeat(64),
			input_signature: 'mock-signature',
			configuration_version: 1,
			plan_expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
			snapshot_url: '/api/jobs/job-mock-extract/snapshot',
			detail_url: '/api/jobs/job-mock-extract/presentation',
			confirmation_url: '/api/jobs/job-mock-extract/mutation-confirmation'
		});
	}
	return apiSend<SubtitlePlan>(
		fetch,
		'POST',
		`/media-files/${mediaFileId}/subtitles/${trackId}/extract`,
		undefined,
		{ 'Idempotency-Key': `subtitle_extract:${crypto.randomUUID()}` }
	);
}

export function scanLibrarySubtitles(
	fetch: Fetch,
	force: boolean = false
): Promise<{ job_id: string; status: 'queued' }> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-scan-all-${Date.now()}`,
			status: 'queued'
		});
	}
	return apiSend<{ job_id: string; status: 'queued' }>(
		fetch,
		'POST',
		`/subtitles/scan-library?force=${force}`
	);
}

export function getAudioSubsSummary(fetch: Fetch): Promise<AudioSubsSummary> {
	if (useMocks()) {
		return Promise.resolve({
			movies: {
				total: 10,
				audio_ok: 8,
				audio_gap: 2,
				subtitle_ok: 7,
				subtitle_gap: 3,
				both_gap: 1,
				unknown: 1,
				forced_coverage: 5,
				sdh_coverage: 4,
				unknown_language_tracks: 2,
				generated_tracks: 1
			},
			tv: {
				audio_ok: 50,
				audio_gap: 10,
				subtitle_ok: 45,
				subtitle_gap: 15,
				both_gap: 5,
				unknown: 2,
				forced_coverage: 20,
				sdh_coverage: 15,
				show_status_counts: { ok: 4, gaps: 1, 'none met': 0, unknown: 0 },
				uniformity_counts: { uniform: 3, uniform_by_season: 1, mixed: 1 },
				dub_coverage_highlights: [
					{
						series_id: 1,
						title: 'Mock Show 1',
						missing_audio_languages: ['fr'],
						coverage: { ok: 8, of: 10 }
					}
				]
			},
			preferred: {
				audio: ['en'],
				subtitles: ['en'],
				shared: ['en']
			},
			policies: {
				active_count: 1,
				last_audit_summary: null
			},
			generator: [
				{
					name: 'Mock Generator',
					type: 'whisper',
					url: 'http://localhost:3165',
					online: true,
					version: '1.0.0',
					model: 'large-v3',
					device: 'cuda',
					capabilities: {
						language_hint: true,
						translate: true,
						concurrent: 2
					}
				}
			],
			deep_scan: {
				enabled: true,
				hour: 3,
				last_run_at: '2026-07-04T03:00:00Z',
				pending_file_count: 0
			}
		});
	}
	return apiGet<AudioSubsSummary>(fetch, '/audio-subs/summary');
}

export function getAudioSubsTv(
	fetch: Fetch,
	params: {
		status?: string | null;
		uniformity?: string | null;
		missing_language?: string | null;
		q?: string | null;
		sort_by?: 'title' | 'status' | 'coverage';
	} = {}
): Promise<AudioSubsTvIndex> {
	if (useMocks()) {
		const items: AudioSubsTvItem[] = [
			{
				series_id: 1,
				title: 'Mock Show 1',
				year: 2020,
				rollup: {
					episodes_total: 10,
					episodes_counted: 10,
					status_counts: { ok: 8, audio_gap: 1, subtitle_gap: 1, both_gap: 0, unknown: 0 },
					missing_languages: ['fr'],
					missing_audio_languages: ['fr'],
					missing_subtitle_languages: [],
					dub_coverage: { ok: 9, of: 10 },
					subtitle_coverage: { ok: 9, of: 10 },
					status: 'gaps',
					uniformity: 'uniform_by_season'
				},
				missing_languages: ['fr'],
				dub_coverage: { ok: 9, of: 10 },
				uniformity: 'uniform_by_season',
				episode_fraction: '9/10',
				active_scan_job_ids: [],
				active_generation_job_ids: []
			},
			{
				series_id: 2,
				title: 'Mock Show 2',
				year: 2021,
				rollup: {
					episodes_total: 5,
					episodes_counted: 5,
					status_counts: { ok: 5, audio_gap: 0, subtitle_gap: 0, both_gap: 0, unknown: 0 },
					missing_languages: [],
					missing_audio_languages: [],
					missing_subtitle_languages: [],
					dub_coverage: { ok: 5, of: 5 },
					subtitle_coverage: { ok: 5, of: 5 },
					status: 'ok',
					uniformity: 'uniform'
				},
				missing_languages: [],
				dub_coverage: { ok: 5, of: 5 },
				uniformity: 'uniform',
				episode_fraction: '5/5',
				active_scan_job_ids: [],
				active_generation_job_ids: []
			}
		];
		return Promise.resolve({
			total: items.length,
			items,
			applied_filters: {
				status: params.status || null,
				uniformity: params.uniformity || null,
				missing_language: params.missing_language || null,
				q: params.q || null,
				sort_by: params.sort_by || 'title'
			}
		});
	}

	return apiGet<AudioSubsTvIndex>(fetch, '/audio-subs/tv', params);
}

export function getAudioSubsTvDetail(fetch: Fetch, seriesId: number): Promise<AudioSubsTvDetail> {
	if (useMocks()) {
		return Promise.resolve({
			series: {
				id: seriesId,
				title: 'Mock Show ' + seriesId,
				year: 2020
			},
			preferred_audio_languages: ['en'],
			preferred_subtitle_languages: ['en'],
			rollup: {
				episodes_total: 2,
				episodes_counted: 2,
				status_counts: { ok: 2, audio_gap: 0, subtitle_gap: 0, both_gap: 0, unknown: 0 },
				missing_languages: [],
				missing_audio_languages: [],
				missing_subtitle_languages: [],
				dub_coverage: { ok: 2, of: 2 },
				subtitle_coverage: { ok: 2, of: 2 },
				status: 'ok',
				uniformity: 'uniform'
			},
			seasons: [
				{
					season_number: 1,
					rollup: {
						episodes_total: 2,
						episodes_counted: 2,
						status_counts: { ok: 2, audio_gap: 0, subtitle_gap: 0, both_gap: 0, unknown: 0 },
						missing_languages: [],
						missing_audio_languages: [],
						missing_subtitle_languages: [],
						dub_coverage: { ok: 2, of: 2 },
						subtitle_coverage: { ok: 2, of: 2 },
						status: 'ok',
						uniformity: 'uniform'
					},
					episodes: [
						{
							episode_id: 101,
							code: 's01e01',
							title: 'Episode 1',
							audio_languages: ['en'],
							subtitle_languages: ['en'],
							forced_languages: [],
							sdh_languages: [],
							tier: 'synced',
							status: 'ok',
							media_file_id: 1001
						},
						{
							episode_id: 102,
							code: 's01e02',
							title: 'Episode 2',
							audio_languages: ['en'],
							subtitle_languages: ['en'],
							forced_languages: [],
							sdh_languages: [],
							tier: 'probed',
							status: 'ok',
							media_file_id: 1002
						}
					],
					active_scan_job_ids: [],
					active_generation_job_ids: []
				}
			]
		});
	}
	return apiGet<AudioSubsTvDetail>(fetch, `/audio-subs/tv/${seriesId}`);
}

export function putAudioSubsPreferences(
	fetch: Fetch,
	prefs: {
		preferred_languages?: string[] | null;
		preferred_audio_languages?: string[] | null;
		preferred_subtitle_languages?: string[] | null;
	},
	expectedVersion: number
): Promise<{ ok: boolean; configuration_version: number; etag: string; changed: boolean }> {
	if (useMocks()) {
		return Promise.resolve({
			ok: true,
			configuration_version: expectedVersion + 1,
			etag: `"configuration-${expectedVersion + 1}"`,
			changed: true
		});
	}
	return apiSend<{ ok: boolean; configuration_version: number; etag: string; changed: boolean }>(
		fetch,
		'PUT',
		'/audio-subs/preferences',
		{ ...prefs, expected_version: expectedVersion }
	);
}

export function putSeriesPreferences(
	fetch: Fetch,
	seriesId: number,
	prefs: {
		preferred_audio_languages?: string[] | null;
		preferred_subtitle_languages?: string[] | null;
	}
): Promise<{ ok: boolean }> {
	if (useMocks()) {
		return Promise.resolve({ ok: true });
	}
	return apiSend<{ ok: boolean }>(fetch, 'PUT', `/audio-subs/tv/${seriesId}/preferences`, prefs);
}

export function deepScan(
	fetch: Fetch,
	scope: 'movies' | 'tv' | 'all' = 'all'
): Promise<{ job_id: string; status: string }> {
	if (useMocks()) {
		return Promise.resolve({ job_id: `job-mock-deep-scan-${Date.now()}`, status: 'queued' });
	}
	return apiSend<{ job_id: string; status: string }>(fetch, 'POST', '/audio-subs/deep-scan', {
		scope
	});
}

export function deepScanSeries(
	fetch: Fetch,
	seriesId: number,
	seasonNumber?: number | null
): Promise<{ job_id: string; status: string }> {
	if (useMocks()) {
		return Promise.resolve({ job_id: `job-mock-deep-scan-tv-${Date.now()}`, status: 'queued' });
	}
	return apiSend<{ job_id: string; status: string }>(
		fetch,
		'POST',
		`/audio-subs/tv/${seriesId}/deep-scan`,
		{ season_number: seasonNumber ?? null }
	);
}

export function generateTv(
	fetch: Fetch,
	seriesId: number,
	body: {
		season_number?: number | null;
		language_hint?: string | null;
		output?: 'external' | 'embedded';
		task?: 'transcribe' | 'translate';
		stream_index?: number | null;
	}
): Promise<{ job_id: string; total: number; snapshot_url: string }> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-gen-tv-${Date.now()}`,
			total: 5,
			snapshot_url: `/api/jobs/job-mock-gen-tv-${Date.now()}/snapshot`
		});
	}
	return apiSend<{ job_id: string; total: number; snapshot_url: string }>(
		fetch,
		'POST',
		`/audio-subs/tv/${seriesId}/generate`,
		body,
		{ 'Idempotency-Key': `subtitle_generate_batch:${crypto.randomUUID()}` }
	);
}
