import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
	DiagnosticsReport,
	HealthReport,
	SidecarConnection,
	SidecarSnapshot
} from './types.js';

const { fetchHealthMock, invokeMock, listenMock } = vi.hoisted(() => ({
	fetchHealthMock: vi.fn<(connection: SidecarConnection) => Promise<HealthReport>>(),
	invokeMock: vi.fn<(command: string) => Promise<unknown>>(),
	listenMock: vi.fn()
}));

vi.mock('@tauri-apps/api/core', () => ({
	invoke: invokeMock,
	isTauri: () => true
}));
vi.mock('@tauri-apps/api/event', () => ({ listen: listenMock }));
vi.mock('./client.js', () => ({ fetchHealth: fetchHealthMock }));

import { SidecarGate } from './gate.svelte.js';

const CONNECTION: SidecarConnection = {
	base_url: 'unname-api://localhost',
	generation: 1
};

const READY: SidecarSnapshot = {
	status: { state: 'ready', port: null, transport: 'unix', pid: 42, version: '0.1.0' },
	restarts: 0,
	changed_at: '2026-07-30T12:00:00Z',
	can_upload: true,
	can_export: true
};

const BLOCKED_READY: SidecarSnapshot = {
	...READY,
	changed_at: '2026-07-30T12:00:05Z',
	can_upload: false,
	can_export: false
};

const HEALTH = {
	status: 'ok',
	service: 'presidio-sidecar',
	version: '0.1.0'
} as HealthReport;

const DIAGNOSTICS: DiagnosticsReport = { entries: [], count: 0 };

function mockShell(snapshot: SidecarSnapshot): void {
	invokeMock.mockImplementation(async (command) => {
		switch (command) {
			case 'sidecar_status':
				return snapshot;
			case 'sidecar_connection':
				return CONNECTION;
			case 'diagnostics_report':
				return DIAGNOSTICS;
			default:
				throw new Error(`Unerwarteter Command: ${command}`);
		}
	});
}

afterEach(() => {
	fetchHealthMock.mockReset();
	invokeMock.mockReset();
	listenMock.mockReset();
});

describe('SidecarGate Health-Fail-Closed', () => {
	it('blocks writes on failed health check, reads stay allowed', async () => {
		// A brief outage must block upload and export but keep the UI visible.
		// Users should not see a blank red page; they can still read existing data.
		mockShell(READY);
		fetchHealthMock.mockRejectedValue(new Error('antwortet nicht'));
		const gate = new SidecarGate();

		await gate.update();

		expect(gate.mayUpload).toBe(false);
		expect(gate.mayExport).toBe(false);
		expect(gate.snapshot.can_upload).toBe(false);
		expect(gate.connection).toEqual(CONNECTION);
		expect(gate.mayRead).toBe(true);
		expect(gate.impaired).toBe(true);
	});

	it('drops connection when shell stops reporting service', async () => {
		mockShell(READY);
		fetchHealthMock.mockResolvedValue(HEALTH);
		const gate = new SidecarGate();
		await gate.update();
		expect(gate.connection).not.toBeNull();

		mockShell({
			...READY,
			status: { state: 'unreachable', reason: 'beendet', restarting: true },
			can_upload: false,
			can_export: false
		});
		await gate.update();

		expect(gate.connection).toBeNull();
		expect(gate.health).toBeNull();
		expect(gate.mayRead).toBe(false);
	});

	it('reopens safely after proven health recovery', async () => {
		mockShell(READY);
		fetchHealthMock
			.mockRejectedValueOnce(new Error('kurzer Aussetzer'))
			.mockResolvedValueOnce(HEALTH);
		const gate = new SidecarGate();

		await gate.update();
		expect(gate.mayUpload).toBe(false);

		await gate.update();

		expect(gate.mayUpload).toBe(true);
		expect(gate.mayExport).toBe(true);
		expect(gate.connection).toEqual(CONNECTION);
		expect(gate.health).toBe(HEALTH);
	});

	it('keeps read access open when the shell blocks Ready state', async () => {
		// The shell blocks upload and export, but the process continues —
		// so the result list stays usable instead of disappearing.
		mockShell(BLOCKED_READY);
		fetchHealthMock.mockResolvedValue(HEALTH);
		const gate = new SidecarGate();

		await gate.update();

		expect(gate.mayUpload).toBe(false);
		expect(gate.mayExport).toBe(false);
		expect(gate.mayRead).toBe(true);
		expect(gate.connection).toEqual(CONNECTION);
	});

	it('preserves the reviewed connection across a re-check', async () => {
		// After being blocked and then reopened, each `$effect` on `connection`
		// ran twice: views were torn down and rebuilt — visible as flicker and a jump to the start.
		mockShell(READY);
		let resolve!: (health: HealthReport) => void;
		const schwebend = new Promise<HealthReport>((next) => {
			resolve = next;
		});
		fetchHealthMock.mockResolvedValueOnce(HEALTH).mockReturnValueOnce(schwebend);
		const gate = new SidecarGate();

		await gate.update();
		const reviewed = gate.connection;
		expect(reviewed).not.toBeNull();

		const second = gate.update();
		// The second health check is still running here. The already
		// verified connection remains the exact same instance.
		await vi.waitFor(() => expect(fetchHealthMock).toHaveBeenCalledTimes(2));
		expect(gate.connection).toBe(reviewed);
		expect(gate.mayUpload).toBe(true);

		resolve(HEALTH);
		await second;
		expect(gate.connection).toBe(reviewed);
	});

	it('returns the same instance for the same endpoint', async () => {
		// A new but identical instance would trigger every `$effect` again
		// and reload history and result without cause.
		mockShell(READY);
		fetchHealthMock.mockResolvedValue(HEALTH);
		const gate = new SidecarGate();

		await gate.update();
		const first = gate.connection;
		await gate.update();

		expect(gate.connection).toBe(first);
	});

	it('unsubscribes the listener only when the last view releases it', async () => {
		// Three views share the instance. Without counting, the
		// view that was closed last unsubscribed the listener of the currently open view.
		mockShell(READY);
		fetchHealthMock.mockResolvedValue(HEALTH);
		const abmelden = vi.fn();
		listenMock.mockResolvedValue(abmelden);
		const gate = new SidecarGate();

		const firstView = await gate.start();
		const secondView = await gate.start();
		expect(listenMock).toHaveBeenCalledTimes(1);

		firstView();
		expect(abmelden).not.toHaveBeenCalled();

		secondView();
		expect(abmelden).toHaveBeenCalledTimes(1);
	});

	it('clears a previously healthy state if the shell fails', async () => {
		mockShell(READY);
		fetchHealthMock.mockResolvedValue(HEALTH);
		const gate = new SidecarGate();
		await gate.update();
		expect(gate.connection).toEqual(CONNECTION);

		invokeMock.mockRejectedValue(new Error('Hülle beendet'));
		await gate.update();

		expect(gate.mayUpload).toBe(false);
		expect(gate.mayExport).toBe(false);
		expect(gate.connection).toBeNull();
		expect(gate.health).toBeNull();
		// Technical details are logged. The UI shows what the user can do —
		// “harness finished” does not answer any of their questions.
		expect(gate.harnessError).toContain('Neustart der Anwendung');
		expect(gate.harnessError?.toLowerCase()).not.toContain('sidecar');
	});
});
