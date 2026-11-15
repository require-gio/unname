<script lang="ts">
	import HistoryBackLink from '#lib/features/history/HistoryBackLink.svelte';
	import { isTauri } from '@tauri-apps/api/core';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { onMount, tick, untrack } from 'svelte';
	import { MediaQuery } from 'svelte/reactivity';
	import { toast } from 'svelte-sonner';
	import { cn } from '#lib/utils.js';
	import { ablehnungsText } from '#lib/inputs/rejection.js';
	import { flowMap, gridMap, pagesMap } from '#lib/layout/map.js';
	import { CATALOG, type CategoryId } from '#lib/masking/catalog.js';
	import { sourceSection } from '#lib/review/selection.js';
	import { anchorFrom, rectReference, schweben, type Reference } from '#lib/review/dom.js';
	import { protectedName } from '#lib/review/detection.js';
	import {
		readTextPosition,
		textAnchorRect,
		type ViewPosition
	} from '#lib/review/view-position.js';
	import { codeMenu, type MenuAction } from '#lib/review/menu.js';
	import {
		resultSegments,
		inParagraphs,
		maskedPlaces,
		maskedShare,
		withSelection,
		originalSegments,
		percent,
		segmentsFor,
		type SegmentInput
	} from '#lib/review/segments.js';
	import {
		touchedMatch,
		aiSuggestion,
		nextOpen,
		openQuestions,
		type Action,
		type Section
	} from '#lib/review/state.js';
	import type { Segment } from '#lib/ui/segments.js';
	import type {
		LayoutPagedResponse,
		EntryView,
		LayoutSheetRowsResponse,
		OccurrencesResponse
	} from '#lib/sidecar/api.gen.js';
	import {
		fetchLayoutDocument,
		fetchPageImage,
		fetchPageFaithful,
		fetchPageFaithfulImage,
		fetchTablesExcerpt
	} from '#lib/sidecar/api-v2.js';
	import { listenDropzone, type Selection, type SubmitResult } from '#lib/sidecar/inputs.js';
	import { GATE_TEXT, gateNotice } from '#lib/sidecar/gate-notice.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import type { StorageError } from '#lib/sidecar/save.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { entryViewModel } from '#lib/state/entry.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { ui } from '#lib/state/ui.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import HelpButton from '#lib/ui/HelpButton.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import MaskingControl from '#lib/ui/MaskingControl.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import Menu from '#lib/ui/Menu.svelte';
	import Spinner from '#lib/ui/Spinner.svelte';
	import { coachmark } from '#lib/ui/coachmark.svelte.js';
	import DocumentPage from './DocumentPage.svelte';
	import FileTabs from './FileTabs.svelte';
	import FloatingBar, { type View } from './FloatingBar.svelte';
	import DocxViewer from './layout/DocxViewer.svelte';
	import PageViewer from './layout/PageViewer.svelte';
	import PageFaithfulViewer from './layout/PageFaithfulViewer.svelte';
	import TableViewer from './layout/TableViewer.svelte';
	import ReviewPanel from './ReviewPanel.svelte';
	import SelectionMenu, { TYPE_FOR_KIND, type InfoKind } from './SelectionMenu.svelte';
	import SideBySide from './SideBySide.svelte';
	import OcrNotice from './OcrNotice.svelte';
	import UploaderOverlay from './UploaderOverlay.svelte';
	import { ZOOM_CLASS, type ZoomLevel } from './zoom.js';

	/**
	 * Board “3 · Review”: document viewer without header, with floating toolbars and a
	 * question pane on the right.
	 *
	 * - Clicking a code opens a shared menu for that code
	 *   (“Keep only here”, “Keep all names”, …).
	 * - Selecting text opens the selection menu: add annotation, optionally apply to all
	 *   files; extend the annotation across a detected location (reason: apply to whole entry).
	 * - Masking applies to the entire entry; toggling only recomputes, detection is not rerun.
	 * - Keyboard shortcuts: Tab cycles codes, Alt+N next open question, Alt+M add annotation,
	 *   Escape closes menus, holding Space shows the original.
	 *
	 * Below 1200 px the question pane starts collapsed; below 1040 px the sidebar (`ui`) collapses.
	 *
	 * For fixed-page files (PDF, scan, image), “Page” shows the page image (layout plan):
	 * the result is overpainted after the placement plan, masking is word-by-word in the image.
	 * Word files show “Page” from the preview copy with pages, tables, header and footer,
	 * replaced in the DOM. “Text” remains the linear view – the fallback when preview fails.
	 */
	let { index, onselect }: { index: number; onselect: (index: number) => void } = $props();

	const entryView = entryViewModel;
	/** Reason for “New detection”; unknown reasons are left unnamed. */
	const REASONS = ['languages', 'second_opinion', 'ocr_engine', 'ner'] as const;
	const currentFile = $derived(entryView.files[index] ?? null);
	const findings = $derived(currentFile?.findings ?? null);
	const entry = $derived(entryView.entry);
	const panelId = $props.id();
	const tastaturId = `${panelId}-tastatur`;

	/* -- View ----------------------------------------------------------- */

	let viewChoice = $state<View>('result');
	let spaceKey = $state(false);
	const visible = $derived<View>(spaceKey ? 'original' : viewChoice);
	let zoom = $state<ZoomLevel>(100);
	let pageWidth = $state(false);
	let maskingOpen = $state(false);
	let uploaderOpen = $state(false);
	let zieht = $state(false);
	let adds = $state(false);
	let scroller = $state<HTMLElement>();
	let article = $state<HTMLElement>();
	let barElement = $state<HTMLElement>();
	let representation = $state<EntryView['representation']>('page');
	let pendingPosition = $state.raw<ViewPosition | null>(null);
	let pendingFocus = $state<string | null>(null);
	let restoredFile: string | null = null;
	let restoredEntry: string | null = null;
	let positionFrame = 0;
	let pageViewer = $state<ReturnType<typeof PageViewer>>();
	let faithfulViewer = $state<ReturnType<typeof PageFaithfulViewer>>();

	// The device preference is only the initial value for a new entry.
	$effect(() => {
		const file = currentFile;
		if (
			!file ||
			!entryView.viewLoaded ||
			(restoredEntry === entryView.id && restoredFile === file.id)
		)
			return;
		untrack(() => {
			const view = entryView.view;
			if (restoredEntry !== entryView.id) {
				representation =
					view?.representation ??
					(ui.appearance === 'text'
						? 'text'
						: ui.appearance === 'pageFaithful'
							? 'faithful'
							: 'page');
				viewChoice =
					view?.preview === 'original'
						? 'original'
						: view?.preview === 'side_by_side'
							? 'sideBySide'
							: 'result';
			}
			const sameFile = view?.active_file_id === file.id;
			activeQuestion = sameFile ? (view?.focused_question_id ?? null) : null;
			pendingFocus = activeQuestion;
			pendingPosition = sameFile
				? { page: view?.page ?? 1, anchor: view?.anchor ?? null }
				: { page: 1, anchor: null };
			restoredFile = file.id;
			restoredEntry = entryView.id;
			entryView.updateView({
				active_file_id: file.id,
				representation,
				preview:
					viewChoice === 'original'
						? 'original'
						: viewChoice === 'sideBySide'
							? 'side_by_side'
							: 'result',
				focused_question_id: activeQuestion,
				...pendingPosition
			});
		});
	});

	function setRepresentation(value: string): void {
		rememberPosition();
		const position = entryView.view;
		representation =
			value === 'text'
				? 'text'
				: value === 'pageFaithful'
					? 'faithful'
					: flow
						? 'flow'
						: grid
							? 'grid'
							: 'page';
		pendingPosition = { page: position?.page ?? 1, anchor: position?.anchor ?? null };
		entryView.updateView({ representation });
	}

	function rememberPosition(): void {
		if (!currentFile || !scroller || !entryView.viewLoaded || pendingPosition) return;
		const top = scroller.getBoundingClientRect().top;
		const bottom = scroller.getBoundingClientRect().bottom;
		const position = pagesView
			? pageViewer?.readPosition(top, bottom)
			: pageFaithfulView
				? faithfulViewer?.readPosition(top, bottom)
				: flowView
					? flowViewer?.readPosition(top)
					: gridView
						? gridViewer?.readPosition()
						: article
							? readTextPosition(article, top)
							: null;
		entryView.updateView({
			active_file_id: currentFile.id,
			representation:
				representation === 'text' || representation === 'faithful'
					? representation
					: flowView
						? 'flow'
						: gridView
							? 'grid'
							: 'page',
			preview:
				viewChoice === 'original'
					? 'original'
					: viewChoice === 'sideBySide'
						? 'side_by_side'
						: 'result',
			focused_question_id: activeQuestion,
			...(position ?? {})
		});
	}

	function schedulePosition(): void {
		cancelAnimationFrame(positionFrame);
		positionFrame = requestAnimationFrame(rememberPosition);
	}

	// Layouts and fonts load asynchronously. Restore once the actual preview is ready.
	$effect(() => {
		const position = pendingPosition;
		const file = currentFile;
		const container = scroller;
		if (!position || !file || !container) return;
		if (file.info.status !== 'review') {
			pendingPosition = null;
			untrack(rememberPosition);
			return;
		}
		// Starting at the top needs no late jump after an asynchronous layout appears.
		if (position.page === 1 && !position.anchor) {
			container.scrollTop = 0;
			pendingPosition = null;
			untrack(rememberPosition);
			return;
		}
		if (!file.layout || !file.result || !current) return;
		let stopped = false;
		let timer: ReturnType<typeof setTimeout>;
		const restore = async () => {
			if (stopped) return;
			const restored = await untrack(() => {
				if (pagesView) return pageViewer?.restorePosition(container, position);
				if (pageFaithfulView) return faithfulViewer?.restorePosition(container, position);
				if (flowView) return flowViewer?.restorePosition(container, position);
				if (gridView) return gridViewer?.restorePosition(container, position);
				if (!article) return false;
				const rect = textAnchorRect(article, position.anchor);
				container.scrollTop = rect
					? container.scrollTop + rect.top - container.getBoundingClientRect().top
					: 0;
				return true;
			});
			if (stopped) return;
			if (restored) {
				pendingPosition = null;
				rememberPosition();
			} else timer = setTimeout(() => void restore(), 100);
		};
		timer = setTimeout(() => void restore(), 0);
		return () => {
			stopped = true;
			clearTimeout(timer);
		};
	});

	$effect(() => {
		void viewChoice;
		void activeQuestion;
		untrack(rememberPosition);
	});

	onMount(() => {
		const unregister = entryView.registerViewCapture(rememberPosition);
		return () => {
			cancelAnimationFrame(positionFrame);
			unregister();
		};
	});

	const narrow = new MediaQuery('max-width: 1199px');
	let barWide = $state(true);
	let barNarrow = $state(false);
	const barOpen = $derived(narrow.current ? barNarrow : barWide);
	function setBar(open: boolean): void {
		if (narrow.current) barNarrow = open;
		else barWide = open;
	}

	/* -- File data --------------------------------------------------- */

	const questionsList = $derived(currentFile ? entryView.questionsBy(currentFile) : []);
	const open = $derived(openQuestions(questionsList));
	const suggestions = $derived(
		open.filter((question) => aiSuggestion(question.finding) !== undefined)
	);
	const input = $derived<SegmentInput | null>(
		currentFile && findings
			? {
					text: findings.text,
					findings: findings.findings,
					status: currentFile.status,
					result: currentFile.result,
					questions: questionsList,
					categoryActive: entryView.categoryActive
				}
			: null
	);
	const blocks = $derived(findings?.blocks ?? []);
	const map = $derived(pagesMap(currentFile?.layout));
	const flow = $derived(flowMap(currentFile?.layout));
	const appearances = $derived([
		{
			value: 'page' as const,
			label: t('review.appearance.page'),
			title: t('review.appearance.page.title'),
			icon: 'document' as const
		},
		//
		...(flow?.pageFaithful
			? [
					{
						value: 'pageFaithful' as const,
						label: t('review.appearance.pageFaithful'),
						title: t('review.appearance.pageFaithful.title'),
						icon: 'pageFaithful' as const
					}
				]
			: []),
		{
			value: 'text' as const,
			label: t('review.appearance.text'),
			title: t('review.appearance.text.title'),
			icon: 'text' as const
		}
	]);
	/** "Seite" or "Seitentreu" – if no Seitentreu view exists, treat as "Seite". */
	const asPage = $derived(representation !== 'text');
	const pagesView = $derived(map !== null && asPage);
	/** File whose faithful view failed – falls back to "Seite". */
	let pageFaithfulError = $state<string | null>(null);
	const pageFaithfulView = $derived(
		flow?.pageFaithful === true &&
			representation === 'faithful' &&
			pageFaithfulError !== currentFile?.id
	);
	/** File whose Word preview failed – falls back to text view. */
	let flowError = $state<string | null>(null);
	const flowView = $derived(
		flow !== null && asPage && !pageFaithfulView && flowError !== currentFile?.id
	);
	const grid = $derived(gridMap(currentFile?.layout));
	/** File whose table could not load – falls back to text view. */
	let gridError = $state<string | null>(null);
	const gridView = $derived(grid !== null && asPage && gridError !== currentFile?.id);
	/** Fixed pages for viewing: PDF, scan, image – and faithful Word view. */
	const pageImageView = $derived(pagesView || pageFaithfulView);
	const layoutView = $derived(pageImageView || flowView || gridView);
	let flowViewer = $state<ReturnType<typeof DocxViewer>>();
	let gridViewer = $state<ReturnType<typeof TableViewer>>();

	/* Selection menu (add later, expand, search) */
	interface SelectionState {
		mode: 'addLater' | 'expand' | 'shorten' | 'search';
		section: Section | null;
		text: string;
		reference: Reference;
		kind: InfoKind;
		occurrence: OccurrencesResponse | null;
		loads: boolean;
		notice?: string;
		targetId?: string;
		back: HTMLElement | null;
	}
	let selection = $state<SelectionState | null>(null);
	let selectionElement = $state<HTMLElement>();

	const marked = $derived(selection?.section ?? null);
	const paragraphs = $derived(
		input
			? inParagraphs(
					withSelection(segmentsFor(visible === 'original' ? 'original' : 'result', input), marked),
					blocks
				)
			: []
	);
	const lines = $derived.by(() => {
		if (!input || visible !== 'sideBySide') return [];
		const links = inParagraphs(withSelection(originalSegments(input), marked), blocks);
		const right = inParagraphs(
			withSelection(input.result ? resultSegments(input) : originalSegments(input), marked),
			blocks
		);
		return links.map((part, i) => ({ links: part, right: right[i] ?? [] }));
	});

	const pagesOriginal = $derived(
		input && layoutView ? withSelection(originalSegments(input), marked) : []
	);
	const pagesResult = $derived(
		input?.result && layoutView ? withSelection(resultSegments(input), marked) : pagesOriginal
	);
	const wide = $derived(layoutView && pageWidth);

	function pageImage(page: number, level: number, signal: AbortSignal): Promise<ImageBitmap> {
		const connection = sidecarGate.connection;
		if (!connection || !currentFile) return Promise.reject(new Error('keine Verbindung'));
		return fetchPageImage(connection, currentFile.id, page, level, signal);
	}

	function loadPageFaithfulMap(
		page: 'original' | 'result',
		signal: AbortSignal
	): Promise<LayoutPagedResponse> {
		const connection = sidecarGate.connection;
		if (!connection || !currentFile) return Promise.reject(new Error('keine Verbindung'));
		return fetchPageFaithful(connection, currentFile.id, page, signal);
	}

	function pageFaithfulImage(
		page: 'original' | 'result',
		revision: number,
		number: number,
		level: number,
		signal: AbortSignal
	): Promise<ImageBitmap> {
		const connection = sidecarGate.connection;
		if (!connection || !currentFile) return Promise.reject(new Error('keine Verbindung'));
		return fetchPageFaithfulImage(
			connection,
			currentFile.id,
			page,
			revision,
			number,
			level,
			signal
		);
	}

	function layoutDocument(signal: AbortSignal): Promise<Blob> {
		const connection = sidecarGate.connection;
		if (!connection || !currentFile) return Promise.reject(new Error('keine Verbindung'));
		return fetchLayoutDocument(connection, currentFile.id, signal);
	}

	function tablesExcerpt(
		sheet: number,
		by: number,
		until: number,
		language: 'de' | 'en',
		signal: AbortSignal
	): Promise<LayoutSheetRowsResponse> {
		const connection = sidecarGate.connection;
		if (!connection || !currentFile) return Promise.reject(new Error('keine Verbindung'));
		return fetchTablesExcerpt(connection, currentFile.id, sheet, by, until, language, signal);
	}

	/** An element at the position – either in the view document or in a Word island. */
	function inDocument(selektor: string): HTMLElement | SVGElement | null {
		return (
			article?.querySelector<HTMLElement | SVGElement>(selektor) ??
			flowViewer?.finden(selektor) ??
			null
		);
	}

	const current = $derived(currentFile ? entryView.current(currentFile) : true);
	const places = $derived(maskedPlaces(currentFile?.result ?? null, questionsList));
	const share = $derived(maskedShare(currentFile?.result ?? null, findings?.text.length ?? 0));
	const count = $derived(
		(findings?.counts.by_category ?? {}) as Partial<Record<CategoryId, number>>
	);
	const filesCount = $derived(entryView.files.length);
	const otherOpen = $derived(
		filesCount > 1
			? entryView.files.filter((other) => other !== currentFile && entryView.openBy(other) > 0)
					.length
			: null
	);

	const reiter = $derived(
		entryView.files.map((entryFile) => ({
			id: entryFile.id,
			name: entryFile.info.name,
			open: entryView.openBy(entryFile)
		}))
	);

	const kindLabel = $derived(
		entryView.masking.all
			? t('masking.all')
			: t(entryView.masking.method === 'reversible' ? 'masking.reversible' : 'masking.irreversible')
	);
	const kindIcon = $derived(
		entryView.masking.all
			? ('lockFull' as const)
			: entryView.masking.method === 'reversible'
				? ('restore' as const)
				: ('lock' as const)
	);

	const header = $derived.by(() => {
		if (!currentFile) return '';
		if (visible === 'sideBySide') {
			return t(
				pagesView
					? 'review.header.sideBySide.page'
					: pageFaithfulView
						? 'review.header.sideBySide.pageFaithful'
						: flowView
							? 'review.header.sideBySide.flow'
							: gridView
								? 'review.header.sideBySide.grid'
								: 'review.header.sideBySide'
			);
		}
		const info = currentFile.info;
		const name =
			visible === 'original'
				? t('review.header.original', { name: info.name })
				: protectedName(info.name, { addition: t('save.addition') });
		return info.pages ? `${name} · ${t('file.pages', { n: info.pages })}` : name;
	});

	const notice = $derived(
		gateNotice({ snapshot: sidecarGate.snapshot, outside: sidecarGate.outside })
	);
	const uploadLock = $derived(
		!sidecarGate.mayUpload && notice ? t(GATE_TEXT[notice.position].lock) : undefined
	);
	const backTarget = $derived(
		entry ? `${resolve('/(app)/entry/[id]', { id: entry.id })}?overview=1` : resolve('/(app)')
	);

	/* -- Save operation ----------------------------------------------------- */

	const saveLock = $derived(
		entryView.saveLock
			? t(entryView.saveLock)
			: !sidecarGate.mayExport && notice
				? t(GATE_TEXT[notice.position].text)
				: undefined
	);
	let confirmOpen = $state(false);
	let otherFolder = false;

	/** "Alle n Dateien speichern" — first irreversible save requires confirmation. */
	function startSave(select = false): void {
		if (saveLock || entryView.saves) return;
		if (entryView.confirmationNeeded) {
			otherFolder = select;
			confirmOpen = true;
			return;
		}
		void save(false, select);
	}

	async function save(confirmed: boolean, select: boolean): Promise<void> {
		const id = entry?.id;
		if (!id) return;
		const exit = await entryView.save({
			confirmed,
			select,
			folderName: t('save.folder'),
			dialogTitle: t('save.dialog'),
			tag: t('save.tag')
		});
		if (!exit) return;
		if (exit.ok) {
			if (exit.result.cloud) toast(t('save.cloud'));
			await goto(resolve('/(app)/entry/[id]', { id }));
			return;
		}
		reportStorageError(exit.error, select);
	}

	function reportStorageError(error: StorageError, select: boolean): void {
		const name = entryView.files.find((kandidat) => kandidat.id === error.file_id)?.info.name;
		switch (error.code) {
			case 'irreversible_confirmation_required':
				otherFolder = select;
				confirmOpen = true;
				return;
			case 'pdf_check_failed':
				toast.error(t('save.error.pdf', { name: name ?? '' }));
				return;
			case 'export_failed':
				toast.error(t('save.error.export', { name: name ?? '' }));
				return;
			case 'files_expired':
				entryView.update();
				toast.error(t('save.error.expired'), {
					action: { label: t('save.reimport'), onClick: () => void reimport() }
				});
				return;
			case 'files_unavailable':
				entryView.update();
				toast.error(t('save.error.unavailable'), {
					action: {
						label: t('review.file.chooseOriginal'),
						onClick: () => void reimport()
					}
				});
				return;
			case 'still_processing':
				toast.error(t('save.error.running'));
				return;
			case 'not_ready':
				toast.error(t('save.error.ready'));
				return;
			case 'target':
			case 'publish':
				toast.error(t('save.error.target'), {
					action: { label: t('save.selectFolder'), onClick: () => startSave(true) }
				});
				return;
			default:
				toast.error(t('save.error'));
		}
	}

	/** Reload originals: legacy files and unreadable copies. */
	async function reimport(): Promise<void> {
		try {
			const result = await entryView.reimportOriginals(t('saved.dialog.originals'));
			if (!result) return;
			if (result.replaced.length > 0) {
				toast(t('saved.newImported', { n: result.replaced.length }));
			}
			const missing = [...result.missing, ...result.failed];
			if (missing.length > 0) {
				toast.error(t('saved.missing', { n: missing.length, names: missing.join(', ') }));
			}
		} catch {
			toast.error(t('saved.newImportError'));
		}
	}

	/* -- Redetect files ------------------------------------------------------ */

	const redetect = $derived(entryView.redetectFiles);
	const redetectReasons = $derived(
		[...new Set(redetect.flatMap((entryFile) => entryFile.changes))]
			.filter((reason): reason is (typeof REASONS)[number] =>
				(REASONS as readonly string[]).includes(reason)
			)
			.map((reason) => t(`review.redetect.${reason}`))
			.join(', ')
	);

	async function startRedetect(): Promise<void> {
		try {
			await entryView.redetect();
		} catch {
			toast.error(t('review.redetect.error'));
		}
	}

	async function retryFile(): Promise<void> {
		if (!currentFile) return;
		try {
			await entryView.retryFile(currentFile.id);
		} catch {
			toast.error(t('review.file.retryError'));
		}
	}

	async function removeFile(): Promise<void> {
		if (!currentFile) return;
		try {
			await entryView.removeFile(currentFile.id);
		} catch {
			toast.error(t('review.file.removeError'));
		}
	}

	/* -- Apply ------------------------------------------------------------- */

	// Masking, state or connection changed: trigger recompute.
	// File queue is compacted; detection does not rerun.
	$effect(() => {
		const active = currentFile;
		if (!active || !active.findings) return;
		void entryView.masking.asRequest();
		void active.status;
		void sidecarGate.connection;
		entryView.apply(active);
	});

	/* -- Single-location menu ---------------------------------------------- */

	let place = $state<{ segment: Segment; element: HTMLElement | SVGElement } | null>(null);
	let placeOpen = $state(false);

	const menuModel = $derived.by(() => {
		if (!place || !currentFile || !findings) return null;
		const segment = place.segment;
		const id = segment.id ?? '';
		const category = segment.category ?? 'numbers';
		const finding = findings.findings.find((kandidat) => kandidat.id === id);
		const handId = segment.hand ?? id;
		const hand = currentFile.status.manual.find((entryHand) => entryHand.id === handId);
		return codeMenu(
			segment,
			{
				question: finding?.question != null,
				decision: currentFile.status.decisions[id],
				locked: CATALOG.find((k) => k.id === category)?.locked ?? false,
				places:
					findings.counts.by_category[category] ??
					findings.findings.filter((f) => f.category === category).length,
				correction: hand?.corrected !== undefined
			},
			t
		);
	});

	function openPlace(segment: Segment, element: HTMLElement | SVGElement): void {
		closeSelection(false);
		maskingOpen = false;
		place = { segment, element };
		placeOpen = true;
	}

	function focus(id: string | undefined): void {
		if (!id) return;
		void tick().then(() =>
			inDocument(`:is(button, [role="button"])[data-id="${CSS.escape(id)}"]`)?.focus()
		);
	}

	// Close the menu (selection, Escape, click outside) and return focus to the
	// original place. The menu stays attached until UI releases the page lock
	// after closing – otherwise a prematurely removed menu would linger.
	let warOpen = false;
	$effect(() => {
		const open = placeOpen;
		if (warOpen && !open) focus(untrack(() => place?.segment.id));
		warOpen = open;
	});

	function setCategory(category: string, on: boolean): void {
		const id = category as CategoryId;
		if (entryView.masking.categories.has(id) !== on) entryView.masking.toggleCategory(id);
	}

	function runMenu(action: MenuAction): void {
		const current = place;
		if (!current || !currentFile) return;
		const id = current.segment.id ?? '';
		const category = current.segment.category ?? 'numbers';
		switch (action) {
			case 'onlyHere':
			case 'keep':
				entryView.decide(currentFile, id, 'reject');
				break;
			case 'mask':
				entryView.decide(currentFile, id, 'accept');
				break;
			case 'undo':
				entryView.undo(currentFile, id);
				break;
			case 'categoryFrom':
				setCategory(category, false);
				break;
			case 'categoryOn':
				setCategory(category, true);
				break;
			case 'removeHand':
				// Another occurrence clears its value's marking.
				entryView.removeHand(currentFile, current.segment.hand ?? id);
				break;
		}
	}

	/* -- Marking and Addendum ------------------------------------------ */

	let occurrenceAbort: AbortController | null = null;

	function countOccurrence(text: string): void {
		occurrenceAbort?.abort();
		const abort = new AbortController();
		occurrenceAbort = abort;
		const request = entryView.searchOccurrence(text, abort.signal);
		if (!request) {
			if (selection) {
				selection.occurrence = null;
				selection.loads = false;
			}
			return;
		}
		if (selection) selection.loads = true;
		request
			.then((answer) => {
				if (abort.signal.aborted || !selection) return;
				selection.occurrence = answer;
				selection.loads = false;
				if (selection.mode === 'search') {
					selection.notice = answer.count === 0 ? t('selection.noOccurrence') : undefined;
				}
			})
			.catch(() => {
				if (!abort.signal.aborted && selection) {
					selection.occurrence = null;
					selection.loads = false;
				}
			});
	}

	function openSelection(section: Section, reference: Reference): void {
		if (!currentFile || !findings) return;
		const touched = touchedMatch(currentFile.status, findings.findings, section);
		let mode: SelectionState['mode'] = 'addLater';
		let targetId: string | undefined;
		if (touched.findings.length === 1 && touched.hand.length === 0) {
			const target = touched.findings[0];
			if (target.start === section.start && target.end === section.end) return;
			targetId = target.id;
			mode = section.start >= target.start && section.end <= target.end ? 'shorten' : 'expand';
		}
		placeOpen = false;
		maskingOpen = false;
		selection = {
			mode,
			section,
			text: findings.text.slice(section.start, section.end),
			reference,
			kind: 'name',
			occurrence: null,
			loads: false,
			targetId,
			back: document.activeElement instanceof HTMLElement ? document.activeElement : null
		};
		if (touched.findings.length > 1 || touched.hand.length > 0) {
			selection.notice = t(touched.hand.length > 0 ? 'addendum.overlaps' : 'addendum.several');
		}
		if (mode === 'addLater') countOccurrence(selection.text);
		void tick().then(() =>
			selectionElement?.querySelector<HTMLElement>('[role="dialog"]')?.focus()
		);
	}

	/** Read the current browser selection and open the context menu at that location. */
	function selectionReview(): boolean {
		if (!article || !findings) return false;
		if (gridView) {
			const read = gridViewer?.readSelection();
			if (!read) return false;
			openSelection(read.section, rectReference(read.rect, scroller ?? null));
			return true;
		}
		if (flowView) {
			const read = flowViewer?.readSelection();
			if (!read) return false;
			openSelection(read.section, rectReference(read.rect, scroller ?? null));
			return true;
		}
		const browserSelection = window.getSelection();
		if (!browserSelection || browserSelection.isCollapsed || browserSelection.rangeCount === 0)
			return false;
		const a = anchorFrom(browserSelection.anchorNode, browserSelection.anchorOffset, article);
		const b = anchorFrom(browserSelection.focusNode, browserSelection.focusOffset, article);
		if (!a || !b || a.column !== b.column) return false;
		const source =
			a.column === 'original'
				? lines.map((line) => line.links)
				: a.column === 'result'
					? lines.map((line) => line.right)
					: paragraphs;
		const section = sourceSection(source, a, b, findings.text);
		if (!section) return false;
		const rect = browserSelection.getRangeAt(0).getBoundingClientRect();
		browserSelection.removeAllRanges();
		openSelection(section, rectReference(rect, scroller ?? null));
		return true;
	}

	/** Alt+M: add a later marking or, if no marking, start a search. */
	function openAddLater(): void {
		if (!currentFile?.findings || !barElement) return;
		if (selectionReview()) return;
		placeOpen = false;
		maskingOpen = false;
		const element = barElement;
		selection = {
			mode: 'search',
			section: null,
			text: '',
			reference: element,
			kind: 'name',
			occurrence: null,
			loads: false,
			back: document.activeElement instanceof HTMLElement ? document.activeElement : null
		};
		void tick().then(() => selectionElement?.querySelector<HTMLElement>('input')?.focus());
	}

	// In search mode each keystroke triggers a fresh count, debounced.
	const searchText = $derived(selection?.mode === 'search' ? selection.text : null);
	$effect(() => {
		const text = searchText;
		if (text === null) return;
		const timer = setTimeout(() => countOccurrence(text), 250);
		return () => clearTimeout(timer);
	});

	function closeSelection(focusBack = true): void {
		occurrenceAbort?.abort();
		const back = selection?.back;
		selection = null;
		if (focusBack && back?.isConnected) back.focus();
	}

	function maskSelection(): void {
		const current = selection;
		if (!current || !currentFile) return;
		const type = TYPE_FOR_KIND[current.kind];
		const result =
			current.mode === 'search'
				? current.occurrence
					? entryView.maskOccurrence(type, current.occurrence)
					: ({ ok: false, reason: 'addendum.invalid' } as const)
				: current.mode === 'shorten' && current.targetId && current.section
					? entryView.shortenPlace(currentFile, current.targetId, current.section)
					: current.section
						? entryView.maskMarking(
								currentFile,
								current.section,
								type,
								// Apply to every occurrence across all files of the entry.
								current.occurrence
							)
						: ({ ok: false, reason: 'addendum.invalid' } as const);
		if (!result.ok) {
			current.notice = t(result.reason);
			return;
		}
		toast(
			result.files > 1
				? t('selection.result.files', { n: result.fresh, files: result.files })
				: t('selection.result', { n: result.fresh })
		);
		closeSelection();
	}

	/** ⌘C/Strg+C copies the selected text after a word-wise marking. */
	function copy(event: ClipboardEvent): void {
		if (!pageImageView || !selection?.section || !findings || !event.clipboardData) return;
		const target = event.target;
		if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
		const browserSelection = window.getSelection();
		if (browserSelection && !browserSelection.isCollapsed) return;
		event.clipboardData.setData(
			'text/plain',
			findings.text.slice(selection.section.start, selection.section.end)
		);
		event.preventDefault();
	}

	function outsideClick(event: PointerEvent): void {
		if (!selection || !selectionElement) return;
		if (event.target instanceof Node && selectionElement.contains(event.target)) return;
		closeSelection(false);
	}

	/* -- Questions ------------------------------------------------------------ */

	let activeQuestion = $state<string | null>(null);

	$effect(() => {
		const id = pendingFocus;
		if (!id || !currentFile?.findings) return;
		setBar(true);
		void tick().then(() => {
			if (pendingFocus !== id) return;
			document
				.querySelector<HTMLElement>(`[data-question="${CSS.escape(id)}"] button`)
				?.focus({ preventScroll: true });
			pendingFocus = null;
		});
	});

	function decide(findingId: string, action: Action): void {
		if (!currentFile) return;
		entryView.decide(currentFile, findingId, action);
		activeQuestion = findingId;
	}

	function acceptSuggestions(): void {
		if (!currentFile) return;
		const list = suggestions.map(
			(question) => [question.finding.id, aiSuggestion(question.finding)!] as const
		);
		const undo = entryView.decideAll(currentFile, list, 'ai-suggestion');
		toast(t('review.accepted', { n: list.length }), {
			action: { label: t('review.undo'), onClick: undo }
		});
	}

	/** Alt+N: go to the next open question – show it in the document and focus it in the sidebar. */
	function nextQuestion(): void {
		const question = nextOpen(questionsList, activeQuestion);
		if (!question) {
			toast(t('review.open.title', { n: 0 }));
			return;
		}
		const id = question.finding.id;
		activeQuestion = id;
		setBar(true);
		void tick().then(async () => {
			// The grid cell may not yet be in the DOM; scroll to it first.
			if (gridView) await gridViewer?.show(id);
			inDocument(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'center' });
			document.querySelector<HTMLElement>(`[data-question="${CSS.escape(id)}"] button`)?.focus();
		});
	}

	/* -- Keyboard ---------------------------------------------------------- */

	const CONTROL =
		'button, a, input, textarea, select, [role="menuitem"], [role="radio"], [role="tab"], [role="switch"], [role="dialog"], [contenteditable]';

	function hotkeyRunter(event: KeyboardEvent): void {
		if (event.altKey && !event.ctrlKey && !event.metaKey) {
			if (event.code === 'KeyN') {
				event.preventDefault();
				nextQuestion();
				return;
			}
			if (event.code === 'KeyM') {
				event.preventDefault();
				openAddLater();
				return;
			}
		}
		if (event.key === 'Escape' && selection) {
			event.preventDefault();
			closeSelection();
			return;
		}
		if (
			event.code === 'Space' &&
			!event.repeat &&
			!event.altKey &&
			!event.ctrlKey &&
			!event.metaKey
		) {
			const target = event.target;
			if (target instanceof Element && target.closest(CONTROL)) return;
			event.preventDefault();
			spaceKey = true;
		}
	}

	function keyUp(event: KeyboardEvent): void {
		if (event.code === 'Space') spaceKey = false;
	}

	/* -- Additional files --------------------------------------------------- */

	function reportSubmit(result: SubmitResult | null): void {
		if (!result) return;
		if (result.accepted.length > 0) {
			toast(t('uploader.added', { n: result.accepted.length }));
			uploaderOpen = false;
		}
		for (const rejected of result.rejected) {
			toast.error(`${rejected.name}: ${t(ablehnungsText(rejected.code))}`);
		}
	}

	async function add(): Promise<void> {
		if (adds) return;
		adds = true;
		try {
			reportSubmit(await entryView.addFiles(t('dropzone.dialog.files')));
		} catch {
			toast.error(t('dropzone.error'));
		} finally {
			adds = false;
		}
	}

	async function acceptDropzone(selectionHarness: Selection): Promise<void> {
		adds = true;
		try {
			reportSubmit(await entryView.submit(selectionHarness));
		} catch {
			toast.error(t('dropzone.error'));
		} finally {
			adds = false;
		}
	}

	onMount(() => {
		if (!isTauri()) return;
		const abmelden = listenDropzone({
			ziehen: (active) => (zieht = active),
			abgelegt: (selectionHarness) => void acceptDropzone(selectionHarness)
		});
		return () => void abmelden.then((ab) => ab());
	});

	async function asStandard(): Promise<void> {
		try {
			await entryView.acceptAsStandard();
			toast(t('masking.asStandard.ok'));
		} catch {
			toast.error(t('masking.asStandard.error'));
		}
	}
