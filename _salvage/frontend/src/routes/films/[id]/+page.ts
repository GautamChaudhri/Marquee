import { getMovie } from '$lib/api/library';
import { getMovieTextProfile, listTextProfiles } from '$lib/api/text-profiles';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, params, url }) => {
	const id = Number(params.id);
	if (!id)
		return {
			movie: null,
			tab: 'poster',
			textProfiles: null,
			movieTextProfile: null,
			error: 'Invalid movie id'
		};
	try {
		const movie = await getMovie(fetch, id);
		const tab = url.searchParams.get('tab') ?? 'poster';
		let textProfiles = null;
		let movieTextProfile = null;
		try {
			[textProfiles, movieTextProfile] = await Promise.all([
				listTextProfiles(fetch),
				getMovieTextProfile(fetch, id)
			]);
		} catch {
			/* keep the movie page usable without the override control */
		}
		return { movie, tab, textProfiles, movieTextProfile, error: null };
	} catch (e) {
		return {
			movie: null,
			tab: 'poster',
			textProfiles: null,
			movieTextProfile: null,
			error: e instanceof Error ? e.message : 'Failed to load movie'
		};
	}
};
