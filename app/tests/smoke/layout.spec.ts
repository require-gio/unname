import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import type { PlacementOut } from '../../src/lib/sidecar/api.gen.js';
import { board, entry, harness, open, Roh } from './harness.js';

/**
 * Layout plan: milestone M shows a delivery-note replica, rotated page and scan
 * as page images; each detected finding is highlighted on its word.
 *
 * Milestone N: the overpaint from the placement plan is shown on the page
 * (mouse cursor and space bar reveal the original). Both pages are displayed
 * side-by-side, highlighting is word-wise in the image – keyboard access remains.
 *
 * Milestone O (A-26): the overpaint view matches the control image within the
 * placement areas, i.e. the page after real editing, with the same behaviour as
 * when saving.
 *
 * Layout map, page image and control image come from the real sidecar script
 * (`scripts/layout-corpus.py --smoke`). The sidecar (`test_layout_map.py`) checks
 * that the word boxes lie on the image pixels; here we verify that the view draws
 * them to within ≤ 1 px accuracy.
 */

const FIXTURES = resolve(import.meta.dirname, 'fixtures/layout');

interface Place {
	id: string;
	value: string;
	type: string;
	category: string;
	question?: { kind: 'uncertain'; n: number };
}

interface Fixture {
	text: string;
	layout: {
		kind: 'pages';
		pages: { index: number; width: number; height: number }[];
		words: [number, number, number, number, number, number, number, number][];
	};
	places: Place[];
	placements: PlacementOut[];
}

const FILES = [
	{ id: 'j-1', name: 'lieferschein.pdf', master: 'delivery-note' },
	{ id: 'j-2', name: 'lieferschein-rot90.pdf', master: 'delivery-note-rot90' },
	{ id: 'j-3', name: 'scan.pdf', master: 'scan' }
].map((file) => ({
	...file,
	fixture: JSON.parse(readFileSync(`${FIXTURES}/${file.master}.json`, 'utf8')) as Fixture,
	image: readFileSync(`${FIXTURES}/${file.master}.png`),
	/** Control image (`/pages/0/result`, level 1): the page after real editing. */
	kontrollbild: readFileSync(`${FIXTURES}/${file.master}-result.png`)
}));

type File = (typeof FILES)[number];

/** UTF-16 range of a finding; the texts are pure BMP. */
function section(file: File, place: Place): { start: number; end: number } {
	const start = file.fixture.text.indexOf(place.value);
	return { start, end: start + place.value.length };
}

function findings(file: File) {
	const list = file.fixture.places.map((place) => {
		const { start, end } = section(file, place);
		return {
			id: place.id,
			kind: 'confirmed',
			type: place.type,
			category: place.category,
			score: place.question ? 0.5 : 0.97,
			start,
			end,
			text: place.value,
			context: { before: '', match: place.value, after: '' },
			source: 'ner',
			detector: 'test',
			review_required: place.question !== undefined,
			auto_accepted: place.question === undefined,
			question: place.question ?? null,
			assessment: null
		};
	});
	return {
		job_id: file.id,
		offset_encoding: 'utf-16',
		text: file.fixture.text,
		confirmed: list,
		suspicious: [],
		findings: list,
		blocks: [{ index: 0, start: 0, end: file.fixture.text.length, kind: 'paragraph', page: null }],
		counts: {
			confirmed: list.length,
			suspicious: 0,
			total: list.length,
			by_entity: {},
			by_category: { names: 1, addresses: 1, contact: 2 },
			questions: 1
		}
	};
}

