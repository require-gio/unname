import {
	applyTemplate,
	deleteList,
	exportTemplate,
	fetchLists,
	fetchPatterns,
	fetchTemplates,
	fetchTerms,
	importTemplate,
	saveTemplate,
	updatePatterns,
	updateTerms
} from '#lib/sidecar/api-v2.js';
import type {
	ListsResponse,
	Pattern,
	PatternIn,
	TemplateApplyResponse,
	TemplatesResponse,
	Term,
	TermIn
} from '#lib/sidecar/api.gen.js';
import { updateList, addList, type Dialog } from '#lib/sidecar/settings.js';
import type { SidecarConnection } from '#lib/sidecar/types.js';
import { settings } from './settings.svelte.js';

export type KindOfInfo = Term['entity_type'];

/**
 * Settings › Names & Terms: name lists, custom terms,
 * custom patterns and templates — exactly as stored by the sidecar.
 *
 * Every change is sent immediately to the sidecar; only its response
 * updates the UI. A pattern that the sidecar rejects never appears.
 */
class Names {
	listen = $state<ListsResponse | null>(null);
	terms = $state<Term[]>([]);
	pattern = $state<Pattern[]>([]);
	templates = $state<TemplatesResponse | null>(null);
	loaded = $state(false);

	/** Show a notice when any list is older than one week. */
	get notice(): boolean {
		return this.listen?.lists.some((list) => list.stale) ?? false;
	}

	async load(connection: SidecarConnection): Promise<void> {
		const [listen, terms, pattern, templates] = await Promise.all([
			fetchLists(connection),
			fetchTerms(connection),
			fetchPatterns(connection),
			fetchTemplates(connection)
		]);
		this.listen = listen;
		this.terms = terms.terms;
		this.pattern = pattern.patterns;
		this.templates = templates;
		this.loaded = true;
	}

	/** Load only the lists – needed for the notice, even when the section is collapsed. */
	async loadListen(connection: SidecarConnection): Promise<void> {
		this.listen = await fetchLists(connection);
	}

	// -- Name lists --------------------------------------------------------

	/** `false` means the dialog was cancelled. */
	async addList(kind: 'persons' | 'organizations', dialog: Dialog): Promise<boolean> {
		const answer = await addList(kind, dialog);
		if (answer) this.listen = answer;
		return answer !== null;
	}

	async updateList(id: string, dialog: Dialog): Promise<boolean> {
		const answer = await updateList(id, dialog);
		if (answer) this.listen = answer;
		return answer !== null;
	}

	async removeList(connection: SidecarConnection, id: string): Promise<void> {
		await deleteList(connection, id);
		this.listen = await fetchLists(connection);
	}

	// -- Terms ------------------------------------------------------------

	#asInput(terms: readonly Term[]): TermIn[] {
		return terms.map(({ text, entity_type }) => ({ text, entity_type }));
	}

	async addTerm(connection: SidecarConnection, text: string, kind: KindOfInfo): Promise<void> {
		const answer = await updateTerms(connection, [
			...this.#asInput(this.terms),
			{ text, entity_type: kind }
		]);
		this.terms = answer.terms;
	}

	async removeTerm(connection: SidecarConnection, id: string): Promise<void> {
		const answer = await updateTerms(
			connection,
			this.#asInput(this.terms.filter((term) => term.id !== id))
		);
		this.terms = answer.terms;
	}

	// -- Patterns --------------------------------------------------------------

	async savePattern(connection: SidecarConnection, pattern: PatternIn[]): Promise<void> {
		const answer = await updatePatterns(connection, pattern);
		this.pattern = answer.patterns;
	}

	togglePattern(connection: SidecarConnection, id: string, on: boolean): Promise<void> {
		return this.savePattern(
			connection,
			this.pattern.map((pattern) => (pattern.id === id ? { ...pattern, enabled: on } : pattern))
		);
	}

	removePattern(connection: SidecarConnection, id: string): Promise<void> {
		return this.savePattern(
			connection,
			this.pattern.filter((pattern) => pattern.id !== id)
		);
	}

	// -- Templates ------------------------------------------------------------

	#accept(answer: TemplateApplyResponse): void {
		this.templates = answer.templates;
		this.terms = answer.terms;
		this.pattern = answer.patterns;
		settings.set(answer.settings);
	}

	async selectTemplate(connection: SidecarConnection, id: string): Promise<void> {
		this.#accept(await applyTemplate(connection, id));
	}

	/** "No template": keep terms and patterns, only clear the binding. */
	async withoutTemplate(connection: SidecarConnection): Promise<void> {
		await settings.change(connection, { names_terms: { template: null } });
		this.templates = await fetchTemplates(connection);
	}

	async saveTemplate(connection: SidecarConnection, title: string): Promise<void> {
		this.templates = await saveTemplate(connection, title);
	}

	templateExport(connection: SidecarConnection, title: string) {
		const active = this.templates?.active;
		return exportTemplate(connection, active ? { id: active } : { title: title });
	}

	async importTemplate(connection: SidecarConnection, content: string): Promise<void> {
		this.#accept(await importTemplate(connection, content));
	}
}

export const names = new Names();
