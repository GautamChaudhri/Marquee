import type { PageLoad } from './$types';
import { listSeries } from '$lib/api/library';
import type { Paginated, SeriesListItem } from '$lib/api/types';

async function loadAllSeries(fetchFn: typeof fetch): Promise<SeriesListItem[]> {
	const pageSize = 200;
	const first = await listSeries(fetchFn, { page: 1, page_size: pageSize });
	const items = [...first.items];
	const totalPages = Math.max(1, Math.ceil(first.total / first.page_size));
	for (let page = 2; page <= totalPages; page += 1) {
		const next = await listSeries(fetchFn, { page, page_size: pageSize });
		items.push(...next.items);
	}
	return items;
}

export const load: PageLoad = async ({ fetch, url }) => {
	const query = {
		q: url.searchParams.get('q') ?? '',
		sort: (url.searchParams.get('sort') as 'title' | 'year') || 'title'
	};
	try {
		const items = await loadAllSeries(fetch);
		return {
			data: {
				total: items.length,
				page: 1,
				page_size: items.length || 1,
				items
			} satisfies Paginated<SeriesListItem>,
			query,
			error: null as string | null
		};
	} catch (e) {
		return {
			data: null as Paginated<SeriesListItem> | null,
			query,
			error: e instanceof Error ? e.message : 'Failed to load series'
		};
	}
};
