/**
 * Number and date/time formats for the UI.
 *
 * Use `de-DE` for numbers because CLDR formats Austrian German (`de-AT`) with a narrow
 * space ("1 284"), while the spec and practice expect a dot ("1.284") in both locales.
 * Keep `de-AT` for dates and times.
 */

const NUM = new Intl.NumberFormat('de-DE');
const DATE = new Intl.DateTimeFormat('de-AT', { dateStyle: 'medium' });
const TIME_OF_DAY = new Intl.DateTimeFormat('de-AT', { hour: '2-digit', minute: '2-digit' });
const ZEITPUNKT = new Intl.DateTimeFormat('de-AT', { dateStyle: 'short', timeStyle: 'short' });

export function num(value: number): string {
	return NUM.format(value);
}

export function date(value: Date): string {
	return DATE.format(value);
}

export function timeOfDay(value: Date): string {
	return TIME_OF_DAY.format(value);
}

export function timePoint(value: Date): string {
	return ZEITPUNKT.format(value);
}

const SIZE = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 });

/** "340 KB", "1,2 MB" – steps of 1024 like a file manager. */
export function fileSize(bytes: number): string {
	if (bytes < 1024) return `${SIZE.format(bytes)} B`;
	if (bytes < 1024 * 1024) return `${SIZE.format(Math.round(bytes / 1024))} KB`;
	if (bytes < 1024 * 1024 * 1024) return `${SIZE.format(bytes / (1024 * 1024))} MB`;
	return `${SIZE.format(bytes / (1024 * 1024 * 1024))} GB`;
}
