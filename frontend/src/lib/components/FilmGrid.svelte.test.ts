import { render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import type { MovieListItem } from '$lib/api/types';
import { libraryPosterSize } from '$lib/theme';
import FilmGrid from './FilmGrid.svelte';

const film: MovieListItem = {
	id: 17,
	title: 'Signal House',
	year: 2026,
	tmdb_id: 1017,
	genres: ['Mystery'],
	container: 'mkv',
	video_width: 1920,
	video_height: 1080,
	resolution: '1080p',
	poster_status: 'missing',
	review_pending: false,
	poster_url: null,
	media_file_id: 17
};

describe('FilmGrid', () => {
	afterEach(() => libraryPosterSize.set('medium'));

	it('keeps the poster clean and places one status dot immediately before the year', () => {
		const { container } = render(FilmGrid, { props: { items: [film] } });

		expect(
			screen.getByRole('button', {
				name: 'Open Signal House, 2026. Missing: poster not present'
			})
		).toBeVisible();
		expect(container.querySelector('.poster .title')).toHaveTextContent('Signal House');
		expect(container.querySelector('.poster .year')).not.toBeInTheDocument();
		expect(container.querySelector('.poster .dot')).not.toBeInTheDocument();
		const cap = container.querySelector('.cap');
		expect(cap?.children[0]).toHaveClass('dot');
		expect(cap?.children[1]).toHaveTextContent('2026');
	});

	it('uses the purple override and the shared grid-size class', () => {
		libraryPosterSize.set('large');
		const { container } = render(FilmGrid, {
			props: { items: [{ ...film, review_pending: true }] }
		});

		expect(container.querySelector('.grid')).toHaveClass('size-large');
		expect(container.querySelector('.cap .dot')).toHaveStyle('--c: var(--review)');
		expect(screen.getByRole('button', { name: /Needs review/ })).toBeVisible();
	});
});
