import type { ApplyRequest, Entry } from '#lib/sidecar/api.gen.js';

/** A preview error is safe to leave only after the durable decisions are confirmed. */
export function reviewIsStored(request: ApplyRequest, entry: Entry, fileId: string): boolean {
	const normalizeMasking = (value: ApplyRequest['masking']) => ({
		method: value.method,
		style: value.style,
		categories: [...value.categories].sort(),
		anonymize: value.anonymize ?? false
	});
	const normalizeReview = (value: ApplyRequest['review']) => ({
		decisions: (value?.decisions ?? [])
			.map((item) => ({
				finding_id: item.finding_id,
				action: item.action,
				by: item.by ?? 'user'
			}))
			.sort((left, right) => left.finding_id.localeCompare(right.finding_id)),
		manual: (value?.manual ?? [])
			.map((item) => ({
				entity_type: item.entity_type,
				start: item.start,
				end: item.end,
				corrects_finding_id: item.corrects_finding_id ?? null
			}))
			.sort(
				(left, right) =>
					left.start - right.start ||
					left.end - right.end ||
					left.entity_type.localeCompare(right.entity_type)
			)
	});
	return (
		JSON.stringify(normalizeMasking(request.masking)) ===
			JSON.stringify(normalizeMasking(entry.masking)) &&
		JSON.stringify(normalizeReview(request.review)) ===
			JSON.stringify(normalizeReview(entry.review?.[fileId]))
	);
}
