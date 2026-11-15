import type { Translator } from '#lib/i18n/translate.js';
import type { Entry, EntryFile, FileOutput, MaskingSpec } from '#lib/sidecar/api.gen.js';
import type { Label } from '#lib/sidecar/result-policy.js';

/**
 * Board “4 · Saved”: shows what is visible after a save.
 * All numbers and labels come from the sidecar (`EntryFile.output`,
 * `Entry.exports`); this code only builds rows and sentences.
 *
 * Since the draft (AS3) a saved entry is a draft with exports:
 * each save appears in the export history, newest first.
 */

/** One save: method, folder and result for each file. */
export interface Saving {
	/** ID in the export history; `null` for legacy entries without history. */
	id: string | null;
	time: string | null;
	masking: MaskingSpec;
	folder: string | null;
	files: { id: string; name: string; output: FileOutput; path: string | null }[];
}

/** All saves, newest first. Legacy entries have at most one. */
export function savings(entry: Entry): Saving[] {
	const history = entry.exports ?? [];
	if (history.length > 0) {
		return [...history].reverse().map((saving) => ({
			id: saving.id,
			time: saving.saved_at,
			masking: saving.masking,
			folder: saving.directory,
			files: (saving.files ?? []).map((file) => ({
				id: file.file_id,
				name: file.name,
				output: file.output,
				path: file.path ?? null
			}))
		}));
	}
	const files = (entry.files ?? []).flatMap((file) =>
		file.output
			? [{ id: file.id, name: file.name, output: file.output, path: file.output_path ?? null }]
			: []
	);
	if (files.length === 0 && !entry.saved_at) return [];
	return [
		{
			id: null,
			time: entry.saved_at ?? null,
			masking: entry.masking,
			folder: entry.output_dir ?? null,
			files
		}
	];
}

/** The most recent save – or an empty placeholder if nothing is saved yet. */
export function lastSaving(entry: Entry): Saving {
	return (
		savings(entry)[0] ?? {
			id: null,
			time: null,
			masking: entry.masking,
			folder: entry.output_dir ?? null,
			files: []
		}
	);
}

type Source = Entry | Saving;

function asSaving(source: Source): Saving {
	return 'kind' in source ? lastSaving(source) : source;
}

/**
 * Confirmation key for a save: method and style – same rule as
 * `confirmation_key` in the sidecar. Changing the method on a saved entry
 * requires reconfirmation on the next irreversible save.
 */
export function confirmationKey(
	masking: Pick<MaskingSpec, 'method' | 'style' | 'anonymize'>
): string {
	return `${masking.method}:${masking.style}:${masking.anonymize ? 'anonymous' : '-'}`;
}

/**
 * “Since the save changed”: the sidecar counts any change to
 * masking, decisions, files, or detection; viewing only does not.
 */
export function sinceTheSaveChanged(
	entry: Pick<Entry, 'draft_revision' | 'saved_revision'>
): boolean {
	const saved = entry.saved_revision;
	return saved !== null && saved !== undefined ? (entry.draft_revision ?? 0) > saved : false;
}

type SidecarLabel = 'reversible' | 'irreversible' | 'anonymous';

/**
 * Translate the sidecar label into the UI language.
 * The sidecar assigns the label (`#lib/sidecar/result-policy.ts`); this function
 * only translates, never decides.
 */
export function labelFrom(label: SidecarLabel): Label {
	if (label === 'anonymous') return 'anonymous';
	return label === 'reversible' ? 'reversible' : 'irreversible';
}

export interface SavedLine {
	id: string;
	/** File number starting at 1 for “View” (`?datei=n`). */
	number: number;
	/** Name of the protected copy, otherwise the original name. */
	name: string;
	meta: string;
	label: Label | null;
	inventedNames: boolean;
	/** No masking needed: no copy created. */
	unchanged: boolean;
}

/**
 * Saved files in entry order. `number` refers to the file in review;
 * a removed file has 0.
 */
export function savedLines(
	source: Source,
	t: Translator,
	entry?: Pick<Entry, 'files'>
): SavedLine[] {
	const saving = asSaving(source);
	const order = ('kind' in source ? source : entry)?.files ?? [];
	const numberBy = (id: string) => order.findIndex((file) => file.id === id) + 1;
	return saving.files.flatMap((file): SavedLine[] => {
		const output = file.output;
		if (output.status === 'unchanged') {
			return [
				{
					id: file.id,
					number: numberBy(file.id),
					name: file.name,
					meta: t('saved.unchanged'),
					label: null,
					inventedNames: false,
					unchanged: true
				}
			];
		}
		const parts = [t('masking.places', { n: output.masked ?? 0 }), ...outputNotices(output, t)];
		return [
			{
				id: file.id,
				number: numberBy(file.id),
				name: output.name ?? file.name,
				meta: parts.join(' · '),
				label: output.label ? labelFrom(output.label) : null,
				inventedNames: output.contains_surrogates ?? false,
				unchanged: false
			}
		];
	});
}

