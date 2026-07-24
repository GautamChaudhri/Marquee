import { readFile, readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';

const clientRoot = join(process.cwd(), '.svelte-kit', 'output', 'client');
const immutableRoot = join(clientRoot, '_app', 'immutable');
const manifest = JSON.parse(await readFile(join(clientRoot, '.vite', 'manifest.json'), 'utf8'));
const normalRoots = ['entry', 'nodes'];
const maxNormalChunkBytes = 128 * 1024;

async function javascriptFiles(directory) {
	return (await readdir(directory)).filter((name) => name.endsWith('.js'));
}

const normalChunks = await Promise.all(
	normalRoots.flatMap(async (root) => {
		const directory = join(immutableRoot, root);
		return Promise.all(
			(await javascriptFiles(directory)).map(async (name) => ({
				name: `${root}/${name}`,
				bytes: (await stat(join(directory, name))).size
			}))
		);
	})
).then((groups) => groups.flat());

const oversized = normalChunks.filter((chunk) => chunk.bytes > maxNormalChunkBytes);
if (oversized.length) {
	throw new Error(
		`normal-entry bundle budget exceeded (${maxNormalChunkBytes} bytes): ${oversized
			.map((chunk) => `${chunk.name}=${chunk.bytes}`)
			.join(', ')}`
	);
}

const tasteMap = manifest['src/lib/components/TasteMap.svelte'];
const plotly = manifest['node_modules/plotly.js-dist-min/plotly.min.js'];
if (!tasteMap?.isDynamicEntry || !tasteMap.dynamicImports?.includes(plotly?.src)) {
	throw new Error('TasteMap must remain a lazy entry that dynamically imports Plotly');
}
if (!plotly?.isDynamicEntry || normalChunks.some((chunk) => plotly.file.endsWith(chunk.name))) {
	throw new Error('Plotly must not be emitted into a normal entry or route chunk');
}

console.log(
	`bundle budget passed: ${normalChunks.length} normal chunks, ` +
		`each at or below ${maxNormalChunkBytes} bytes; TasteMap remains lazy`
);
