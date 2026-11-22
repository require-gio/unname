import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import type { EntryView } from '../../src/lib/sidecar/api.gen.js';
import {
	APP_ORIGIN,
	JOB,
	WITHOUT_CONTENT,
	Roh,
	board,
	entry,
	harness,
	open,
	type Capture
} from './harness.js';

/**
 * Phase E: detection and review — Phase F: save and saved.
 *
 * “Review three files, toggle masking (no new analysis), keep places, add across files,
 * decide questions — fully keyboard driven.”
 *
 * The simulated sidecar applies masking like the real one: confirmed hits and open questions
 * are masked (“mask when in doubt”), unchosen categories stay, hand entries are always masked.
 */

interface Match {
	id: string;
	value: string;
	type: string;
	category: string;
	question?: { kind: 'suspicion' | 'uncertain'; n: number };
	/** For multiple occurrences: which instance. */
	nr?: number;
}

interface File {
	id: string;
	name: string;
	format: string;
	text: string;
	match: Match[];
}

const INVOICE: File = {
	id: 'j-1',
	name: 'Rechnung_2026-0418.pdf',
	format: 'pdf',
	text:
		'Tischlerei Josef Brandstetter e.U.\n\nFrau Sabine Hofer\nMozartgasse 7/3, 1040 Wien\n\n' +
		'Sehr geehrte Frau Hofer,\n\nwie mit Herrn Markus Leitner besprochen, stammt das Holz von den ' +
		'Gebrüdern Wachtelhuber. Ein Muster der Arbeitsplatte liegt bei, wie von Ihrer Tochter Lena ' +
		'gewünscht. Lieferung nach Linz.\n\nIBAN AT61 1904 3002 3457 3201',
	match: [
		{ id: 'f_brandstetter', value: 'Josef Brandstetter', type: 'PERSON', category: 'names' },
		{ id: 'f_hofer', value: 'Sabine Hofer', type: 'PERSON', category: 'names' },
		{
			id: 'f_adresse',
			value: 'Mozartgasse 7/3, 1040 Wien',
			type: 'ADDRESS',
			category: 'addresses'
		},
		{ id: 'f_hofer2', value: 'Hofer', type: 'PERSON', category: 'names', nr: 2 },
		{ id: 'f_leitner', value: 'Markus Leitner', type: 'PERSON', category: 'names' },
		{
			id: 'f_wachtel',
			value: 'Gebrüdern Wachtelhuber',
			type: 'PERSON',
			category: 'names',
			question: { kind: 'suspicion', n: 1 }
		},
		{
			id: 'f_sample',
			value: 'Muster',
			type: 'PERSON',
			category: 'names',
			question: { kind: 'uncertain', n: 2 }
		},
		{ id: 'f_linz', value: 'Linz', type: 'LOCATION', category: 'locations' },
		{ id: 'f_iban', value: 'AT61 1904 3002 3457 3201', type: 'IBAN_CODE', category: 'finance_ids' }
	]
};

const OFFER: File = {
	id: 'j-2',
	name: 'Angebot.docx',
	format: 'docx',
	text: 'Angebot für Frau Sabine Hofer.\n\nLena hat die Farbe ausgesucht.',
	match: [{ id: 'f_a_hofer', value: 'Sabine Hofer', type: 'PERSON', category: 'names' }]
};

const VOLLMACHT: File = {
	id: 'j-3',
	name: 'Vollmacht_Scan.jpg',
	format: 'jpg',
	text: 'Vollmacht für Lena.\n\nUnterschrift: Sabine Hofer',
	match: [{ id: 'f_v_hofer', value: 'Sabine Hofer', type: 'PERSON', category: 'names' }]
};

const FILES = [INVOICE, OFFER, VOLLMACHT];

const CATEGORY: Record<string, string> = {
	PERSON: 'names',
	LOCATION: 'locations',
	ORGANIZATION: 'organizations',
	CUSTOMER_NUMBER: 'numbers',
	OTHER: 'other_indirect'
};

const PLACEHOLDER: Record<string, string> = {
	PERSON: 'Name',
	ADDRESS: 'Anschrift',
	LOCATION: 'Ort',
	IBAN_CODE: 'IBAN'
};

function section(file: File, match: Match): { start: number; end: number } {
	let start = -1;
	for (let i = 0; i < (match.nr ?? 1); i += 1) start = file.text.indexOf(match.value, start + 1);
	return { start, end: start + match.value.length };
}

/** Local AI assessment per finding ID (Phase K) as provided by the sidecar. */
type Ai = Record<string, Record<string, unknown>>;

function findings(file: File, ai: Ai = {}) {
	const list = file.match.map((match) => {
		const { start, end } = section(file, match);
		const suspicion = match.question?.kind === 'suspicion';
		return {
			id: match.id,
			kind: suspicion ? 'suspicious' : 'confirmed',
			type: match.type,
			category: match.category,
			score: match.question ? 0.5 : 0.97,
			start,
			end,
			text: match.value,
			context: {
				before: file.text.slice(Math.max(0, start - 30), start),
				match: match.value,
				after: file.text.slice(end, end + 30)
			},
			source: suspicion ? 'suspicion' : 'ner',
			detector: 'test',
			review_required: match.question?.kind === 'uncertain',
			auto_accepted: !match.question,
			question: match.question ?? null,
			assessment: ai[match.id] ?? null
		};
	});
	const paragraphs: { index: number; start: number; end: number; kind: string; page: null }[] = [];
	let start = 0;
	for (const part of file.text.split('\n\n')) {
		paragraphs.push({
			index: paragraphs.length,
			start,
			end: start + part.length,
			kind: 'paragraph',
			page: null
		});
		start += part.length + 2;
	}
	const afterCategory: Record<string, number> = {};
	for (const match of file.match) {
		afterCategory[match.category] = (afterCategory[match.category] ?? 0) + 1;
	}
	return {
		job_id: file.id,
		offset_encoding: 'utf-16',
		text: file.text,
		confirmed: list.filter((f) => f.kind === 'confirmed'),
		suspicious: list.filter((f) => f.kind === 'suspicious'),
		findings: list,
		blocks: paragraphs,
		counts: {
			confirmed: list.filter((f) => f.kind === 'confirmed').length,
			suspicious: list.filter((f) => f.kind === 'suspicious').length,
			total: list.length,
			by_entity: {},
			by_category: afterCategory,
			questions: list.filter((f) => f.question).length
		}
	};
}

function shortCode(type: string, value: string): string {
	let sum = 7;
	for (const character of value) sum = (sum * 31 + character.charCodeAt(0)) >>> 0;
	return `<<${type}_${sum.toString(16).padStart(8, '0').slice(0, 8)}>>`;
}

interface ApplyBody {
	masking: { method: string; style: string; categories: string[] };
	review?: {
		decisions?: { finding_id: string; action: string }[];
		manual?: { entity_type: string; start: number; end: number }[];
		unresolved?: string;
	};
}

/** Apply small-scale rules as implemented in the sidecar (`processing.py`). */
function apply(file: File, body: ApplyBody) {
	const categories = new Set(body.masking.categories);
	const decisions = new Map((body.review?.decisions ?? []).map((d) => [d.finding_id, d.action]));
	const places: { id: string; start: number; end: number; type: string; category: string }[] = [];
	for (const f of findings(file).findings) {
		const decision = decisions.get(f.id);
		let masked = f.kind === 'confirmed';
		if (decision) masked = decision === 'accept';
		else if (f.kind === 'suspicious') masked = (body.review?.unresolved ?? 'mask') === 'mask';
		if (!masked || !categories.has(f.category)) continue;
		places.push({ id: f.id, start: f.start, end: f.end, type: f.type, category: f.category });
	}
	for (const hand of body.review?.manual ?? []) {
		places.push({
			id: `f_hand_${hand.start}`,
			start: hand.start,
			end: hand.end,
			type: hand.entity_type,
			category: CATEGORY[hand.entity_type] ?? 'numbers'
		});
	}
	places.sort((a, b) => a.start - b.start);
	let text = '';
	let cursor = 0;
	const applied = places.map((place) => {
		text += file.text.slice(cursor, place.start);
		const value = file.text.slice(place.start, place.end);
		const replacement =
			body.masking.method === 'reversible'
				? shortCode(place.type, value)
				: `[${PLACEHOLDER[place.type] ?? 'Angabe'}]`;
		const output_start = text.length;
		text += replacement;
		cursor = place.end;
		return {
			finding_id: place.id,
			kind: 'confirmed',
			entity_type: place.type,
			category: place.category,
			source: 'ner',
			mode: body.masking.method === 'reversible' ? 'token_deterministic' : 'placeholder',
			reversibility: body.masking.method === 'reversible' ? 'vault' : 'none',
			original_start: place.start,
			original_end: place.end,
			output_start,
			output_end: text.length
		};
	});
	text += file.text.slice(cursor);
	return {
		offset_encoding: 'utf-16',
		text,
		masking: { ...body.masking, anonymize: false },
		labels: {
			label: body.masking.method === 'reversible' ? 'reversible' : 'irreversible',
			contains_surrogates: false,
			anonymous_blockers: ['not_requested'],
			protection_level: 2,
			kept_here_count: 0,
			masked_without_decision: 0
		},
		applied_findings: applied,
		applied_by_category: {},
		residual_risk: { status: 'not_run', heuristic: true, threshold: 2, classes: [], counts: {} },
		facts: {},
		report: {},
		job_id: file.id,
		entry_id: 'e-1'
	};
}

