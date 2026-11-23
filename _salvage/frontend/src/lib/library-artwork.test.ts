import { describe, expect, it } from 'vitest';
import type { MovieListItem, PosterStatus } from '$lib/api/types';
import { deriveFilmArtworkStatus } from '$lib/library-artwork';

function movie(posterStatus: PosterStatus, reviewPending = false): MovieListItem {
	return {
		id: 1,
		title: 'Signal House',
		year: 2026,
		tmdb_id: 1001,
		genres: ['Drama'],
		container: 'mkv',
		video_width: 1920,
		video_height: 1080,
		resolution: '1080p',
		poster_status: posterStatus,
		review_pending: reviewPending,
		poster_url: posterStatus === 'missing' ? null : '/api/library/movies/1/poster',
		media_file_id: 1
	};
}

describe('deriveFilmArtworkStatus', () => {
	it.each(['deployed', 'approved', 'review'] as const)(
		'collapses a present %s poster into deployed',
		(posterStatus) => {
			expect(deriveFilmArtworkStatus(movie(posterStatus))).toMatchObject({
				state: 'deployed',
				tone: 'good',
				label: 'Deployed'
			});
		}
	);

	it('shows missing only when no poster or review is present', () => {
		expect(deriveFilmArtworkStatus(movie('missing'))).toMatchObject({
			state: 'missing',
			tone: 'bad',
			label: 'Missing'
		});
	});

	it.each(['missing', 'deployed', 'approved', 'review'] as const)(
		'lets Pipeline review override the underlying %s state',
		(posterStatus) => {
			expect(deriveFilmArtworkStatus(movie(posterStatus, true))).toMatchObject({
				state: 'review',
				tone: 'review',
				label: 'Needs review'
			});
		}
	);
});
