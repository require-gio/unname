import type { MessageKey } from '#lib/i18n/translate.js';
import { categoryForType } from '#lib/masking/catalog.js';
import type { FileReview, FindingOut, ReviewIn } from '#lib/sidecar/api.gen.js';

/**
 * Review state for a file in the review view.
 *
 * Pure value without Svelte binding: the same data drives document, question bar,
 * file tabs and the apply call. What gets masked is decided by the sidecar; the UI
 * just displays its answer.
 *
 * No per-match replacement type: the style applies to the whole entry
 * (`MaskingSpec`). Each decision records its origin (user or AI suggestion), the
 * review report and audit counts.
 *
 * All offsets are UTF-16 code units in the file source text.
 */

export type Action = 'accept' | 'reject';
export type Origin = 'user' | 'ai-suggestion';

export interface Decision {
	action: Action;
	by: Origin;
}

/** A manually masked region (addendum or boundary correction). */
export interface ManualEntry {
	/** UI-only; the sidecar generates its own identifier. */
	id: string;
	type: string;
	start: number;
	end: number;
	/** Finding ID whose boundaries this entry corrects. */
	corrected?: string;
}

export interface ReviewState {
	readonly decisions: Readonly<Record<string, Decision>>;
	readonly manual: readonly ManualEntry[];
}

export const EMPTY_STATE: ReviewState = { decisions: {}, manual: [] };

export type StateResult =
	{ ok: true; status: ReviewState } | { ok: false; reason: MessageKey; status: ReviewState };

export interface Section {
	start: number;
	end: number;
}

function handId(type: string, section: Section): string {
	return `m_${section.start}_${section.end}_${type}`;
}

/**
 * Adopt the state stored on the entry (`entry.review[file]`).
 *
 * The sidecar records it on every apply without content – finding IDs, offsets,
 * origin. This survives a reload and is the basis for “edit again”. Unknown
 * finding IDs are dropped because they belong to a previous detection and would
 * cause the next apply to fail.
 *
 * Without a stored state the review starts with decisions already made by the
 * local AI (origin `ai-suggestion`), shown under “Resolved by local AI” and
 * reversible. With a stored state only those decisions apply: it contains the
 * AI decisions from the last apply and any reversals.
 */
export function fromSaved(
	review: FileReview | null | undefined,
	findings: readonly FindingOut[]
): ReviewState {
	if (!review) return aiEntschieden(findings);
	const known = new Set(findings.map((finding) => finding.id));
	const decisions: Record<string, Decision> = {};
	for (const entry of review.decisions ?? []) {
		if (!known.has(entry.finding_id)) continue;
		decisions[entry.finding_id] = { action: entry.action, by: entry.by ?? 'user' };
	}
	const manual = (review.manual ?? [])
		.filter((entry) => !entry.corrects_finding_id || known.has(entry.corrects_finding_id))
		.map((entry) => ({
			id: handId(entry.entity_type, entry),
			type: entry.entity_type,
			start: entry.start,
			end: entry.end,
			...(entry.corrects_finding_id ? { corrected: entry.corrects_finding_id } : {})
		}));
	return { decisions, manual };
}

/**
 * Review portion of the apply call.
 *
 * “Mask when in doubt” also applies to the preview: what is shown in the window
 * is what will be saved. Open questions are still highlighted in the view with
 * the original text so a decision can be made.
 */
function aiEntschieden(findings: readonly FindingOut[]): ReviewState {
	const decisions: Record<string, Decision> = {};
	for (const finding of findings) {
		const assessment = finding.assessment;
		if (finding.question && assessment?.decided && assessment.suggestion) {
			decisions[finding.id] = { action: assessment.suggestion, by: 'ai-suggestion' };
		}
	}
	return Object.keys(decisions).length > 0 ? { decisions, manual: [] } : EMPTY_STATE;
}

export function asReview(status: ReviewState): ReviewIn {
	return {
		decisions: Object.entries(status.decisions).map(([finding_id, { action, by }]) => ({
			finding_id,
			action: action,
			by: by
		})),
		manual: status.manual.map((entry) => ({
			entity_type: entry.type,
			start: entry.start,
			end: entry.end,
			...(entry.corrected ? { corrects_finding_id: entry.corrected } : {})
		})),
		unresolved: 'mask'
	};
}

export function decide(
	status: ReviewState,
	findingId: string,
	action: Action,
	by: Origin = 'user'
): ReviewState {
	return { ...status, decisions: { ...status.decisions, [findingId]: { action, by } } };
}

