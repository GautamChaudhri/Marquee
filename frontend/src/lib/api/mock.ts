/** Offline fixtures, used when PUBLIC_USE_MOCKS=true so screens build without a
 *  backend. Mirrors the real response shapes from ./types. */
import type {
	HdrPreferenceChoice,
	MovieDetail,
	MovieListItem,
	MovieQuery,
	Paginated,
	PosterStatus,
	HdrKind,
	RadarrOverlayQuery,
	RadarrOverlayResponse,
	RadarrOverlayStatus,
	SystemMetrics,
	SystemMetricsHistory,
	SubtitleInventory,
	SubtitleGenerator,
	SubtitlePolicy,
	SubtitleTrack
} from './types';
import { compareBySortTitle } from '../sort-title';

const TITLES = [
	'Arrival',
	'Blade Runner 2049',
	'Dune',
	'Everything Everywhere All at Once',
	'The Grand Budapest Hotel',
	'Heat',
	'Interstellar',
	'La La Land',
	'Mad Max: Fury Road',
	'No Country for Old Men',
	'Oppenheimer',
	'Parasite'
];
const POSTER: PosterStatus[] = ['deployed', 'approved', 'review', 'missing'];
const HDR: (HdrKind | null)[] = ['dovi', 'hdr10p', 'hdr10', 'hdr', 'sdr', null];
const RES = ['4K', '1080p', '720p', null];

function makeItem(i: number): MovieListItem {
	return {
		id: i + 1,
		title: TITLES[i % TITLES.length],
		year: 2014 + (i % 10),
		tmdb_id: 1000 + i,
		genres: ['Drama', 'Sci-Fi'].slice(0, (i % 2) + 1),
		container: 'mkv',
		video_width: RES[i % RES.length] === '4K' ? 3840 : 1920,
		video_height: 1600,
		resolution: RES[i % RES.length],
		poster_status: POSTER[i % POSTER.length],
		poster_url: null,
		hdr: HDR[i % HDR.length],
		hdr_tags: HDR[i % HDR.length] ? [HDR[i % HDR.length] as HdrKind] : [],
		letterbox_status: i % 3 === 0 ? 'candidate' : 'none',
		subtitle_status: i % 4 === 0 ? 'gap' : 'ok',
		media_file_id: i + 1,
		subtitle_coverage: null
	};
}

const ALL: MovieListItem[] = Array.from({ length: TITLES.length }, (_, i) => makeItem(i));

export function mockMovies(params: MovieQuery = {}): Paginated<MovieListItem> {
	let items = [...ALL];
	if (params.q)
		items = items.filter((m) => m.title.toLowerCase().includes(params.q!.toLowerCase()));
	if (params.poster_status) items = items.filter((m) => m.poster_status === params.poster_status);
	if (params.hdr) items = items.filter((m) => m.hdr === params.hdr);
	if (params.sort === 'year') items.sort((a, b) => b.year - a.year);
	else items.sort((a, b) => compareBySortTitle(a.title, b.title));
	return { total: items.length, page: params.page ?? 1, page_size: params.page_size ?? 50, items };
}

export function mockMovieDetail(id: number): MovieDetail {
	const base = ALL.find((m) => m.id === id) ?? makeItem(0);
	return { ...base, media_file_path: `/movies/${base.title}/${base.title}.mkv` };
}