function occurrence(search: string) {
	const pattern = new RegExp(`(?<![\\p{L}\\p{N}_])${search}(?![\\p{L}\\p{N}_])`, 'gu');
	const matches = FILES.flatMap((file) => {
		const used = findings(file).findings;
		return [...file.text.matchAll(pattern)].map((match) => {
			const start = match.index ?? 0;
			const end = start + search.length;
			return {
				file_id: file.id,
				start,
				end,
				covered: used.some((f) => f.start < end && start < f.end)
			};
		});
	});
	return {
		entry_id: 'e-1',
		offset_encoding: 'utf-16',
		matches,
		count: matches.length,
		file_count: new Set(matches.map((m) => m.file_id)).size
	};
}

function entryFile(file: File, status = 'review') {
	return {
		id: file.id,
		name: file.name,
		format: file.format,
		size_bytes: 200 * 1024,
		pages: file.format === 'pdf' ? 1 : null,
		ocr_used: file.format === 'jpg',
		status,
		error_code: null,
		counts: {},
		label: null,
		output_path: null
	};
}

const ENTRY = entry({
	title: 'Rechnung + 2 weitere',
	status: 'review',
	files: FILES.map((file) => entryFile(file))
});

function progressResponse(status: (file: File) => string) {
	return {
		entry_id: 'e-1',
		status: 'review',
		files: FILES.map((file) => ({
			id: file.id,
			name: file.name,
			status: status(file),
			phase: status(file) === 'processing' ? 'extraction' : null,
			progress: status(file) === 'processing' ? 35 : 100,
			counts: status(file) === 'processing' ? {} : { names: file === INVOICE ? 5 : 1 },
			questions: status(file) === 'review' ? (file === INVOICE ? 2 : 0) : null,
			error_code: null
		})),
		totals: { names: 7, addresses: 1, finance_ids: 1 },
		done: FILES.filter((file) => status(file) === 'review').length,
		total: FILES.length,
		ready: true
	};
}

async function reviewHarness(context: Parameters<typeof harness>[0], ai: Ai = {}) {
	return harness(context, {
		entries: () => [ENTRY],
		sidecar: (method, path, body) => {
			if (path === '/entries/e-1' && method === 'GET') return ENTRY;
			if (path === '/entries/e-1/progress') return progressResponse(() => 'review');
			if (path === '/entries/e-1/occurrences') return occurrence((body as { text: string }).text);
			const file = FILES.find((kandidat) => path.startsWith(`/jobs/${kandidat.id}/`));
			if (file && path.endsWith('/findings')) return findings(file, ai);
			if (file && path.endsWith('/apply')) return apply(file, body as ApplyBody);
			return undefined;
		}
	});
}

/** Body of the last apply request for a file. */
function lastApply(capture: Capture, jobId: string): ApplyBody | undefined {
	const calls = capture.requests.filter((a) => a.path === `/jobs/${jobId}/apply`);
	return calls.at(-1)?.body as ApplyBody | undefined;
}

/** Double-click a word in the document, simulating a human. */
async function markWord(page: Page, word: string): Promise<void> {
	const point = await page.evaluate((searched) => {
		const article = document.querySelector('article');
		if (!article) return null;
		const run = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
		for (let node = run.nextNode(); node; node = run.nextNode()) {
			const index = node.textContent?.search(new RegExp(`\\b${searched}\\b`)) ?? -1;
			if (index < 0 || !node.parentElement?.closest('[data-seg]')) continue;
			const section = document.createRange();
			section.setStart(node, index);
			section.setEnd(node, index + searched.length);
			section.startContainer.parentElement?.scrollIntoView({ block: 'center' });
			const rect = section.getBoundingClientRect();
			return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
		}
		return null;
	}, word);
	expect(point, word).not.toBeNull();
	await page.mouse.dblclick(point!.x, point!.y);
}

test('Detection runs: per-file state, review from first completed, cancel', async ({
	context,
	page
}) => {
	let cancelled = false;
	const laufend = (file: File) =>
		file !== VOLLMACHT ? 'review' : cancelled ? 'cancelled' : 'processing';
	const { capture } = await harness(context, {
		entries: () => [ENTRY],
		sidecar: (method, path) => {
			if (path === '/entries/e-1' && method === 'GET') {
				return {
					...ENTRY,
					status: cancelled ? 'review' : 'processing',
					files: FILES.map((file) => entryFile(file, laufend(file)))
				};
			}
			if (path === '/entries/e-1/progress') return progressResponse(laufend);
			if (path === '/jobs/j-3' && method === 'DELETE') {
				cancelled = true;
				return WITHOUT_CONTENT;
			}
			const file = FILES.find((kandidat) => path === `/jobs/${kandidat.id}/findings`);
			if (file) return findings(file);
			return undefined;
		}
	});
	await open(page, '/entry/e-1');

	await expect(page.getByRole('heading', { name: 'Wird geprüft …', level: 1 })).toBeVisible();
	const list = page.getByRole('list', { name: 'Dateien' });
	await expect(list).toContainText('1 Seite · 5 Stellen gefunden · 2 brauchen Ihren Blick');
	await expect(list).toContainText('Text wird aus dem Bild gelesen');
	await expect(list).toContainText('Foto eines Dokuments');
	const previous = page.getByRole('region', { name: 'Bisher gefunden' });
	await expect(previous).toContainText('Namen');
	await expect(previous).toContainText('Konto-, Ausweis- & Zugangsdaten');
	// First file is done — "Review" does not wait for the rest.
	await expect(page.getByRole('link', { name: 'Prüfen', exact: true })).toBeVisible();
	// Phase L: Board "2 · detection running", light and dark.
	await board(page, '2-detection');

	await page.getByRole('button', { name: 'Abbrechen' }).click();
	await expect(page.getByText('Die Erkennung wurde abgebrochen.')).toBeVisible();
	expect(capture.requests.filter((a) => a.method === 'DELETE').map((a) => a.path)).toEqual([
		'/jobs/j-3'
	]);
	await expect(page.getByRole('heading', { name: 'Bereit zum Prüfen', level: 1 })).toBeVisible();
	await expect(list).toContainText('abgebrochen');
	await expect(page.getByRole('button', { name: 'Abbrechen' })).toHaveCount(0);
});

