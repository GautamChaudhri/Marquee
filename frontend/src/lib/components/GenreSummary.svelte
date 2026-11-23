<script lang="ts">
	let { genres }: { genres: string[] | null } = $props();

	const genreText = $derived(genres?.length ? genres.join(', ') : '—');
</script>

<div class="genre-block">
	<span class="genre-label">Genres</span>
	<span class="genres" title={genres?.length ? genreText : undefined}>{genreText}</span>
</div>

<style>
	.genre-block {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 7px;
		padding-top: 2px;
	}
	.genre-label {
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.genres {
		display: -webkit-box;
		overflow: hidden;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 5;
		line-clamp: 5;
		font-size: 12.5px;
		line-height: 1.45;
		color: var(--muted);
	}
</style>
