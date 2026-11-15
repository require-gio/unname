import { describe, expect, it } from 'vitest';
import type { ApplyRequest, Entry } from '#lib/sidecar/api.gen.js';
import { reviewIsStored } from './review-persistence.js';

const request: ApplyRequest = {
	masking: { method: 'reversible', style: 'codes', categories: ['names', 'finance_ids'] },
	review: {
		decisions: [
			{ finding_id: 'second', action: 'reject' },
			{ finding_id: 'first', action: 'accept' }
		],
		manual: [{ entity_type: 'PERSON', start: 3, end: 7 }]
	}
};

function storedEntry(): Entry {
	return {
		id: 'entry',
		kind: 'documents',
		title: 'Document',
		created_at: '2026-11-14T09:00:00Z',
		updated_at: '2026-11-14T09:00:00Z',
		masking: { ...request.masking, categories: ['finance_ids', 'names'], anonymize: false },
		review: {
			file: {
				decisions: [
					{ finding_id: 'first', action: 'accept', by: 'user' },
					{ finding_id: 'second', action: 'reject', by: 'user' }
				],
				manual: [{ entity_type: 'PERSON', start: 3, end: 7, corrects_finding_id: null }]
			}
		}
	};
}

describe('confirmation before closing after a failed preview', () => {
	it('accepts equivalent durable decisions regardless of order and default fields', () => {
		expect(reviewIsStored(request, storedEntry(), 'file')).toBe(true);
	});

	it('requires the newest decision, manual boundaries and masking to be durable', () => {
		const older = storedEntry();
		older.review!.file.decisions![0].action = 'reject';
		expect(reviewIsStored(request, older, 'file')).toBe(false);
		const differentBoundary = storedEntry();
		differentBoundary.review!.file.manual![0].end = 8;
		expect(reviewIsStored(request, differentBoundary, 'file')).toBe(false);
		const differentMasking = storedEntry();
		differentMasking.masking.style = 'surrogates';
		expect(reviewIsStored(request, differentMasking, 'file')).toBe(false);
		expect(reviewIsStored(request, storedEntry(), 'other-file')).toBe(false);
	});
});
