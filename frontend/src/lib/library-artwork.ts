import type { MovieListItem } from '$lib/api/types';
import type { Tone } from '$lib/display';

export type FilmArtworkState = 'deployed' | 'missing' | 'review';

export interface FilmArtworkStatus {
	state: FilmArtworkState;
	tone: Tone;
	label: 'Deployed' | 'Missing' | 'Needs review';
	accessibleLabel: string;
}

/** Collapse legacy poster provenance into the three library-facing states. */
export function deriveFilmArtworkStatus(movie: MovieListItem): FilmArtworkStatus {
	if (movie.review_pending) {
		return {
			state: 'review',
			tone: 'review',
			label: 'Needs review',
			accessibleLabel: 'Needs review: poster selection is waiting in the Pipeline'
		};
	}

	if (movie.poster_status !== 'missing') {
		return {
			state: 'deployed',
			tone: 'good',
			label: 'Deployed',
			accessibleLabel: 'Deployed: poster present'
		};
	}

	return {
		state: 'missing',
		tone: 'bad',
		label: 'Missing',
		accessibleLabel: 'Missing: poster not present'
	};
}
