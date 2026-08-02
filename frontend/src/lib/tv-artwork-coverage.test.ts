import { describe, expect, it } from 'vitest';
import type { PosterSummary, SeasonSummary, SeriesListItem } from '$lib/api/types';
import { deriveArtworkCoverage } from '$lib/tv-artwork-coverage';

const poster = (hasPoster: boolean): PosterSummary => ({
	has_poster: hasPoster,
	ai_selected: false,
	user_approved: hasPoster,
	deployed_at: hasPoster ? '2026-08-01T12:00:00Z' : null
});

const season = (seasonNumber: number, hasPoster: boolean): SeasonSummary => ({
	id: seasonNumber + 10,
	season_number: seasonNumber,
	episode_count: 8,
	episode_file_count: 8,
	poster: poster(hasPoster)
});

function series(overrides: Partial<SeriesListItem> = {}): SeriesListItem {
	return {
		id: 1,
		title: 'The Complete Series',
		year: 2026,
		tmdb_id: 1001,
		genres: ['Drama'],
		poster: poster(true),
		downloaded_seasons: 3,
		seasons_with_poster: 3,
		season_poster_status: 'complete',
		season_count: 3,
		seasons: [season(0, true), season(1, true), season(2, true)],
		...overrides
	};
}

describe('deriveArtworkCoverage', () => {
	it('is green and deployed only when the show and every downloaded season are present', () => {
		expect(deriveArtworkCoverage(series())).toEqual({
			state: 'deployed',
			tone: 'good',
			label: 'Deployed',
			accessibleLabel: 'Deployed: all 4 posters present',
			presentPosterCount: 4,
			totalPosterCount: 4
		});
	});

	it('is yellow and partial when only the show poster is present', () => {
		expect(
			deriveArtworkCoverage(series({ seasons: [season(0, false), season(1, false)] }))
		).toEqual({
			state: 'partial',
			tone: 'warn',
			label: 'Partially deployed',
			accessibleLabel: 'Partially deployed: 1 of 3 posters present',
			presentPosterCount: 1,
			totalPosterCount: 3
		});
	});

	it('is yellow and partial when only season artwork is present', () => {
		expect(
			deriveArtworkCoverage(
				series({ poster: poster(false), seasons: [season(0, true), season(1, true)] })
			)
		).toMatchObject({
			state: 'partial',
			tone: 'warn',
			presentPosterCount: 2,
			totalPosterCount: 3
		});
	});

	it('is red and missing when neither show nor season posters are present', () => {
		expect(
			deriveArtworkCoverage(
				series({ poster: poster(false), seasons: [season(0, false), season(1, false)] })
			)
		).toEqual({
			state: 'missing',
			tone: 'bad',
			label: 'Missing',
			accessibleLabel: 'Missing: none of 3 posters present',
			presentPosterCount: 0,
			totalPosterCount: 3
		});
	});

	it('counts downloaded specials from the exact season array', () => {
		const coverage = deriveArtworkCoverage(
			series({ seasons: [season(0, false), season(1, true)] })
		);

		expect(coverage).toMatchObject({
			state: 'partial',
			presentPosterCount: 2,
			totalPosterCount: 3
		});
	});

	it('ignores contradictory aggregate counters and trusts exact season records', () => {
		const coverage = deriveArtworkCoverage(
			series({
				downloaded_seasons: 99,
				seasons_with_poster: 0,
				season_poster_status: 'missing',
				seasons: [season(0, true)]
			})
		);

		expect(coverage).toMatchObject({
			state: 'deployed',
			presentPosterCount: 2,
			totalPosterCount: 2
		});
	});

	it('treats a present show with no downloaded seasons as deployed', () => {
		expect(deriveArtworkCoverage(series({ seasons: [] }))).toMatchObject({
			state: 'deployed',
			accessibleLabel: 'Deployed: poster present',
			presentPosterCount: 1,
			totalPosterCount: 1
		});
	});

	it('uses singular accessible grammar for a missing show with no downloaded seasons', () => {
		expect(deriveArtworkCoverage(series({ poster: poster(false), seasons: [] }))).toMatchObject({
			state: 'missing',
			accessibleLabel: 'Missing: poster not present'
		});
	});
});
