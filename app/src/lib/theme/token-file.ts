/**
 * Read tokens from `tokens.css` for tests and check scripts.
 *
 * The test must examine the same file the browser loads – keeping values in a
 * second place would cause them to diverge. We only support the forms used in
 * `tokens.css`: blocks for `:root` and `.dark` with `--name: value;` and
 * references `var(--name)`.
 */

export type Mode = 'light' | 'dark';

export type TokenTable = ReadonlyMap<string, string>;

function blocks(css: string, selektor: ':root' | '.dark'): string[] {
	const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
	const pattern = new RegExp(`(^|[}\\s])${selektor.replace('.', '\\.')}\\s*\\{([^}]*)\\}`, 'g');
	return [...withoutComments.matchAll(pattern)].map((match) => match[2]);
}

function entries(block: string): [string, string][] {
	return [...block.matchAll(/--([a-z0-9-]+)\s*:\s*([^;]+);/gi)].map((match) => [
		match[1],
		match[2].trim()
	]);
}

/** All tokens per mode; dark mode inherits any it doesn't override. */
export function readTokens(css: string): Record<Mode, TokenTable> {
	const light = new Map<string, string>();
	for (const block of blocks(css, ':root')) {
		for (const [name, value] of entries(block)) light.set(name, value);
	}
	const dark = new Map(light);
	for (const block of blocks(css, '.dark')) {
		for (const [name, value] of entries(block)) dark.set(name, value);
	}
	return { light, dark };
}

/** Resolve `var(--name)` recursively. Throws on missing token or cycle. */
export function tokenValue(table: TokenTable, name: string, besucht: string[] = []): string {
	if (besucht.includes(name)) {
		throw new Error(`Zyklus in tokens.css: ${[...besucht, name].join(' → ')}`);
	}
	const value = table.get(name);
	if (value === undefined) throw new Error(`--${name} fehlt in tokens.css`);
	const reference = /^var\(--([a-z0-9-]+)\)$/i.exec(value);
	return reference ? tokenValue(table, reference[1], [...besucht, name]) : value;
}
