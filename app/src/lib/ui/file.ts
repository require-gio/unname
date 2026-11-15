/**
 * File types for `FileBadge`: PDF red, Word blue, tables and images green,
 * text neutral. The short code appears as text on the badge; color alone
 * does not convey meaning.
 */

export type FileKind = 'pdf' | 'doc' | 'table' | 'image' | 'text';

const KINDS: Record<string, { kind: FileKind; shortCode: string }> = {
	pdf: { kind: 'pdf', shortCode: 'PDF' },
	doc: { kind: 'doc', shortCode: 'DOC' },
	docx: { kind: 'doc', shortCode: 'DOC' },
	xls: { kind: 'table', shortCode: 'XLS' },
	xlsx: { kind: 'table', shortCode: 'XLS' },
	csv: { kind: 'table', shortCode: 'CSV' },
	jpg: { kind: 'image', shortCode: 'JPG' },
	jpeg: { kind: 'image', shortCode: 'JPG' },
	png: { kind: 'image', shortCode: 'PNG' },
	tif: { kind: 'image', shortCode: 'TIF' },
	tiff: { kind: 'image', shortCode: 'TIF' },
	md: { kind: 'text', shortCode: 'MD' },
	txt: { kind: 'text', shortCode: 'TXT' }
};

/** Determine kind and short code from filename; unknown extensions default to text. */
export function fileKind(name: string): { kind: FileKind; shortCode: string } {
	const extension = /\.([a-z0-9]+)$/i.exec(name)?.[1]?.toLowerCase() ?? '';
	return (
		KINDS[extension] ?? { kind: 'text', shortCode: extension.slice(0, 4).toUpperCase() || 'TXT' }
	);
}