</script>

<svelte:window
	onkeydown={hotkeyRunter}
	onkeyup={keyUp}
	onblur={() => (spaceKey = false)}
	onpointerdown={outsideClick}
	oncopy={copy}
/>

{#snippet maskingMenu()}
	<MaskingControl
		masking={entryView.masking}
		categories
		stile
		eyebrow
		{count}
		replaced={t('masking.places', { n: places })}
		usability={currentFile?.result ? t('masking.share', { share: percent(share) }) : undefined}
		scope={entryView.istStandard
			? t('masking.applies.standard', { n: filesCount })
			: t('masking.applies.onlyHere', { n: filesCount })}
	>
		{#snippet reference()}
			{#if !entryView.istStandard}
				<Button variant="link" size="sm" onclick={asStandard}>
					{t('masking.asStandard')}
				</Button>
			{:else}
				<Button variant="link" size="sm" onclick={() => settings.open('masking')}>
					{t('common.changeStandard')}
				</Button>
			{/if}
		{/snippet}
	</MaskingControl>
{/snippet}

<h1 class="sr-only">{entry?.title}</h1>
<div
	class="flex min-h-0 flex-1"
	{@attach (element) => {
		const focus = (event: FocusEvent) => {
			const target = event.target as Element | null;
			const question = target?.closest<HTMLElement>('[data-question]');
			const segment = target?.closest<HTMLElement>('[data-id]');
			if (question || segment)
				activeQuestion = question?.dataset.question ?? segment?.dataset.id ?? null;
		};
		element.addEventListener('focusin', focus);
		return () => element.removeEventListener('focusin', focus);
	}}
>
	<section
		aria-label={t('review.document')}
		class="relative flex min-w-0 flex-[999_1_520px] flex-col bg-canvas"
	>
		<!--
			The top bar visually floats over the document but appears earlier in the DOM.
			This order matches the visual tab order: top bar, document, bottom bar, then questions (WCAG 2.4.3).
		-->
		<div
			class="pointer-events-none absolute inset-x-4 top-3.5 z-20 flex items-start justify-between gap-3"
		>
			<FileTabs
				files={reiter}
				active={index}
				back={backTarget}
				{panelId}
				addLocked={!sidecarGate.mayUpload}
				onselect={(fresh) => {
					rememberPosition();
					closeSelection(false);
					placeOpen = false;
					activeQuestion = null;
					onselect(fresh);
				}}
				onadd={() => {
					maskingOpen = false;
					uploaderOpen = true;
				}}
			/>
			<div class="pointer-events-auto flex shrink-0 items-center gap-2">
				{#if !barOpen}
					{#if open.length > 0}
						<button
							type="button"
							onclick={() => setBar(true)}
							class="inline-flex h-control-md cursor-pointer items-center gap-2 rounded-[12px] border border-amber-line bg-amber-1 px-3.5 text-label font-semibold text-amber-ink shadow-float"
						>
							<span aria-hidden="true" class="size-2 rounded-full bg-amber-strong"></span>
							{t('review.questionsOpen', { n: open.length })}
						</button>
					{/if}
					<Button
						variant="primary"
						icon="save"
						disabled={saveLock !== undefined}
						busy={entryView.saves}
						title={saveLock}
						class="rounded-[12px] shadow-float"
						onclick={() => startSave()}
					>
						{entryView.saveAgain
							? t('review.saveAgain')
							: t('review.save.short', { n: filesCount })}
					</Button>
				{/if}
				<HistoryBackLink kompakt />
				<HelpButton size="md" />
			</div>
		</div>

		<div
			bind:this={scroller}
			{@attach (element) => {
				element.addEventListener('scroll', schedulePosition, true);
				return () => element.removeEventListener('scroll', schedulePosition, true);
			}}
			id={panelId}
			role="tabpanel"
			tabindex="-1"
			aria-labelledby={currentFile ? `reiter-${currentFile.id}` : undefined}
			aria-describedby={tastaturId}
			class="min-h-0 flex-1 overflow-y-auto px-7 pt-18 pb-29 outline-none"
		>
			<div
				class={cn(
					'relative mx-auto flex flex-col gap-2.5',
					visible === 'sideBySide' || wide || flowView || gridView ? 'max-w-full' : 'max-w-[720px]',
					// Word and tables zoom themselves; an outer zoom would apply twice.
					!wide && !flowView && !gridView && ZOOM_CLASS[zoom]
				)}
			>
				<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
					<p
						class="flex flex-wrap items-baseline gap-x-1.5 text-meta text-muted"
						{@attach coachmark(1, 'help.review.code', { page: 'bottom' })}
					>
						<span>{header}</span>
						{#if currentFile?.findings && !current && !currentFile.applyError}
							<span role="status" class="text-subtle">· {t('review.updated')}</span>
						{/if}
					</p>
					{#if map || flow || grid}
						<!-- The entry owns its representation; the device supplies only the default. -->
						<Segmented
							size="sm"
							label={t('review.appearance')}
							options={appearances}
							bind:value={
								() =>
									representation === 'faithful' && !flow?.pageFaithful
										? 'page'
										: representation === 'faithful'
											? 'pageFaithful'
											: representation === 'text'
												? 'text'
												: 'page',
								(value) => setRepresentation(value)
							}
						/>
					{/if}
				</div>

				{#if currentFile?.applyError}
					<div
						role="alert"
						class="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-line-soft bg-red-0 px-4 py-3 text-label text-red-ink"
					>
						<span>{t('review.error.apply')}</span>
						<Button variant="secondary" size="sm" onclick={() => entryView.loadAgain(currentFile!)}>
							{t('review.again')}
						</Button>
					</div>
				{/if}

				{#if currentFile && flow?.pageFaithful && pageFaithfulError === currentFile.id && representation === 'faithful'}
					<p
						role="note"
						class="flex items-start gap-2 rounded-md border border-amber-line bg-amber-1 px-4 py-3 text-label text-amber-ink"
					>
						<Icon name="info" size={15} class="mt-0.5 shrink-0" />{t('review.pageFaithful.error')}
					</p>
				{/if}

				{#if currentFile && flow && flowError === currentFile.id && asPage}
					<p
						role="note"
						class="flex items-start gap-2 rounded-md border border-amber-line bg-amber-1 px-4 py-3 text-label text-amber-ink"
					>
						<Icon name="info" size={15} class="mt-0.5 shrink-0" />{t('review.flow.error')}
					</p>
				{/if}

				{#if currentFile && grid && gridError === currentFile.id && asPage}
					<p
						role="note"
						class="flex items-start gap-2 rounded-md border border-amber-line bg-amber-1 px-4 py-3 text-label text-amber-ink"
					>
						<Icon name="info" size={15} class="mt-0.5 shrink-0" />{t('review.grid.error')}
					</p>
				{/if}

				{#if currentFile?.info.source_changed}
					<p
						role="note"
						class="flex items-start gap-2 rounded-md border border-amber-line bg-amber-1 px-4 py-3 text-label text-amber-ink"
					>
						<Icon name="info" size={15} class="mt-0.5 shrink-0" />{t('saved.changed')}
					</p>
				{/if}

				{#if currentFile?.info.extraction_changed}
					<p
						role="note"
						class="flex items-start gap-2 rounded-md border border-amber-line bg-amber-1 px-4 py-3 text-label text-amber-ink"
					>
						<Icon name="info" size={15} class="mt-0.5 shrink-0" />{t(
							'review.file.extractionChanged'
						)}
					</p>
				{/if}

				{#if redetect.length > 0}
					<div
						role="note"
						class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-md border border-line bg-surface-2 px-4 py-3 text-label text-ink-2"
					>
						<span class="flex min-w-0 flex-1 items-start gap-2">
							<Icon name="info" size={15} class="mt-0.5 shrink-0" />
							<span class="text-pretty"
								>{t('review.redetect.text', {
									n: redetect.length,
									was: redetectReasons
								})}</span
							>
						</span>
						<Button
							variant="secondary"
							size="sm"
							busy={entryView.detectsAgain}
							disabled={!sidecarGate.mayUpload}
							onclick={startRedetect}
						>
							{t('review.redetect')}
						</Button>
					</div>
				{/if}

				{#if !currentFile}
					{#if entry && entryView.files.length === 0}
						<Button
							variant="secondary"
							icon="fileHigh"
							disabled={!sidecarGate.mayUpload}
							onclick={add}
						>
							{t('review.further')}
						</Button>
					{:else}
						<Spinner size={22} label={t('common.loads')} class="text-subtle" />
					{/if}
				{:else if currentFile.info.status === 'queued' || currentFile.info.status === 'processing'}
					<p
						class="flex items-center gap-2.5 rounded-md bg-surface px-5 py-4 text-lead text-muted shadow-page"
					>
						<Spinner size={18} />{t('review.file.running')}
					</p>
				{:else if currentFile.info.status === 'failed' || currentFile.info.status === 'cancelled'}
					<p
						role="alert"
						class="rounded-md bg-surface px-5 py-4 text-lead text-red-ink shadow-page"
					>
						{currentFile.info.status === 'cancelled'
							? t('review.file.cancelled')
							: t('review.file.error', {
									reason: t(ablehnungsText(currentFile.info.error_code))
								})}
					</p>
					<div class="flex gap-2">
						<Button
							variant="secondary"
							size="sm"
							busy={entryView.pendingFiles.has(currentFile.id)}
							disabled={!sidecarGate.mayUpload}
							onclick={retryFile}
						>
							{t('review.file.retry')}
						</Button>
					</div>
					{#if currentFile.info.error_code === 'ocr_unavailable'}
						<OcrNotice class="mt-3" />
					{/if}
				{:else if currentFile.info.status === 'unavailable'}
					<div
						role="alert"
						class="flex flex-col items-start gap-3 rounded-md bg-surface px-5 py-4 shadow-page"
					>
						<p class="text-lead text-muted">{t('review.file.unavailable')}</p>
						<Button
							variant="secondary"
							size="sm"
							icon="fileHigh"
							busy={entryView.rereads}
							disabled={!sidecarGate.mayUpload}
							onclick={reimport}
						>
							{t('review.file.chooseOriginal')}
						</Button>
						<Button
							variant="ghost"
							size="sm"
							busy={entryView.pendingFiles.has(currentFile.id)}
							onclick={removeFile}
						>
							{t('review.file.remove')}
						</Button>
					</div>
				{:else if currentFile.info.status === 'expired'}
					<div class="flex flex-col items-start gap-3 rounded-md bg-surface px-5 py-4 shadow-page">
						<p class="text-lead text-muted">{t('review.file.expired')}</p>
						{#if currentFile.info.status === 'expired'}
							<Button
								variant="secondary"
								size="sm"
								icon="fileHigh"
								busy={entryView.rereads}
								disabled={!sidecarGate.mayUpload}
								onclick={reimport}
							>
								{t('save.reimport')}
							</Button>
						{/if}
					</div>
				{:else if currentFile.ladefehler}
					<div
						role="alert"
						class="flex flex-col items-start gap-3 rounded-md bg-surface px-5 py-4 shadow-page"
					>
						<p class="text-lead text-red-ink">{t('review.error.load')}</p>
						<Button variant="secondary" size="sm" onclick={() => entryView.loadAgain(currentFile!)}>
							{t('review.again')}
						</Button>
					</div>
				{:else if !input}
					<p class="flex items-center gap-2.5 text-lead text-muted">
						<Spinner size={18} />{t('review.loads')}
					</p>
				{:else}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						onpointerup={() => selectionReview()}
						onkeyup={(event) => {
							if (event.shiftKey) selectionReview();
						}}
						{@attach coachmark(2, pageImageView ? 'help.review.mark.page' : 'help.review.mark', {
							page: 'right'
						})}
					>
						{#if pagesView && map}
							<!-- Each file has its own page images and measurements. -->
							{#key currentFile.id}
								<PageViewer
									bind:this={pageViewer}
									bind:article
									{map}
									mode={visible}
									original={pagesOriginal}
									result={pagesResult}
									placements={currentFile.result?.placements}
									active={(placeOpen ? place?.segment.id : undefined) ??
										activeQuestion ??
										undefined}
									fill={wide}
									scale={zoom}
									onsegment={openPlace}
									onmark={(section, rect) =>
										openSelection(section, rectReference(rect, scroller ?? null))}
									load={pageImage}
								/>
							{/key}
						{:else if pageFaithfulView}
							<!-- Each file has its own engine pages. -->
							{#key currentFile.id}
								<PageFaithfulViewer
									bind:this={faithfulViewer}
									bind:article
									mode={visible}
									original={pagesOriginal}
									result={pagesResult}
									status={currentFile.result ?? null}
									active={(placeOpen ? place?.segment.id : undefined) ??
										activeQuestion ??
										undefined}
									fill={wide}
									scale={zoom}
									onsegment={openPlace}
									onmark={(section, rect) =>
										openSelection(section, rectReference(rect, scroller ?? null))}
									onerror={() => (pageFaithfulError = currentFile.id)}
									loadMap={loadPageFaithfulMap}
									loadImage={pageFaithfulImage}
								/>
							{/key}
						{:else if flowView && flow}
							<!-- Each file has its own islands, preview copies, and illustrations. -->
							{#key currentFile.id}
								<DocxViewer
									bind:this={flowViewer}
									bind:article
									map={flow}
									sourceText={findings?.text ?? ''}
									mode={visible}
									original={pagesOriginal}
									result={pagesResult}
									active={(placeOpen ? place?.segment.id : undefined) ??
										activeQuestion ??
										undefined}
									fill={wide}
									scale={zoom}
									onsegment={openPlace}
									onmark={(section, rect) =>
										openSelection(section, rectReference(rect, scroller ?? null))}
									onerror={() => (flowError = currentFile.id)}
									load={layoutDocument}
								/>
							{/key}
						{:else if gridView && grid}
							<!-- Each file has its own cells and scroll state. -->
							{#key currentFile.id}
								<TableViewer
									bind:this={gridViewer}
									bind:article
									map={grid}
									sourceText={findings?.text ?? ''}
									mode={visible}
									original={pagesOriginal}
									result={pagesResult}
									active={(placeOpen ? place?.segment.id : undefined) ??
										activeQuestion ??
										undefined}
									fill={wide}
									scale={zoom}
									onsegment={openPlace}
									onerror={() => (gridError = currentFile.id)}
									load={tablesExcerpt}
								/>
							{/key}
						{:else if visible === 'sideBySide'}
							<SideBySide
								bind:article
								{lines}
								active={(placeOpen ? place?.segment.id : undefined) ?? activeQuestion ?? undefined}
								onsegment={openPlace}
							/>
						{:else}
							<DocumentPage
								bind:article
								{paragraphs}
								active={(placeOpen ? place?.segment.id : undefined) ?? activeQuestion ?? undefined}
								onsegment={openPlace}
							/>
						{/if}
					</div>
				{/if}
			</div>
		</div>

		<FloatingBar
			bind:view={viewChoice}
			bind:zoom
			bind:pageWidth
			pageWidthPossible={layoutView}
			bind:menuOpen={maskingOpen}
			bind:bar={barElement}
			kind={kindLabel}
			{kindIcon}
			{places}
			menu={maskingMenu}
		/>

		<UploaderOverlay
			bind:open={uploaderOpen}
			files={filesCount}
			active={zieht}
			locked={uploadLock}
			busy={adds ? t('dropzone.accept') : undefined}
			onselect={add}
		/>
	</section>

	<ReviewPanel
		bind:open={() => barOpen, setBar}
		ready={findings !== null}
		questions={questionsList}
		files={filesCount}
		{otherOpen}
		suggestions={suggestions.length}
		{saveLock}
		saves={entryView.saves}
		again={entryView.saveAgain}
		onsave={() => startSave()}
		onOtherFolder={() => startSave(true)}
		ondecide={decide}
		onundo={(id) => currentFile && entryView.undo(currentFile, id)}
		onsuggestions={acceptSuggestions}
	/>
</div>

{#if selection}
	<div
		bind:this={selectionElement}
		class="fixed top-0 left-0 z-50"
		{@attach schweben(selection.reference, {
			page: selection.mode === 'search' ? 'top' : 'bottom',
			gap: selection.mode === 'search' ? 14 : 10,
			sequences: scroller ?? null
		})}
	>
		<SelectionMenu
			bind:text={selection.text}
			bind:kind={selection.kind}
			mode={selection.mode}
			occurrence={selection.occurrence?.count ?? 0}
			files={selection.occurrence?.file_count ?? 1}
			loads={selection.loads}
			notice={selection.notice}
			pfeil={selection.mode !== 'search'}
			onmask={maskSelection}
			oncancel={() => closeSelection()}
		/>
	</div>
{/if}

{#if place}
	<Menu
		bind:open={placeOpen}
		anchor={place.element}
		title={menuModel?.title}
		entries={(menuModel?.entries ?? []).map((entryMenu) => ({
			label: entryMenu.label,
			onselect: () => runMenu(entryMenu.action)
		}))}
	/>
{/if}

<p id={tastaturId} class="sr-only">{t('review.keyboard')}</p>

<ConfirmDialog
	bind:open={confirmOpen}
	title={t('save.confirm.title')}
	description={t('save.confirm.text', { n: entryView.prepareFiles.length })}
	sequences={[t('save.confirm.sequence.names'), t('save.confirm.sequence.original')]}
	confirm={t('save.confirm.go')}
	onconfirm={() => void save(true, otherFolder)}
/>
