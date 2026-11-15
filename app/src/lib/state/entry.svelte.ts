import { untrack } from 'svelte';
import { samePreset } from '#lib/masking/rules.js';
import { CATALOG, type CategoryId } from '#lib/masking/catalog.js';
import {
	asReview,
	fromSaved,
	touchedMatch,
	decide,
	questions,
	correctBoundary,
	removeManualEntry,
	EMPTY_STATE,
	addLater,
	openQuestions,
	addLaterOccurrence,
	undo,
	type Action,
	type Section,
	type Question,
	type Origin,
	type ReviewState
} from '#lib/review/state.js';
import type { MessageKey } from '#lib/i18n/translate.js';
import { ApplyQueue } from '#lib/review/queue.js';
import { reviewIsStored } from '#lib/review/review-persistence.js';
import {
	applyMasking,
	cancelJob,
	deleteEntry,
	fetchEntry,
	fetchRedetectStatus,
	redetectEntry,
	fetchEntryView,
	fetchEntryProgress,
	fetchFindings,
	fetchLayout,
	findOccurrences,
	removeEntryFile,
	retryEntryFile,
	updateEntryView
} from '#lib/sidecar/api-v2.js';
import type {
	ApplyRequest,
	ApplyResponse,
	Entry,
	EntryFile,
	EntryView,
	FindingsResponse,
	LayoutResponse,
	OccurrencesResponse,
	ProgressResponse,
	RedetectFile
} from '#lib/sidecar/api.gen.js';
import { SidecarError } from '#lib/sidecar/client.js';
import {
	submitSelection,
	selectFiles,
	type Selection,
	type SubmitResult
} from '#lib/sidecar/inputs.js';
import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
import {
	saveEntry,
	istStorageError,
	reimportOriginals,
	type Reimported,
	type StorageResult,
	type StorageError
} from '#lib/sidecar/save.js';
import {
	expired,
	confirmationKey,
	lastSaving,
	needingOriginal,
	sinceTheSaveChanged
} from '#lib/saved/saved.js';
import { spaceReview } from '#lib/history/disk-space.js';
import { dockShare, dockProgress, reportDone } from '#lib/sidecar/system.js';
import type { SidecarConnection } from '#lib/sidecar/types.js';
import { settings } from './settings.svelte.js';
import { t } from './i18n.svelte.js';
import { Masking } from './masking.svelte.js';
import { start } from './start.svelte.js';
import { history } from './history.svelte.js';

/**
 * An open entry: detection running and review pending.
 *
 * Holds the entry's files with findings, review state and the latest apply
 * result, plus the entry's masking. Both views share this state so that when
 * detection finishes and review starts, the files are already loaded.
 *
 * Rule: The UI reflects the sidecar's answer. Any change to masking or state
 * queues an apply (dry-run) for the file; apply does not modify the translation
 * table.
 */

const NACHFRAGEN_MS = 1000;
const AGAIN_MS = 5000;
/** Keep jobs alive while the entry is open (TTL 15 min). */
const WACHHALTEN_MS = 4 * 60_000;

interface Job {
	connection: SidecarConnection;
	request: ApplyRequest;
	key: string;
}

export class File {
	readonly id: string;
	info = $state.raw({} as EntryFile);
	findings = $state.raw<FindingsResponse | null>(null);
	/**
	 * Mapping of each word's position in the rendered document. If layout is
	 * unavailable or fails, the review view shows the raw text.
	 */
	layout = $state.raw<LayoutResponse | null>(null);
	status = $state.raw<ReviewState>(EMPTY_STATE);
	result = $state.raw<ApplyResponse | null>(null);
	/** Which request the result applies to. */
	resultFor = $state<string | null>(null);
	loads = $state(false);
	ladefehler = $state(false);
	calculates = $state(false);
	applyError = $state(false);
	#gestellt: string | null = null;
	#latest: Job | null = null;
	readonly queue: ApplyQueue<Job, ApplyResponse>;

	constructor(info: EntryFile) {
		this.id = info.id;
		this.info = info;
		this.queue = new ApplyQueue<Job, ApplyResponse>(
			(job) => applyMasking(job.connection, this.id, job.request),
			{
				result: (result, job) => {
					this.result = result;
					this.resultFor = job.key;
					this.applyError = false;
				},
				error: () => {
					this.applyError = true;
					this.#gestellt = null;
				},
				running: (running) => (this.calculates = running)
			}
		);
	}

