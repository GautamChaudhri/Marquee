/** Offline fixtures, used when PUBLIC_USE_MOCKS=true so screens build without a
 *  backend. Mirrors the real response shapes from ./types. */
import type {
	MovieDetail,
	MovieListItem,
	MovieQuery,
	Paginated,
	PosterStatus,
	HdrKind,
	SystemMetrics,
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
const HDR: (HdrKind | null)[] = ['dovi', 'hdr10', 'sdr', null];
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

export function mockMetrics(): SystemMetrics {
	return {
		cpu: { model: 'Mock CPU', cores: 8, threads: 16, avg: 23.4, freq: 3600, load: 1.2, temp: 48 },
		gpu: {
			model: 'NVIDIA GeForce RTX 3070',
			util: 17,
			vramUsed: 1_900_000_000,
			vramTotal: 8_589_934_592,
			temp: 44,
			power: 62.5,
			enc: 0
		},
		ram: { used: 9_000_000_000, total: 25_000_000_000, pct: 36 },
		disk: { used: 96_000_000_000, total: 134_000_000_000, pct: 71.6 },
		workers: { active: 1, queued: 2 },
		uptime: '13h 11m'
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
			kind: 'text',
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
			kind: 'text',
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
			kind: 'text',
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
			full_dialogue_languages: ['en', 'ja'],
			forced_only_languages: [],
			sdh_languages: [],
			commentary_present: false,
			external_present: true,
			embedded_present: true,
			generated_present: false,
			unknown_present: false,
			missing_preferred_languages: [],
			track_count: 3
		},
		capabilities: {
			can_remove: true,
			can_embed_text: true,
			can_embed_bitmap: true,
			can_edit_metadata: true
		},
		audio_streams: [
			{ index: 0, language: 'en', channels: 6, codec: 'ac3' },
			{ index: 1, language: 'ja', channels: 2, codec: 'aac' }
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