test('Milestone E: review three files, switch, keep, add, decide', async ({ context, page }) => {
	const { capture } = await reviewHarness(context);
	await open(page, '/entry/e-1/review');

	const reiter = page.getByRole('tablist', { name: 'Dateien' });
	await expect(reiter.getByRole('tab')).toHaveCount(3);
	await expect(reiter.getByRole('tab', { selected: true })).toContainText('Rechnung_2026-0418.pdf');
	await expect(reiter.getByRole('tab').first()).toContainText('2 Fragen offen');

	const doc = page.getByRole('region', { name: 'Dokument' });
	await expect(doc.getByRole('button', { name: 'Person: Sabine Hofer' })).toContainText(
		'<<PERSON_'
	);
	await expect(doc).toContainText('Rechnung_2026-0418_geschützt.pdf · 1 Seite');
	// Open questions appear with their original text and number in the document.
	await expect(doc.locator('sup', { hasText: '1' })).toBeVisible();
	await expect(doc).toContainText('Gebrüdern Wachtelhuber');
	const bar = page.getByRole('complementary', { name: 'Prüfen' });
	await expect(bar.getByRole('heading', { name: '2 Stellen brauchen Ihren Blick' })).toBeVisible();
	await expect(bar).toContainText('2 offene Stellen werden beim Speichern maskiert');
	await expect(bar.getByRole('button', { name: 'Alle 3 Dateien speichern' })).toBeEnabled();
	// Without local AI assessment (Phase K) there is nothing to adopt.
	await expect(bar.getByRole('button', { name: 'Alle KI-Vorschläge übernehmen' })).toHaveCount(0);

	// Switch masking: only Apply, no new detection.
	const findingsBefore = capture.requests.filter((a) => a.path.endsWith('/findings')).length;
	await page.getByRole('button', { name: /^Maskierung: Reversibel/ }).click();
	const menu = page.getByRole('dialog', { name: 'Maskierung' });
	await expect(menu).toContainText('Ihr Standard · gilt für alle 3 Dateien.');
	await menu.getByRole('button', { name: /Orte/ }).click();
	await expect.poll(() => lastApply(capture, 'j-1')?.masking.categories).toContain('locations');
	await expect(menu).toContainText('Gilt nur für diese 3 Dateien.');
	await expect(menu.getByRole('button', { name: 'Als Standard übernehmen' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(doc.getByRole('button', { name: 'Ort: Linz' })).toBeVisible();
	expect(capture.requests.filter((a) => a.path.endsWith('/findings')).length).toBe(findingsBefore);
	expect(capture.requests.some((a) => a.method === 'POST' && a.path.endsWith('/files'))).toBe(
		false
	);

	// Keep a single occurrence: the shared menu in the code.
	await doc.getByRole('button', { name: 'Person: Markus Leitner' }).click();
	const codeMenu = page.getByRole('menu');
	await expect(codeMenu).toContainText('Name · 6 Stellen');
	await codeMenu.getByRole('menuitem', { name: 'Nur hier behalten' }).click();
	await expect
		.poll(() => lastApply(capture, 'j-1')?.review?.decisions)
		.toContainEqual({ finding_id: 'f_leitner', action: 'reject', by: 'user' });
	await expect(doc.getByRole('button', { name: 'Person: Markus Leitner' })).toHaveText(
		'Markus Leitner'
	);

	// Add across files: "Lena" appears in all three files.
	await markWord(page, 'Lena');
	const selection = page.getByRole('dialog', { name: '»Lena« auch maskieren' });
	// No "only here" option: the value is masked everywhere, the menu indicates this.
	await expect(selection).toContainText('Wird an allen 3 Stellen in 3 Dateien maskiert.');
	await expect(selection.getByRole('checkbox')).toHaveCount(0);
	// Counting follows sidecar logic: "LENA" is the same value as "Lena".
	expect(capture.requests.find((a) => a.path === '/entries/e-1/occurrences')?.body).toMatchObject({
		text: 'Lena',
		ignore_case: true
	});
	await selection.getByRole('button', { name: /^Maskieren/ }).click();
	await expect(page.getByText('3 Stellen in 3 Dateien zusätzlich maskiert.')).toBeVisible();
	for (const file of FILES) {
		const start = file.text.indexOf('Lena');
		await expect
			.poll(() => lastApply(capture, file.id)?.review?.manual)
			.toContainEqual({ entity_type: 'PERSON', start, end: start + 4 });
	}

	// Resolve questions.
	await bar
		.locator('[data-question="f_wachtel"]')
		.getByRole('button', { name: 'Maskieren' })
		.click();
	await bar.locator('[data-question="f_sample"]').getByRole('button', { name: 'Behalten' }).click();
	await expect(bar.getByRole('heading', { name: 'Alles geprüft.' })).toBeVisible();
	await expect(bar).toContainText('Alle Stellen entschieden.');
	await expect(bar).toContainText('Gebrüdern Wachtelhuber · wird maskiert');
	await expect(bar).toContainText('Muster · bleibt stehen');
	const decisions = lastApply(capture, 'j-1')?.review?.decisions;
	expect(decisions).toContainEqual({ finding_id: 'f_wachtel', action: 'accept', by: 'user' });
	expect(decisions).toContainEqual({ finding_id: 'f_sample', action: 'reject', by: 'user' });
	await expect(reiter.getByRole('tab').first()).not.toContainText('Fragen offen');

	// The second file shows the addendum; the address remembers the file.
	await reiter.getByRole('tab', { name: /Angebot\.docx/ }).click();
	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1/review?file=2`);
	await expect(doc.getByRole('button', { name: 'Person: Lena' })).toBeVisible();
	await page.reload({ waitUntil: 'networkidle' });
	await expect(reiter.getByRole('tab', { selected: true })).toContainText('Angebot.docx');
});

test('Second opinion: AI box, “Resolved by local AI”, reversible', async ({ context, page }) => {
	const { capture } = await reviewHarness(context, {
		f_wachtel: {
			label: 'organization_or_product',
			confidence: 0.93,
			protect: 0.97,
			suggestion: 'accept',
			decided: true,
			model: 'zweitmeinung-test'
		},
		f_sample: {
			label: 'term',
			confidence: 0.88,
			protect: 0.12,
			suggestion: 'reject',
			decided: false,
			model: 'zweitmeinung-test'
		}
	});
	await open(page, '/entry/e-1/review');
	const bar = page.getByRole('complementary', { name: 'Prüfen' });

	// The AI's decision is no longer an open question – shown with justification.
	await expect(bar.getByRole('heading', { name: '1 Stelle braucht Ihren Blick' })).toBeVisible();
	const resolved = bar.getByRole('region', { name: 'Von der lokalen KI geklärt' });
	await expect(resolved).toContainText('Gebrüdern Wachtelhuber · wird maskiert');
	await expect(resolved).toContainText('Firma oder Produkt · 97 %');
	await expect
		.poll(() => lastApply(capture, 'j-1')?.review?.decisions)
		.toContainEqual({ finding_id: 'f_wachtel', action: 'accept', by: 'ai-suggestion' });

	// The open question shows the AI box; the suggested button is highlighted.
	const map = bar.locator('[data-question="f_sample"]');
	await expect(map).toContainText('Lokale KI');
	await expect(map).toContainText('Wahrscheinlich ein Begriff, kein Name');
	await expect(map).toContainText('88 %');
	await expect(map.getByRole('button', { name: /Behalten/ })).toContainText('Vorschlag');

	// “Apply all AI suggestions” applies the rest with provenance.
	await bar.getByRole('button', { name: 'Alle KI-Vorschläge übernehmen' }).click();
	await expect
		.poll(() => lastApply(capture, 'j-1')?.review?.decisions)
		.toContainEqual({ finding_id: 'f_sample', action: 'reject', by: 'ai-suggestion' });
	await expect(bar.getByRole('heading', { name: 'Alles geprüft.' })).toBeVisible();

	// Each AI decision can be reversed – then the human decides.
	await resolved.getByRole('button', { name: 'Trotzdem maskieren' }).click();
	await expect
		.poll(() => lastApply(capture, 'j-1')?.review?.decisions)
		.toContainEqual({ finding_id: 'f_sample', action: 'accept', by: 'user' });
	await expect(bar).toContainText('Muster · wird maskiert');
});

test('Board “3 · Review”: accessible and matches reference, light and dark', async ({
	context,
	page
}) => {
	await reviewHarness(context);
	await open(page, '/entry/e-1/review');
	const doc = page.getByRole('region', { name: 'Dokument' });
	await expect(doc.getByRole('button', { name: 'Person: Sabine Hofer' })).toBeVisible();

	await board(page, '3-review');

	await page.getByRole('radio', { name: 'Nebeneinander' }).click();
	await expect(page.getByText('Original und Ergebnis, Absatz für Absatz')).toBeVisible();
	await board(page, '3-review-side-by-side');
});

test('Focus order: Document, floating toolbar, question panel – menu returns focus', async ({
	context,
	page
}) => {
	await reviewHarness(context);
	await open(page, '/entry/e-1/review');
	const doc = page.getByRole('region', { name: 'Dokument' });
	await expect(doc.getByRole('button', { name: 'Person: Sabine Hofer' })).toBeVisible();
	const bar = page.getByRole('toolbar', { name: 'Ansicht und Maskierung' });

	// Where is focus? Top (tabs, “+”, “?”), in the document, in the bottom
	// toolbar or in the question panel – and with which label.
	const focus = () =>
		page.evaluate(() => {
			const active = document.activeElement as HTMLElement | null;
			const place = active?.closest('article')
				? 'dokument'
				: active?.closest('[role="toolbar"]')
					? 'leiste'
					: active?.closest('aside')
						? 'panel'
						: 'oben';
			const name = active?.getAttribute('aria-label') ?? active?.textContent?.trim() ?? '';
			return `${place}:${name}`;
		});
	const tab = async (num: number, backward = false) => {
		const spots: string[] = [];
		for (let step = 0; step < num; step++) {
			await page.keyboard.press(backward ? 'Shift+Tab' : 'Tab');
			spots.push(await focus());
		}
		return spots;
	};

	// The toolbars float over the document; tab order still follows the visual
	// order: top, document, bottom, right (WCAG 2.4.3).
	await page.getByRole('link', { name: 'Zurück zur Übersicht' }).focus();
	const top = await tab(4);
	expect(top.slice(0, 3).every((place) => place.startsWith('oben:'))).toBe(true);
	expect(top).toContain('oben:Hilfe anzeigen');
	expect(top.at(-1)).toMatch(/^dokument:/);

	await doc.locator('article').getByRole('button').last().focus();
	const order = await tab(5);
	expect(order.slice(0, 4)).toEqual([
		'leiste:Ergebnis',
		expect.stringMatching(/^leiste:Maskierung: Reversibel, \d+ Stellen – ändern$/),
		'leiste:Verkleinern',
		'leiste:Vergrößern'
	]);
	expect(order[4]).toMatch(/^panel:/);

	// Reverse navigation works the same, and the masking menu returns focus.
	await page.keyboard.press('Shift+Tab');
	await expect(bar.getByRole('button', { name: 'Vergrößern' })).toBeFocused();
	const button = bar.getByRole('button', { name: /^Maskierung: / });
	await button.focus();
	await page.keyboard.press('Enter');
	const menu = page.getByRole('dialog', { name: 'Maskierung' });
	await expect(menu).toBeVisible();
	await expect(menu.locator(':focus')).toHaveCount(1);
	await page.keyboard.press('Escape');
	await expect(menu).toHaveCount(0);
	await expect(button).toBeFocused();

	// The view changes with arrow keys without leaving the toolbar.
	await bar.getByRole('radio', { name: 'Ergebnis' }).focus();
	await page.keyboard.press('ArrowRight');
	await expect(bar.getByRole('radio', { name: 'Nebeneinander' })).toBeFocused();
	await expect(bar.getByRole('radio', { name: 'Nebeneinander' })).toBeChecked();
});

/** Performance: a file with 1,000 codes and eight small ones alongside. */
const THOUSAND_CODES: File = (() => {
	const lines: string[] = [];
	const match: Match[] = [];
	for (let i = 0; i < 1000; i += 1) {
		const value = `Kundin ${String(i).padStart(4, '0')} Hofer`;
		lines.push(`Position ${i + 1}: Rückruf von ${value} bestätigt.`);
		match.push({ id: `f_t${i}`, value, type: 'PERSON', category: 'names' });
	}
	// Paragraphs of ten lines each, like a long log.
	const paragraphs: string[] = [];
	for (let i = 0; i < lines.length; i += 10) paragraphs.push(lines.slice(i, i + 10).join('\n'));
	return {
		id: 'j-big',
		name: 'Protokoll_1000.txt',
		format: 'txt',
		text: paragraphs.join('\n\n'),
		match
	};
})();
const SMALL: File[] = Array.from({ length: 8 }, (_, i) => ({
	id: `j-k${i}`,
	name: `Brief_${i + 1}.txt`,
	format: 'txt',
	text: `Sehr geehrte Frau Hofer,\n\nanbei Unterlage ${i + 1}.`,
	match: [{ id: `f_k${i}`, value: 'Hofer', type: 'PERSON', category: 'names' }]
}));

test('Performance: 9 files, one with 1,000 codes, remain usable', async ({ context, page }) => {
	const files = [THOUSAND_CODES, ...SMALL];
	const large = entry({
		title: 'Protokoll + 8 weitere',
		status: 'review',
		files: files.map((file) => entryFile(file))
	});
	await harness(context, {
		entries: () => [large],
		sidecar: (method, path, body) => {
			if (path === '/entries/e-1' && method === 'GET') return large;
			if (path === '/entries/e-1/progress') {
				return {
					entry_id: 'e-1',
					status: 'review',
					files: files.map((file) => ({
						id: file.id,
						name: file.name,
						status: 'review',
						phase: null,
						progress: 100,
						counts: { names: file.match.length },
						questions: 0,
						error_code: null
					})),
					totals: { names: 1008 },
					done: files.length,
					total: files.length,
					ready: true
				};
			}
			const file = files.find((kandidat) => path.startsWith(`/jobs/${kandidat.id}/`));
			if (file && path.endsWith('/findings')) return findings(file);
			if (file && path.endsWith('/apply')) return apply(file, body as ApplyBody);
			return undefined;
		}
	});
	const times: Record<string, number> = {};
	const measure = async (was: string, step: () => Promise<void>) => {
		const beginn = Date.now();
		await step();
		times[was] = Date.now() - beginn;
	};

	const doc = page.getByRole('region', { name: 'Dokument' });
	const codes = doc.locator('article').getByRole('button', { name: /^Person: Kundin/ });
	await measure('öffnen bis 1.000 Codes stehen', async () => {
		await open(page, '/entry/e-1/review');
		await expect(codes).toHaveCount(1000, { timeout: 15_000 });
	});
	await expect(page.getByRole('tablist', { name: 'Dateien' }).getByRole('tab')).toHaveCount(9);

	await measure('Code-Menü öffnen', async () => {
		await codes.nth(500).click();
		await expect(page.getByRole('menuitem', { name: 'Nur hier behalten' })).toBeVisible();
	});
	await page.keyboard.press('Escape');

	await measure('Nebeneinander', async () => {
		await page.getByRole('radio', { name: 'Nebeneinander' }).click();
		await expect(page.getByText('Original und Ergebnis, Absatz für Absatz')).toBeVisible();
		await expect(codes).toHaveCount(1000);
	});
	await page.getByRole('radio', { name: 'Ergebnis' }).click();

	await measure('„Nur hier behalten" an Stelle 501', async () => {
		await codes.nth(500).click();
		await page.getByRole('menuitem', { name: 'Nur hier behalten' }).click();
		// The location stays usable ("Re-mask"), but shows the original.
		await expect(codes.nth(500)).toHaveText('Kundin 0500 Hofer');
		await expect(codes.nth(499)).toContainText('<<PERSON_');
	});

	await measure('Dateiwechsel', async () => {
		await page.getByRole('tab', { name: /Brief_8\.txt/ }).click();
		await expect(doc.locator('article').getByRole('button', { name: 'Person: Hofer' })).toHaveCount(
			1
		);
	});

	console.log(
		'[leistung] ' +
			Object.entries(times)
				.map(([was, ms]) => `${was}: ${ms} ms`)
				.join(' · ')
	);
	// Generous limits: they catch a fallback by an order of magnitude,
	// not the daily performance of the machine.
	expect(times['öffnen bis 1.000 Codes stehen']).toBeLessThan(10_000);
	expect(times['Code-Menü öffnen']).toBeLessThan(1_500);
	expect(times['Nebeneinander']).toBeLessThan(5_000);
	expect(times['„Nur hier behalten" an Stelle 501']).toBeLessThan(5_000);
	expect(times['Dateiwechsel']).toBeLessThan(3_000);
});

test('Fail-Closed: a glitch during review blocks saving but not viewing', async ({
	context,
	page
}) => {
	await reviewHarness(context);
	await open(page, '/entry/e-1/review');
	const doc = page.getByRole('region', { name: 'Dokument' });
	const bar = page.getByRole('complementary', { name: 'Prüfen' });
	const save = bar.getByRole('button', { name: 'Alle 3 Dateien speichern' });
	await expect(doc.getByRole('button', { name: 'Person: Sabine Hofer' })).toBeVisible();
	await expect(save).toBeEnabled();

	const status = (ready: boolean) => ({
		status: ready
			? { state: 'ready', port: 51234, pid: 4242, version: '0.1.0' }
			: { state: 'unreachable', reason: 'Health nicht erreichbar.', restarting: true },
		restarts: 1,
		changed_at: '2026-10-26T09:00:00Z',
		can_upload: ready,
		can_export: ready,
		setup_required: false
	});
	const report = (ready: boolean) =>
		page.evaluate(
			(snapshot) =>
				(window as unknown as { __EMIT__: (n: string, p: unknown) => void }).__EMIT__(
					'sidecar://status',
					snapshot
				),
			status(ready)
		);

	// The harness reports the failure: the output is locked, not the read path.
	await report(false);
	await expect(
		page.getByRole('status').filter({ hasText: 'Gerade nicht verfügbar' })
	).toBeVisible();
	await expect(save).toBeDisabled();
	await expect(doc.getByRole('button', { name: 'Person: Sabine Hofer' })).toBeVisible();
	await expect(doc).toContainText('Gebrüdern Wachtelhuber');
	// No partial result: nothing was saved.
	await save.click({ force: true });
	await expect(page.getByRole('alertdialog')).toHaveCount(0);

	// Back online: saving works, document remains unchanged.
	await report(true);
	await expect(page.getByRole('status').filter({ hasText: 'Gerade nicht verfügbar' })).toHaveCount(
		0
	);
	await expect(save).toBeEnabled();
	await expect(doc.getByRole('button', { name: 'Person: Sabine Hofer' })).toContainText(
		'<<PERSON_'
	);
});

test('Review works fully with the keyboard', async ({ context, page }) => {
	const { capture } = await reviewHarness(context);
	await open(page, '/entry/e-1/review');
	const doc = page.getByRole('region', { name: 'Dokument' });
	const hofer = doc.getByRole('button', { name: 'Person: Sabine Hofer' });
	await expect(hofer).toBeVisible();

	// Tab moves focus from one code button to the next.
	await doc.getByRole('button', { name: 'Person: Josef Brandstetter' }).focus();
	await page.keyboard.press('Tab');
	await expect(hofer).toBeFocused();

	// Enter opens the menu, Escape closes it and returns focus.
	await page.keyboard.press('Enter');
	const menu = page.getByRole('menu');
	await expect(menu).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(menu).toHaveCount(0);
	await expect(hofer).toBeFocused();

	// Arrow keys navigate the menu, Enter selects.
	await page.keyboard.press('Enter');
	await expect(menu).toBeVisible();
	// When opened via keyboard, focus is already on the first entry.
	const onlyHere = menu.getByRole('menuitem', { name: 'Nur hier behalten' });
	if (!(await onlyHere.evaluate((element) => element === document.activeElement))) {
		await page.keyboard.press('ArrowDown');
	}
	await expect(onlyHere).toBeFocused();
	await page.keyboard.press('Enter');
	await expect
		.poll(() => lastApply(capture, 'j-1')?.review?.decisions)
		.toContainEqual({ finding_id: 'f_hofer', action: 'reject', by: 'user' });

	// Alt+N jumps to the next open question; the card is usable.
	await page.keyboard.press('Alt+KeyN');
	const first = page
		.locator('[data-question="f_wachtel"]')
		.getByRole('button', { name: 'Maskieren' });
	await expect(first).toBeFocused();
	await page.keyboard.press('Enter');
	await page.keyboard.press('Alt+KeyN');
	const second = page
		.locator('[data-question="f_sample"]')
		.getByRole('button', { name: 'Maskieren' });
	await expect(second).toBeFocused();
	await page.keyboard.press('Tab');
	await page.keyboard.press('Enter');
	await expect(page.getByRole('heading', { name: 'Alles geprüft.' })).toBeVisible();
	const decisions = lastApply(capture, 'j-1')?.review?.decisions;
	expect(decisions).toContainEqual({ finding_id: 'f_wachtel', action: 'accept', by: 'user' });
	expect(decisions).toContainEqual({ finding_id: 'f_sample', action: 'reject', by: 'user' });

	// Alt+M without a selection: search and add.
	await page.keyboard.press('Alt+KeyM');
	const search = page.getByRole('dialog', { name: 'Was soll zusätzlich maskiert werden?' });
	await expect(search.getByRole('textbox')).toBeFocused();
	await page.keyboard.type('Lena');
	await expect(search).toContainText('Wird an allen 3 Stellen in 3 Dateien maskiert.');
	await page.keyboard.press('Enter');
	await expect(search).toHaveCount(0);
	const lena = INVOICE.text.indexOf('Lena');
	await expect
		.poll(() => lastApply(capture, 'j-1')?.review?.manual)
		.toContainEqual({ entity_type: 'PERSON', start: lena, end: lena + 4 });

	// Holding space shows the original.
	await page.getByRole('tabpanel').focus();
	await page.keyboard.down('Space');
	await expect(doc).toContainText('Rechnung_2026-0418.pdf · Original');
	await page.keyboard.up('Space');
	await expect(doc).toContainText('Rechnung_2026-0418_geschützt.pdf');
});

test('Correct boundary: extending selection across a code expands the finding', async ({
	context,
	page
}) => {
	const { capture } = await reviewHarness(context);
	await open(page, '/entry/e-1/review');
	await page.getByRole('radio', { name: 'Original' }).click();
	const doc = page.getByRole('region', { name: 'Dokument' });
	await expect(doc).toContainText('Rechnung_2026-0418.pdf · Original');

	// Mark “Frau Sabine Hofer”: selection extends beyond the name.
	await page.evaluate(() => {
		const article = document.querySelector('article')!;
		const run = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
		let start: Text | null = null;
		let end: Text | null = null;
		for (let node = run.nextNode(); node; node = run.nextNode()) {
			if (!start && node.textContent?.includes('Frau ')) start = node as Text;
			if (node.textContent === 'Sabine Hofer') end = node as Text;
		}
		const selection = window.getSelection()!;
		const section = document.createRange();
		section.setStart(start!, start!.textContent!.indexOf('Frau '));
		section.setEnd(end!, end!.textContent!.length);
		selection.removeAllRanges();
		selection.addRange(section);
		start!.parentElement!.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
	});
	const menu = page.getByRole('dialog', { name: '»Frau Sabine Hofer« als eine Stelle maskieren' });
	await menu.getByRole('button', { name: /Stelle erweitern/ }).click();
	const frau = INVOICE.text.indexOf('Frau Sabine');
	await expect
		.poll(() => lastApply(capture, 'j-1')?.review)
		.toMatchObject({
			decisions: [{ finding_id: 'f_hofer', action: 'reject', by: 'user' }],
			manual: [
				{
					entity_type: 'PERSON',
					start: frau,
					end: frau + 'Frau Sabine Hofer'.length,
					corrects_finding_id: 'f_hofer'
				}
			]
		});
	await doc.getByRole('button', { name: 'Person: Frau Sabine Hofer' }).click();
	await expect(page.getByRole('menu')).toContainText('Von Ihnen angepasst');
	await page.getByRole('menuitem', { name: 'Anpassung zurücknehmen' }).click();
	await expect.poll(() => lastApply(capture, 'j-1')?.review?.manual).toEqual([]);
});

test('narrow window: question bar collapsed below 1200 px, sidebar collapsed below 1040 px', async ({
	context,
	page
}) => {
	await reviewHarness(context);
	await page.setViewportSize({ width: 1100, height: 860 });
	await open(page, '/entry/e-1/review');

	const bar = page.getByRole('complementary', { name: 'Prüfen' });
	await expect(bar.getByRole('button', { name: 'Leiste ausklappen' })).toBeVisible();
	await expect(bar.getByRole('button', { name: '2 Fragen offen' })).toBeVisible();
	// Review collapses the sidebar to give the document room, even above 1040 px.
	await expect(page.getByRole('button', { name: 'Seitenleiste ausklappen' })).toBeVisible();
	// Save and move open questions to the tabs.
	await expect(page.getByRole('button', { name: 'Alle 3 speichern' })).toBeEnabled();
	await page.getByRole('button', { name: '2 Fragen offen' }).first().click();
	await expect(bar.getByRole('heading', { name: '2 Stellen brauchen Ihren Blick' })).toBeVisible();

	await page.setViewportSize({ width: 1000, height: 860 });
	await expect(page.getByRole('button', { name: 'Seitenleiste ausklappen' })).toBeVisible();
	await page.getByRole('button', { name: 'Seitenleiste ausklappen' }).click();
	await expect(page.getByRole('button', { name: 'Seitenleiste einklappen' })).toBeVisible();
	// Expanding in a narrow window does not overwrite the stored choice.
	expect(await page.evaluate(() => localStorage.getItem('unname.sidebar'))).toBeNull();
	expect(JOB.method).toBe('reversible');
});

test('a failed Apply reports the error and can be retried', async ({ context, page }) => {
	let failures = 1;
	const { capture } = await harness(context, {
		entries: () => [ENTRY],
		sidecar: (method, path, body) => {
			if (path === '/entries/e-1' && method === 'GET') return ENTRY;
			if (path === '/entries/e-1/progress') return progressResponse(() => 'review');
			if (path === '/jobs/j-1/findings') return findings(INVOICE);
			if (path === '/jobs/j-1/apply') {
				// `undefined` makes the mock respond with 500.
				if (failures-- > 0) return undefined;
				return apply(INVOICE, body as ApplyBody);
			}
			return undefined;
		}
	});
	await open(page, '/entry/e-1/review');

	const warning = page.getByRole('alert').filter({ hasText: 'nicht berechnen' });
	await expect(warning).toBeVisible();
	await warning.getByRole('button', { name: 'Noch einmal versuchen' }).click();
	await expect(warning).toHaveCount(0);
	await expect(
		page
			.getByRole('region', { name: 'Dokument' })
			.getByRole('button', { name: 'Person: Sabine Hofer' })
	).toContainText('<<PERSON_');
	expect(capture.requests.filter((a) => a.path === '/jobs/j-1/apply')).toHaveLength(2);
});

/* -- Saving and saved ----------------------------------------------------- */

const IRREVERSIBLE = {
	method: 'irreversible',
	style: 'placeholder',
	categories: ['finance_ids', 'names', 'addresses', 'contact', 'numbers'],
	anonymize: false
};

function savedFile(file: File, method: 'reversible' | 'irreversible') {
	// Scans and images keep their original format.
	const point = file.name.lastIndexOf('.');
	const extension = file.name.slice(point);
	const label = method === 'reversible' ? 'reversible' : 'irreversible';
	return {
		...entryFile(file),
		label,
		output_path: `/Users/test/Dokumente/Kunden/geschützt/${file.name.slice(0, point)}_geschützt${extension}`,
		output: {
			status: 'saved',
			name: `${file.name.slice(0, point)}_geschützt${extension}`,
			format: extension.slice(1),
			sha256: 'a'.repeat(64),
			size_bytes: 1000,
			from_scan: false,
			label,
			masked: file.match.length,
			decided_by_user: file === INVOICE ? 2 : 0,
			decided_by_ai: 0,
			masked_without_decision: file === INVOICE ? 1 : 0,
			residual_risk: 'not_run',
			audit_id: 1
		}
	};
}

function savedEntry(method: 'reversible' | 'irreversible') {
	return {
		...ENTRY,
		status: 'saved',
		masking: method === 'reversible' ? JOB : IRREVERSIBLE,
		saved_at: new Date().toISOString(),
		output_dir: '/Users/test/Dokumente/Kunden/geschützt',
		irreversible_confirmed: method === 'irreversible',
		files: FILES.map((file) => savedFile(file, method))
	};
}

function report(method: 'reversible' | 'irreversible') {
	const entrySaved = savedEntry(method);
	return {
		entry_id: 'e-1',
		title: ENTRY.title,
		created_at: ENTRY.created_at,
		saved_at: entrySaved.saved_at,
		masking: entrySaved.masking,
		protection_level: 2,
		app_version: '0.1.0',
		profile_version: 'grundprofil@1.0.0',
		files: entrySaved.files.map((file) => ({
			id: file.id,
			name: file.output.name,
			format: file.format,
			pages: file.pages,
			ocr_used: file.ocr_used,
			source_changed: false,
			output: file.output
		})),
		totals: { status: 'saved', masked: 11, decided_by_user: 2, masked_without_decision: 1 }
	};
}

async function saveHarness(
	context: Parameters<typeof harness>[0],
	method: 'reversible' | 'irreversible'
) {
	const status = { saved: false };
	const start = { ...ENTRY, masking: method === 'reversible' ? JOB : IRREVERSIBLE };
	const result = await harness(context, {
		entries: () => [status.saved ? savedEntry(method) : start],
		save: {
			entry: savedEntry(method),
			directory: '/Users/test/Dokumente/Kunden/geschützt',
			files: [],
			unchanged: [],
			committed_mappings: method === 'reversible' ? 9 : 0
		},
		sidecar: (method_, path, body) => {
			if (path === '/entries/e-1' && method_ === 'GET') {
				return status.saved ? savedEntry(method) : start;
			}
			if (path === '/entries/e-1/progress') return progressResponse(() => 'review');
			if (path === '/entries/e-1/report') return report(method);
			if (path === '/restore/text') {
				return {
					offset_encoding: 'utf-16',
					text: 'Liebe Frau Sabine Hofer, danke.',
					spans: [
						{
							start: 11,
							end: 23,
							status: 'restored',
							kind: 'code',
							entity_type: 'PERSON',
							tolerant: false,
							origin: 'here',
							entry_id: 'e-1'
						}
					],
					counts: {
						restored: 1,
						here: 1,
						other: 0,
						earlier: 0,
						unknown: 0,
						unknown_unique: 0,
						tolerant: 0
					},
					origins: [],
					recorded: false
				};
			}
			const file = FILES.find((kandidat) => path.startsWith(`/jobs/${kandidat.id}/`));
			if (file && path.endsWith('/findings')) return findings(file);
			if (file && path.endsWith('/apply')) return apply(file, body as ApplyBody);
			return undefined;
		}
	});
	return { ...result, status };
}

function storageCall(capture: Capture) {
	return capture.commands.find((command) => command.command === 'save_entry')?.args as
		| {
				entryId: string;
				folderName: string;
				choose: boolean;
				lastDirectory: string | null;
				request: {
					masking: { method: string };
					files: { file_id: string; review: { decisions?: unknown[] } }[];
					confirm_irreversible: boolean;
				};
		  }
		| undefined;
}

test('Milestone F: irreversible save with confirmation, saved, report, folder', async ({
	context,
	page
}) => {
	const { capture, status } = await saveHarness(context, 'irreversible');
	await open(page, '/entry/e-1/review');

	const bar = page.getByRole('complementary', { name: 'Prüfen' });
	await bar
		.locator('[data-question="f_wachtel"]')
		.getByRole('button', { name: 'Behalten' })
		.click();
	await bar.getByRole('button', { name: 'Alle 3 Dateien speichern' }).click();

	// Once per entry a compact confirmation that states the consequences.
	const followUp = page.getByRole('alertdialog', { name: 'Irreversibel speichern?' });
	await expect(followUp).toContainText('Die 3 Dateien werden ohne Rückweg maskiert.');
	await expect(followUp).toContainText('Die Originale bleiben unverändert.');
	await expect(followUp.getByRole('button', { name: 'Abbrechen' })).toBeFocused();
	expect(storageCall(capture)).toBeUndefined();
	status.saved = true;
	await followUp.getByRole('button', { name: 'Irreversibel speichern' }).click();

	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1`);
	const call = storageCall(capture);
	expect(call?.entryId).toBe('e-1');
	expect(call?.folderName).toBe('geschützt');
	expect(call?.choose).toBe(false);
	expect(call?.request.confirm_irreversible).toBe(true);
	expect(call?.request.masking.method).toBe('irreversible');
	// Final state shown by the window: review state per file.
	expect(call?.request.files.map((file) => file.file_id)).toEqual(['j-1', 'j-2', 'j-3']);
	expect(call?.request.files[0].review.decisions).toContainEqual({
		finding_id: 'f_wachtel',
		action: 'reject',
		by: 'user'
	});

	await expect(
		page.getByRole('heading', { name: '3 Dateien gespeichert', level: 1 })
	).toBeVisible();
	await expect(page.getByText('Dokumente › Kunden › geschützt')).toBeVisible();
	await expect(page.getByText('Originale unverändert')).toBeVisible();
	const list = page.getByRole('list', { name: 'Gespeicherte Dateien' });
	await expect(list.getByRole('listitem')).toHaveCount(3);
	await expect(list).toContainText('Rechnung_2026-0418_geschützt.pdf');
	await expect(list).toContainText('Irreversibel maskiert');
	await expect(page.getByText('11 Stellen maskiert · 2 von Ihnen entschieden')).toBeVisible();
	await expect(
		page.getByRole('heading', { name: 'Irreversibel maskiert – nicht wiederherstellbar' })
	).toBeVisible();
	await expect(
		page.getByRole('heading', { name: 'Antwort einer KI zurückübersetzen' })
	).toHaveCount(0);

	await page.getByRole('button', { name: 'Prüfbericht speichern' }).click();
	await expect(page.getByText('Prüfbericht gespeichert.')).toBeVisible();
	const reportCall = capture.commands.find((command) => command.command === 'save_text_file');
	const content = String(reportCall?.args.content);
	expect(reportCall?.args.name).toBe('Prüfbericht – Rechnung + 2 weitere.txt');
	expect(content).toContain('Irreversibel maskiert (Schwärzung – noch nicht anonym)');
	expect(content).toContain('Maskierte Stellen: 11');
	expect(content).not.toContain('Sabine Hofer');

	await page.getByRole('button', { name: 'Ordner öffnen' }).click();
	await expect
		.poll(() => capture.commands.find((command) => command.command === 'reveal_export')?.args)
		.toEqual({ path: '/Users/test/Dokumente/Kunden/geschützt' });

	// The history marks the entry as completed; the label stays in the “Saved” view.
	await expect(page.getByRole('region', { name: 'Zuletzt erstellt' })).toContainText('3 Dateien');
	await expect(page.getByRole('link', { name: 'Abgeschlossen, 1 Eintrag' })).toBeVisible();
});

test('reversible save to another folder, then restore a KI answer', async ({ context, page }) => {
	const { capture, status } = await saveHarness(context, 'reversible');
	await open(page, '/entry/e-1/review');

	status.saved = true;
	await page.getByRole('button', { name: 'In anderen Ordner speichern …' }).click();
	// Reversible does not need a confirmation dialog.
	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1`);
	expect(page.getByRole('alertdialog')).toHaveCount(0);
	expect(storageCall(capture)?.choose).toBe(true);
	expect(storageCall(capture)?.request.confirm_irreversible).toBe(false);

	const list = page.getByRole('list', { name: 'Gespeicherte Dateien' });
	await expect(list).toContainText('Vollmacht_Scan_geschützt.jpg');
	await expect(list).not.toContainText('Text aus dem Bild');
	await expect(list).toContainText('Reversibel maskiert');
	// Board “Done” (saved, reversible), light and dark.
	await board(page, '4-saved');
	await list.getByRole('link', { name: 'Bearbeiten' }).nth(1).click();
	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1/review?file=2`);
	await page.goBack();

	const map = page.getByRole('region', { name: 'Antwort einer KI zurückübersetzen' });
	await expect(map).toContainText('Jetzt möglich');
	await expect(map).toContainText('Oder eine Datei von der KI hierher ziehen');
	const restore = map.getByRole('button', { name: 'Namen wiederherstellen' });
	await expect(restore).toBeDisabled();
	await map.getByRole('button', { name: 'Aus Zwischenablage einfügen' }).click();
	await expect(map.getByLabel('Antwort der KI')).toHaveValue(/Josef Brandstetter/);
	await map.getByLabel('Antwort der KI').fill('Liebe <<PERSON_ab12cd34>>, danke.');
	await restore.click();

	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1/restore`);
	await expect(page.getByText('Liebe Frau Sabine Hofer, danke.')).toBeVisible();
	await expect(page.getByRole('status', { name: 'Zählung' })).toContainText('1 von hier');
	const request = capture.requests.find((entry_) => entry_.path === '/restore/text');
	expect(request?.body).toEqual({
		text: 'Liebe <<PERSON_ab12cd34>>, danke.',
		entry_id: 'e-1',
		surrogates: false
	});
	// The answer never appears in the URL.
	expect(page.url()).not.toContain('PERSON');
});

/* -- spec §6 -------------------------------- */

test('draft: after a restart “Edit again” opens without a dialog', async ({ context, page }) => {
	// The sidecar was restarted: it loads the saved entry from the draft, no file is marked `expired`.
	const { capture, status } = await saveHarness(context, 'reversible');
	status.saved = true;
	await open(page, '/entry/e-1');
	await expect(
		page.getByRole('heading', { name: '3 Dateien gespeichert', level: 1 })
	).toBeVisible();

	await page.getByRole('button', { name: 'Nochmal bearbeiten' }).click();
	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1/review`);
	await expect(page.getByRole('complementary', { name: 'Prüfen' })).toBeVisible();
	await expect
		.poll(() => capture.requests.some((request) => request.path === '/jobs/j-1/findings'))
		.toBe(true);

	// No file dialog, no reloading of originals.
	const commands = capture.commands.map((command) => command.command);
	expect(commands).not.toContain('reload_entry_files');
	expect(commands).not.toContain('pick_inputs');
	await expect(page.getByRole('button', { name: 'Neu einlesen' })).toHaveCount(0);
	await expect(page.getByText('nicht mehr geladen')).toHaveCount(0);
});

