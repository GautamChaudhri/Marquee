import { writable } from 'svelte/store';

export type ToastTone = 'good' | 'bad' | 'info';
export interface ToastItem {
	id: number;
	message: string;
	tone: ToastTone;
}

export const toasts = writable<ToastItem[]>([]);
let seq = 0;

export function toast(message: string, tone: ToastTone = 'info', ms = 3000) {
	const id = ++seq;
	toasts.update((t) => [...t, { id, message, tone }]);
	if (ms) setTimeout(() => dismiss(id), ms);
}

export function dismiss(id: number) {
	toasts.update((t) => t.filter((x) => x.id !== id));
}