/** Small-scale apply: generate codes for all findings, e.g. “Reversibel” with default categories. */
function apply(file: File) {
	const places = file.fixture.places
		.map((place) => ({ place, ...section(file, place) }))
		.sort((a, b) => a.start - b.start);
	let text = '';
	let cursor = 0;
	const applied = places.map(({ place, start, end }) => {
		text += file.fixture.text.slice(cursor, start);
		const output_start = text.length;
		text += `<<${place.type}_${place.id.slice(2).padEnd(8, '0').slice(0, 8)}>>`;
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
	text += file.fixture.text.slice(cursor);
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
		job_id: file.id,
		entry_id: 'e-1',
		placements: file.fixture.placements
	};
}

const ENTRY = entry({
	title: 'Lieferschein + 2 weitere',
	status: 'review',
	files: FILES.map((file) => ({
		id: file.id,
		name: file.name,
		format: 'pdf',
		size_bytes: file.image.length,
		pages: 1,
		ocr_used: file.master === 'scan',
		status: 'review',
		error_code: null,
		counts: {},
		label: null,
		output_path: null
	}))
});

/** `seiten`: how often the first file repeats its page (memory for long PDFs). */
async function layoutHarness(context: Parameters<typeof harness>[0], docPages = 1) {
	return harness(context, {
		entries: () => [ENTRY],
		sidecar: (method, path) => {
			if (path === '/entries/e-1' && method === 'GET') return ENTRY;
			if (path === '/entries/e-1/progress') {
				return {
					entry_id: 'e-1',
					status: 'review',
					files: [],
					totals: {},
					done: 3,
					total: 3,
					ready: true
				};
			}
			const file = FILES.find((kandidat) => path.startsWith(`/jobs/${kandidat.id}/`));
			if (!file) return undefined;
			if (path.endsWith('/findings')) return findings(file);
			if (path.endsWith('/layout')) {
				const [docPage] = file.fixture.layout.pages;
				return {
					job_id: file.id,
					...file.fixture.layout,
					pages: Array.from({ length: docPages }, (_, index) => ({ ...docPage, index }))
				};
			}
			// page image: fixture is at level 1; Playwright renders with
			// devicePixelRatio 1 and 100%, so the view requests level 1.
			if (/\/pages\/\d+$/.test(path)) return new Roh(file.image, 'image/png');
			if (path.endsWith('/apply')) return apply(file);
			return undefined;
		}
	});
}

/** Wait until the page canvas has been drawn. */
async function pageLoaded(page: Page): Promise<void> {
	await expect
		.poll(() =>
			page
				.locator('[data-page] canvas')
				.first()
				.evaluate((c: HTMLCanvasElement) => c.width)
		)
		.toBeGreaterThan(0);
}

/**
 * Shows the page of `file`. A tab switch is a navigation, so the previous file
 * stays on screen for a moment; on a slow machine a measurement would read it.
 */
async function showFile(page: Page, file: File, switchTab: boolean): Promise<void> {
	const tab = page.getByRole('tab', { name: new RegExp(file.name) });
	if (switchTab) await tab.click();
	await expect(tab).toHaveAttribute('aria-selected', 'true');
	const { width, height } = file.fixture.layout.pages[0];
	await expect(page.locator('[data-page] svg').first()).toHaveAttribute(
		'viewBox',
		`0 0 ${width} ${height}`
	);
	await pageLoaded(page);
}

/**
 * Expected rectangle per line of a finding in screen pixels:
 * word boxes of the layout map × rendered width ÷ page width, plus 1 px margin.
 */
async function deviations(page: Page, file: File): Promise<{ id: string; delta: number }[]> {
	return page.evaluate(
		({ words, docPage, places }) => {
			const svg = document.querySelector('[data-page] svg')!;
			const frame = svg.getBoundingClientRect();
			const k = frame.width / docPage.width;
			return places.flatMap((place) => {
				const lines = new Map<number, number[]>();
				for (const w of words) {
					if (w[5] >= place.end || w[6] <= place.start) continue;
					const box = lines.get(w[7]);
					lines.set(
						w[7],
						box
							? [
									Math.min(box[0], w[1]),
									Math.min(box[1], w[2]),
									Math.max(box[2], w[3]),
									Math.max(box[3], w[4])
								]
							: [w[1], w[2], w[3], w[4]]
					);
				}
				const ist = [
					...document.querySelectorAll(`[data-page] g[data-id="${place.id}"] [data-area]`)
				].map((element) => element.getBoundingClientRect());
				const soll = [...lines.values()];
				if (ist.length !== soll.length) return [{ id: place.id, delta: Infinity }];
				return soll.map(([x0, y0, x1, y1], i) => ({
					id: place.id,
					delta: Math.max(
						Math.abs(frame.left + x0 * k - 1 - ist[i].left),
						Math.abs(frame.top + y0 * k - 1 - ist[i].top),
						Math.abs(frame.left + x1 * k + 1 - ist[i].right),
						Math.abs(frame.top + y1 * k + 1 - ist[i].bottom)
					)
				}));
			});
		},
		{
			words: file.fixture.layout.words,
			docPage: file.fixture.layout.pages[0],
			places: file.fixture.places.map((place) => ({ id: place.id, ...section(file, place) }))
		}
	);
}

test('Fixed pages: page image with markings on its words (≤ 1 px)', async ({ context, page }) => {
	await layoutHarness(context);
	await open(page, '/entry/e-1/review');

	for (const [index, file] of FILES.entries()) {
		await showFile(page, file, index > 0);
		await expect(page.getByRole('img', { name: 'Seite 1 von 1' })).toBeVisible();
		const list = await deviations(page, file);
		expect(list.length, file.name).toBeGreaterThanOrEqual(file.fixture.places.length);
		for (const { id, delta } of list) expect(delta, `${file.name} ${id}`).toBeLessThanOrEqual(1);
	}
});

test('Page | Text: toggle, code menu via keyboard, reference image', async ({ context, page }) => {
	const { capture } = await layoutHarness(context);
	await open(page, '/entry/e-1/review');
	await pageLoaded(page);

	// The place is a button in reading order; Enter opens the menu.
	const name = page.getByRole('button', { name: /MUSTERMANN/ });
	await name.focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('menu')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('menu')).toBeHidden();
	await expect(name).toBeFocused();

	// The open question carries its number, the invisible control text a frame.
	await expect(page.getByRole('group', { name: /Frage 1/ })).toBeVisible();
	await expect(page.getByRole('img', { name: 'Unsichtbarer Text' })).toHaveCount(1);

	// No error: neither during calculation nor when loading the page image.
	await expect(page.getByRole('alert')).toHaveCount(0);
	await board(page, 'layout-page');

	const appearance = page.getByRole('radiogroup', { name: 'Darstellung' });
	await appearance.getByRole('radio', { name: 'Text', exact: true }).click();
	await expect(page.locator('[data-page]')).toHaveCount(0);
	await expect(page.locator('article[data-column="einzeln"]')).toContainText('MUSTERGASSE');
	// The entry remembers its view; new entries keep the device default.
	await expect
		.poll(() =>
			capture.requests.some(
				(request) =>
					request.method === 'PUT' &&
					request.path === '/entries/e-1/view' &&
					(request.body as { representation?: string })?.representation === 'text'
			)
		)
		.toBe(true);
	expect(await page.evaluate(() => localStorage.getItem('unname.appearance'))).toBeNull();

	await page.reload({ waitUntil: 'networkidle' });
	await expect(page.locator('article[data-column="einzeln"]')).toBeVisible();
	await appearance.getByRole('radio', { name: 'Seite', exact: true }).click();
	await pageLoaded(page);
});