	get ready(): boolean {
		return this.info.status === 'review';
	}

	/** Enqueue an apply when the job changed. */
	places(job: Job): void {
		if (job.key === this.#gestellt) return;
		this.#gestellt = job.key;
		this.#latest = job;
		this.queue.places(job);
	}

	async flush(entryId: string): Promise<void> {
		await this.queue.flush();
		const latest = this.#latest;
		if (!this.applyError || !latest) return;
		const confirmed = async () => {
			try {
				const entry = await fetchEntry(sidecarGate.connection ?? latest.connection, entryId);
				return (
					!entry.files?.some((file) => file.id === this.id) ||
					reviewIsStored(latest.request, entry, this.id)
				);
			} catch (error) {
				if (error instanceof SidecarError && error.status === 404) return true;
				throw error;
			}
		};
		if (await confirmed()) return;
		this.#gestellt = null;
		this.places({ ...latest, connection: sidecarGate.connection ?? latest.connection });
		await this.queue.flush();
		if (this.applyError && !(await confirmed()))
			throw new Error('The latest review could not be saved.');
	}

	again(): void {
		this.#gestellt = null;
	}
}

export type AddendumResult =
	{ ok: true; fresh: number; files: number } | { ok: false; reason: MessageKey };

/** Result of “Save all n files”; null means the dialog was cancelled. */
export type StorageExit =
	{ ok: true; result: StorageResult } | { ok: false; error: StorageError } | null;

class EntryViewModel {
	id = $state<string | null>(null);
	entry = $state.raw<Entry | null>(null);
	progress = $state.raw<ProgressResponse | null>(null);
	error = $state<'missing' | 'error' | null>(null);
	files = $state.raw<File[]>([]);
	saves = $state(false);
	rereads = $state(false);
	view = $state.raw<EntryView | null>(null);
	viewLoaded = $state(false);
	/** Files whose detection no longer matches the current settings. */
	redetectFiles = $state.raw<RedetectFile[]>([]);
	detectsAgain = $state(false);
	/** Draft version for which the offer was last reviewed. */
	#redetectFor: number | null = null;
	pendingFiles = $state.raw<ReadonlySet<string>>(new Set());
	readonly masking = new Masking('reversible', CATALOG, () => settings.values?.masking);
	#timer: ReturnType<typeof setTimeout> | undefined;
	#open = 0;
	#generation = 0;
	/** The previous request was still pending; the completion will be reported later. */
	#lief = false;
	#viewTimer: ReturnType<typeof setTimeout> | undefined;
	#viewDirty = false;
	#viewWrite: Promise<void> = Promise.resolve();
	#viewCapture: (() => void) | null = null;

