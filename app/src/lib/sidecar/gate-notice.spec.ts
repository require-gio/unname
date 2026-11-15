import { describe, expect, it } from 'vitest';
import { de } from '#lib/i18n/de.js';
import { GATE_TEXT, gateNotice } from './gate-notice.js';
import { LOCKED, type SidecarSnapshot } from './types.js';

const ready: SidecarSnapshot = {
	status: { state: 'ready', port: 1, transport: 'tcp', pid: 2, version: '0.1.0' },
	restarts: 0,
	changed_at: '',
	can_upload: true,
	can_export: true
};

describe('Shell gate notice', () => {
	it('remains silent when dropzone and save are open', () => {
		expect(gateNotice({ snapshot: ready, outside: false })).toBeNull();
	});

	it('reports start as waiting, not as failure', () => {
		expect(gateNotice({ snapshot: LOCKED, outside: false })).toEqual({
			position: 'starts',
			restart: false
		});
	});

	it('offers a restart only after the shell stops trying itself', () => {
		const failure = (restarting: boolean): SidecarSnapshot => ({
			...LOCKED,
			status: { state: 'unreachable', reason: 'x', restarting }
		});
		expect(gateNotice({ snapshot: failure(true), outside: false })?.restart).toBe(false);
		expect(gateNotice({ snapshot: failure(false), outside: false })).toEqual({
			position: 'failure',
			restart: true
		});
	});

	it('locks on a live-check glitch even though the service is running', () => {
		const aussetzer = { ...ready, can_upload: false, can_export: false };
		expect(gateNotice({ snapshot: aussetzer, outside: false })?.position).toBe('failure');
	});

	it('shows setup pending when models are missing – no restart', () => {
		const withoutModels = { ...ready, can_upload: false, setup_required: true };
		expect(gateNotice({ snapshot: withoutModels, outside: false })).toEqual({
			position: 'setup',
			restart: false
		});
		// While the finding is pending, treat it as waiting.
		const open = { ...ready, can_upload: false, setup_required: null };
		expect(gateNotice({ snapshot: open, outside: false })?.position).toBe('starts');
		expect(GATE_TEXT.setup.lock).toBe('dropzone.locked.setup');
	});

	it('reports outside status regardless of snapshot when called from outside the app', () => {
		expect(gateNotice({ snapshot: ready, outside: true })?.position).toBe('outside');
	});

	it('never mentions "Sidecar" in translations', () => {
		for (const texts of Object.values(GATE_TEXT)) {
			for (const key of Object.values(texts)) {
				expect(de[key]).not.toMatch(/sidecar/i);
			}
		}
	});
});
