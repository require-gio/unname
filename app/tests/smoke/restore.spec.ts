import { expect, test, type Page } from '@playwright/test';
import {
	APP_ORIGIN,
	JOB,
	board,
	entry,
	harness,
	open,
	WITHOUT_CONTENT,
	lastCopy,
	type Capture
} from './harness.js';

/**
 * Restore step.
 *
 * The mock sidecar behaves like `restore.py`: known codes from two entries are
 * replaced, a code from another device stays unchanged and is reported. The test
 * checks what the view does with this, what it sends to the sidecar, and that it
 * does not store anything before the user copies or saves.
 */

const HERE = 'e-1';
const MIETE = 'e-2';

/** Mapping table: code → clear value and entry. */
const TABLE: Record<string, { value: string; entry: string }> = {
	'<<PERSON_51c2e8d4>>': { value: 'Hofer', entry: HERE },
	'<<CUSTOMER_NUMBER_9d03b7aa>>': { value: 'K-4711', entry: HERE },
	'<<PERSON_6a1f0e93>>': { value: 'Renate Wimmer', entry: MIETE }
};
const UNKNOWN = '<<PERSON_8e21d7f0>>';

const ANSWER = [
	'Sehr geehrte Frau <<PERSON_51c2e8d4>>,',
	'',
	'zur Kundennummer <<CUSTOMER_NUMBER_9d03b7aa>> … Die Kaution aus dem Mietvertrag mit <<PERSON_6a1f0e93>> ist nicht betroffen.',
	'',
	`PS: Bitte auch ${UNKNOWN} in Kopie setzen.`
].join('\n');
const WITH_NAMES = [
	'Sehr geehrte Frau Hofer,',
	'',
	'zur Kundennummer K-4711 … Die Kaution aus dem Mietvertrag mit Renate Wimmer ist nicht betroffen.',
	'',
	`PS: Bitte auch ${UNKNOWN} in Kopie setzen.`
].join('\n');

function restore(text: string) {
	const spans = [];
	let output = '';
	let cursor = 0;
	const counter = { restored: 0, here: 0, other: 0, unknown: 0 };
	for (const match of text.matchAll(/<<([A-Z_]+?)_([0-9a-f]{8})>>/g)) {
		const code = match[0];
		const known = TABLE[code];
		output += text.slice(cursor, match.index);
		const start = output.length;
		output += known?.value ?? code;
		const here = known?.entry === HERE;
		spans.push({
			start,
			end: output.length,
			status: known ? 'restored' : 'unknown',
			kind: 'code',
			entity_type: match[1],
			tolerant: false,
			origin: known ? (here ? 'here' : 'entry') : null,
			entry_id: known?.entry ?? null
		});
		if (known) {
			counter.restored += 1;
			counter[here ? 'here' : 'other'] += 1;
		} else counter.unknown += 1;
		cursor = match.index + code.length;
	}
	output += text.slice(cursor);
	return {
		offset_encoding: 'utf-16',
		text: output,
		spans,
		counts: {
			...counter,
			earlier: 0,
			unknown_unique: counter.unknown,
			tolerant: 0
		},
		origins: counter.other
			? [{ entry_id: MIETE, title: 'Mietvertrag (Scan)', count: counter.other }]
			: [],
		recorded: false
	};
}

const SAVED = entry({
	id: HERE,
	title: 'Rechnung & Angebot',
	status: 'saved',
	masking: JOB,
	saved_at: new Date().toISOString(),
	output_dir: '/Users/test/Dokumente/Kunden/geschützt',
	files: [
		{
			id: 'j-1',
			name: 'Rechnung.pdf',
			format: 'pdf',
			size_bytes: 1000,
			status: 'review',
			label: 'reversible',
			output: { status: 'saved', name: 'Rechnung_geschützt.pdf', label: 'reversible', masked: 3 }
		}
	]
});

const FILE_ANSWER = {
	source: { name: 'Zahlungserinnerung_Entwurf.docx', format: 'docx', size_bytes: 24 * 1024 },
	output: {
		id: 'r1',
		name: 'Zahlungserinnerung_Entwurf_mit_Namen.docx',
		format: 'docx',
		sha256: 'a'.repeat(64),
		size_bytes: 25 * 1024
	},
	preview: { ...restore(ANSWER), truncated: false },
	counts: { ...restore(ANSWER).counts },
	origins: restore(ANSWER).origins,
	unreplaced: 0,
	skipped: 0
};

