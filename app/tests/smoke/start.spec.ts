import { expect, test } from '@playwright/test';
import {
	APP_ORIGIN,
	JOB,
	FILE,
	STANDARD,
	board,
	entry,
	harness,
	open,
	storage
} from './harness.js';

/**
 * Shell, start page, text mode.
 *
 * The shell and sidecar are simulated (`huelle.ts`). This checks what the UI does
 * with the data and what it sends to the sidecar.
 */

test('Dropping files creates an entry in history', async ({ context, page }) => {
	let done = false;
	const created = () =>
		entry({
			status: done ? 'review' : 'processing',
			files: [{ ...FILE, status: done ? 'review' : 'processing' }]
		});
	const { capture } = await harness(context, {
		entries: () =>
			capture.requests.some((a) => a.path === '/entries' && a.method === 'POST') ? [created()] : [],
		sidecar: (method, path) => {
			if (path === '/entries' && method === 'POST') return entry({});
			if (path === '/entries/e-1' && method === 'GET') return created();
			if (path === '/entries/e-1/progress') {
				return {
					entry_id: 'e-1',
					status: done ? 'review' : 'processing',
					files: [
						{
							...FILE,
							status: done ? 'review' : 'processing',
							phase: done ? null : 'ner',
							progress: done ? 100 : 40,
							counts: { names: 2 },
							questions: done ? 1 : null
						}
					],
					totals: { names: 2 },
					done: done ? 1 : 0,
					total: 1,
					ready: done
				};
			}
			return undefined;
		}
	});
	await open(page);

	await expect(page.getByRole('region', { name: 'Zuletzt erstellt' })).toContainText(
		'Noch nichts im Verlauf.'
	);
	await page.getByRole('button', { name: 'Dateien auswählen' }).click();

	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1`);
	// Board “2 · Detection running”: status per file and “Found so far”.
	await expect(page.getByRole('heading', { name: 'Wird geprüft …', level: 1 })).toBeVisible();
	await expect(page.getByText('Angebot_Hofer.pdf')).toBeVisible();
	await expect(page.getByText('bisher 2 Stellen gefunden', { exact: false })).toBeVisible();
	await expect(page.getByText('Namen werden erkannt')).toBeVisible();
	await expect(page.getByRole('region', { name: 'Bisher gefunden' })).toContainText('Namen');
	await expect(page.getByText('Maskierung: reversibel (Ihr Standard)')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Prüfen', exact: true })).toHaveCount(0);
	await expect(page.getByText('Dieses Format wird nicht unterstützt.')).toBeVisible();

	// The entry appears under “Zuletzt erstellt” with a title but no name
	// and is counted as open.
	const history = page.getByRole('region', { name: 'Zuletzt erstellt' });
	await expect(history.getByRole('link', { name: /Angebot/ })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(history).toContainText('PDF · 2 Seiten');
	await expect(page.getByRole('link', { name: 'Offen, 1 Eintrag' })).toBeVisible();

	// The UI only shows the ID, never a path.
	const submit = capture.commands.find((b) => b.command === 'submit_inputs');
	expect(submit?.args).toEqual({ entryId: 'e-1', selection: 'auswahl-1' });
	const create = capture.requests.find((a) => a.path === '/entries' && a.method === 'POST');
	expect(create?.body).toEqual({ kind: 'documents', masking: JOB });

	done = true;
	await expect(page.getByRole('link', { name: 'Offen, 1 Eintrag' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Bereit zum Prüfen', level: 1 })).toBeVisible();
	await expect(page.getByText('1 braucht Ihren Blick')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Prüfen', exact: true })).toHaveAttribute(
		'href',
		'/entry/e-1/review'
	);
	// An entry “to review” opens the review view directly from history.
	await expect(history.getByRole('link', { name: /Angebot/ })).toHaveAttribute(
		'href',
		'/entry/e-1/review'
	);
});

test('Dropping on the window follows the same path', async ({ context, page }) => {
	const { capture } = await harness(context, {
		sidecar: (method, path) => {
			if (path === '/entries' && method === 'POST') return entry({});
			if (path === '/entries/e-1') return entry({ status: 'review', files: [FILE] });
			return undefined;
		}
	});
	await open(page);
	await page.getByRole('radio', { name: 'Text' }).click();

	await page.evaluate(() => {
		const emit = (window as unknown as Record<string, (n: string, p: unknown) => void>).__EMIT__;
		emit('inputs://drag', { active: true });
	});
	await page.evaluate(() => {
		const emit = (window as unknown as Record<string, (n: string, p: unknown) => void>).__EMIT__;
		emit('inputs://drag', { active: false });
		emit('inputs://dropped', {
			id: 'ablage-7',
			names: ['Angebot_Hofer.pdf'],
			skipped: 0,
			truncated: false
		});
	});

	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1`);
	expect(capture.commands.find((b) => b.command === 'submit_inputs')?.args).toEqual({
		entryId: 'e-1',
		selection: 'ablage-7'
	});
});

