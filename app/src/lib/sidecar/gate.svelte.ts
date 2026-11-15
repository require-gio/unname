import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { fetchHealth } from './client.js';
import {
	LOCKED,
	type DiagnosticsReport,
	type HealthReport,
	type SidecarConnection,
	type SidecarSnapshot
} from './types.js';

const STATUS_EVENT = 'sidecar://status';

/**
 * Local processing state as seen by the UI.
 *
 * Fail-closed in two places:
 *
 * 1. Initial state is `LOCKED`. Only a message from the shell opens the UI –
 *    not the absence of an error.
 * 2. When the UI runs without a shell (plain browser), everything stays locked.
 *    Errors in this layer must never let documents pass unchecked.
 *
 * Fail-closed means: **writes** stay locked. It does not mean the view crashes.
 * Reading – history, already loaded findings, the last computed state – continues
 * as long as the service runs. The first failed ping already discarded the
 * connection; a user who only browsed findings would then see an empty red page
 * without doing anything.
 *
 * Equally important: a re-check does **not** pre-emptively close an already
 * checked connection. Previously each status update locked then re-checked;
 * the UI treated a harmless message as a failure. Every `$effect` on
 * `connection` ran twice, views were torn down and rebuilt – visible as flicker
 * and a jump to the top of the page.
 */
export class SidecarGate {
	snapshot = $state<SidecarSnapshot>(LOCKED);
	connection = $state<SidecarConnection | null>(null);
	health = $state<HealthReport | null>(null);
	diagnostics = $state<DiagnosticsReport>({ entries: [], count: 0 });
	/** Reason why the shell is unreachable (no Tauri context, etc.). */
	harnessError = $state<string | null>(null);
	/** UI runs without a shell (plain browser) – nothing is processed here. */
	outside = $state(false);

	private unlisten: UnlistenFn | null = null;
	/** Prevent a late health response from overwriting newer state. */
	private revision = 0;
	/**
	 * Three views share this instance. Without counting, the last
	 * abandoned view would unregister the listener of the currently open one –
	 * the UI would then receive no more status updates.
	 */
	private user = 0;
	private registration: Promise<void> | null = null;

	get ready(): boolean {
		return (
			this.snapshot.status.state === 'ready' && this.snapshot.can_upload && this.snapshot.can_export
		);
	}

	get mayUpload(): boolean {
		return this.snapshot.can_upload;
	}

	get mayExport(): boolean {
		return this.snapshot.can_export;
	}

	/** There is an address that read-only calls can use. */
	get mayRead(): boolean {
		return this.connection !== null;
	}

	/**
	 * Service is running but not responding as expected.
	 *
	 * UI should show a subtle notice: only a real write attempt receives a denial.
	 */
	get impaired(): boolean {
		return this.connection !== null && !this.ready;
	}

	/**
	 * The lock is a waiting state, not a failure.
	 *
	 * Views colour their notice accordingly: waiting for start or a brief hiccup
	 * shows a calm notice, not a red alarm. Red is reserved for real failures
	 * so users don’t become desensitized.
	 */
	get lockTemporary(): boolean {
		return this.impaired || this.snapshot.status.state === 'starting';
	}

	/** Connect to the shell. Returns a logout function. */
	async start(): Promise<() => void> {
		if (!isTauri()) {
			this.outside = true;
			this.harnessError =
				'Diese Ansicht läuft ausserhalb der Anwendung. Dokumente laden und Export bleiben ' +
				'gesperrt, weil hier nichts verarbeitet werden kann.';
			return () => {};
		}

		this.user += 1;
		let abgemeldet = false;
		if (this.registration === null) {
			this.registration = this.register();
		}
		await this.registration;

		return () => {
			// Repeated calls of the same logout must not reduce the counter
			// below the actual number of users.
			if (abgemeldet) return;
			abgemeldet = true;
			this.user = Math.max(0, this.user - 1);
			if (this.user === 0) this.stop();
		};
	}

	stop(): void {
		this.revision += 1;
		this.unlisten?.();
		this.unlisten = null;
		this.registration = null;
	}

	async update(): Promise<void> {
		try {
			await this.accept(await invoke<SidecarSnapshot>('sidecar_status'));
		} catch (error) {
			this.revision += 1;
			// Log technical reason for debugging. Show user what they can do.
			console.warn('Die Hülle antwortet nicht:', error);
			this.harnessError =
				'Die Anwendung antwortet nicht mehr richtig. Ein Neustart der Anwendung behebt das ' +
				'in der Regel.';
			this.lock(LOCKED);
		}
	}

	async restart(): Promise<void> {
		await invoke('restart_sidecar');
	}

	async loadDiagnostics(): Promise<void> {
		const report = await invoke<DiagnosticsReport>('diagnostics_report');
		// Keep existing report instance to avoid re-rendering all views on each status update.
		if (report.count !== this.diagnostics.count) this.diagnostics = report;
	}

	private async register(): Promise<void> {
		const abmelden = await listen<SidecarSnapshot>(STATUS_EVENT, (event) => {
			void this.accept(event.payload);
		});
		// If the view is left while registration is pending, we would otherwise leak a listener.
		if (this.user === 0) {
			abmelden();
			return;
		}
		this.unlisten = abmelden;

		// The sidecar may become ready before the first UI event; query it once it is active.
		await this.update();
	}

	private async accept(snapshot: SidecarSnapshot): Promise<void> {
		const revision = ++this.revision;
		this.harnessError = null;

		if (snapshot.status.state !== 'ready') {
			// No active service to communicate with.
			this.snapshot = snapshot;
			this.connection = null;
			this.health = null;
			await this.loadDiagnostics();
			return;
		}

		// Even after a positive harness response we wait for a dedicated health check
		// for upload/export. If the verified connection already points to the same endpoint,
		// it remains valid across the re-check.
		try {
			const connection = await invoke<SidecarConnection | null>('sidecar_connection');
			if (revision !== this.revision) return;
			if (!connection) {
				this.lock(snapshot);
				await this.loadDiagnostics();
				return;
			}

			this.connection = this.stableConnection(connection);
			try {
				const health = await fetchHealth(connection);
				if (revision !== this.revision) return;
				this.snapshot = snapshot;
				this.health = health;
			} catch {
				if (revision !== this.revision) return;
				// Service is running but not responding; writes are locked, connection stays,
				// and the last known health report is kept instead of updating device and master-data state.
				this.snapshot = { ...snapshot, can_upload: false, can_export: false };
			}
		} catch {
			if (revision === this.revision) this.lock(snapshot);
		}
		await this.loadDiagnostics();
	}

	/**
	 * Keep the existing instance as long as address and sidecar process (`generation`) stay the same.
	 *
	 * Views attach their `$effect`s to this object. If a new, content-identical instance were created
	 * on each check, history and results would reload unnecessarily.
	 */
	private stableConnection(fresh: SidecarConnection): SidecarConnection {
		const alt = this.connection;
		if (alt && alt.base_url === fresh.base_url && alt.generation === fresh.generation) return alt;
		return fresh;
	}

	private lock(snapshot: SidecarSnapshot): void {
		this.snapshot = {
			...snapshot,
			can_upload: false,
			can_export: false
		};
		this.connection = null;
		this.health = null;
	}
}

export const sidecarGate = new SidecarGate();
