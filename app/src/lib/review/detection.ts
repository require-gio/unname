import type { MessageKey, Translator } from '#lib/i18n/translate.js';
import { CATALOG, type CategoryId } from '#lib/masking/catalog.js';
import type { Method } from '#lib/masking/rules.js';
import type { EntryFile, FileProgress, ProgressResponse } from '#lib/sidecar/api.gen.js';
import { fileKind } from '#lib/ui/file.js';

/**
 * Board "2 · detection running": files with state and interim count,
 * "previously found" per category, cancel and "review" once the first
 * file is done. Texts and numbers are provided here; the sidecar does the counting.
 */

const PHASES: Record<string, MessageKey> = {
	extraction: 'phase.extraction',
	master_data: 'phase.master_data',
	rules: 'phase.rules',
	ner: 'phase.ner',
	suspicion: 'phase.suspicion'
};

/** Current activity for a file; for images this is OCR extraction. */
export function phasesText(
	phase: string | null | undefined,
	name: string,
	t: Translator
): string | undefined {
	if (!phase) return undefined;
	if (phase === 'extraction' && fileKind(name).kind === 'image') return t('phase.ocr');
	const key = PHASES[phase];
	return key ? t(key) : undefined;
}

export interface FileMeta {
	/** "1 page · 15 findings" */
	text: string;
	/** "2 need your review" — highlighted, only for completed files with questions. */
	questions?: string;
}

/**
 * Meta line for a file: size, findings, questions. While detection runs it says
 * "previously … found"; for images the page count is shown as "photo of a document".
 */
export function fileMeta(
	file: Pick<EntryFile, 'name' | 'pages' | 'format'>,
	status: Pick<FileProgress, 'status' | 'counts' | 'questions'> | undefined,
	t: Translator
): FileMeta {
	const parts: string[] = [];
	if (fileKind(file.name).kind === 'image') parts.push(t('file.photo'));
	else if (file.pages) parts.push(t('file.pages', { n: file.pages }));
	const found = Object.values(status?.counts ?? {}).reduce((sum, n) => sum + n, 0);
	const running = status?.status === 'processing' || status?.status === 'queued';
	if (running && found > 0) parts.push(t('file.previousFound', { n: found }));
	if (!running && status?.status === 'review') parts.push(t('file.found', { n: found }));
	const questions = status?.status === 'review' ? status.questions : null;
	if (questions === null || questions === undefined) return { text: parts.join(' · ') };
	if (questions === 0) return { text: [...parts, t('file.questions', { n: 0 })].join(' · ') };
	return { text: parts.join(' · '), questions: t('file.questions', { n: questions }) };
}

/** "previously found": categories in catalog order, without zeros. */
export function previousFound(
	totals: ProgressResponse['totals'] | undefined,
	t: Translator
): { num: number; label: string }[] {
	return CATALOG.filter((category) => (totals?.[category.id] ?? 0) > 0).map((category) => ({
		num: totals![category.id],
		label: t(`category.${category.id as CategoryId}`)
	}));
}

/** "Masking: reversible (your default)". */
export function maskingLine(
	method: Method,
	all: boolean,
	standard: boolean,
	t: Translator
): string {
	const kind = t(
		all
			? 'detection.kind.all'
			: method === 'reversible'
				? 'detection.kind.reversible'
				: 'detection.kind.irreversible'
	);
	return t(standard ? 'detection.masking.standard' : 'detection.masking', { kind });
}

/**
 * Name of the protected copy: `<name>_protected.<ext>`. Each format stays the same:
 * a scan remains PDF with text layer, an image stays image with drawn codes.
 * If OCR cannot read a code in the image, the sidecar stores text – this is known only
 * after saving, when "saved" is shown. The same rule is in the sidecar
 * (`saving.output_plan`), which assigns the name during save.
 */
export function protectedName(name: string, options: { addition?: string } = {}): string {
	const point = name.lastIndexOf('.');
	const master = point > 0 ? name.slice(0, point) : name;
	const extension = point > 0 ? name.slice(point) : '';
	return `${master}${options.addition ?? '_geschützt'}${extension}`;
}

/**
 * What the pane next to the file list shows: while detection runs it probes;
 * afterwards a symbol indicates the finding – instead of a probe that would
 * continue even though nothing else happens.
 *
 * Questions take precedence over findings: pending decisions should be visible first.
 * Without a question count (e.g., no progress answer) only findings are counted.
 */
export type DetectionFinding = 'running' | 'questions' | 'found' | 'nothing' | 'error';

export function detectionFinding(
	running: boolean,
	files: Pick<FileProgress, 'status' | 'counts' | 'questions'>[]
): DetectionFinding {
	// Without files the entry is not yet loaded; otherwise an error icon would flash briefly.
	if (running || files.length === 0) return 'running';
	const done = files.filter((file) => file.status === 'review');
	if (done.length === 0) return 'error';
	if (done.some((file) => (file.questions ?? 0) > 0)) return 'questions';
	const found = done.some((file) => Object.values(file.counts ?? {}).some((num) => num > 0));
	return found ? 'found' : 'nothing';
}
