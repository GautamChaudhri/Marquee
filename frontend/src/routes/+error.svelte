<script lang="ts">
	import { page } from '$app/state';
	import { messageFor } from '$lib/api/errors';

	const message = $derived(
		page.status === 404 ? 'There is no page here.' : messageFor(page.status, page.error)
	);
</script>

<svelte:head>
	<title>{page.status} · Marquee</title>
</svelte:head>

<section class="error">
	<h1>{page.status === 404 ? 'Not found' : 'Something went wrong'}</h1>
	<p>{message}</p>
	<a href="/">Back to the library</a>
</section>

<style>
	.error {
		max-width: 520px;
		margin: 10vh auto;
		padding: 0 16px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	h1 {
		margin: 0;
		font-size: 20px;
	}
	p {
		margin: 0;
		color: var(--muted);
	}
	a {
		color: var(--gold-copy);
	}
</style>