test('50 pages: pixels only hold pages near the window', async ({ context, page }) => {
	const { capture } = await layoutHarness(context, 50);
	await open(page, '/entry/e-1/review');
	await pageLoaded(page);
	await expect(page.locator('[data-page]')).toHaveCount(50);

	const gezeichnet = () =>
		page
			.locator('[data-page] canvas')
			.evaluateAll((all) => all.filter((canvas) => (canvas as HTMLCanvasElement).width > 0).length);
	const images = () => capture.requests.filter((a) => /\/pages\/\d+$/.test(a.path)).length;

	// Initially only the first pages are drawn, not all 50.
	await expect.poll(gezeichnet).toBeGreaterThan(0);
	expect(images()).toBeLessThan(10);

	// Scroll to the end: upper pages release their pixels.
	await page
		.getByRole('tabpanel')
		.evaluate((scroller) => (scroller.scrollTop = scroller.scrollHeight));
	await expect
		.poll(() => page.locator('[data-page="49"] canvas').evaluate((c: HTMLCanvasElement) => c.width))
		.toBeGreaterThan(0);
	await expect.poll(gezeichnet).toBeLessThanOrEqual(6);
	expect(
		await page.locator('[data-page="0"] canvas').evaluate((c: HTMLCanvasElement) => c.width)
	).toBe(0);
	expect(images()).toBeLessThan(20);
});

