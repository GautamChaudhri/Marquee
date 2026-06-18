/** Offline fixtures, used when PUBLIC_USE_MOCKS=true so screens build without a
 *  backend. Mirrors the real response shapes from ./types. */
import type {
	MovieDetail,
	MovieListItem,
	MovieQuery,
	Paginated,
	PosterStatus,
	HdrKind,
	SystemMetrics
} from './types';

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
	else items.sort((a, b) => a.title.localeCompare(b.title));
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