test('draft: an unreadable copy offers “Select original”', async ({ context, page }) => {
	const unreadable = { ...entryFile(INVOICE), status: 'unavailable' };
	const start = { ...ENTRY, files: [unreadable, ...ENTRY.files.slice(1)] };
	const { capture } = await harness(context, {
		entries: () => [start],
		sidecar: (method, path, body) => {
			if (path === '/entries/e-1' && method === 'GET') return start;
			if (path === '/entries/e-1/progress') {
				return progressResponse((file) => (file === INVOICE ? 'unavailable' : 'review'));
			}
			const file = FILES.find((kandidat) => path.startsWith(`/jobs/${kandidat.id}/`));
			if (file && path.endsWith('/findings')) return findings(file);
			if (file && path.endsWith('/apply')) return apply(file, body as ApplyBody);
			return undefined;
		}
	});
	// Without a selection the first reviewable file opens; the unreadable one is first.
	await open(page, '/entry/e-1/review?file=1');

	await expect(
		page.getByText('Die gespeicherte Kopie dieser Datei ist nicht mehr lesbar.')
	).toBeVisible();
	const bar = page.getByRole('complementary', { name: 'Prüfen' });
	await expect(bar).toContainText(
		'Erst für die nicht mehr lesbaren Dateien das Original auswählen.'
	);
	await page.getByRole('button', { name: 'Original auswählen' }).click();
	await expect
		.poll(() => capture.commands.find((command) => command.command === 'reload_entry_files')?.args)
		.toMatchObject({ entryId: 'e-1', files: [{ id: 'j-1', name: INVOICE.name }] });
});