/* --  ---------------------------------------------------------------- */

/** Middle of a word on the page in screen coordinates (n-th occurrence). */
async function wordMiddle(
	page: Page,
	file: File,
	word: string,
	column = '[data-page]'
): Promise<{ x: number; y: number }> {
	const box = file.fixture.layout.words.find((w) => file.fixture.text.slice(w[5], w[6]) === word);
	if (!box) throw new Error(`Wort fehlt: ${word}`);
	return page.evaluate(
		({ box, width, column }) => {
			const frame = document.querySelector(`${column} svg`)!.getBoundingClientRect();
			const k = frame.width / width;
			return {
				x: frame.left + ((box[1] + box[3]) / 2) * k,
				y: frame.top + ((box[2] + box[4]) / 2) * k
			};
		},
		{ box, width: file.fixture.layout.pages[0].width, column }
	);
}

test('Result: Overpaint according to placement plan, Peek and Space', async ({ context, page }) => {
	await layoutHarness(context);
	await open(page, '/entry/e-1/review');
	await pageLoaded(page);

	for (const [index, file] of FILES.entries()) {
		await showFile(page, file, index > 0);
		// All positions except the open question are overpainted – area and text from the plan.
		const uebermalt = file.fixture.placements.filter((p) => p.finding_id !== 'f_tel');
		await expect(page.locator('[data-overpaint]')).toHaveCount(uebermalt.length);
		await expect(page.locator('[data-id="f_tel"] [data-overpaint]')).toHaveCount(0);
		for (const plan of uebermalt) {
			const replacement = page.locator(`[data-id="${plan.finding_id}"] [data-replacement]`);
			await expect(replacement).toHaveText(plan.text!.value);
			if (plan.text!.rotate) {
				await expect(replacement).toHaveAttribute(
					'transform',
					new RegExp(`^rotate\\(${plan.text!.rotate} `)
				);
			}
			// The area is within ≤ 1 px of where the plan places it.
			const delta = await page.evaluate(
				({ plan, width }) => {
					const svg = document.querySelector('[data-page] svg')!;
					const frame = svg.getBoundingClientRect();
					const k = frame.width / width;
					const ist = document
						.querySelector(`[data-id="${plan.finding_id}"] [data-overpaint] rect`)!
						.getBoundingClientRect();
					const [x0, y0, x1, y1] = plan.cover[0];
					return Math.max(
						Math.abs(frame.left + x0 * k - ist.left),
						Math.abs(frame.top + y0 * k - ist.top),
						Math.abs(frame.left + x1 * k - ist.right),
						Math.abs(frame.top + y1 * k - ist.bottom)
					);
				},
				{ plan, width: file.fixture.layout.pages[0].width }
			);
			expect(delta, `${file.name} ${plan.finding_id}`).toBeLessThanOrEqual(1);
		}
	}

	await page.getByRole('tab', { name: /^lieferschein\.pdf/ }).click();
	await pageLoaded(page);
	const overpaint = page.locator('[data-id="f_name"] [data-overpaint]');

	// Hovering shows original, overpaint disappears.
	await page.getByRole('button', { name: /MUSTERMANN/ }).hover();
	await expect(overpaint).toHaveCSS('opacity', '0');
	await page.mouse.move(5, 5);
	await expect(overpaint).toHaveCSS('opacity', '1');

	// Hold Space to show original (no overpaint) while pressed.
	await page.getByRole('tabpanel').focus();
	await page.keyboard.down('Space');
	await expect(page.locator('[data-overpaint]')).toHaveCount(0);
	await page.keyboard.up('Space');
	await expect(page.locator('[data-overpaint]')).toHaveCount(3);

	// Invisible text carries a dashed frame on its page instead of a note.
	await expect(page.getByRole('img', { name: 'Unsichtbarer Text' })).toHaveCount(1);
	await expect(page.getByRole('alert')).toHaveCount(0);
});