export function mockRadarrOverlay(params: RadarrOverlayQuery = {}): RadarrOverlayResponse {
	let items = ALL.map((item, index) => ({
		...item,
		hdr_tags:
			item.hdr === 'dovi'
				? ['dovi', index % 2 ? 'dovi_no_fallback' : 'hdr10']
				: item.hdr
					? [item.hdr]
					: [],
		dovi_no_fallback: index % 2 === 1 && item.hdr === 'dovi',
		dovi_status: item.hdr === 'dovi' ? 'analyzed' : 'unknown',
		dovi_profile: item.hdr === 'dovi' ? (index % 3 === 0 ? 5 : index % 3 === 1 ? 7 : 8) : null,
		dovi_el_type: item.hdr === 'dovi' && index % 3 === 1 ? (index % 2 ? 'FEL' : 'MEL') : null,
		dovi_bl_signal_compatibility_id: item.hdr === 'dovi' ? (index % 3 === 2 ? 1 : 0) : null,
		profile_id: index % 3 === 0 ? 3 : 4,
		profile_name: index % 3 === 0 ? 'UHD Cinema' : 'Web 4K',
		cf_score: 40 - index * 2,
		cf_cutoff: index % 3 === 0 ? 100 : 60,
		cutoff_met: index % 4 === 0 ? true : index % 4 === 1 ? false : null,
		profile_targets: (index % 3 === 0 ? ['dovi', 'hdr10'] : ['hdr10p']) as HdrKind[],
		available_preference_targets: (index % 3 === 0
			? ['hdr10', 'dovi_no_fallback', 'dovi_fallback']
			: ['hdr10p']) as HdrPreferenceChoice[],
		meet_target: (index % 3 === 0 ? 'hdr10' : 'hdr10p') as HdrPreferenceChoice,
		exceed_target: (index % 3 === 0 ? 'dovi_fallback' : null) as HdrPreferenceChoice | null,
		preference_status: (index % 4 === 0
			? 'exceeds_target'
			: index % 4 === 1
				? 'meets_target'
				: index % 4 === 2
					? 'below_target'
					: 'no_hdr_target') as RadarrOverlayStatus
	})) as RadarrOverlayResponse['items'];

	if (params.hdr_tags?.length) {
		items = items.filter((item) =>
			params.hdr_tags!.some((tag) => item.hdr_tags.includes(tag as HdrKind))
		);
	}
	if (params.preference_status) {
		items = items.filter((item) => item.preference_status === params.preference_status);
	}
	if (params.profile_id) items = items.filter((item) => item.profile_id === params.profile_id);
	if (params.dovi_no_fallback) items = items.filter((item) => item.dovi_no_fallback);
	if (params.sort_by === 'year') items.sort((a, b) => b.year - a.year);
	else if (params.sort_by === 'title') items.sort((a, b) => compareBySortTitle(a.title, b.title));
	else if (params.sort_by === 'preference_status') {
		const rank: Record<RadarrOverlayStatus, number> = {
			no_hdr_target: -1,
			below_target: 0,
			meets_target: 1,
			exceeds_target: 2
		};
		items.sort(
			(a, b) =>
				rank[b.preference_status] - rank[a.preference_status] ||
				(b.cf_score ?? -999999) - (a.cf_score ?? -999999) ||
				compareBySortTitle(a.title, b.title)
		);
	} else
		items.sort(
			(a, b) =>
				(b.cf_score ?? -999999) - (a.cf_score ?? -999999) || compareBySortTitle(a.title, b.title)
		);

	return {
		total: items.length,
		page: params.page ?? 1,
		page_size: params.page_size ?? 50,
		items,
		distribution: {
			hdr: 2,
			hdr10: 3,
			hdr10p: 2,
			dovi: 2,
			dovi_no_fallback: 1,
			sdr: 2,
			unknown: 1
		},
		distribution_order: ['hdr', 'hdr10', 'hdr10p', 'dovi', 'dovi_no_fallback', 'sdr', 'unknown'],
		profiles: [
			{ id: 3, name: 'UHD Cinema', cutoff_format_score: 100 },
			{ id: 4, name: 'Web 4K', cutoff_format_score: 60 }
		],
		profile_preferences: [
			{
				profile_id: 3,
				profile_name: 'UHD Cinema',
				profile_targets: ['hdr10', 'dovi'],
				available_preference_targets: ['hdr10', 'dovi_no_fallback', 'dovi_fallback'],
				meet_target: 'hdr10',
				exceed_target: 'dovi_fallback',
				excluded_targets: []
			},
			{
				profile_id: 4,
				profile_name: 'Web 4K',
				profile_targets: ['hdr10p'],
				available_preference_targets: ['hdr10p'],
				meet_target: 'hdr10p',
				exceed_target: null,
				excluded_targets: []
			}
		],
		applied_filters: {}
	};
}

