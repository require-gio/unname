import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { board, entry, harness, open } from './harness.js';

/**
 * Verify that the XLSX from the corpus loads with correct column widths,
 * row heights, merged cells and formatting; replacements appear in the cells
 * that differ from the exported file (cell comparison with the saved file:
 * `raster.spec.ts`); scrolling 10 000 rows is smooth.
 *
 * This tests the UI view: the grid with frozen panes, codes shown as buttons
 * with a shared menu, cell selection, sheet tabs, side-by-side view, CSV export,
 * and a large sheet.
 *
 * The table model and cells come from the real sidecar script
 * (`scripts/layout-corpus.py --smoke`).
 */

test.describe.configure({ timeout: 90_000 });

const FIXTURES = resolve(import.meta.dirname, 'fixtures/layout');

interface Place {
	id: string;
	value: string;
	type: string;
	category: string;
	after?: string;
	question?: { kind: 'uncertain'; n: number };
}

interface Probe {
	text: string;
	layout: Record<string, unknown> & { sheets: { rows: number }[] };
	cells: unknown[][][];
	places: Place[];
}

const load = (name: string) =>
	JSON.parse(readFileSync(`${FIXTURES}/${name}.json`, 'utf8')) as Probe;
const CUSTOMERS = load('customers');
const CUSTOMERS_CSV = load('customers-csv');

function section(probe: Probe, place: Place): { start: number; end: number } {
	const ab = place.after ? probe.text.indexOf(place.after) : 0;
	const start = probe.text.indexOf(place.value, ab);
	return { start, end: start + place.value.length };
}

function code(place: Place): string {
	return `<<${place.type}_${place.id.slice(2).padEnd(8, '0').slice(0, 8)}>>`;
}

function findings(probe: Probe, job: string) {
	const list = probe.places.map((place) => ({
		id: place.id,
		kind: 'confirmed',
		type: place.type,
		category: place.category,
		score: place.question ? 0.5 : 0.97,
		...section(probe, place),
		text: place.value,
		context: { before: '', match: place.value, after: '' },
		source: 'ner',
		detector: 'test',
		review_required: place.question !== undefined,
		auto_accepted: place.question === undefined,
		question: place.question ?? null,
		assessment: null
	}));
	return {
		job_id: job,
		offset_encoding: 'utf-16',
		text: probe.text,
		confirmed: list,
		suspicious: [],
		findings: list,
		blocks: [{ index: 0, start: 0, end: probe.text.length, kind: 'paragraph', page: null }],
		counts: {
			confirmed: list.length,
			suspicious: 0,
			total: list.length,
			by_entity: {},
			by_category: { names: 2, addresses: 1 },
			questions: list.filter((f) => f.question).length
		}
	};
}

/** Apply small-scale masking: insert codes for all positions. */
function apply(probe: Probe, job: string) {
	const places = probe.places
		.map((place) => ({ place, ...section(probe, place) }))
		.sort((a, b) => a.start - b.start);
	let text = '';
	let cursor = 0;
	const applied = places.map(({ place, start, end }) => {
		text += probe.text.slice(cursor, start);
		const output_start = text.length;
		text += code(place);
		cursor = end;
		return {
			finding_id: place.id,
			kind: 'confirmed',
			entity_type: place.type,
			category: place.category,
			source: 'ner',
			mode: 'token_deterministic',
			reversibility: 'vault',
			original_start: start,
			original_end: end,
			output_start,
			output_end: text.length
		};
	});
	text += probe.text.slice(cursor);
	return {
		offset_encoding: 'utf-16',
		text,
		masking: { method: 'reversible', style: 'codes', categories: [], anonymize: false },
		labels: {
			label: 'reversible',
			contains_surrogates: false,
			anonymous_blockers: ['not_requested'],
			protection_level: 2,
			kept_here_count: 0,
			masked_without_decision: 1
		},
		applied_findings: applied,
		applied_by_category: {},
		residual_risk: { status: 'not_run', heuristic: true, threshold: 2, classes: [], counts: {} },
		facts: {},
		report: {},
		job_id: job,
		entry_id: 'e-t',
		placements: []
	};
}

