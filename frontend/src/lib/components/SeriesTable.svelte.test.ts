import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { SeriesListItem } from '$lib/api/types';
import SeriesTable from './SeriesTable.svelte';

const series: SeriesListItem = {
	id: 7,
	title: 'Partial Signal',
	year: 2024,
	tmdb_id: 107,
	genres: ['Drama', 'Science Fiction'],
	poster: {
		has_poster: true,
		ai_selected: false,
		user_approved: true,
		deployed_at: '2026-08-01T12:00:00Z'
	},
	downloaded_seasons: 1,
	seasons_with_poster: 0,
	season_poster_status: 'missing',
	season_count: 1,
	seasons: [
		{
			id: 70,
			season_number: 0,
			episode_count: 2,
			episode_file_count: 2,
			poster: {
				has_poster: false,
				ai_selected: false,
				user_approved: false,
				deployed_at: null
			}
		}
	]
};

describe('SeriesTable', () => {
	it('shows one uncluttered series-and-posters column in a two-up layout', () => {
		const { container } = render(SeriesTable, { props: { items: [series] } });

		expect(
			screen.getByRole('table', { name: 'Television series and poster previews' })
		).toBeVisible();
		expect(screen.getByRole('columnheader', { name: 'Series and posters' })).toBeInTheDocument();
		expect(screen.queryByRole('columnheader', { name: 'Artwork' })).not.toBeInTheDocument();
		expect(screen.queryByRole('columnheader', { name: 'Genres' })).not.toBeInTheDocument();
		expect(screen.queryByText('Season art')).not.toBeInTheDocument();
		expect(screen.queryByText('In library')).not.toBeInTheDocument();
		expect(screen.queryByText('Drama, Science Fiction')).not.toBeInTheDocument();
		expect(container.querySelector('.table-body')).toBeInTheDocument();
		expect(container.querySelector('svg')).not.toBeInTheDocument();
	});

	it('places one accessible partial-status dot immediately before the linked title', () => {
		render(SeriesTable, { props: { items: [series] } });

		expect(screen.getByRole('link', { name: 'Open Partial Signal, 2024' })).toHaveAttribute(
			'href',
			'/television/7'
		);
		expect(
			screen.getByRole('region', { name: 'Partial Signal show and season posters' })
		).toHaveAttribute('tabindex', '0');
		expect(
			screen.getByRole('img', { name: 'Partially deployed: 1 of 2 posters present' })
		).toBeVisible();
		expect(screen.queryByText('Partially deployed')).not.toBeInTheDocument();
		expect(screen.queryByText('Missing')).not.toBeInTheDocument();
		expect(screen.queryByText('Deployed')).not.toBeInTheDocument();
	});
});