export function mockMetrics(): SystemMetrics {
	return {
		cpu: {
			model: 'Intel Core i5-13600KF',
			cores: 14,
			threads: 20,
			avg: 23.4,
			perCore: Array.from({ length: 20 }, (_, i) => 10 + ((i * 7) % 60)),
			freq: 3600,
			load: 1.2,
			temp: 48
		},
		gpu: {
			model: 'NVIDIA GeForce RTX 3070',
			util: 17,
			memUtil: 9,
			vramUsed: 1_900_000_000,
			vramTotal: 8_589_934_592,
			temp: 44,
			power: 62.5,
			enc: 0,
			dec: 0
		},
		ram: { used: 9_000_000_000, total: 25_000_000_000, pct: 36 },
		disk: {
			used: 96_000_000_000,
			total: 134_000_000_000,
			pct: 71.6,
			readBytes: 482_000_000_000,
			writeBytes: 119_000_000_000
		},
		net: { bytesSent: 21_400_000_000, bytesRecv: 88_900_000_000 },
		workers: { active: 1, queued: 2 },
		uptime: '13h 11m'
	};
}

export function mockMetricsHistory(
	window: '15m' | '1h' | '6h' | '24h' = '1h'
): SystemMetricsHistory {
	const now = Date.now();
	const seconds = { '15m': 900, '1h': 3600, '6h': 21600, '24h': 86400 }[window];
	const step = seconds / 30;
	return {
		window,
		start_at: new Date(now - seconds * 1000).toISOString(),
		end_at: new Date(now).toISOString(),
		points: Array.from({ length: 30 }, (_, index) => ({
			ts: new Date(now - (29 - index) * step * 1000).toISOString(),
			cpu_avg: 20 + ((index * 7) % 35),
			gpu_util: 10 + ((index * 9) % 40),
			gpu_mem: 8 + ((index * 5) % 30),
			gpu_enc: index % 8 === 0 ? 22 : 0,
			gpu_dec: index % 10 === 0 ? 16 : 0,
			ram_pct: 34 + ((index * 3) % 12),
			disk_read_bps: 8_000_000 + ((index * 2_500_000) % 22_000_000),
			disk_write_bps: 4_000_000 + ((index * 1_700_000) % 18_000_000),
			net_recv_bps: 1_500_000 + ((index * 350_000) % 6_500_000),
			net_sent_bps: 600_000 + ((index * 190_000) % 2_000_000),
			active_jobs: index % 6 === 0 ? 2 : index % 4 === 0 ? 1 : 0
		})),
		jobs: [
			{
				job_id: 'job-1',
				type: 'poster_pipeline',
				label: 'Poster Pipeline',
				status: 'succeeded',
				subject: 'Blade Runner 2049',
				started_at: new Date(now - seconds * 0.72 * 1000).toISOString(),
				finished_at: new Date(now - seconds * 0.52 * 1000).toISOString()
			},
			{
				job_id: 'job-2',
				type: 'subtitle_generate',
				label: 'Subtitle Generation',
				status: 'running',
				subject: 'Arrival',
				started_at: new Date(now - seconds * 0.22 * 1000).toISOString(),
				finished_at: null
			}
		]
	};
}

