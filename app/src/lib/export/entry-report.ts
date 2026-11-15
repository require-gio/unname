import type { MessageKey, Translator } from '#lib/i18n/translate.js';
import { CATALOG } from '#lib/masking/catalog.js';
import type { EntryReportResponse, FileOutput } from '#lib/sidecar/api.gen.js';
import { LABEL_DISPLAY } from '#lib/ui/labels.js';
import { outputNotices, labelFrom } from '#lib/saved/saved.js';

/**
 * Report for a saved entry.
 *
 * In UI terminology: “Reversibly masked” means pseudonymisation, “Irreversibly masked”
 * means redaction – not yet anonymised, the strongest anonymisation level. The report lists
 * counters, decisions by origin and review data – never original values, never replacements,
 * never text snippets. Data comes from `GET /entries/{id}/report`; this code only formats it.
 */

export interface ReportLine {
	label: string;
	value: string;
}

export interface ReportChapter {
	title: string;
	lines: ReportLine[];
	notices?: string[];
}

export interface Eintragsbericht {
	title: string;
	chapters: ReportChapter[];
}

const RESIDUAL_RISK: Record<NonNullable<FileOutput['residual_risk']>, MessageKey> = {
	clear: 'report.residualRisk.clear',
	warning: 'report.residualRisk.warning',
	not_run: 'report.residualRisk.not_run',
	failed: 'report.residualRisk.failed'
};

function categoriesText(ids: readonly string[], t: Translator): string {
	return CATALOG.filter((category) => ids.includes(category.id))
		.map((category) => t(`category.${category.id}`))
		.join(', ');
}

function lineCount(afterCategory: Record<string, number> | undefined, t: Translator) {
	return CATALOG.filter((category) => (afterCategory?.[category.id] ?? 0) > 0).map((category) => ({
		label: `  ${t(`category.${category.id}`)}`,
		value: String(afterCategory![category.id])
	}));
}

const RESIDUAL_RISK_CLASSES: Record<string, MessageKey> = {
	date_time: 'residualRisk.date_time',
	amount_quantity: 'residualRisk.amount_quantity',
	reference_number: 'residualRisk.reference_number',
	location_region: 'residualRisk.location_region',
	rare_term: 'residualRisk.rare_term'
};

/** Map identifiers from `residual_risk.py`; unknown ones are left unchanged. */
function residualRiskClasses(classes: readonly string[], t: Translator): string[] {
	return classes.map((klass) =>
		RESIDUAL_RISK_CLASSES[klass] ? t(RESIDUAL_RISK_CLASSES[klass]) : klass
	);
}

function residualRisk(output: FileOutput, t: Translator): string {
	const status = output.residual_risk ?? 'not_run';
	return t(RESIDUAL_RISK[status], {
		classes: residualRiskClasses(output.residual_classes ?? [], t).join(', ')
	});
}

function labelText(label: FileOutput['label'], t: Translator): string {
	if (!label) return t('report.noLabel');
	const display = LABEL_DISPLAY[labelFrom(label)];
	return `${t(display.text)} (${t(display.technicalTerm)})`;
}

export function entryReport(
	report: EntryReportResponse,
	t: Translator,
	time: (iso: string) => string
): Eintragsbericht {
	const total = report.totals;
	const masking = report.masking;
	const chapters: ReportChapter[] = [
		{
			title: t('report.entry'),
			lines: [
				{ label: t('report.entry.title'), value: report.title },
				{ label: t('report.entry.created'), value: time(report.created_at) },
				...(report.saved_at
					? [{ label: t('report.entry.saved'), value: time(report.saved_at) }]
					: []),
				{
					label: t('report.entry.generated'),
					value: t('report.entry.version', {
						version: report.app_version,
						profile: report.profile_version
					})
				}
			]
		},
		{
			title: t('report.masking'),
			lines: [
				{
					label: t('report.masking.kind'),
					value: t(masking.method === 'reversible' ? 'masking.reversible' : 'masking.irreversible')
				},
				{
					label: t('report.masking.style'),
					value: t(`masking.style.${masking.style}`)
				},
				{
					label: t('report.masking.categories'),
					value: categoriesText(masking.categories, t)
				},
				{
					label: t('report.masking.level'),
					value: String(report.protection_level)
				}
			]
		},
		{
			title: t('report.result'),
			lines: [
				{ label: t('report.result.masked'), value: String(total.masked ?? 0) },
				...lineCount(total.masked_by_category, t),
				{ label: t('report.result.byYou'), value: String(total.decided_by_user ?? 0) },
				{ label: t('report.result.byAi'), value: String(total.decided_by_ai ?? 0) },
				{
					label: t('report.result.inDoubt'),
					value: String(total.masked_without_decision ?? 0)
				},
				{ label: t('report.result.keep'), value: String(total.kept_here ?? 0) },
				{ label: t('report.result.byHand'), value: String(total.added_by_hand ?? 0) },
				{ label: t('report.result.corrected'), value: String(total.adjusted ?? 0) },
				{ label: t('report.residualRisk'), value: residualRisk(total, t) }
			],
			notices: [t('report.notice.heuristic')]
		}
	];

	const files: ReportLine[] = [];
	const notices: string[] = [];
	report.files.forEach((file, index) => {
		const output = file.output;
		const number = `${index + 1}. ${file.name}`;
		if (!output) {
			files.push({ label: number, value: t('report.file.notSaved') });
			return;
		}
		if (output.status === 'unchanged') {
			files.push({ label: number, value: t('report.file.unchanged') });
			return;
		}
		const parts = [labelText(output.label, t), t('masking.places', { n: output.masked ?? 0 })];
		parts.push(...outputNotices(output, t));
		if (output.text_extraction_checked) {
			parts.push(t('report.file.pdf', { n: output.rendered_pages ?? 0 }));
		}
		if (
			output.label !== 'anonymous' &&
			(output.anonymous_blockers ?? []).includes('residual_risk')
		) {
			parts.push(residualRisk(output, t));
		}
		files.push({ label: number, value: parts.join(' · ') });
		if (output.sha256) {
			files.push({ label: `   ${t('report.file.checksum')}`, value: output.sha256 });
		}
		if (output.audit_id != null) {
			files.push({
				label: `   ${t('report.file.audit')}`,
				value: `#${output.audit_id}`
			});
		}
		if (file.source_changed) notices.push(t('report.notice.changed', { name: file.name }));
	});
	chapters.push({
		title: t('report.files'),
		lines: files,
		notices: [...notices, t('report.notice.original'), t('report.notice.content')]
	});

	return { title: `${t('report.title')} – ${report.title}`, chapters };
}

/** Plain-text report for saving alongside the stored files. */
export function entryReportAsText(report: Eintragsbericht): string {
	const lines: string[] = [report.title, '='.repeat(report.title.length), ''];
	for (const chapter of report.chapters) {
		lines.push(chapter.title, '-'.repeat(chapter.title.length));
		for (const line of chapter.lines) lines.push(`${line.label}: ${line.value}`);
		for (const notice of chapter.notices ?? []) lines.push(`* ${notice}`);
		lines.push('');
	}
	return lines.join('\n');
}
