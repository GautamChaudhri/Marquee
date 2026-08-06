<script lang="ts">
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	let { data } = $props();

	const movieCoverage = $derived(
		data.movies?.total_movies
			? Math.round((data.movies.movies_with_poster / data.movies.total_movies) * 100)
			: 0
	);
	const showCoverage = $derived(
		data.television?.shows_total
			? Math.round((data.television.shows_fully_covered / data.television.shows_total) * 100)
			: 0
	);
</script>

<SectionHeader title="Dashboard" subtitle="Library health & system status" />

<div class="grid">
	<StatCard
		label="Film coverage"
		value={data.movies?.movies_with_poster ?? 0}
		sub={`${data.movies?.total_movies ?? 0} total · ${movieCoverage}%`}
		bar={movieCoverage}
		tone={movieCoverage >= 90 ? 'good' : movieCoverage >= 70 ? 'warn' : 'bad'}
	/>
	<StatCard
		label="Television coverage"
		value={data.television?.shows_fully_covered ?? 0}
		sub={`${data.television?.shows_total ?? 0} shows · ${showCoverage}%`}
		bar={showCoverage}
		tone={showCoverage >= 90 ? 'good' : showCoverage >= 70 ? 'warn' : 'bad'}
	/>
	<StatCard
		label="Season posters"
		value={data.television?.seasons_with_poster ?? 0}
		sub={`${data.television?.seasons_total ?? 0} total`}
		tone="info"
	/>
	<StatCard
		label="Review queue"
		value={(data.movies?.movies_in_review ?? 0) + (data.television?.shows_in_review ?? 0)}
		sub="films + television"
		tone="gold"
	/>
</div>

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 12px;
	}
</style>