test('Live masking in text mode, copy writes and creates an entry', async ({ context, page }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: APP_ORIGIN });
	const text = 'Hallo Frau Hofer,\n\nviele Grüße\nJosef Brandstetter';
	const codeA = '<<PERSON_51c2e8d4>>';
	const codeB = '<<PERSON_3f9a1c07>>';
	const output = `Hallo Frau ${codeA},\n\nviele Grüße\n${codeB}`;
	const result = {
		offset_encoding: 'utf-16',
		text: output,
		masking: JOB,
		labels: {
			label: 'reversible',
			contains_surrogates: false,
			anonymous_blockers: ['not_requested'],
			protection_level: 2,
			kept_here_count: 0,
			masked_without_decision: 0
		},
		applied_findings: [
			{ start: 11, end: 16, out: 11 },
			{ start: text.indexOf('Josef'), end: text.length, out: output.indexOf(codeB) }
		].map(({ start, end, out }, index) => ({
			finding_id: `f${index}`,
			kind: 'confirmed',
			entity_type: 'PERSON',
			category: 'names',
			source: 'ner',
			mode: 'token_deterministic',
			reversibility: 'vault',
			original_start: start,
			original_end: end,
			output_start: out,
			output_end: out + codeA.length
		})),
		applied_by_category: { names: 2 },
		residual_risk: { status: 'not_run', heuristic: true, threshold: 2, classes: [], counts: {} },
		facts: {},
		report: {}
	};
	let copied = false;
	const { capture } = await harness(context, {
		entries: () =>
			copied ? [entry({ id: 't-1', title: 'E-Mail-Entwurf', kind: 'text', status: 'copied' })] : [],
		sidecar: (method, path) => {
			if (path === '/text/mask') return { findings: [], counts: {}, result: result };
			if (path === '/text/commit') {
				copied = true;
				return {
					entry: entry({ id: 't-1', kind: 'text', status: 'copied' }),
					result: result,
					committed_mappings: 2
				};
			}
			return undefined;
		}
	});
	await open(page);
	await page.getByRole('radio', { name: 'Text' }).click();
	await page.getByRole('button', { name: 'Aus Zwischenablage einfügen' }).click();
	await expect(page.getByLabel('Ihr Text')).toHaveValue(text);

	const outputCard = page
		.getByRole('heading', { name: 'Pseudonymisiert', exact: true })
		.locator('../..');
	await expect(outputCard).toContainText(codeA);
	await expect(outputCard).toContainText('2 Stellen maskiert · keine Fragen');

	// Live masking does not persist; the “when in doubt, mask” rule applies here.
	const mask = capture.requests.find((a) => a.path === '/text/mask');
	expect(mask?.body).toEqual({ text, masking: JOB, review: { unresolved: 'mask' } });
	expect(capture.requests.some((a) => a.path === '/text/commit')).toBe(false);
	// Before copying, the codes are not shown in the table: nothing to restore.
	await expect(outputCard.getByRole('link', { name: 'Antwort zurückübersetzen' })).toHaveCount(0);

	await page.getByRole('button', { name: 'Kopieren' }).click();
	await expect(page.getByText(/^Kopiert/)).toBeVisible();
	expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(output);
	// Afterwards, “restore answer” creates a new entry.
	await expect(outputCard.getByRole('link', { name: 'Antwort zurückübersetzen' })).toHaveAttribute(
		'href',
		'/entry/t-1/restore'
	);
	await expect(page.getByRole('region', { name: 'Zuletzt erstellt' })).toContainText(
		'E-Mail-Entwurf'
	);
	await expect(page.getByRole('link', { name: 'Abgeschlossen, 1 Eintrag' })).toBeVisible();

	// Board “1 · New” in text mode, light and dark variants.
	await page.getByText(/^Kopiert/).waitFor({ state: 'hidden' });
	await board(page, '1-new-text');
});

test('Board “1 · New”: accessible and like the reference, light and dark', async ({
	context,
	page
}) => {
	await harness(context, {
		entries: () => [
			entry({ status: 'saved', files: [{ ...FILE, status: 'saved' }] }),
			entry({ id: 't-1', title: 'E-Mail-Entwurf', kind: 'text', status: 'copied' })
		]
	});
	await open(page);
	await expect(page.getByRole('region', { name: 'Zuletzt erstellt' })).toContainText(
		'E-Mail-Entwurf'
	);

	// “Zuletzt erstellt” shows the entry timestamps; they follow runtime.
	const uhrzeiten = [page.getByRole('complementary', { name: 'Verlauf' }).locator('time')];
	await board(page, '1-new', { mask: uhrzeiten });

	// The help notices belong to the board and must not be interfered with by axe.
	await page.getByRole('button', { name: 'Hilfe anzeigen' }).click();
	await expect(page.getByRole('note')).toHaveCount(2);
	await board(page, '1-new-help', { mask: uhrzeiten });
});

