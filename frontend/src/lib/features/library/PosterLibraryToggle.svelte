<script lang="ts">
	// The poster workspaces switch library by path; /taste switches by query param.
	// Same control either way, so the destinations are props rather than a second copy.
	let {
		active,
		films = '/pipeline/movies',
		television = '/pipeline/tv',
		label = 'Poster library'
	}: {
		active: 'films' | 'television';
		films?: string;
		television?: string;
		label?: string;
	} = $props();
</script>

<nav class="library-switch" aria-label={label}>
	<a
		href={films}
		class:active={active === 'films'}
		aria-current={active === 'films' ? 'page' : undefined}
	>
		Films
	</a>
	<a
		href={television}
		class:active={active === 'television'}
		aria-current={active === 'television' ? 'page' : undefined}
	>
		Television
	</a>
</nav>

<style>
	.library-switch {
		display: inline-flex;
		gap: 4px;
		padding: 4px;
		margin-bottom: 16px;
		border: 1px solid var(--line);
		border-radius: 999px;
		background: var(--panel);
	}
	.library-switch a {
		padding: 7px 12px;
		border-radius: 999px;
		color: var(--muted);
		text-decoration: none;
	}
	.library-switch a:hover {
		color: var(--text);
	}
	.library-switch a.active {
		background: var(--gold);
		color: var(--on-gold);
	}
	.library-switch a:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
</style>
