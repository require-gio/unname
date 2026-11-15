import { isTauri } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { applyEvent } from '#lib/models/setup.js';
import {
	MODELS_EVENT,
	cancel,
	remove,
	herunterladen,
	loadModels,
	installPackage,
	review,
	setSource,
	type ModelEvent,
	type ModelError,
	type ModelOverview,
	type ReviewReport,
	type SourceChoice
} from '#lib/sidecar/models.js';

/**
 * Downloadable models: initial setup and settings ›
 * Models and storage share this state.
 *
 * The shell reports each step via `models://progress`; the overview follows
 * events and is refreshed after a task completes. What the shell reports
 * appears in the window – no guessing.
 */
class Models {
	overview = $state<ModelOverview | null>(null);
	/** Error from the last completed task; `cancelled` is ignored. */
	error = $state<ModelError | null>(null);
	notAvailable = $state(false);

	private abmelden: UnlistenFn | null = null;
	private user = 0;

	get running(): boolean {
		return this.overview?.busy ?? false;
	}

	/** Subscribe to events; returns the unlisten function. */
	async listen(): Promise<() => void> {
		if (!isTauri()) {
			this.notAvailable = true;
			return () => {};
		}
		this.user += 1;
		if (!this.abmelden) {
			this.abmelden = await listen<ModelEvent>(MODELS_EVENT, (event) => this.accept(event.payload));
		}
		await this.load();
		let done = false;
		return () => {
			if (done) return;
			done = true;
			this.user = Math.max(0, this.user - 1);
			if (this.user === 0) {
				this.abmelden?.();
				this.abmelden = null;
			}
		};
	}

	async load(): Promise<void> {
		try {
			this.overview = await loadModels();
			this.notAvailable = false;
		} catch {
			this.notAvailable = true;
		}
	}

	accept(event: ModelEvent): void {
		if (this.overview) this.overview = applyEvent(this.overview, event);
		if (event.component === null) {
			this.error = event.error && event.error.code !== 'cancelled' ? event.error : null;
			void this.load();
		}
	}

	async herunterladen(ids: readonly string[]): Promise<void> {
		this.error = null;
		await herunterladen(ids);
		if (this.overview) this.overview = { ...this.overview, busy: true };
	}

	async cancel(): Promise<void> {
		await cancel();
	}

	async source(choice: SourceChoice): Promise<void> {
		this.overview = await setSource(choice);
	}

	/** `true` if a package was installed; `false` means cancelled. */
	async bundle(dialog: { title: string; filter: string }): Promise<boolean> {
		this.error = null;
		if (this.overview) this.overview = { ...this.overview, busy: true };
		try {
			const fresh = await installPackage(dialog);
			if (fresh) this.overview = fresh;
			return fresh !== null;
		} finally {
			await this.load();
		}
	}

	async review(): Promise<ReviewReport> {
		if (this.overview) this.overview = { ...this.overview, busy: true };
		try {
			const report = await review();
			this.overview = report.overview;
			return report;
		} finally {
			await this.load();
		}
	}

	async remove(id: string): Promise<void> {
		this.overview = await remove(id);
	}
}

export const models = new Models();