test('Adjust masking changes example and default', async ({ context, page }) => {
	const { capture } = await harness(context);
	await open(page);

	await expect(page.getByRole('heading', { name: 'Namen werden pseudonymisiert' })).toBeVisible();
	await page.getByRole('button', { name: 'Maskierung anpassen' }).click();
	await page.getByRole('radio', { name: /^Irreversibel/ }).click();

	await expect(
		page.getByRole('heading', { name: 'Namen werden irreversibel maskiert' })
	).toBeVisible();
	await expect
		.poll(() => capture.requests.find((a) => a.method === 'PUT')?.body)
		.toEqual({ masking: { ...STANDARD, method: 'irreversible', style: 'placeholder' } });
});

test('Sidebar collapses and stays; "?" shows notices', async ({ context, page }) => {
	await harness(context);
	await open(page);

	await page.getByRole('button', { name: 'Hilfe anzeigen' }).click();
	await expect(page.getByRole('note').filter({ hasText: 'nach 30 Tagen gelöscht' })).toBeVisible();
	await expect(
		page.getByRole('note').filter({ hasText: 'ob Namen wiederherstellbar bleiben' })
	).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('note')).toHaveCount(0);

	await page.getByRole('button', { name: 'Seitenleiste einklappen' }).click();
	await expect(page.getByRole('button', { name: 'Seitenleiste ausklappen' })).toBeVisible();
	await page.reload({ waitUntil: 'networkidle' });
	await expect(page.getByRole('button', { name: 'Seitenleiste ausklappen' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Neu' })).toBeVisible();
});

test('First start shows notices automatically (first-run behavior)', async ({ context, page }) => {
	await harness(context, { firstStart: true });
	await open(page);

	const notice = page.getByRole('note').filter({ hasText: 'ob Namen wiederherstellbar bleiben' });
	await expect(notice).toBeVisible();
	await expect(page.getByRole('note').filter({ hasText: 'nach 30 Tagen gelöscht' })).toBeVisible();
	// Notices only explain; the page remains usable.
	await expect(page.getByRole('button', { name: 'Dateien auswählen' })).toBeEnabled();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('note')).toHaveCount(0);

	// Only once: after reload they stay hidden until "?" fetches them.
	await page.reload({ waitUntil: 'networkidle' });
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	await expect(page.getByRole('note')).toHaveCount(0);
});

test('While detection starts, a dropzone is marked and then submitted', async ({
	context,
	page
}) => {
	const { capture } = await harness(context, {
		ready: false,
		sidecar: (method, path) => {
			if (path === '/entries' && method === 'POST') return entry({});
			if (path === '/entries/e-1') return entry({ status: 'review', files: [FILE] });
			return undefined;
		}
	});
	await open(page);

	await expect(page.getByRole('status').filter({ hasText: 'Wird vorbereitet' })).toBeVisible();
	// Quiet and open: no blocking notice, selection already works.
	await expect(
		page.getByText('Die Erkennung wird noch vorbereitet – gleich geht es los.')
	).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Dateien auswählen' })).toBeEnabled();
	await expect(page.getByText(/Sidecar/i)).toHaveCount(0);

	await page.evaluate(() => {
		const emit = (window as unknown as Record<string, (n: string, p: unknown) => void>).__EMIT__;
		emit('inputs://dropped', {
			id: 'ablage-7',
			names: ['Angebot_Hofer.pdf', 'Notiz.pdf'],
			skipped: 0,
			truncated: false
		});
	});
	await expect(page.getByText('2 Dateien vorgemerkt', { exact: false })).toBeVisible();
	expect(capture.commands.some((b) => b.command === 'submit_inputs')).toBe(false);

	await page.evaluate(() => (window as unknown as Record<string, () => void>).__FREIGEBEN__());

	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1`);
	expect(capture.commands.find((b) => b.command === 'submit_inputs')?.args).toEqual({
		entryId: 'e-1',
		selection: 'ablage-7'
	});
});

test('State AS3.5: low free space triggers warning without blocking', async ({ context, page }) => {
	const { capture } = await harness(context, {
		sidecar: (method, path) => {
			if (path === '/storage') return storage([], { free_bytes: 1024 ** 3 });
			if (path === '/entries' && method === 'POST') return entry({});
			if (path === '/entries/e-1') return entry({ status: 'review', files: [FILE] });
			return undefined;
		}
	});
	await open(page);
	await page.getByRole('button', { name: 'Dateien auswählen' }).click();

	await expect(page.getByText('Wenig freier Speicherplatz (1 GB).')).toBeVisible();
	await expect(page).toHaveURL(`${APP_ORIGIN}/entry/e-1`);
	expect(capture.commands.map((command) => command.command)).toContain('submit_inputs');
});