export function mockSubtitleInventory(mediaFileId: number): SubtitleInventory {
	const tracks: SubtitleTrack[] = [
		{
			id: 'track-en-1',
			source: 'embedded',
			stream_index: 2,
			tool_track_id: 3,
			external_path: null,
			codec: 'subrip',
			codec_label: 'SRT',
			kind: 'text',
			kind_label: 'Text',
			language_raw: 'eng',
			language_tag: 'en',
			language_source: 'metadata',
			title: 'English Dialogue',
			is_default: true,
			is_forced: false,
			is_sdh: false,
			is_commentary: false,
			is_generated: false,
			size_bytes: 45000,
			per_track_actions: {
				remove: { available: true, reason: null },
				embed: { available: false, reason: 'Already embedded' },
				extract: { available: true, reason: null }
			}
		},
		{
			id: 'track-en-forced',
			source: 'embedded',
			stream_index: 3,
			tool_track_id: 4,
			external_path: null,
			codec: 'subrip',
			codec_label: 'SRT',
			kind: 'text',
			kind_label: 'Text',
			language_raw: 'eng',
			language_tag: 'en',
			language_source: 'metadata',
			title: 'English Forced',
			is_default: false,
			is_forced: true,
			is_sdh: false,
			is_commentary: false,
			is_generated: false,
			size_bytes: 2500,
			per_track_actions: {
				remove: { available: true, reason: null },
				embed: { available: false, reason: 'Already embedded' },
				extract: { available: true, reason: null }
			}
		},
		{
			id: 'track-ja-ext',
			source: 'external',
			stream_index: 0,
			tool_track_id: 0,
			external_path: '/movies/MockMovie/MockMovie.ja.srt',
			codec: 'subrip',
			codec_label: 'SRT',
			kind: 'text',
			kind_label: 'Text',
			language_raw: 'jpn',
			language_tag: 'ja',
			language_source: 'filename',
			title: 'Japanese Sidecar',
			is_default: false,
			is_forced: false,
			is_sdh: false,
			is_commentary: false,
			is_generated: false,
			size_bytes: 38000,
			per_track_actions: {
				remove: { available: true, reason: null },
				embed: { available: true, reason: null },
				extract: { available: false, reason: 'Already external' }
			}
		}
	];

	return {
		inventory_id: `inv-${mediaFileId}`,
		file_path: `/movies/MockMovie/MockMovie.mkv`,
		container: 'mkv',
		duration_seconds: 7200,
		tracks,
		coverage: {
			audio_languages: ['en', 'ja'],
			audio_channels_by_language: { en: ['5.1'], ja: ['2.0'] },
			full_dialogue_languages: ['en', 'ja'],
			forced_only_languages: [],
			sdh_languages: [],
			commentary_present: false,
			external_present: true,
			embedded_present: true,
			generated_present: false,
			unknown_present: false,
			preferred_audio_languages: ['en'],
			preferred_subtitle_languages: ['en'],
			missing_preferred_audio_languages: [],
			missing_preferred_languages: [],
			audio_status: 'ok',
			subtitle_status: 'ok',
			status: 'ok',
			track_count: 3
		},
		capabilities: {
			can_remove: true,
			can_embed_text: true,
			can_embed_bitmap: true,
			can_edit_metadata: true
		},
		audio_streams: [
			{
				index: 0,
				language: 'en',
				language_tag: 'en',
				channels: 6,
				channel_layout: '5.1',
				channel_label: '5.1',
				codec: 'ac3',
				format_label: 'Dolby Digital',
				is_default: true
			},
			{
				index: 1,
				language: 'ja',
				language_tag: 'ja',
				channels: 2,
				channel_layout: 'stereo',
				channel_label: '2.0',
				codec: 'aac',
				format_label: 'AAC'
			}
		],
		file_signature: `sig-${mediaFileId}`,
		scanned_at: new Date().toISOString()
	};
}

export function mockGenerators(): { generators: SubtitleGenerator[] } {
	return {
		generators: [
			{
				name: 'faster-whisper-local',
				type: 'subgen',
				url: 'http://localhost:9000',
				online: true,
				version: '2026.06.3',
				model: 'medium',
				device: 'cuda',
				capabilities: {
					language_hint: true,
					translate: true,
					concurrent: 2
				}
			}
		]
	};
}

export function mockPolicies(): { policies: SubtitlePolicy[] } {
	return {
		policies: [
			{
				id: 1,
				name: 'English Cleanup',
				enabled: true,
				revision: 1,
				mode: 'allowlist',
				languages: ['en'],
				unknown_action: 'review',
				protect_forced: true,
				protect_default: true,
				protect_last_full_dialogue: true,
				include_external: true,
				auto_apply: false,
				audit_only: false,
				hardlink_action: 'block',
				backup_mode: 'keep_original',
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString()
			}
		]
	};
}