async function restoreHarness(context: Parameters<typeof harness>[0]) {
	return harness(context, {
		entries: () => [SAVED, entry({ id: MIETE, title: 'Mietvertrag (Scan)', status: 'saved' })],
		restore: FILE_ANSWER,
		sidecar: (method, path, requestBody) => {
			if (path === `/entries/${HERE}` && method === 'GET') return SAVED;
			if (path === `/entries/${HERE}/progress`) {
				return {
					entry_id: HERE,
					status: 'saved',
					files: [],
					totals: {},
					done: 1,
					total: 1,
					ready: true
				};
			}
			if (path === '/restore/text') {
				const body = requestBody as { text: string; record?: boolean };
				return { ...restore(body.text), recorded: body.record === true };
			}
			if (path === '/restore/file/r1' && method === 'DELETE') return WITHOUT_CONTENT;
			return undefined;
		}
	});
}

const backRequests = (capture: Capture) =>
	capture.requests.filter((request) => request.path === '/restore/text');

function output(page: Page) {
	return page.getByRole('region', { name: 'Mit echten Namen' });
}

test('Milestone G: AI answer with codes from two entries, unknown code, nothing saved', async ({
	context,
	page
}) => {
	const { capture } = await restoreHarness(context);
	await open(page, `/entry/${HERE}/restore`);

	const navigation = page.getByRole('navigation', { name: 'Navigation' });
	await expect(navigation.getByRole('link', { name: 'Übersicht' })).toHaveAttribute(
		'href',
		`/entry/${HERE}`
	);
	await expect(navigation).toContainText('Rechnung & Angebot');
	await expect(navigation.locator('[aria-current="page"]')).toHaveText('Antwort zurückübersetzt');
	await expect(
		page.getByRole('heading', { name: 'Antwort zurückübersetzt', level: 1 })
	).toBeVisible();

	await page.getByRole('textbox', { name: 'Antwort der KI' }).fill(ANSWER);
	await expect(output(page)).toContainText('Sehr geehrte Frau Hofer,');
	await expect(output(page)).toContainText('Renate Wimmer');
	await expect(output(page)).toContainText(UNKNOWN);

	const count = page.getByRole('status', { name: 'Zählung' });
	await expect(count).toContainText('3 Codes ersetzt');
	await expect(count).toContainText('2 von hier');
	await expect(count).toContainText('1 aus »Mietvertrag (Scan)«');
	await expect(count).toContainText(
		'1 unbekannt – nicht in der Übersetzungstabelle: anderes Gerät oder älterer Schlüssel'
	);
	// Show board for restore step 5.
	await board(page, '5-restore');

	// Highlight used locations: from here Indigo, from other entry Green.
	const hofer = output(page).locator('span', { hasText: /^Hofer$/ });
	const wimmer = output(page).locator('span', { hasText: /^Renate Wimmer$/ });
	await expect(hofer).toHaveClass(/tint-5/);
	await expect(wimmer).toHaveClass(/green-2/);
	await output(page).getByRole('switch', { name: 'Eingesetzte Stellen markieren' }).click();
	await expect(hofer).not.toHaveClass(/tint-5/);
	// Unknown code stays visible.
	await expect(output(page).locator('span', { hasText: new RegExp(`^${UNKNOWN}$`) })).toHaveClass(
		/amber/
	);

	// Up to here only restored, not counted, not saved.
	const live = backRequests(capture);
	expect(live.at(-1)?.body).toEqual({ text: ANSWER, entry_id: HERE, surrogates: false });
	expect(live.every((request) => !(request.body as { record?: boolean }).record)).toBe(true);
	expect(
		capture.requests.some(
			(request) => request.method !== 'GET' && !request.path.startsWith('/restore/')
		)
	).toBe(false);
	expect(page.url()).not.toContain('PERSON');

	await page.getByRole('button', { name: 'Kopieren' }).click();
	await expect(page.getByText('Kopiert – mit den echten Namen.')).toBeVisible();
	expect(lastCopy(capture)).toEqual({ text: WITH_NAMES, kind: 'restored' });
	await expect
		.poll(
			() =>
				backRequests(capture).filter((request) => (request.body as { record?: boolean }).record)
					.length
		)
		.toBe(1);

	await page.getByRole('button', { name: 'Als Datei speichern' }).click();
	await expect(page.getByText('Gespeichert.', { exact: true })).toBeVisible();
	const file = capture.commands.find((command) => command.command === 'save_text_file');
	expect(file?.args).toEqual({
		title: 'Antwort mit echten Namen speichern',
		name: 'Antwort_mit_Namen.txt',
		content: WITH_NAMES
	});

	await page.getByRole('button', { name: 'Leeren' }).click();
	await expect(page.getByRole('textbox', { name: 'Antwort der KI' })).toHaveValue('');
	await expect(output(page)).toContainText('Hier erscheint die Antwort mit den echten Namen.');
});

