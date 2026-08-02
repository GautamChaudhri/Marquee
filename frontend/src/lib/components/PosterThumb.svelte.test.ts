import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import PosterThumb from './PosterThumb.svelte';

describe('PosterThumb', () => {
	it('places the default gradient title and year at the bottom-left', () => {
		const { container } = render(PosterThumb, {
			props: { title: 'Signal House', year: 2026 }
		});

		const poster = container.querySelector('.poster');
		expect(poster).toHaveClass('bottom-left-title');
		expect(poster).not.toHaveClass('plain-fallback');
		expect(screen.getByText('Signal House')).toBeVisible();
		expect(screen.getByText('2026')).toBeVisible();
	});

	it('centers season fallback copy', () => {
		const { container } = render(PosterThumb, {
			props: { title: 'S00', gradientKey: 'Signal House', fallbackPlacement: 'center' }
		});

		expect(container.querySelector('.poster')).toHaveClass('centered-title');
		expect(screen.getByText('S00')).toBeVisible();
	});

	it('can keep a small gradient thumbnail free of fallback text', () => {
		const { container } = render(PosterThumb, {
			props: { title: 'Signal House', year: 2026, fallbackPlacement: 'hidden' }
		});

		expect(container.querySelector('.poster')).not.toHaveClass('plain-fallback');
		expect(container.querySelector('.meta')).not.toBeInTheDocument();
		expect(screen.queryByText('Signal House')).not.toBeInTheDocument();
		expect(screen.queryByText('2026')).not.toBeInTheDocument();
	});

	it('uses distinct deployed-image alt text and falls back gracefully after an image error', async () => {
		const { container } = render(PosterThumb, {
			props: {
				title: 'S02',
				imageAlt: 'Signal House S02 poster',
				posterUrl: '/api/library/seasons/202/poster',
				fallbackPlacement: 'center'
			}
		});

		const image = screen.getByAltText('Signal House S02 poster');
		expect(image).toHaveAttribute('src', '/api/library/seasons/202/poster');
		await fireEvent.error(image);
		expect(container.querySelector('img')).not.toBeInTheDocument();
		expect(screen.getByText('S02')).toBeVisible();
	});
});