/**
 * Compare screen area with a reference image (control or original). Both are blurred with a 3×3 kernel
 * to ignore edge smoothing, subpixel shifts and minor font differences, but not missing or shifted text.
 * A pixel is considered different if gray values differ by more than 48 levels.
 * The indigo border of the view (not saved) is excluded with a 2 px margin.
 */
async function deviation(
	page: Page,
	view: Buffer,
	pageImage: Buffer,
	areas: number[][],
	position: { dx: number; dy: number; k: number; width: number; height: number }
): Promise<number[]> {
	return page.evaluate(
		async ({ view, pageImage, areas, position }) => {
			const load = (b64: string) =>
				createImageBitmap(
					new Blob([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], { type: 'image/png' })
				);
			const [a, b] = await Promise.all([load(view), load(pageImage)]);
			const { width: w, height: h } = a;
			const ctx = new OffscreenCanvas(w, h).getContext('2d', { willReadFrequently: true })!;
			const weich = () => {
				const { data } = ctx.getImageData(0, 0, w, h);
				const grau = new Float32Array(w * h);
				for (let i = 0; i < grau.length; i++) {
					grau[i] = 0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2];
				}
				const from = new Float32Array(w * h);
				for (let y = 1; y < h - 1; y++) {
					for (let x = 1; x < w - 1; x++) {
						let sum = 0;
						for (let oy = -1; oy <= 1; oy++) {
							for (let ox = -1; ox <= 1; ox++) sum += grau[(y + oy) * w + x + ox];
						}
						from[y * w + x] = sum / 9;
					}
				}
				return from;
			};
			ctx.drawImage(a, 0, 0);
			const screen = weich();
			ctx.fillStyle = '#fff';
			ctx.fillRect(0, 0, w, h);
			ctx.drawImage(
				b,
				position.dx,
				position.dy,
				position.width * position.k,
				position.height * position.k
			);
			const file = weich();
			return areas.map(([x0, y0, x1, y1]) => {
				let total = 0;
				let abweichend = 0;
				const links = Math.ceil(x0 * position.k + position.dx) + 2;
				const top = Math.ceil(y0 * position.k + position.dy) + 2;
				const right = Math.floor(x1 * position.k + position.dx) - 2;
				const bottom = Math.floor(y1 * position.k + position.dy) - 2;
				for (let y = top; y < bottom; y++) {
					for (let x = links; x < right; x++) {
						total++;
						if (Math.abs(screen[y * w + x] - file[y * w + x]) > 48) abweichend++;
					}
				}
				return abweichend / total;
			});
		},
		{
			view: view.toString('base64'),
			pageImage: pageImage.toString('base64'),
			areas,
			position
		}
	);
}