/**
 * Result of processing an image or scan: a scan PDF with a text layer,
 * or a plain text file if OCR could not read an embedded code.
 * Used for “Saved” and the review report.
 */
export function outputNotices(output: FileOutput, t: Translator): string[] {
	const parts: string[] = [];
	if (output.readback_failed) parts.push(t('saved.fallback'));
	else if (output.from_scan) parts.push(t('saved.fromImage'));
	if (output.scan_text_layer) parts.push(t('saved.scanTextLayer'));
	return parts;
}

/** How many files this save wrote as protected copies to the target folder. */
export function savedNum(source: Source): number {
	return asSaving(source).files.filter((file) => file.output.status === 'saved').length;
}

/**
 * “28 places masked · 2 decided by you · 1 resolved by local AI”.
 * Null values are omitted except for masked places.
 */
export function summary(source: Source, t: Translator): string[] {
	const outputs = asSaving(source).files.map((file) => file.output);
	const sum = (field: 'masked' | 'decided_by_user' | 'decided_by_ai' | 'masked_without_decision') =>
		outputs.reduce((total, output) => total + (output[field] ?? 0), 0);
	const parts = [t('masking.places', { n: sum('masked') })];
	if (sum('decided_by_user') > 0) {
		parts.push(t('saved.byYou', { n: sum('decided_by_user') }));
	}
	if (sum('decided_by_ai') > 0) parts.push(t('saved.byAi', { n: sum('decided_by_ai') }));
	if (sum('masked_without_decision') > 0) {
		parts.push(t('saved.inDoubt', { n: sum('masked_without_decision') }));
	}
	return parts;
}

function labelsBy(saving: Saving): SidecarLabel[] {
	return saving.files.flatMap((file) =>
		file.output.status === 'saved' && file.output.label ? [file.output.label] : []
	);
}

/**
 * Determines which card appears in the list: use restore only when something can be
 * restored; use the strongest level only when every saved file carries it.
 * (`level3` instead of the label itself: the word stem must not appear in any Svelte file,
 * `result-policy.spec.ts`.)
 *
 * Export history counts every save: if an earlier save was reversible, its mappings stay in the
 * table and restore can continue.
 */
export function returnPath(entry: Entry): 'restore' | 'irreversible' | 'level3' {
	const all = savings(entry);
	if (all.some((saving) => labelsBy(saving).includes('reversible'))) {
		return 'restore';
	}
	const last = lastSaving(entry);
	const labels = labelsBy(last);
	if (labels.length > 0 && labels.every((label) => label === 'anonymous')) return 'level3';
	return last.masking.method === 'reversible' && labels.length === 0 ? 'restore' : 'irreversible';
}

/**
 * Short folder name: the last three parts, e.g. “Documents › Clients › protected”.
 * Full path is shown in the tooltip.
 */
export function folderShort(path: string): string {
	const parts = path.split(/[\\/]+/).filter((part) => part !== '' && !/^[A-Za-z]:$/.test(part));
	return parts.slice(-3).join(' › ') || path;
}

/**
 * Files whose job data have expired – only archive without a draft (E9).
 * Since the draft (AS1) no new file expires.
 */
export function expired(files: readonly Pick<EntryFile, 'id' | 'name' | 'status'>[]) {
	return files
		.filter((file) => file.status === 'expired')
		.map((file) => ({ id: file.id, name: file.name }));
}

/**
 * Files that need their original again: archive (E9) and files whose saved copy is
 * no longer readable (`unavailable`).
 */
export function needingOriginal(files: readonly Pick<EntryFile, 'id' | 'name' | 'status'>[]) {
	return files
		.filter((file) => file.status === 'expired' || file.status === 'unavailable')
		.map((file) => ({ id: file.id, name: file.name }));
}

/** Name under which the review report is suggested. */
export function reportFileName(entry: Pick<Entry, 'title'>, t: Translator): string {
	const title = [...entry.title]
		.map((character) =>
			character.charCodeAt(0) < 32 || '\\/:*?"<>|'.includes(character) ? ' ' : character
		)
		.join('')
		.replace(/ +/g, ' ')
		.trim();
	return `${t('report.file', { title: title || t('report.title') })}.txt`;
}

/**
 * “Anonymize”, “Reversible · Invented Names”, “Irreversible · Redact” —
 * how a save masked data, for the export history.
 */
export function methodText(masking: MaskingSpec, t: Translator): string {
	if (masking.anonymize) return t('masking.all');
	const method = t(masking.method === 'reversible' ? 'masking.reversible' : 'masking.irreversible');
	return `${method} · ${t(`masking.style.${masking.style}`)}`;
}
