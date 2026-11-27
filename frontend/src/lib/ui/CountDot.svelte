<script lang="ts">
	/** A count in a yellow dot at the top right of a poster. Its container must be positioned.
	 *  `word` says what is counted, for a tooltip and for a screen reader. */
	let { count, word, testid }: { count: number; word: string; testid?: string } = $props();
</script>

<span class="count" title="{count} {word}" data-testid={testid}>
	<span>{count}</span> <span class="sr-only">{word}</span>
</span>

<style>
	/* A round dot (a short pill past two digits) with a dark ring, so it reads over light and
	   dark artwork alike. */
	.count {
		position: absolute;
		top: 6px;
		right: 6px;
		min-width: 22px;
		height: 22px;
		padding: 0 5px;
		display: grid;
		place-items: center;
		border-radius: var(--radius-pill);
		background: var(--warn);
		color: var(--on-warn);
		font-size: 11px;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		line-height: 1;
		box-shadow:
			0 0 0 1.5px var(--poster-shade),
			0 1px 4px var(--shadow);
	}
</style>