test('View = File: Overpaint matches control image (A-26)', async ({ context, page }) => {
	await layoutHarness(context);
	await open(page, '/entry/e-1/review');
	await page.evaluate(async () => document.fonts.ready);

	for (const [index, file] of FILES.entries()) {
		await showFile(page, file, index > 0);
		await page.mouse.move(1, 1);
		// Open questions stay visible in original view; saved entries are masked – we compare the overpainted spots.
		const plans = file.fixture.placements.filter((plan) => plan.finding_id !== 'f_tel');
		await expect(page.locator('[data-overpaint]')).toHaveCount(plans.length);
		for (const g of await page.locator('[data-overpaint]').all()) {
			await expect(g).toHaveCSS('opacity', '1');
		}
		const docPage = file.fixture.layout.pages[0];
		const kontrolle: number[] = [];
		const original: number[] = [];
		for (const plan of plans) {
			// Scroll wide (rotated) pages horizontally to bring the spot into view.
			await page
				.locator(`[data-id="${plan.finding_id}"] [data-overpaint]`)
				.evaluate((g) => g.scrollIntoView({ block: 'center', inline: 'center' }));
			const frame = (await page.locator('[data-page] svg').first().boundingBox())!;
			const k = frame.width / docPage.width;
			const xs = plan.cover.flatMap(([x0, , x1]) => [x0, x1]).map((x) => frame.x + x * k);
			const ys = plan.cover.flatMap(([, y0, , y1]) => [y0, y1]).map((y) => frame.y + y * k);
			const clip = {
				x: Math.floor(Math.min(...xs)) - 4,
				y: Math.floor(Math.min(...ys)) - 4,
				width: Math.ceil(Math.max(...xs) - Math.min(...xs)) + 9,
				height: Math.ceil(Math.max(...ys) - Math.min(...ys)) + 9
			};
			const aufnahme = await page.screenshot({ clip });
			const position = {
				dx: frame.x - clip.x,
				dy: frame.y - clip.y,
				k,
				width: docPage.width,
				height: docPage.height
			};
			kontrolle.push(...(await deviation(page, aufnahme, file.kontrollbild, plan.cover, position)));
			// Counter check: the same area differs noticeably from the original – the comparison should detect that.
			original.push(...(await deviation(page, aufnahme, file.image, plan.cover, position)));
		}
		for (const [part, share] of kontrolle.entries()) {
			expect(share, `${file.name} Fläche ${part}`).toBeLessThanOrEqual(0.03);
			expect(original[part], `${file.name} Fläche ${part} (Original)`).toBeGreaterThan(0.1);
		}
	}
});

test('Side-by-side: original and result per page, reference image', async ({ context, page }) => {
	await layoutHarness(context);
	await open(page, '/entry/e-1/review');
	await pageLoaded(page);

	await page.getByRole('radio', { name: 'Nebeneinander' }).click();
	await expect(page.getByRole('img', { name: 'Seite 1 von 1 · Original' })).toBeVisible();
	await expect(page.getByRole('img', { name: 'Seite 1 von 1 · Ergebnis' })).toBeVisible();
	await expect(page.locator('[data-page]')).toHaveCount(2);
	// Only the result column is overpainted; it is the only interactive side, as in the text.
	await expect(page.locator('[data-page]').first().locator('[data-overpaint]')).toHaveCount(0);
	await expect(page.locator('[data-page]').last().locator('[data-overpaint]')).toHaveCount(3);
	await expect(page.getByRole('button', { name: /MUSTERMANN/ })).toHaveCount(1);
	await expect
		.poll(() =>
			page
				.locator('[data-page] canvas')
				.evaluateAll((all) => all.every((c) => (c as HTMLCanvasElement).width > 0))
		)
		.toBe(true);
	await board(page, 'layout-side-by-side');
});