for (const fileStatus of ['failed', 'cancelled'] as const) {
	for (const route of ['', '/review']) {
		test(`AS2: ${fileStatus} survives restart and retries from the stored original (${route || 'board'})`, async ({
			context,
			page
		}) => {
			let retried = false;
			const currentEntry = () => ({
				...ENTRY,
				files: [
					{
						...entryFile(INVOICE, retried ? 'review' : fileStatus),
						error_code: !retried && fileStatus === 'failed' ? 'ocr_unavailable' : null
					},
					...ENTRY.files.slice(1)
				]
			});
			const { capture } = await harness(context, {
				entries: () => [currentEntry()],
				sidecar: (method, path, body) => {
					if (path === '/entries/e-1' && method === 'GET') return currentEntry();
					if (path === '/entries/e-1/progress') {
						const progress = progressResponse((file) =>
							file === INVOICE && !retried ? fileStatus : 'review'
						);
						return {
							...progress,
							files: progress.files.map((file) => ({
								...file,
								error_code:
									file.id === INVOICE.id && !retried && fileStatus === 'failed'
										? 'ocr_unavailable'
										: null
							}))
						};
					}
					if (path === '/entries/e-1/files/j-1/retry' && method === 'POST') {
						retried = true;
						return { entry: currentEntry(), file: currentEntry().files[0] };
					}
					const file = FILES.find((candidate) => path.startsWith(`/jobs/${candidate.id}/`));
					if (file && path.endsWith('/findings')) return findings(file);
					if (file && path.endsWith('/apply')) return apply(file, body as ApplyBody);
					return undefined;
				}
			});
			await open(page, `/entry/e-1${route}${route ? '?file=1' : '?overview=1'}`);
			const retry = page.getByRole('button', { name: 'Erneut versuchen', exact: true });
			await expect(retry).toBeVisible();
			const errorText =
				fileStatus === 'failed' ? 'Für Scans fehlt gerade die Texterkennung.' : 'abgebrochen';
			await expect(page.getByText(errorText, { exact: false }).first()).toBeVisible();

			// A new document recreates component state, like reopening after an app restart.
			await page.reload();
			await expect(retry).toBeVisible();
			await expect(page.getByText(errorText, { exact: false }).first()).toBeVisible();
			await retry.click();
			await expect(retry).toHaveCount(0);
			expect(capture.requests.filter((request) => request.path.endsWith('/retry'))).toHaveLength(1);
			expect(capture.commands.map((command) => command.command)).not.toContain('pick_inputs');
			expect(capture.commands.map((command) => command.command)).not.toContain(
				'reload_entry_files'
			);
		});
	}
}