test('File from AI: saved, preview, open, save as, delete', async ({ context, page }) => {
	const { capture } = await restoreHarness(context);
	await open(page, `/entry/${HERE}`);
	const map = page.getByRole('region', { name: 'Antwort einer KI zurückübersetzen' });
	await expect(map).toContainText('Oder eine Datei von der KI hierher ziehen');

	// Drop on window: harness reports id and names, never the path.
	await page.evaluate(() =>
		(window as unknown as { __EMIT__: (name: string, payload: unknown) => void }).__EMIT__(
			'inputs://dropped',
			{ id: 'ablage-7', names: ['Zahlungserinnerung_Entwurf.docx'], skipped: 0, truncated: false }
		)
	);
	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/${HERE}/restore`);
	await expect
		.poll(() => capture.commands.find((command) => command.command === 'restore_file')?.args)
		.toEqual({ selection: 'ablage-7', entryId: HERE, surrogates: false });
	await expect(page.getByRole('radio', { name: 'Datei' })).toBeChecked();

	const input = page.getByRole('list', { name: 'Antwort der KI' });
	await expect(input).toContainText('Zahlungserinnerung_Entwurf.docx');
	await expect(input).toContainText('von der KI · 24 KB · 4 Codes gefunden');
	const fresh = output(page).getByRole('list', { name: 'Mit echten Namen' });
	await expect(fresh).toContainText('Zahlungserinnerung_Entwurf_mit_Namen.docx');
	await expect(fresh).toContainText('neue Datei · die Datei der KI bleibt unverändert');
	await expect(output(page)).toContainText('Vorschau');
	await expect(output(page)).toContainText('Sehr geehrte Frau Hofer,');
	await expect(page.getByRole('status', { name: 'Zählung' })).toContainText('3 Codes ersetzt');
	// Copy and “Save as file” are part of the text, not the file.
	await expect(page.getByRole('button', { name: 'Kopieren' })).toHaveCount(0);

	// “Open” needs a location: save first, then open.
	await fresh.getByRole('button', { name: 'Öffnen' }).click();
	await expect(fresh).toContainText('gespeichert in Users › test › Downloads');
	expect(
		capture.commands.find((command) => command.command === 'save_restored_file')?.args
	).toEqual({
		id: 'r1',
		title: 'Datei mit echten Namen speichern',
		// Only a confirmed cloud target (iCloud, OneDrive …) is written without asking again.
		confirmCloud: false
	});
	await expect
		.poll(() => capture.commands.find((command) => command.command === 'open_restored_file')?.args)
		.toEqual({ path: '/Users/test/Downloads/Zahlungserinnerung_Entwurf_mit_Namen.docx' });

	await fresh.getByRole('button', { name: 'Speichern unter …' }).click();
	await expect
		.poll(
			() => capture.commands.filter((command) => command.command === 'save_restored_file').length
		)
		.toBe(2);

	await input.getByRole('button', { name: 'Entfernen' }).click();
	await expect(input).toHaveCount(0);
	await expect
		.poll(() =>
			capture.requests.some(
				(request) => request.method === 'DELETE' && request.path === '/restore/file/r1'
			)
		)
		.toBe(true);
	await expect(page.getByRole('region', { name: 'Datei von der KI hierher ziehen' })).toBeVisible();
});

test('a text entry triggers restore (E10)', async ({ context, page }) => {
	const text = entry({ id: 't-1', title: 'E-Mail-Entwurf', kind: 'text', status: 'copied' });
	await harness(context, {
		entries: () => [text],
		sidecar: (method, path) => (path === '/entries/t-1' && method === 'GET' ? text : undefined)
	});
	await open(page, '/entry/t-1');
	await page.getByRole('link', { name: 'Antwort zurückübersetzen' }).click();
	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/t-1/restore`);
	await expect(page.getByRole('navigation', { name: 'Navigation' })).toContainText(
		'E-Mail-Entwurf'
	);
});
