import type { MessageKey } from '#lib/i18n/translate.js';
import type { SidecarSnapshot } from './types.js';

/**
 * Describes what the shell shows about processing state (banner) and why the
 * dropzone is locked.
 *
 * Four states: start = waiting (quiet), failure = error (red), outside = no
 * processing possible, and setup = initial configuration pending. Only
 * uploading, saving and copying are blocked; reading remains possible.
 */
export type GatePosition = 'starts' | 'failure' | 'outside' | 'setup';

export interface GateNotice {
	position: GatePosition;
	/** The shell stops trying itself; offer a "Restart". */
	restart: boolean;
}

export function gateNotice(status: {
	snapshot: SidecarSnapshot;
	outside: boolean;
}): GateNotice | null {
	if (status.outside) return { position: 'outside', restart: false };
	const { snapshot } = status;
	if (snapshot.can_upload && snapshot.can_export) return null;
	if (snapshot.status.state === 'ready' && snapshot.setup_required === true) {
		return { position: 'setup', restart: false };
	}
	// Service responded but the finding for the models is still pending –
	// typically right after a restart following installation.
	if (
		snapshot.status.state === 'ready' &&
		snapshot.setup_required === null &&
		snapshot.can_export
	) {
		return { position: 'starts', restart: false };
	}
	switch (snapshot.status.state) {
		case 'starting':
			return { position: 'starts', restart: false };
		case 'unreachable':
			return { position: 'failure', restart: !snapshot.status.restarting };
		default:
			return { position: 'failure', restart: false };
	}
}

export const GATE_TEXT: Record<
	GatePosition,
	{ title: MessageKey; text: MessageKey; lock: MessageKey }
> = {
	starts: {
		title: 'gate.starts.title',
		text: 'gate.starts.text',
		lock: 'dropzone.locked.starts'
	},
	failure: {
		title: 'gate.failure.title',
		text: 'gate.failure.text',
		lock: 'dropzone.locked.failure'
	},
	outside: {
		title: 'gate.outside.title',
		text: 'gate.outside.text',
		lock: 'dropzone.locked.outside'
	},
	setup: {
		title: 'gate.setup.title',
		text: 'gate.setup.text',
		lock: 'dropzone.locked.setup'
	}
};