/** Undo a decision – e.g., "remask" or "change" on a question. */
export function undo(status: ReviewState, findingId: string): ReviewState {
	if (!(findingId in status.decisions)) return status;
	const decisions = { ...status.decisions };
	delete decisions[findingId];
	return {
		decisions,
		// A boundary correction without a discarded target was rejected by the sidecar.
		manual: status.manual.filter((entry) => entry.corrected !== findingId)
	};
}

/**
 * Remove a manual entry. If it was a boundary correction, the original finding is restored.
 */
export function removeManualEntry(status: ReviewState, id: string): ReviewState {
	const entry = status.manual.find((kandidat) => kandidat.id === id);
	if (!entry) return status;
	const manual = status.manual.filter((kandidat) => kandidat.id !== id);
	if (!entry.corrected) return { ...status, manual };
	const decisions = { ...status.decisions };
	delete decisions[entry.corrected];
	return { decisions, manual };
}

/**
 * Locations that could conflict with a new manual entry: any non-rejected finding and any manual entry.
 * Open and plaintext findings also count – the sidecar rejected two replacements at the same spot
 * once the category was enabled.
 */
function used(
	status: ReviewState,
	findings: readonly FindingOut[],
	except: { finding?: string; hand?: string } = {}
): Section[] {
	const corrected = new Set(status.manual.map((entry) => entry.corrected));
	return [
		...findings.filter(
			(finding) =>
				finding.id !== except.finding &&
				!corrected.has(finding.id) &&
				status.decisions[finding.id]?.action !== 'reject'
		),
		...status.manual.filter((entry) => entry.id !== except.hand)
	];
}

function overlaps(a: Section, b: Section): boolean {
	return a.start < b.end && b.start < a.end;
}

function valid(section: Section): boolean {
	return (
		Number.isInteger(section.start) &&
		Number.isInteger(section.end) &&
		section.start >= 0 &&
		section.end > section.start
	);
}

/** Matches that touch a manual entry – basis for “expand location”. */
export function touchedMatch(
	status: ReviewState,
	findings: readonly FindingOut[],
	section: Section
): { findings: FindingOut[]; hand: ManualEntry[] } {
	const corrected = new Set(status.manual.map((entry) => entry.corrected));
	return {
		findings: findings.filter(
			(finding) => !corrected.has(finding.id) && overlaps(finding, section)
		),
		hand: status.manual.filter((entry) => overlaps(entry, section))
	};
}

/**
 * Add a new manual location (“mask «Lena» as well”).
 *
 * Overlaps are rejected here with a clear menu message rather than a 422 from the sidecar.
 */
export function addLater(
	status: ReviewState,
	findings: readonly FindingOut[],
	addendum: Section & { type: string }
): StateResult {
	if (!valid(addendum)) return { ok: false, reason: 'addendum.invalid', status };
	if (used(status, findings).some((section) => overlaps(section, addendum))) {
		return { ok: false, reason: 'addendum.overlaps', status };
	}
	const entry: ManualEntry = {
		id: handId(addendum.type, addendum),
		type: addendum.type,
		start: addendum.start,
		end: addendum.end
	};
	return { ok: true, status: { ...status, manual: [...status.manual, entry] } };
}

/**
 * Add many occurrences at once (“all 4 occurrences in 3 files”).
 * Locations already covered are silently skipped – they are already masked or deliberately decided.
 */
export function addLaterOccurrence(
	status: ReviewState,
	findings: readonly FindingOut[],
	type: string,
	sections: readonly Section[]
): { status: ReviewState; fresh: number } {
	let current = status;
	let fresh = 0;
	for (const section of sections) {
		const result = addLater(current, findings, { ...section, type });
		if (result.ok) {
			current = result.status;
			fresh += 1;
		}
	}
	return { status: current, fresh };
}

/**
 * Correct the boundaries of a match (used for “expand location” when the
 * highlight exceeds code or needs to be shortened).
 *
 * As in the sidecar, the original match is discarded and the corrected
 * location with its type is re-entered – this also appears in the audit log.
 */
