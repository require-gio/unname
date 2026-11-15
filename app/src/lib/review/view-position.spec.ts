// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { pagesMap } from '#lib/layout/map.js';
import {
	readPagePosition,
	readTextPosition,
	restorePagePosition,
	textAnchorRect
} from './view-position.js';

afterEach(() => vi.restoreAllMocks());

function rect(top: number, height = 20): DOMRect {
	return new DOMRect(0, top, 600, height);
}

describe('persistent view source anchors', () => {
	it('finds the first visible word inside a long segment in UTF-16 units', () => {
		const root = document.createElement('article');
		root.innerHTML = '<span data-source="40:57">😀 alpha beta end</span>';
		vi.spyOn(root.firstElementChild!, 'getBoundingClientRect').mockReturnValue(rect(0, 60));
		const createRange = document.createRange.bind(document);
		vi.spyOn(document, 'createRange').mockImplementation(() => {
			const range = createRange();
			range.getBoundingClientRect = () => rect(range.startOffset < 9 ? 0 : 30);
			return range;
		});
		expect(readTextPosition(root, 25)).toEqual({ page: 1, anchor: 49 });
		expect(textAnchorRect(root, 49)?.top).toBe(30);
	});

	it('keeps the original offset for a replacement with a different length', () => {
		const root = document.createElement('article');
		root.innerHTML =
			'<section class="docx"></section><section class="docx"><span data-source="21:28">&lt;&lt;PERSON_12345678&gt;&gt;</span></section>';
		vi.spyOn(root.querySelector('span')!, 'getBoundingClientRect').mockReturnValue(rect(60));
		expect(readTextPosition(root, 50)).toEqual({ page: 2, anchor: 21 });
		expect(textAnchorRect(root, 25)?.top).toBe(60);
	});

	it('restores the source word when repagination moves it to another page', () => {
		const root = document.createElement('article');
		root.innerHTML = '<div data-page="0"></div><div data-page="1"></div><div data-page="2"></div>';
		const map = pagesMap({
			job_id: 'file',
			kind: 'pages',
			pages: [0, 1, 2].map((index) => ({
				index,
				width: 600,
				height: 800,
				unit: 'pt',
				source: 'text',
				scales: [1]
			})),
			words: [[2, 10, 120, 60, 140, 90, 96, 0]]
		})!;
		[...root.children].forEach((page, index) => {
			vi.spyOn(page, 'getBoundingClientRect').mockReturnValue(rect(index * 800, 800));
		});
		const scroller = document.createElement('div');
		vi.spyOn(scroller, 'getBoundingClientRect').mockReturnValue(rect(100, 600));
		expect(restorePagePosition(root, map, scroller, { page: 2, anchor: 90 })).toBe(true);
		expect(scroller.scrollTop).toBe(1620);
		expect(readPagePosition(root, map, 1700)).toEqual({ page: 3, anchor: 90 });
	});

	it('falls back to the saved page for a page without text', () => {
		const root = document.createElement('article');
		root.innerHTML = '<div data-page="0"></div>';
		vi.spyOn(root.firstElementChild!, 'getBoundingClientRect').mockReturnValue(rect(200, 800));
		const map = pagesMap({
			job_id: 'file',
			kind: 'pages',
			pages: [{ index: 0, width: 600, height: 800, unit: 'pt', source: 'text', scales: [1] }],
			words: []
		})!;
		expect(readPagePosition(root, map, 300)).toEqual({ page: 1, anchor: null });
	});

	it('preserves the page when repeated headers share a source anchor', () => {
		const root = document.createElement('article');
		root.innerHTML = '<div data-page="0"></div><div data-page="1"></div>';
		[...root.children].forEach((page, index) => {
			vi.spyOn(page, 'getBoundingClientRect').mockReturnValue(rect(index * 800, 800));
		});
		const map = pagesMap({
			job_id: 'file',
			kind: 'pages',
			pages: [0, 1].map((index) => ({
				index,
				width: 600,
				height: 800,
				unit: 'pt',
				source: 'text',
				scales: [1]
			})),
			words: [
				[0, 10, 20, 90, 40, 5, 12, 0],
				[1, 10, 20, 90, 40, 5, 12, 0]
			]
		})!;
		const scroller = document.createElement('div');
		vi.spyOn(scroller, 'getBoundingClientRect').mockReturnValue(rect(100, 600));
		expect(restorePagePosition(root, map, scroller, { page: 2, anchor: 5 })).toBe(true);
		expect(scroller.scrollTop).toBe(720);
	});
});