/** Return cells of a sheet for `?rows=a-b` – matches sidecar slicing. */
function excerpt(
	cells: (sheet: number, by: number, until: number) => unknown[],
	job: string,
	path: string,
	search: string
) {
	const sheet = Number(path.split('/').at(-1));
	const [by, until] = (new URLSearchParams(search).get('rows') ?? '0-499').split('-').map(Number);
	return { job_id: job, sheet: sheet, first: by, last: until, cells: cells(sheet, by, until) };
}

function probeCells(probe: Probe) {
	return (sheet: number, by: number, until: number) =>
		(probe.cells[sheet] ?? []).filter((cell) => {
			const line = cell[0] as number;
			return line >= by && line <= until;
		});
}

async function tablesHarness(
	context: Parameters<typeof harness>[0],
	probe: Probe,
	{
		name = 'kunden.xlsx',
		format = 'xlsx',
		cells = probeCells(probe),
		withoutCells = false
	}: {
		name?: string;
		format?: string;
		cells?: (sheet: number, by: number, until: number) => unknown[];
		withoutCells?: boolean;
	} = {}
) {
	const job = 'j-t';
	const ENTRY = entry({
		id: 'e-t',
		title: 'Tabelle',
		status: 'review',
		files: [
			{
				id: job,
				name,
				format,
				size_bytes: 4096,
				pages: null,
				ocr_used: false,
				status: 'review',
				error_code: null,
				counts: {},
				label: null,
				output_path: null
			}
		]
	});
	return harness(context, {
		entries: () => [ENTRY],
		sidecar: (method, path, _body, search) => {
			if (path === '/entries/e-t' && method === 'GET') return ENTRY;
			if (path === '/entries/e-t/progress') {
				return {
					entry_id: 'e-t',
					status: 'review',
					files: [],
					totals: {},
					done: 1,
					total: 1,
					ready: true
				};
			}
			if (!path.startsWith(`/jobs/${job}/`)) return undefined;
			if (path.endsWith('/findings')) return findings(probe, job);
			if (path.endsWith('/layout')) return { ...probe.layout, job_id: job };
			if (path.includes('/layout/sheets/')) {
				return withoutCells ? undefined : excerpt(cells, job, path, search);
			}
			if (path.endsWith('/apply')) return apply(probe, job);
			return undefined;
		}
	});
}

/** Interactive grid (right one when side-by-side). */
function grid(page: Page): Locator {
	return page.locator(
		'[role="table"][data-column="einzeln"], [role="table"][data-column="result"]'
	);
}

/** Visible view: without hidden peek ("Original: …") in code. */
const VISIBLE = { useInnerText: true };

/** Cell named like Excel: row and column start at 1 (header A,B,C is ARIA row 1). */
function cell(table: Locator, line: number, column: number): Locator {
	return table.locator(
		`[role="row"][aria-rowindex="${line + 1}"] > [role="cell"][aria-colindex="${column}"]`
	);
}

async function gridReady(page: Page): Promise<Locator> {
	const table = grid(page);
	await expect(table).toBeVisible();
	await expect(table.getByRole('button', { name: /Max Mustermann/ }).first()).toBeVisible();
	return table;
}