export function correctBoundary(
	status: ReviewState,
	findings: readonly FindingOut[],
	findingId: string,
	section: Section
): StateResult {
	const finding = findings.find((kandidat) => kandidat.id === findingId);
	if (!finding) return { ok: false, reason: 'addendum.unknown', status };
	if (!valid(section)) return { ok: false, reason: 'addendum.invalid', status };
	if (section.start === finding.start && section.end === finding.end) {
		return { ok: false, reason: 'addendum.unchanged', status };
	}
	const vorige = status.manual.find((entry) => entry.corrected === findingId);
	if (
		used(status, findings, { finding: findingId, hand: vorige?.id }).some((other) =>
			overlaps(other, section)
		)
	) {
		return { ok: false, reason: 'addendum.several', status };
	}
	const entry: ManualEntry = {
		id: handId(finding.type, section),
		type: finding.type,
		start: section.start,
		end: section.end,
		corrected: findingId
	};
	return {
		ok: true,
		status: {
			decisions: {
				...status.decisions,
				[findingId]: { action: 'reject', by: 'user' }
			},
			manual: [...status.manual.filter((kandidat) => kandidat.id !== vorige?.id), entry]
		}
	};
}

/* ---------------------------------------------------------------------------
 * Questions
 * ------------------------------------------------------------------------- */

export interface Question {
	finding: FindingOut;
	/** Sequential number per file, starting at 1. */
	number: number;
	kind: 'suspicion' | 'uncertain';
	decision?: Decision;
}

/** Category of a match; unknown types fall back to catalog numbers. */
export function categoryBy(finding: Pick<FindingOut, 'category' | 'type'>): string {
	return finding.category ?? categoryForType(finding.type);
}

/**
 * Questions for a file: suspicious and uncertain detections whose
 * category is masked. No question is created for a category that remains
 * in clear text – “mask” would have no effect.
 *
 * Numbers include already decided questions so the list does not jump
 * when decisions are made. A question whose boundaries were corrected
 * is considered resolved.
 */
export function questions(
	findings: readonly FindingOut[],
	status: ReviewState,
	categoryActive: (category: string) => boolean
): Question[] {
	const corrected = new Set(status.manual.map((entry) => entry.corrected));
	return findings
		.filter(
			(finding) =>
				finding.question !== null &&
				!corrected.has(finding.id) &&
				categoryActive(categoryBy(finding))
		)
		.sort((a, b) => a.start - b.start || b.end - a.end)
		.map((finding, index) => ({
			finding,
			number: index + 1,
			kind: finding.question?.kind === 'suspicion' ? 'suspicion' : 'uncertain',
			decision: status.decisions[finding.id]
		}));
}

export function openQuestions(list: readonly Question[]): Question[] {
	return list.filter((question) => !question.decision);
}

/**
 * Find the next open question after `afterId` (Alt+N). Wrap to the start so a
 * skipped question remains reachable.
 */
export function nextOpen(list: readonly Question[], afterId: string | null): Question | null {
	const open = openQuestions(list);
	if (open.length === 0) return null;
	const index =
		afterId === null ? -1 : open.findIndex((question) => question.finding.id === afterId);
	if (index < 0) {
		// After a decided question: return the next open one that follows it.
		const after = list.find((question) => question.finding.id === afterId);
		if (after) {
			return open.find((question) => question.number > after.number) ?? open[0];
		}
		return open[0];
	}
	return open[(index + 1) % open.length];
}

/**
 * Local AI suggestion for a question (`assessment` in the findings, phase K).
 * No model or low confidence yields no suggestion.
 */
export function aiSuggestion(finding: FindingOut): Action | undefined {
	return finding.assessment?.suggestion ?? undefined;
}

/** What the local AI thinks about a finding, for the question card and rationale. */
export interface AiView {
	/** 0…1: confidence in the suggested direction or label. */
	safety: number;
	/** Translation key for the sentence (“Probably a person name”). */
	sentence: MessageKey;
	/** Short key for “Resolved by local AI” (“Concept, not a name”). */
	short: MessageKey;
	suggestion?: Action;
}

export function aiView(finding: FindingOut): AiView | undefined {
	const assessment = finding.assessment;
	if (!assessment) return undefined;
	const suggestion = assessment.suggestion ?? undefined;
	const safety =
		suggestion === 'accept'
			? assessment.protect
			: suggestion === 'reject'
				? 1 - assessment.protect
				: assessment.confidence;
	return {
		safety,
		sentence: suggestion ? `ai.label.${assessment.label}` : 'ai.uncertain',
		short: `ai.short.${assessment.label}`,
		...(suggestion ? { suggestion } : {})
	};
}
