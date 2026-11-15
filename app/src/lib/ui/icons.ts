import type { Component } from 'svelte';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import ArrowRight from '@lucide/svelte/icons/arrow-right';
import Check from '@lucide/svelte/icons/check';
import CircleCheck from '@lucide/svelte/icons/circle-check';
import ChevronDown from '@lucide/svelte/icons/chevron-down';
import ChevronUp from '@lucide/svelte/icons/chevron-up';
import ClipboardPaste from '@lucide/svelte/icons/clipboard-paste';
import Columns2 from '@lucide/svelte/icons/columns-2';
import Copy from '@lucide/svelte/icons/copy';
import Download from '@lucide/svelte/icons/download';
import ExternalLink from '@lucide/svelte/icons/external-link';
import FileText from '@lucide/svelte/icons/file-text';
import FileUp from '@lucide/svelte/icons/file-up';
import Folder from '@lucide/svelte/icons/folder';
import Inbox from '@lucide/svelte/icons/inbox';
import Info from '@lucide/svelte/icons/info';
import Layers from '@lucide/svelte/icons/layers';
import Lock from '@lucide/svelte/icons/lock';
import Minus from '@lucide/svelte/icons/minus';
import PencilLine from '@lucide/svelte/icons/pencil-line';
import PanelLeft from '@lucide/svelte/icons/panel-left';
import PanelRight from '@lucide/svelte/icons/panel-right';
import Plus from '@lucide/svelte/icons/plus';
import Printer from '@lucide/svelte/icons/printer';
import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
import Search from '@lucide/svelte/icons/search';
import Settings from '@lucide/svelte/icons/settings';
import ShieldCheck from '@lucide/svelte/icons/shield-check';
import ShieldQuestionMark from '@lucide/svelte/icons/shield-question-mark';
import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
import Sparkle from '@lucide/svelte/icons/sparkle';
import TextAlignStart from '@lucide/svelte/icons/text-align-start';
import Trash2 from '@lucide/svelte/icons/trash-2';
import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
import Upload from '@lucide/svelte/icons/upload';
import X from '@lucide/svelte/icons/x';

/**
 * UI icons, named by their purpose.
 *
 * One purpose, one icon: "restore" uses `rotate-ccw`,
 * "irreversible" uses the lock. Add new icons here
 * instead of importing directly from Lucide.
 */
export const ICONS = {
	new: Plus,
	close: X,
	haken: Check,
	aufklappen: ChevronDown,
	zuklappen: ChevronUp,
	back: ArrowLeft,
	next: ArrowRight,
	upload: Upload,
	save: Download,
	/** Load models – same visual, different action. */
	herunterladen: Download,
	folder: Folder,
	document: FileText,
	text: TextAlignStart,
	copy: Copy,
	insert: ClipboardPaste,
	settings: Settings,
	sidebar: PanelLeft,
	reviewBar: PanelRight,
	restore: RotateCcw,
	lock: Lock,
	ai: Sparkle,
	regler: SlidersHorizontal,
	small: Minus,
	larger: Plus,
	info: Info,
	badge: ShieldCheck,
	badgeQuestion: ShieldQuestionMark,
	warning: TriangleAlert,
	delete: Trash2,
	extern: ExternalLink,
	fileHigh: FileUp,
	sideBySide: Columns2,
	/** "pageFaithful": pages as they would be printed. */
	pageFaithful: Printer,
	/** History view; "All entries" also means "Back to history". */
	open: Inbox,
	abgeschlossen: CircleCheck,
	allEntries: Layers,
	search: Search,
	rename: PencilLine
} satisfies Record<string, Component>;

/** Icons not provided by Lucide; `Icon.svelte` draws them. */
export const OWN_ICONS = ['lockFull'] as const;

export type IconName = keyof typeof ICONS | (typeof OWN_ICONS)[number];

/** Filled icons (the AI icon is a solid star in the mockup). */
export const FILLED = new Set<IconName>(['ai']);