	/** Open the entry and keep polling while something is running. Returns a close function. */
	open(id: string): () => void {
		untrack(() => {
			if (this.id !== id) {
				void this.flush().catch((error) =>
					console.warn('Could not flush the previous entry.', error)
				);
				this.#generation += 1;
				this.id = id;
				this.entry = null;
				this.progress = null;
				this.error = null;
				this.files = [];
				this.#lief = false;
				this.view = null;
				this.viewLoaded = false;
				this.#viewDirty = false;
				this.pendingFiles = new Set();
				this.redetectFiles = [];
				this.#redetectFor = null;
			}
			this.#open += 1;
			clearTimeout(this.#timer);
			void this.#takt();
		});
		return () => {
			this.#open -= 1;
			if (this.#open === 0) {
				void this.flush().catch((error) => console.warn('Could not flush the entry.', error));
				clearTimeout(this.#timer);
				// No open view, so no progress bar is shown in the dock.
				dockProgress(null);
				this.#lief = false;
			}
		};
	}

	/** View artifacts are encrypted separately; opening an entry does not renew its retention. */
	updateView(change: Partial<EntryView>): void {
		if (!this.viewLoaded || !this.id || this.entry?.kind !== 'documents') return;
		const view: EntryView = {
			active_file_id: null,
			representation: 'page',
			preview: 'result',
			page: 1,
			anchor: null,
			focused_question_id: null,
			...this.view,
			...change
		};
		if (JSON.stringify(view) === JSON.stringify(this.view)) return;
		this.view = view;
		this.#viewDirty = true;
		clearTimeout(this.#viewTimer);
		this.#viewTimer = setTimeout(() => {
			void this.#flushView().catch((error) => console.warn('Could not save the view.', error));
		}, 1000);
	}

	async #flushView(keepalive = false): Promise<void> {
		clearTimeout(this.#viewTimer);
		const id = this.id;
		const view = this.view;
		const connection = sidecarGate.connection;
		if (this.#viewDirty && id && view && !connection) {
			throw new Error('The local processor is unavailable.');
		}
		if (this.#viewDirty && id && view && connection) {
			this.#viewDirty = false;
			this.#viewWrite = this.#viewWrite
				.catch(() => {})
				.then(async () => {
					try {
						await updateEntryView(connection, id, view, keepalive);
					} catch (error) {
						if (error instanceof SidecarError && error.status === 404) {
							if (this.id === id) {
								this.view = null;
								this.#viewDirty = false;
								this.entry = null;
								this.files = [];
								this.error = 'missing';
							}
							return;
						}
						if (this.id === id && this.view === view) this.#viewDirty = true;
						throw error;
					}
				});
		}
		await this.#viewWrite;
	}

	/** Route changes and native close/quit wait for the newest decision and view. */
	async flush(keepalive = false): Promise<void> {
		for (;;) {
			this.#viewCapture?.();
			const id = this.id;
			const view = this.view;
			const files = this.files;
			const requests = files.map((file) => this.job(file).key);
			await Promise.all([
				this.#flushView(keepalive),
				...files.map((file) => (id ? file.flush(id) : file.queue.flush()))
			]);
			if (this.id !== id) return;
			this.#viewCapture?.();
			// Input can still arrive while a slow disk write or preview is finishing.
			if (
				!this.#viewDirty &&
				view === this.view &&
				files === this.files &&
				files.every((file, index) => !file.queue.running && requests[index] === this.job(file).key)
			)
				return;
		}
	}

	registerViewCapture(capture: () => void): () => void {
		this.#viewCapture = capture;
		return () => {
			if (this.#viewCapture === capture) this.#viewCapture = null;
		};
	}

	/** Reload entry and progress immediately, e.g., after adding. */
	update(): void {
		clearTimeout(this.#timer);
		void this.#takt();
	}

	get running(): boolean {
		return (this.entry?.files ?? []).some(
			(file) => file.status === 'queued' || file.status === 'processing'
		);
	}

	get prepareFiles(): File[] {
		return this.files.filter((file) => file.ready);
	}

	/** Entry masking matches saved default. */
	get istStandard(): boolean {
		const saved = settings.values?.masking;
		return saved ? samePreset(this.masking.asPreset(), saved) : false;
	}

	async #takt(): Promise<void> {
		const id = this.id;
		const generation = this.#generation;
		const connection = sidecarGate.connection;
		if (!id) return;
		if (!connection) {
			this.#plan(AGAIN_MS);
			return;
		}
		try {
			const entry = await fetchEntry(connection, id);
			if (generation !== this.#generation) return;
			if (!this.viewLoaded) {
				const view = entry.kind === 'documents' ? await fetchEntryView(connection, id) : null;
				if (generation !== this.#generation) return;
				if (!this.viewLoaded) this.view = view ?? null;
				this.viewLoaded = true;
			}
			const before = this.entry?.status;
			this.#accept(entry);
			if (before !== undefined && before !== entry.status) void history.update();
			if (entry.kind === 'documents' && (entry.files?.length ?? 0) > 0) {
				const status = await fetchEntryProgress(connection, id);
				if (generation !== this.#generation) return;
				this.progress = status;
				dockProgress(dockShare(status));
			}
			this.#reportDone(entry);
			this.error = null;
			void this.#loadFiles(connection, generation);
			this.#redetectReview(connection, entry, generation);
			this.#plan(this.running ? NACHFRAGEN_MS : WACHHALTEN_MS);
		} catch (cause) {
			if (generation !== this.#generation) return;
			if (cause instanceof SidecarError && cause.status === 404) {
				this.error = 'missing';
				return;
			}
			this.error = 'error';
			this.#plan(AGAIN_MS);
		}
	}

	/**
	 * Check if detection still matches settings. Runs once per draft state when idle; reopening never auto-detects, only offers.
	 */
	#redetectReview(connection: SidecarConnection, entry: Entry, generation: number): void {
		const revision = entry.draft_revision ?? 0;
		if (entry.kind !== 'documents' || this.running || this.#redetectFor === revision) return;
		this.#redetectFor = revision;
		fetchRedetectStatus(connection, entry.id)
			.then((answer) => {
				if (generation === this.#generation) this.redetectFiles = answer.files;
			})
			.catch(() => {
				if (generation === this.#generation) this.#redetectFor = null;
			});
	}

	/** "Redetect": from saved state, without original. */
	async redetect(): Promise<void> {
		const id = this.id;
		const connection = sidecarGate.connection;
		if (!id || !connection || this.detectsAgain) return;
		this.detectsAgain = true;
		try {
			const answer = await redetectEntry(connection, id, {
				file_ids: this.redetectFiles.map((file) => file.file_id)
			});
			if (this.id !== id) return;
			const fresh = new Map((answer.entry.files ?? []).map((file) => [file.id, file]));
			const betroffen = new Set(answer.files.map((file) => file.file_id));
			this.files = this.files.map((file) => {
				const info = fresh.get(file.id);
				return betroffen.has(file.id) && info ? new File(info) : file;
			});
			this.redetectFiles = [];
			this.#accept(answer.entry);
			this.update();
			void history.update();
		} finally {
			this.detectsAgain = false;
		}
	}

	/** "n files done" when detection just finished. */
	#reportDone(entry: Entry): void {
		const lief = this.running;
		if (this.#lief && !lief) {
			dockProgress(null);
			const done = (entry.files ?? []).filter((file) => file.status === 'review').length;
			if (done > 0) {
				reportDone(t('notice.done.title'), t('notice.done.text', { n: done }));
			}
		}
		this.#lief = lief;
	}

	#plan(ms: number): void {
		clearTimeout(this.#timer);
		if (this.#open > 0) this.#timer = setTimeout(() => void this.#takt(), ms);
	}

	/**
	 * A rename from the history (H4): only the title changes. The document,
	 * its queues and the review state stay as they are.
	 */
	acceptTitle(entry: Entry): void {
		if (this.id !== entry.id || !this.entry) return;
		this.entry = { ...this.entry, title: entry.title, title_custom: entry.title_custom };
	}

	#accept(entry: Entry): void {
		const first = this.entry === null;
		this.entry = entry;
		if (first) this.masking.accept(entry.masking);
		const previous = new Map(this.files.map((file) => [file.id, file]));
		const files = (entry.files ?? []).map((info) => {
			const file = previous.get(info.id) ?? new File(info);
			file.info = info;
			return file;
		});
		const same =
			files.length === this.files.length &&
			files.every((file, index) => file === this.files[index]);
		if (!same) this.files = files;
	}

	async #loadFiles(connection: SidecarConnection, generation: number): Promise<void> {
		for (const file of this.files) {
			if (!file.ready || file.findings || file.loads || file.ladefehler) continue;
			file.loads = true;
			try {
				const findings = await fetchFindings(connection, file.id);
				if (generation !== this.#generation) return;
				file.findings = findings;
				file.status = fromSaved(this.entry?.review?.[file.id], findings.findings);
				void this.#loadLayout(connection, file, generation);
			} catch {
				if (generation === this.#generation) file.ladefehler = true;
			} finally {
				file.loads = false;
			}
		}
	}

	/**
	 * Load the layout map once per file. If it fails, keep the view on the text –
	 * this is a fallback, not an error shown to the user.
	 */
	async #loadLayout(connection: SidecarConnection, file: File, generation: number): Promise<void> {
		if (file.layout) return;
		try {
			const layout = await fetchLayout(connection, file.id);
			if (generation === this.#generation) file.layout = layout;
		} catch (error) {
			console.warn('Layout-Karte nicht verfügbar, zeige Text.', error);
			if (generation === this.#generation) file.layout = { job_id: file.id, kind: 'text' };
		}
	}

	/** Loading or processing a file failed: try again. */
	loadAgain(file: File): void {
		file.ladefehler = false;
		file.applyError = false;
		file.again();
		if (file.findings) {
			this.apply(file);
			return;
		}
		const connection = sidecarGate.connection;
		if (connection) void this.#loadFiles(connection, this.#generation);
	}

	async retryFile(fileId: string): Promise<void> {
		const id = this.id;
		const connection = sidecarGate.connection;
		if (!id || !connection || this.pendingFiles.has(fileId)) return;
		this.pendingFiles = new Set([...this.pendingFiles, fileId]);
		try {
			const response = await retryEntryFile(connection, id, fileId);
			if (this.id !== id) return;
			this.files = this.files.map((file) => (file.id === fileId ? new File(response.file) : file));
			this.#accept(response.entry);
			this.update();
			void history.update();
		} catch (error) {
			if (this.id === id) this.update();
			throw error;
		} finally {
			this.pendingFiles = new Set([...this.pendingFiles].filter((pending) => pending !== fileId));
		}
	}

	async removeFile(fileId: string): Promise<void> {
		const id = this.id;
		const connection = sidecarGate.connection;
		if (!id || !connection || this.pendingFiles.has(fileId)) return;
		this.pendingFiles = new Set([...this.pendingFiles, fileId]);
		try {
			const entry = await removeEntryFile(connection, id, fileId);
			if (this.id !== id) return;
			this.#accept(entry);
			if (this.view?.active_file_id === fileId) {
				this.updateView({
					active_file_id: entry.files?.[0]?.id ?? null,
					page: 1,
					anchor: null,
					focused_question_id: null
				});
			}
			void history.update();
		} finally {
			this.pendingFiles = new Set([...this.pendingFiles].filter((pending) => pending !== fileId));
		}
	}

	/* -- Apply ------------------------------------------------------------- */

	categoryActive = (category: string): boolean =>
		this.masking.categories.has(category as CategoryId);

	questionsBy(file: File): Question[] {
		if (!file.findings) return [];
		return questions(file.findings.findings, file.status, this.categoryActive);
	}

	openBy(file: File): number {
		return openQuestions(this.questionsBy(file)).length;
	}

	/** The request currently applicable to the file. */
	job(file: File): { request: ApplyRequest; key: string } {
		const request = { masking: this.masking.asRequest(), review: asReview(file.status) };
		return { request, key: JSON.stringify(request) };
	}

	/** Does the file have a response for the current state in the window? */
	current(file: File): boolean {
		return !file.calculates && file.resultFor === this.job(file).key;
	}

	/** Compute the current state of the file if it hasn't been set yet. */
	apply(file: File): void {
		const connection = sidecarGate.connection;
		untrack(() => {
			if (!connection || !file.findings) return;
			file.places({ connection, ...this.job(file) });
		});
	}

	#changed(file: File, status: ReviewState): void {
		file.status = status;
		this.apply(file);
	}

	/* -- decisions ---------------------------------------------------- */

	decide(file: File, findingId: string, action: Action, by: Origin = 'user'): void {
		this.#changed(file, decide(file.status, findingId, action, by));
	}

	undo(file: File, findingId: string): void {
		this.#changed(file, undo(file.status, findingId));
	}

	removeHand(file: File, id: string): void {
		this.#changed(file, removeManualEntry(file.status, id));
	}

	/** Apply several decisions at once; returns the previous state for undo. */
	decideAll(file: File, decisions: readonly (readonly [string, Action])[], by: Origin): () => void {
		const before = file.status;
		let status = before;
		for (const [id, action] of decisions) status = decide(status, id, action, by);
		this.#changed(file, status);
		return () => this.#changed(file, before);
	}

	/**
	 * Mask a marking. If it touches a detected place, its boundary is corrected
	 * (E7); otherwise it is added – using `occurrence` in all files of the entry.
	 */
	maskMarking(
		file: File,
		section: Section,
		type: string,
		occurrence: OccurrencesResponse | null
	): AddendumResult {
		const findings = file.findings?.findings;
		if (!findings) return { ok: false, reason: 'addendum.unknown' };
		const touched = touchedMatch(file.status, findings, section);
		if (touched.hand.length > 0) return { ok: false, reason: 'addendum.overlaps' };
		if (touched.findings.length > 1) return { ok: false, reason: 'addendum.several' };
		if (touched.findings.length === 1) {
			const target = touched.findings[0];
			const result = correctBoundary(file.status, findings, target.id, {
				start: Math.min(section.start, target.start),
				end: Math.max(section.end, target.end)
			});
			if (!result.ok) return result;
			this.#changed(file, result.status);
			return { ok: true, fresh: 1, files: 1 };
		}
		if (!occurrence) {
			const result = addLater(file.status, findings, { ...section, type });
			if (!result.ok) return result;
			this.#changed(file, result.status);
			return { ok: true, fresh: 1, files: 1 };
		}
		return this.maskOccurrence(type, occurrence, { file, section });
	}

	/**
	 * Add all occurrences of a string in all files. Detected matches (`covered`)
	 * and already occupied places stay unchanged. With `marking`, the marked place
	 * itself is always included—even if it is not a whole word and the search
	 * would miss it.
	 */
	maskOccurrence(
		type: string,
		occurrence: OccurrencesResponse,
		marking?: { file: File; section: Section }
	): AddendumResult {
		let fresh = 0;
		let betroffen = 0;
		for (const kandidat of this.files) {
			const own = kandidat.findings?.findings;
			if (!own) continue;
			const sections = occurrence.matches
				.filter((match) => match.file_id === kandidat.id && !match.covered)
				.map(({ start, end }) => ({ start, end }));
			if (marking?.file === kandidat && !sections.some((b) => b.start === marking.section.start)) {
				sections.unshift(marking.section);
			}
			const result = addLaterOccurrence(kandidat.status, own, type, sections);
			if (result.fresh === 0) continue;
			fresh += result.fresh;
			betroffen += 1;
			this.#changed(kandidat, result.status);
		}
		return { ok: true, fresh, files: betroffen };
	}

	/** Shorten a place to the marking — “captured too much” (E7). */
	shortenPlace(file: File, findingId: string, section: Section): AddendumResult {
		const findings = file.findings?.findings;
		if (!findings) return { ok: false, reason: 'addendum.unknown' };
		const result = correctBoundary(file.status, findings, findingId, section);
		if (!result.ok) return result;
		this.#changed(file, result.status);
		return { ok: true, fresh: 1, files: 1 };
	}

	searchOccurrence(text: string, signal?: AbortSignal): Promise<OccurrencesResponse> | null {
		const connection = sidecarGate.connection;
		if (!connection || !this.id || text.trim().length < 2) return null;
		// Case-insensitive search as the sidecar does when masking:
		return findOccurrences(connection, this.id, { text: text.trim(), ignore_case: true }, signal);
	}

	/* -- entry ----------------------------------------------------------- */

	/** "+" – submit additional files via the shell into the same entry. */
	async addFiles(title: string): Promise<SubmitResult | null> {
		if (!this.id || !sidecarGate.mayUpload) return null;
		const selection = await selectFiles(false, title);
		if (!selection) return null;
		return this.submit(selection);
	}

	/** Submit a selection from the shell (dialog or dropzone). */
	async submit(selection: Selection): Promise<SubmitResult | null> {
		const id = this.id;
		if (!id || !sidecarGate.mayUpload || selection.names.length === 0) return null;
		if (sidecarGate.connection) void spaceReview(sidecarGate.connection, t);
		const result = await submitSelection(id, selection.id);
		void history.update();
		this.update();
		return result;
	}

	/**
	 * "Cancel": stop the ongoing detection. The entry disappears from history,
	 * leaving no trace. Returns true when the entry was deleted.
	 */
	async cancel(): Promise<boolean> {
		const connection = sidecarGate.connection;
		const entry = this.entry;
		if (!connection || !entry) return false;
		const laufend = (entry.files ?? []).filter(
			(file) => file.status === 'queued' || file.status === 'processing'
		);
		await Promise.all(
			laufend.map((file) =>
				cancelJob(connection, file.id).catch((error: unknown) => {
					// Already finished or expired: nothing left to cancel.
					if (!(error instanceof SidecarError) || (error.status !== 404 && error.status !== 409)) {
						throw error;
					}
				})
			)
		);
		const stillReviewable = (entry.files ?? []).some((file) => file.status === 'review');
		if (!stillReviewable) {
			await deleteEntry(connection, entry.id);
			this.#generation += 1;
			this.id = null;
			void history.update();
			return true;
		}
		void history.update();
		this.update();
		return false;
	}

	/* -- save -------------------------------------------------------------- */

	/** Files whose job data has expired – only archived copies. */
	get expiredFiles(): { id: string; name: string }[] {
		return expired(this.entry?.files ?? []);
	}

	/** Files that need their original again: archived copies and `unavailable`. */
	get filesNeedingOriginal(): { id: string; name: string }[] {
		return needingOriginal(this.entry?.files ?? []);
	}

	/** Reason why "Save" is currently disabled – or `null`. */
	get saveLock(): MessageKey | null {
		if (this.running) return 'save.locked.running';
		if (this.expiredFiles.length > 0) return 'save.locked.expired';
		if (this.filesNeedingOriginal.length > 0) return 'save.locked.unavailable';
		if (this.prepareFiles.length === 0) return 'save.locked.nothing';
		return null;
	}

	/**
	 * Does this save require confirmation? First irreversible save and again
	 * when method or style changed since the last confirmation. The sidecar
	 * checks the same condition.
	 */
	get confirmationNeeded(): boolean {
		if (this.masking.method !== 'irreversible') return false;
		const confirmed = this.entry?.irreversible_confirmed_for;
		return confirmed !== confirmationKey(this.masking.asRequest());
	}

	/** A saved entry is being saved again: "Save again". */
	get saveAgain(): boolean {
		return this.entry?.status === 'saved';
	}

	/** Work has continued since the last save. */
	get sinceTheSaveChanged(): boolean {
		return this.entry ? sinceTheSaveChanged(this.entry) : false;
	}

	/**
	 * "Save all n files": the final state shown in the window – masking of the
	 * entry and review state per file. The sidecar recomputes it with
	 * "mask if in doubt", and the shell publishes the result.
	 */
	async save(options: {
		confirmed: boolean;
		select: boolean;
		folderName: string;
		dialogTitle: string;
		/** Finder tag for each copy. */
		tag: string;
	}): Promise<StorageExit> {
		const id = this.id;
		if (!id || this.saves) return null;
		this.saves = true;
		try {
			const result = await saveEntry(
				id,
				{
					masking: this.masking.asRequest(),
					files: this.prepareFiles
						.filter((file) => file.findings !== null)
						.map((file) => ({ file_id: file.id, review: asReview(file.status) })),
					confirm_irreversible: options.confirmed
				},
				{
					folderName: options.folderName,
					select: options.select,
					dialogTitle: options.dialogTitle,
					tag: options.tag,
					lastFolder: this.entry ? lastSaving(this.entry).folder : null
				}
			);
			if (!result) return null;
			if (this.id === id) this.#accept(result.entry);
			void history.update();
			return { ok: true, result };
		} catch (error) {
			if (istStorageError(error)) return { ok: false, error };
			return {
				ok: false,
				error: { code: 'save_failed', message: String(error), file_id: null }
			};
		} finally {
			this.saves = false;
		}
	}

	/**
	 * "Select original" for archived copies (E9) and unreadable copies:
	 * the shell re-imports the originals (known ones automatically, otherwise via
	 * dialog); its decisions are propagated to the sidecar. All other files open
	 * from the draft without original.
	 */
	async reimportOriginals(title: string): Promise<Reimported | null> {
		const id = this.id;
		const files = this.filesNeedingOriginal;
		if (!id || files.length === 0 || this.rereads) return null;
		this.rereads = true;
		try {
			const result = await reimportOriginals(id, files, title);
			void history.update();
			this.update();
			return result;
		} finally {
			this.rereads = false;
		}
	}

	/** Make this entry's masking the default for all new entries. */
	async acceptAsStandard(): Promise<void> {
		const connection = sidecarGate.connection;
		if (!connection) throw new Error('keine Verbindung');
		const preset = this.masking.asPreset();
		await settings.change(connection, { masking: preset });
		start.masking.accept(preset);
	}
}

export const entryViewModel = new EntryViewModel();