for (const route of ['', '/review']) {
	test(`AS2: unreadable stored copies can be removed (${route || 'board'})`, async ({
		context,
		page
	}) => {
		let removed = false;
		const currentEntry = () => ({
			...ENTRY,
			files: removed
				? ENTRY.files.slice(1)
				: [{ ...entryFile(INVOICE), status: 'unavailable' }, ...ENTRY.files.slice(1)]
		});
		const { capture } = await harness(context, {
			entries: () => [currentEntry()],
			sidecar: (method, path, body) => {
				if (path === '/entries/e-1' && method === 'GET') return currentEntry();
				if (path === '/entries/e-1/progress') {
					return progressResponse((file) =>
						file === INVOICE && !removed ? 'unavailable' : 'review'
					);
				}
				if (path === '/entries/e-1/files/j-1' && method === 'DELETE') {
					removed = true;
					return currentEntry();
				}
				const file = FILES.find((candidate) => path.startsWith(`/jobs/${candidate.id}/`));
				if (file && path.endsWith('/findings')) return findings(file);
				if (file && path.endsWith('/apply')) return apply(file, body as ApplyBody);
				return undefined;
			}
		});
		await open(page, `/entry/e-1${route}${route ? '?file=1' : '?overview=1'}`);
		await expect(
			page.getByText('Die gespeicherte Kopie dieser Datei ist nicht mehr lesbar.', { exact: false })
		).toBeVisible();
		await expect(
			page.getByRole('button', { name: 'Original auswählen', exact: true })
		).toBeVisible();
		await page.getByRole('button', { name: 'Datei entfernen', exact: true }).click();
		await expect(page.getByRole('button', { name: 'Datei entfernen', exact: true })).toHaveCount(0);
		expect(
			capture.requests
				.filter((request) => request.method === 'DELETE')
				.map((request) => request.path)
		).toEqual(['/entries/e-1/files/j-1']);
	});
}