test('Excel: widths, heights, merged cells and formats; codes in their cells', async ({
	context,
	page
}) => {
	await tablesHarness(context, CUSTOMERS);
	await open(page, '/entry/e-t/review');
	const table = await gridReady(page);

	// Merged A1:F1, 24 pt high, bold, centered.
	const title = cell(table, 1, 1);
	await expect(title).toHaveText('Kundenliste Moebelhaus');
	const kiste = (await title.boundingBox())!;
	expect(Math.abs(kiste.width - (64 * 5 + 173))).toBeLessThanOrEqual(1);
	expect(Math.abs(kiste.height - 32)).toBeLessThanOrEqual(1);
	expect(await title.evaluate((el) => getComputedStyle(el).fontWeight)).toBe('700');
	expect(await title.evaluate((el) => getComputedStyle(el).justifyContent)).toBe('safe center');
	// Header with fill and border from file.
	const header = cell(table, 2, 2);
	expect(await header.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(
		'rgb(242, 242, 242)'
	);
	expect(await header.evaluate((el) => getComputedStyle(el).borderBottomColor)).toBe(
		'rgb(31, 24, 70)'
	);
	// Column B 24 characters wide, G hidden.
	expect(Math.abs((await header.boundingBox())!.width - 173)).toBeLessThanOrEqual(1);
	await expect(table.getByRole('columnheader', { name: 'F', exact: true })).toBeVisible();
	await expect(table.getByRole('columnheader', { name: 'G', exact: true })).toHaveCount(0);
	await expect(table.getByText('intern')).toHaveCount(0);

	// Number formats like Excel, right-aligned; date.
	const aroundSentence = cell(table, 3, 4);
	await expect(aroundSentence).toHaveText('1.234,50 €');
	expect(await aroundSentence.evaluate((el) => getComputedStyle(el).justifyContent)).toBe(
		'safe flex-end'
	);
	await expect(cell(table, 3, 5)).toHaveText('12,5%');
	await expect(cell(table, 3, 6)).toHaveText('01.03.2024');

	// Codes in their cells; open question remains readable with number.
	await expect(cell(table, 3, 2)).toHaveText(code(CUSTOMERS.places[0]), VISIBLE);
	await expect(cell(table, 5, 3)).toHaveText(code(CUSTOMERS.places[2]), VISIBLE);
	await expect(cell(table, 4, 2)).toContainText('Erika Musterfrau');
	await expect(cell(table, 4, 2).locator('sup')).toHaveText('1');
	await expect(table).not.toContainText('Max Mustermann', VISIBLE);

	// Conditional formatting: a hint, not a silent deviation.
	await expect(page.getByRole('note')).toContainText('bedingte Formatierung');
	await board(page, 'layout-table');

	// Space key: original, without codes.
	await page.locator('body').click({ position: { x: 5, y: 400 } });
	await page.keyboard.down('Space');
	await expect(cell(table, 3, 2)).toHaveText('Max Mustermann');
	await page.keyboard.up('Space');
	await expect(cell(table, 3, 2)).toHaveText(code(CUSTOMERS.places[0]), VISIBLE);
});

test('Excel: keyboard code menu, focus back, Alt+N, select in cell', async ({ context, page }) => {
	const { capture } = await tablesHarness(context, CUSTOMERS);
	await open(page, '/entry/e-t/review');
	const table = await gridReady(page);
	const name = table.getByRole('button', { name: /Max Mustermann/ }).first();

	await name.focus();
	await page.keyboard.press('Enter');
	const menu = page.getByRole('menu');
	await expect(menu).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(menu).toBeHidden();
	await expect(name).toBeFocused();

	await name.click();
	await page.getByRole('menuitem', { name: 'Nur hier behalten' }).click();
	await expect
		.poll(() =>
			capture.requests.some(
				(request) =>
					request.path.endsWith('/apply') &&
					JSON.stringify(request.body).includes('"finding_id":"f_max","action":"reject"')
			)
		)
		.toBe(true);

	await page.keyboard.press('Alt+KeyN');
	await expect(page.locator('[data-question="f_erika"] button').first()).toBeFocused();

	// Double-click a cell selects a word and opens the menu.
	await cell(table, 3, 3).getByText('Wien').dblclick();
	const dialog = page.getByRole('dialog', { name: '»Wien« auch maskieren' });
	await expect(dialog).toBeVisible();
	await expect(cell(table, 3, 3).locator('[data-seg]')).toHaveText('Wien');
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
});

test('Excel: sheet tab, merged cell with wrap, formula', async ({ context, page }) => {
	await tablesHarness(context, CUSTOMERS);
	await open(page, '/entry/e-t/review');
	await gridReady(page);

	const reiter = page.getByRole('tablist', { name: 'Blätter' });
	await expect(reiter.getByRole('tab')).toHaveText(['Kunden', 'Notizen']);
	await reiter.getByRole('tab', { name: 'Notizen' }).click();
	await expect(reiter.getByRole('tab', { name: 'Notizen' })).toHaveAttribute(
		'aria-selected',
		'true'
	);
	const table = grid(page);
	const notiz = cell(table, 1, 1);
	await expect(notiz).toContainText(`Rueckruf bei ${code(CUSTOMERS.places[3])}`, VISIBLE);
	// Merged across A1:C2 with wrap.
	const kiste = (await notiz.boundingBox())!;
	expect(Math.abs(kiste.width - 64 * 3)).toBeLessThanOrEqual(1);
	expect(Math.abs(kiste.height - 40)).toBeLessThanOrEqual(1);
	expect(await notiz.evaluate((el) => getComputedStyle(el).whiteSpace)).toBe('pre-wrap');
	await expect(cell(table, 4, 1)).toHaveText('="Kunde: "&Kunden!B3');
	await expect(page.getByRole('note')).toHaveCount(0);
});

test('Excel: side-by-side view shows original and result cell by cell', async ({
	context,
	page
}) => {
	await tablesHarness(context, CUSTOMERS);
	await open(page, '/entry/e-t/review');
	await gridReady(page);
	await page.getByRole('radio', { name: 'Nebeneinander' }).click();

	const links = page.locator('[role="table"][data-column="original"]');
	const right = page.locator('[role="table"][data-column="result"]');
	await expect(cell(links, 3, 2)).toHaveText('Max Mustermann');
	await expect(links.locator('button')).toHaveCount(0);
	await expect(cell(right, 3, 2)).toHaveText(code(CUSTOMERS.places[0]), VISIBLE);
	const [a, b] = await Promise.all([links.boundingBox(), right.boundingBox()]);
	expect(Math.abs(a!.height - b!.height)).toBeLessThan(2);
	await board(page, 'layout-table-side-by-side');
});

test('CSV: same grid without styles, fields without quotes', async ({ context, page }) => {
	await tablesHarness(context, CUSTOMERS_CSV, { name: 'kunden.csv', format: 'csv' });
	await open(page, '/entry/e-t/review');
	const table = await gridReady(page);
	await expect(cell(table, 1, 2)).toHaveText('Name');
	await expect(cell(table, 2, 2)).toHaveText(code(CUSTOMERS_CSV.places[0]), VISIBLE);
	await expect(cell(table, 2, 4)).toHaveText('1234.5');
	expect(await cell(table, 2, 4).evaluate((el) => getComputedStyle(el).justifyContent)).toBe(
		'safe flex-end'
	);
	await expect(page.getByRole('tablist', { name: 'Blätter' })).toHaveCount(0);
});

test('Table: without cells view falls back to text and reports it', async ({ context, page }) => {
	await tablesHarness(context, CUSTOMERS, { withoutCells: true });
	await open(page, '/entry/e-t/review');
	await expect(page.getByRole('note')).toContainText('als Text');
	await expect(page.locator('article[data-column="einzeln"]')).toContainText('Kundenliste');
	await expect(page.locator('[role="table"]')).toHaveCount(0);
});

/* -- 10 000 rows -------------------------------------------------------- */

const LARGE_LINES = 10_000;
const LARGE_COLUMNS = 8;

/** Large sheet: header frozen, eight cells per row, a question in row 9000. */
function largeProbe(): {
	probe: Probe;
	cells: (b: number, by: number, until: number) => unknown[];
} {
	const texts: string[] = [];
	const sections = new Map<string, [number, number]>();
	let text = 'Gross';
	for (let z = 0; z < LARGE_LINES; z++) {
		for (let s = 0; s < LARGE_COLUMNS; s++) {
			text += s === 0 ? '\n' : '\t';
			const value = z === 0 ? `Kopf ${s}` : z === 9000 && s === 1 ? 'Hanna Frage' : `Z${z}S${s}`;
			sections.set(`${z}:${s}`, [text.length, text.length + value.length]);
			text += value;
			texts.push(value);
		}
	}
	const places: Place[] = [
		{
			id: 'f_hanna',
			value: 'Hanna Frage',
			type: 'PERSON',
			category: 'names',
			question: { kind: 'uncertain', n: 1 }
		}
	];
	const layout = {
		kind: 'grid',
		offset_encoding: 'utf-16',
		format: 'xlsx',
		sheets: [
			{
				index: 0,
				name: 'Gross',
				name_range: [0, 5],
				hidden: false,
				conditional: false,
				rows: LARGE_LINES,
				cols: LARGE_COLUMNS,
				frozen: [1, 1],
				col_widths: Array.from({ length: LARGE_COLUMNS }, () => 96),
				default_row_height: 20,
				row_heights: {},
				hidden_rows: [],
				hidden_cols: [],
				merges: [],
				cells: '/jobs/j-t/layout/sheets/0'
			}
		],
		styles: [{}, { bold: true, fill: '#F2F2F2' }]
	};
	const cells = (_sheet: number, by: number, until: number) => {
		const list: unknown[] = [];
		for (let z = by; z <= Math.min(until, LARGE_LINES - 1); z++) {
			for (let s = 0; s < LARGE_COLUMNS; s++) {
				const [a, e] = sections.get(`${z}:${s}`)!;
				list.push([z, s, texts[z * LARGE_COLUMNS + s], z === 0 ? 1 : 0, a, e]);
			}
		}
		return list;
	};
	return {
		probe: { text, layout, cells: [], places } as unknown as Probe,
		cells
	};
}

test('Table: smooth scrolling of 10,000 rows, header and first column stay fixed', async ({
	context,
	page
}) => {
	const { probe, cells } = largeProbe();
	const { capture } = await tablesHarness(context, probe, { cells });
	await open(page, '/entry/e-t/review');
	const table = grid(page);
	await expect(cell(table, 2, 2)).toHaveText('Z1S1');

	// Only cells visible in the viewport are in the DOM.
	const cellsInDom = () => table.locator('[role="cell"]').count();
	expect(await cellsInDom()).toBeLessThan(800);

	// Scroll in mouse-wheel steps; measure each frame.
	const frames = await table.evaluate(async (element) => {
		const duration: number[] = [];
		let last = performance.now();
		const end = element.scrollHeight - element.clientHeight;
		for (let y = 0; y <= end; y += Math.ceil(end / 120)) {
			element.scrollTop = y;
			await new Promise(requestAnimationFrame);
			const now = performance.now();
			duration.push(now - last);
			last = now;
		}
		element.scrollTop = end;
		await new Promise(requestAnimationFrame);
		return duration;
	});
	const slow = frames.filter((duration) => duration > 50).length;
	expect(slow, `Frames über 50 ms: ${slow} von ${frames.length}`).toBeLessThanOrEqual(3);

	await expect(cell(table, LARGE_LINES, 2)).toHaveText(`Z${LARGE_LINES - 1}S1`);
	expect(await cellsInDom()).toBeLessThan(800);
	// Header stays top and column A stays left.
	await expect(cell(table, 1, 3)).toBeInViewport();
	await expect(cell(table, LARGE_LINES, 1)).toBeInViewport();
	// Each block fetched at most once, never the whole sheet at once.
	const fetches = capture.requests.filter((a) => a.path.includes('/layout/sheets/'));
	expect(fetches.length).toBeLessThanOrEqual(LARGE_LINES / 500 + 1);

	// Alt+N brings question in row 9001 into view even if its block isn’t loaded yet.
	await table.evaluate((element) => (element.scrollTop = 0));
	await page.locator('body').click({ position: { x: 5, y: 400 } });
	await page.keyboard.press('Alt+KeyN');
	await expect(cell(table, 9001, 2)).toBeInViewport();
	await expect(cell(table, 9001, 2).locator('sup')).toHaveText('1');
});
