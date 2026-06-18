<script lang="ts">
	import { page } from '$app/state';
	import Stub from '$lib/components/Stub.svelte';
</script>

<Stub
	title="Film #{page.params.id}"
	subtitle="Detail hub"
	note="Tabbed detail (Poster / Video·HDR / Subtitles / Letterbox / Activity) — upcoming slice."
/>