test('AS2: removing the last unreadable copy leaves a usable empty entry', async ({
	context,
	page
}) => {
	let removed = false;
	const currentEntry = () => ({
		...ENTRY,
		files: removed ? [] : [{ ...entryFile(INVOICE), status: 'unavailable' }]
	});
	await harness(context, {
		entries: () => [currentEntry()],
		sidecar: (method, path) => {
			if (path === '/entries/e-1' && method === 'GET') return currentEntry();
			if (path === '/entries/e-1/progress')
				return { files: [], total: 0, done: 0, ready: false, totals: {} };
			if (path === '/entries/e-1/files/j-1' && method === 'DELETE') {
				removed = true;
				return currentEntry();
			}
			return undefined;
		}
	});
	await open(page, '/entry/e-1/review');
	await page.getByRole('button', { name: 'Datei entfernen', exact: true }).click();
	await expect(
		page
			.getByRole('tabpanel')
			.getByRole('button', { name: 'Weitere Dokumente hinzufügen', exact: true })
	).toBeVisible();
	await expect(page.getByRole('tab')).toHaveCount(0);
});

test('AS2: reopening restores the second file, print layout, page three and the last decision', async ({
	context,
	page
}) => {
	const secondFile: File = {
		...OFFER,
		text: 'Erste Seite.\n\nZweite Seite.\n\nDritte Seite mit Sabine Hofer und Muster.',
		match: [
			{ id: 'f_a_hofer', value: 'Sabine Hofer', type: 'PERSON', category: 'names' },
			{
				id: 'f_second_question',
				value: 'Muster',
				type: 'PERSON',
				category: 'names',
				question: { kind: 'uncertain', n: 1 }
			}
		]
	};
	const files = [INVOICE, secondFile];
	const storedReview: Record<string, ApplyBody['review']> = {};
	let storedView: EntryView | null = null;
	const currentEntry = () => ({
		...ENTRY,
		files: files.map((file) => entryFile(file)),
		review: storedReview
	});
	const pageImage = readFileSync(
		resolve(import.meta.dirname, 'fixtures/layout/letter-page-faithful-original-0.png')
	);
	const { capture } = await harness(context, {
		entries: () => [currentEntry()],
		sidecar: (method, path, body, query) => {
			if (path === '/entries/e-1' && method === 'GET') return currentEntry();
			if (path === '/entries/e-1/view') {
				if (method === 'PUT') storedView = body as EntryView;
				return storedView;
			}
			if (path === '/entries/e-1/progress') {
				return {
					...progressResponse(() => 'review'),
					files: [],
					total: files.length,
					done: files.length
				};
			}
			const file = files.find((candidate) => path.startsWith(`/jobs/${candidate.id}/`));
			if (!file) return undefined;
			if (path.endsWith('/findings')) return findings(file);
			if (path.endsWith('/apply')) {
				const request = body as ApplyBody;
				storedReview[file.id] = request.review;
				return apply(file, request);
			}
			if (path.endsWith('/layout')) {
				return file === secondFile
					? {
							job_id: file.id,
							kind: 'flow',
							format: 'docx',
							units: [{ anchor: 'unn_u0', start: 0, end: file.text.length }],
							paged: true
						}
					: { job_id: file.id, kind: 'text' };
			}
			if (path.endsWith('/layout/paged')) {
				const side = new URLSearchParams(query).get('side') === 'original' ? 'original' : 'result';
				const paragraphs = file.text.split('\n\n');
				let start = 0;
				const words = paragraphs.map((paragraph, index) => {
					const word = [index, 60, 60, 500, 76, start, start + paragraph.length, index];
					start += paragraph.length + 2;
					return word;
				});
				return {
					job_id: file.id,
					side,
					revision: side === 'original' ? 0 : 1,
					engine: { name: 'docxide-pdf', version: '0.20.1' },
					offset_encoding: 'utf-16',
					pages: paragraphs.map((_, index) => ({
						index,
						width: 612,
						height: 792,
						unit: 'pt',
						source: 'text',
						scales: [1, 1.5, 2, 3]
					})),
					words,
					unplaced: []
				};
			}
			if (path.includes('/layout/paged/')) return new Roh(pageImage, 'image/png');
			return undefined;
		}
	});
	await open(page, '/entry/e-1/review');
	await page
		.locator('[data-question="f_wachtel"]')
		.getByRole('button', { name: 'Behalten', exact: true })
		.click();
	await page.getByRole('tab', { name: /Angebot.docx/ }).click();
	await page.getByRole('radio', { name: 'Seitentreu', exact: true }).click();
	await page.getByRole('radio', { name: 'Original', exact: true }).click();
	const pageThree = page.locator('article[data-column="pages"]').last().locator('[data-page="2"]');
	await expect(pageThree).toBeVisible();
	await pageThree.evaluate((element) => element.scrollIntoView({ block: 'start' }));
	await expect
		.poll(() => storedView)
		.toMatchObject({ active_file_id: secondFile.id, representation: 'faithful', page: 3 });

	// Close immediately after deciding. The native close guard drains Apply and view writes.
	await page
		.locator('[data-question="f_second_question"]')
		.getByRole('button', { name: 'Behalten', exact: true })
		.click();
	await page.evaluate(() => {
		const runtime = window as unknown as { __EMIT__: (event: string, payload: unknown) => void };
		runtime.__EMIT__('draft://flush', null);
	});
	await expect
		.poll(
			() => capture.commands.findLast((command) => command.command === 'finish_entry_flush')?.args
		)
		.toEqual({ success: true });
	const finalView = storedView;
	expect(storedReview[secondFile.id]?.decisions).toContainEqual({
		finding_id: 'f_second_question',
		action: 'reject',
		by: 'user'
	});
	const countsBefore = await page.getByRole('complementary', { name: 'Prüfen' }).innerText();
	await page.close();

	const reopened = await context.newPage();
	await open(reopened, '/entry/e-1/review');
	await expect(reopened.getByRole('tab', { selected: true })).toContainText(secondFile.name);
	await expect(reopened.getByRole('radio', { name: 'Seitentreu', exact: true })).toBeChecked();
	await expect(reopened.getByRole('radio', { name: 'Original', exact: true })).toBeChecked();
	await expect(reopened.locator('[data-question="f_second_question"] button')).toBeFocused();
	await expect(reopened.getByRole('complementary', { name: 'Prüfen' })).toHaveText(countsBefore, {
		useInnerText: true
	});
	await expect
		.poll(() =>
			reopened
				.locator('article[data-column="pages"]')
				.last()
				.locator('[data-page="2"]')
				.evaluate((element) => element.getBoundingClientRect().top)
		)
		.toBeLessThan(400);
	expect(storedView).toEqual(finalView);
	expect(capture.commands.map((command) => command.command)).not.toContain('reload_entry_files');
});

