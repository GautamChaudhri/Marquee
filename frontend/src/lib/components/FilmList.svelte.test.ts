import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { MovieListItem, PosterStatus } from '$lib/api/types';
import FilmList from './FilmList.svelte';

function film(id: number, posterStatus: PosterStatus, reviewPending = false): MovieListItem {
	return {
		id,
		title: `Film ${id}`,
		year: 2020 + id,
		tmdb_id: 1000 + id,
		genres: id === 1 ? ['Mystery', 'Drama'] : null,
		container: null,
		video_width: null,
		video_height: null,
		resolution: null,
		poster_status: posterStatus,
		review_pending: reviewPending,
		poster_url: posterStatus === 'missing' ? null : `/api/library/movies/${id}/poster`,
		media_file_id: id
	};
}

describe('FilmList', () => {
	it('uses a two-up poster-card table with quiet genre metadata', () => {
		const { container } = render(FilmList, {
			props: {
				items: [film(1, 'approved'), film(2, 'missing'), film(3, 'deployed', true)]
			}
		});

		expect(
			screen.getByRole('table', { name: 'Films with poster previews and genres' })
		).toBeVisible();
		expect(
			screen.getByRole('columnheader', { name: 'Films, posters, and genres' })
		).toBeInTheDocument();
		expect(container.querySelectorAll('.film-row')).toHaveLength(3);
		expect(container.querySelector('.table-body')).toBeInTheDocument();
		expect(screen.getByText('Mystery, Drama')).toBeVisible();
		expect(screen.getAllByText('—')).toHaveLength(2);
	});

	it('keeps status dot-only with purple review precedence', () => {
		render(FilmList, {
			props: {
				items: [film(1, 'approved'), film(2, 'missing'), film(3, 'deployed', true)]
			}
		});

		expect(screen.getByRole('img', { name: 'Deployed: poster present' })).toBeVisible();
		expect(screen.getByRole('img', { name: 'Missing: poster not present' })).toBeVisible();
		expect(
			screen.getByRole('img', { name: 'Needs review: poster selection is waiting in the Pipeline' })
		).toBeVisible();
		expect(screen.queryByText('Deployed')).not.toBeInTheDocument();
		expect(screen.queryByText('Missing')).not.toBeInTheDocument();
		expect(screen.queryByText('Needs review')).not.toBeInTheDocument();
		expect(screen.queryByText('Approved')).not.toBeInTheDocument();
		expect(screen.queryByText('Review')).not.toBeInTheDocument();
	});

	it('renders deployed previews and title-only missing gradients at the enlarged scale', () => {
		const { container } = render(FilmList, {
			props: { items: [film(1, 'deployed'), film(2, 'missing')] }
		});

		expect(screen.getByRole('img', { name: 'Film 1 poster' })).toHaveAttribute(
			'src',
			'/api/library/movies/1/poster'
		);
		const previews = container.querySelectorAll('.poster-preview .poster');
		expect(previews).toHaveLength(2);
		expect(previews[1].querySelector('.title')).toHaveTextContent('Film 2');
		expect(previews[1].querySelector('.year')).not.toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Open Film 2, 2022' })).toHaveAttribute(
			'href',
			'/films/2'
		);
	});
});