test('Select in page view: drag, double, triple and shift-click', async ({ context, page }) => {
	await layoutHarness(context);
	await open(page, '/entry/e-1/review');
	await pageLoaded(page);
	const [file] = FILES;
	const menu = page.getByRole('dialog');

	// Drag from “1110” to “WIEN”: all words between become a single region.
	const by = await wordMiddle(page, file, '1110');
	const until = await wordMiddle(page, file, 'WIEN');
	await page.mouse.move(by.x, by.y);
	await page.mouse.down();
	await page.mouse.move((by.x + until.x) / 2, until.y, { steps: 4 });
	await expect(page.locator('[data-selection]')).toHaveCount(1);
	await page.mouse.move(until.x, until.y, { steps: 4 });
	await page.mouse.up();
	await expect(menu).toContainText('»1110 WIEN« auch maskieren');
	await expect(menu).toBeFocused();
	// The selection stays visible while the menu is open.
	await expect(page.locator('[data-page] g[role="group"]').last()).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(menu).toBeHidden();

	// Double-click selects a single word.
	const word = await wordMiddle(page, file, 'Auftragsnummer');
	await page.mouse.dblclick(word.x, word.y);
	await expect(menu).toContainText('»Auftragsnummer« auch maskieren');
	await page.keyboard.press('Escape');

	// Triple-click selects the whole line.
	await page.mouse.click(by.x, by.y, { clickCount: 3 });
	await expect(menu).toContainText('»1110 WIEN« auch maskieren');
	await page.keyboard.press('Escape');

	// Shift-click extends from the previous selection.
	await page.mouse.dblclick(by.x, by.y);
	await expect(menu).toContainText('»1110«');
	await page.keyboard.down('Shift');
	await page.mouse.click(until.x, until.y);
	await page.keyboard.up('Shift');
	await expect(menu).toContainText('»1110 WIEN« auch maskieren');
	await page.keyboard.press('Escape');

	// Drag across a detected spot: “extend selection” instead of the code menu.
	const max = await wordMiddle(page, file, 'MAX');
	const name = await wordMiddle(page, file, 'MUSTERMANN');
	await page.mouse.move(max.x, max.y);
	await page.mouse.down();
	await page.mouse.move(name.x, name.y, { steps: 6 });
	await page.mouse.up();
	await expect(menu).toContainText('»MAX MUSTERMANN« als eine Stelle maskieren');
	await expect(page.getByRole('menu')).toHaveCount(0);
});

test('Keyboard: Alt+N shows question in page view, zoom and page width', async ({
	context,
	page
}) => {
	const { capture } = await layoutHarness(context, 3);
	await open(page, '/entry/e-1/review');
	await pageLoaded(page);

	// After scrolling to the end, the question on page 1 is out of view – Alt+N brings it back.
	const question = page.locator('[data-page="0"] g[data-id="f_tel"]');
	const scroller = page.getByRole('tabpanel');
	await scroller.evaluate((element) => (element.scrollTop = element.scrollHeight));
	await expect(question).not.toBeInViewport();
	await scroller.focus();
	await page.keyboard.press('Alt+KeyN');
	await expect(question).toBeInViewport();
	await expect(page.locator('[data-question="f_tel"] button').first()).toBeFocused();

	// Alt+M: add entry without mouse – the search opens, Escape closes it.
	await page.keyboard.press('Alt+KeyM');
	await expect(page.getByRole('dialog').getByRole('textbox')).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toBeHidden();

	// Controls follow reading order: Tab moves from one spot to the next.
	await page
		.getByRole('button', { name: /MUSTERMANN/ })
		.first()
		.focus();
	await page.keyboard.press('Tab');
	await expect(page.getByRole('button', { name: /MUSTERGASSE/ }).first()).toBeFocused();

	const docPage = page.locator('[data-page="0"] svg');
	const width = async () => (await docPage.boundingBox())!.width;
	const normal = await width();
	await page.getByRole('button', { name: 'Vergrößern' }).click();
	await expect(page.getByRole('toolbar').getByText('110 %')).toBeVisible();
	await expect.poll(width).toBeGreaterThan(normal * 1.05);

	// Page width: the page fills the column; the image scale grows accordingly.
	await page.getByRole('button', { name: /Seitenbreite/ }).click();
	await expect(page.getByRole('button', { name: /Seitenbreite/ })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(page.getByRole('toolbar').getByText('Breite')).toBeVisible();
	const column = await scroller.evaluate((element) => {
		const style = getComputedStyle(element);
		return element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
	});
	await expect.poll(async () => Math.abs((await width()) - column)).toBeLessThanOrEqual(1);
	await expect
		.poll(() => capture.requests.some((a) => /\/pages\/0\?scale=1\.5/.test(a.path + a.search)))
		.toBe(true);

	// Minus/plus return to the fixed scale.
	await page.getByRole('button', { name: 'Verkleinern' }).click();
	await expect(page.getByRole('toolbar').getByText('110 %')).toBeVisible();
});
