/** Offline fixtures, used when PUBLIC_USE_MOCKS=true so screens build without a
 *  backend. Mirrors the real response shapes from ./types. */
import type {
	MovieDetail,
	MovieListItem,
	MovieQuery,
	Paginated,
	PosterStatus,
	SystemMetrics,
	SystemMetricsHistory
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
		media_file_id: i + 1
	};
}

const ALL: MovieListItem[] = Array.from({ length: TITLES.length }, (_, i) => makeItem(i));

export function mockMovies(params: MovieQuery = {}): Paginated<MovieListItem> {
	let items = [...ALL];
	if (params.q)
		items = items.filter((m) => m.title.toLowerCase().includes(params.q!.toLowerCase()));
	if (params.poster_status) items = items.filter((m) => m.poster_status === params.poster_status);
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