/* -- Continue work after saving (state AS3) ------------------- */

const ANONYMIZE = {
	method: 'irreversible',
	style: 'placeholder',
	categories: ['finance_ids', 'names', 'addresses', 'contact', 'numbers'],
	anonymize: true
};

/** Two saves: first anonymized in “alt”, then a reversible one. */
function entryWithExports(part: Record<string, unknown> = {}) {
	const saved = savedEntry('reversible');
	const earlier = '2026-11-14T08:00:00.000Z';
	return {
		...saved,
		draft_revision: 7,
		saved_revision: 6,
		exports: [
			{
				id: 'x-1',
				saved_at: earlier,
				masking: ANONYMIZE,
				directory: '/Users/test/Dokumente/Kunden/alt',
				revision: 3,
				files: saved.files.map((file) => ({
					file_id: file.id,
					name: file.name,
					format: file.format,
					path: `/Users/test/Dokumente/Kunden/alt/${file.output.name}`,
					output: { ...file.output, label: 'irreversible' }
				}))
			},
			{
				id: 'x-2',
				saved_at: saved.saved_at,
				masking: JOB,
				directory: saved.output_dir,
				revision: 6,
				files: saved.files.map((file) => ({
					file_id: file.id,
					name: file.name,
					format: file.format,
					path: file.output_path,
					output: file.output
				}))
			}
		],
		...part
	};
}

test('AS3: Saved view shows earlier saves with folder and report', async ({ context, page }) => {
	const saved = entryWithExports();
	const { capture } = await harness(context, {
		entries: () => [saved],
		sidecar: (method, path) => {
			if (path === '/entries/e-1' && method === 'GET') return saved;
			if (path === '/entries/e-1/progress') return progressResponse(() => 'review');
			if (path === '/entries/e-1/report') return report('reversible');
			return undefined;
		}
	});
	await open(page, '/entry/e-1');

	await expect(
		page.getByRole('heading', { name: '3 Dateien gespeichert', level: 1 })
	).toBeVisible();
	await expect(page.getByRole('note')).toContainText('Seit dem Speichern geändert');
	const earlier = page.getByRole('region', { name: 'Frühere Speicherungen' });
	await expect(earlier.getByRole('listitem')).toHaveCount(1);
	await expect(earlier).toContainText('09:00 · Anonymisieren · 3 Dateien');
	await expect(earlier).toContainText('Dokumente › Kunden › alt');
	// The last save was reversible: restore remains.
	await expect(
		page.getByRole('region', { name: 'Antwort einer KI zurückübersetzen' })
	).toBeVisible();

	await earlier.getByRole('button', { name: 'Ordner öffnen' }).click();
	await expect
		.poll(() => capture.commands.find((command) => command.command === 'reveal_export')?.args)
		.toEqual({ path: '/Users/test/Dokumente/Kunden/alt' });

	await earlier.getByRole('button', { name: 'Prüfbericht' }).click();
	await expect(page.getByText('Prüfbericht gespeichert.')).toBeVisible();
	expect(capture.requests.find((request) => request.path === '/entries/e-1/report')?.search).toBe(
		'?export_id=x-1'
	);

	// History indicates further work and shows location and deletion date in details.
	await page.getByRole('link', { name: 'Offen, 1 Eintrag' }).click();
	const line = page.getByRole('table').getByRole('row').nth(1);
	await expect(line).toContainText('Seit dem Speichern geändert');
	await expect(line).toContainText('3 Dateien');
	await line.getByRole('button', { name: /^Details zu/ }).click();
	await expect(page.getByRole('dialog', { name: /^Details zu/ })).toContainText(
		/1,2 MB gespeichert · wird am .+ gelöscht/
	);
});

test('AS3: "Save again" prompts again after method change (E4)', async ({ context, page }) => {
	let confirmedFor = 'irreversible:placeholder:-';
	const saved = () => ({
		...entryWithExports(),
		masking: IRREVERSIBLE,
		irreversible_confirmed_for: confirmedFor
	});
	const { capture } = await harness(context, {
		entries: () => [saved()],
		save: {
			entry: saved(),
			directory: '/Users/test/Dokumente/Kunden/geschützt',
			files: [],
			unchanged: [],
			committed_mappings: 0
		},
		sidecar: (method, path, body) => {
			if (path === '/entries/e-1' && method === 'GET') return saved();
			if (path === '/entries/e-1/progress') return progressResponse(() => 'review');
			const file = FILES.find((kandidat) => path.startsWith(`/jobs/${kandidat.id}/`));
			if (file && path.endsWith('/findings')) return findings(file);
			if (file && path.endsWith('/apply')) return apply(file, body as ApplyBody);
			return undefined;
		}
	});
	await open(page, '/entry/e-1/review');
	const bar = page.getByRole('complementary', { name: 'Prüfen' });

	// Already confirmed for this method: no prompt.
	await bar.getByRole('button', { name: 'Erneut speichern' }).click();
	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1`);
	expect(page.getByRole('alertdialog')).toHaveCount(0);
	expect(storageCall(capture)?.request.confirm_irreversible).toBe(false);
	// After restart the folder dialog starts at the last saved directory.
	expect(storageCall(capture)?.lastDirectory).toBe('/Users/test/Dokumente/Kunden/geschützt');

	// A different style was confirmed earlier: the prompt appears again.
	confirmedFor = 'irreversible:mask:-';
	capture.commands.length = 0;
	await open(page, '/entry/e-1/review');
	await bar.getByRole('button', { name: 'Erneut speichern' }).click();
	await expect(page.getByRole('alertdialog', { name: 'Irreversibel speichern?' })).toBeVisible();
	expect(storageCall(capture)).toBeUndefined();
});

test('AS3: "Redetect" is offered when detection no longer fits', async ({ context, page }) => {
	const saved = savedEntry('reversible');
	let fresh = false;
	const { capture } = await harness(context, {
		entries: () => [saved],
		sidecar: (method, path, body) => {
			if (path === '/entries/e-1' && method === 'GET') return saved;
			if (path === '/entries/e-1/progress') return progressResponse(() => 'review');
			if (path === '/entries/e-1/redetect' && method === 'GET') {
				return {
					entry_id: 'e-1',
					files: fresh ? [] : [{ file_id: 'j-1', changes: ['languages', 'ner'], reextract: false }]
				};
			}
			if (path === '/entries/e-1/redetect' && method === 'POST') {
				fresh = true;
				return {
					entry: saved,
					files: [{ file_id: 'j-1', changes: ['languages', 'ner'], reextract: false }]
				};
			}
			const file = FILES.find((kandidat) => path.startsWith(`/jobs/${kandidat.id}/`));
			if (file && path.endsWith('/findings')) return findings(file);
			if (file && path.endsWith('/apply')) return apply(file, body as ApplyBody);
			return undefined;
		}
	});
	await open(page, '/entry/e-1/review');

	const notice = page.getByRole('note').filter({ hasText: 'Neu erkennen nutzt' });
	await expect(notice).toContainText('(Dokumentsprachen, Erkennungsmodell)');
	// Reopening never triggers automatic re-detection.
	expect(
		capture.requests.some(
			(request) => request.method === 'POST' && request.path.endsWith('/redetect')
		)
	).toBe(false);
	await notice.getByRole('button', { name: 'Neu erkennen' }).click();
	await expect(notice).toHaveCount(0);
	const job = capture.requests.find(
		(request) => request.method === 'POST' && request.path === '/entries/e-1/redetect'
	);
	expect(job?.body).toEqual({ file_ids: ['j-1'] });
	expect(capture.commands.map((command) => command.command)).not.toContain('pick_inputs');
});
