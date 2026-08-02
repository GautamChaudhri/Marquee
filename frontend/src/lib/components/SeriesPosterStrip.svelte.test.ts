import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { PosterSummary, SeasonSummary, SeriesListItem } from '$lib/api/types';
import SeriesPosterStrip from './SeriesPosterStrip.svelte';

const poster = (hasPoster: boolean): PosterSummary => ({
	has_poster: hasPoster,
	ai_selected: false,
	user_approved: hasPoster,
	deployed_at: hasPoster ? '2026-08-01T12:00:00Z' : null
});

const season = (id: number, number: number, hasPoster: boolean): SeasonSummary => ({
	id,
	season_number: number,
	episode_count: 8,
	episode_file_count: 8,
	poster: poster(hasPoster)
});

const series: SeriesListItem = {
	id: 42,
	title: 'Signal House',
	year: 2026,
	tmdb_id: 142,
	genres: ['Drama', 'Mystery'],
	poster: poster(true),
	review_pending: false,
	downloaded_seasons: 2,
	seasons_with_poster: 1,
	season_poster_status: 'partial',
	season_count: 2,
	seasons: [season(202, 2, true), season(200, 0, false)]
};

describe('SeriesPosterStrip', () => {
	it('orders the show, specials, and seasons in a keyboard-scrollable region', () => {
		const { container } = render(SeriesPosterStrip, { props: { series } });
		const strip = screen.getByRole('region', { name: 'Signal House show and season posters' });
		const tiles = [...container.querySelectorAll<HTMLElement>('.poster-tile')];

		expect(strip).toHaveAttribute('tabindex', '0');
		expect(tiles.map((tile) => tile.dataset.posterKind)).toEqual(['show', 'season', 'season']);
		expect(tiles[1]).toHaveAttribute('data-season-number', '0');
		expect(tiles[2]).toHaveAttribute('data-season-number', '2');
	});

	it('uses deployed poster endpoints and gradient fallbacks with deliberate copy placement', () => {
		const { container } = render(SeriesPosterStrip, { props: { series } });

		expect(screen.getByAltText('Signal House show poster')).toHaveAttribute(
			'src',
			'/api/library/series/42/poster'
		);
		expect(screen.getByAltText('Signal House S02 poster')).toHaveAttribute(
			'src',
			'/api/library/seasons/202/poster'
		);
		expect(screen.getAllByText('S00')).toHaveLength(2);
		expect(container.querySelectorAll('.poster.plain-fallback')).toHaveLength(0);
		expect(container.querySelector('.poster.centered-title .title')).toHaveTextContent('S00');
		expect(container.querySelectorAll('.poster .dot')).toHaveLength(0);
		expect(container.querySelectorAll('[title="No poster"]')).toHaveLength(0);
	});
});
