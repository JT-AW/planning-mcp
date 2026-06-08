import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  esc,
  renderCompare,
  renderSteps,
  renderCallout,
  renderDecision,
} from '../../src/planning_mcp/static/js/blocks.js';

// A stub inline parser that is visibly distinct from raw escaping, so tests can
// prove that body content is routed through the injected parser (in the browser
// this is marked.parseInline).
const tag = (s) => `<i>${s}</i>`;

test('esc escapes the HTML-significant characters', () => {
  assert.equal(esc('a & b < c > d "e"'), 'a &amp; b &lt; c &gt; d &quot;e&quot;');
  assert.equal(esc('plain'), 'plain');
});

test('esc handles ampersand first (no double-encoding)', () => {
  assert.equal(esc('&lt;'), '&amp;lt;');
});

// ── compare ────────────────────────────────────────────────────────────────
test('compare maps tags to card classes and keeps card order', () => {
  const html = renderCompare('[bad] Current\nold way\n[good] Proposed\nnew way', tag);
  assert.match(html, /class="cmp-grid"/);
  assert.match(html, /class="cmp cmp-bad"/);
  assert.match(html, /class="cmp cmp-good"/);
  // order: bad before good
  assert.ok(html.indexOf('cmp-bad') < html.indexOf('cmp-good'));
});

test('compare supports N>2 cards including neutral', () => {
  const html = renderCompare('[bad] A\nx\n[neutral] B\ny\n[good] C\nz', tag);
  assert.equal((html.match(/class="cmp /g) || []).length, 3);
  assert.match(html, /class="cmp cmp-neutral"/);
});

test('compare uses the title after the tag, escaped', () => {
  const html = renderCompare('[good] A & B <x>\nbody', tag);
  assert.match(html, /class="cmp-label">A &amp; B &lt;x&gt;</);
});

test('compare falls back to a default label when the title is empty', () => {
  const html = renderCompare('[bad]\nbody', tag);
  assert.match(html, /class="cmp-label">[^<]+</); // some non-empty default
  assert.doesNotMatch(html, /class="cmp-label"><\/div>/);
});

test('compare routes body text through the injected inline parser', () => {
  const html = renderCompare('[good] T\nhello world', tag);
  assert.match(html, /<i>hello world<\/i>/);
});

test('compare with no recognized tag renders a single neutral card (no crash)', () => {
  const html = renderCompare('just some text\nmore text', tag);
  assert.match(html, /class="cmp cmp-neutral"/);
  assert.equal((html.match(/class="cmp /g) || []).length, 1);
});

// ── steps ────────────────────────────────────────────────────────────────--
test('steps auto-numbers and strips leading markers', () => {
  const html = renderSteps('1. first\n2. second\n3. third', tag);
  const ns = [...html.matchAll(/class="step-n">(\d+)</g)].map((m) => m[1]);
  assert.deepEqual(ns, ['1', '2', '3']);
  assert.doesNotMatch(html, /<i>1\. first<\/i>/); // marker stripped before inline
  assert.match(html, /<i>first<\/i>/);
});

test('steps accepts dash and star markers and bare lines', () => {
  const html = renderSteps('- a\n* b\nc', tag);
  const ns = [...html.matchAll(/class="step-n">(\d+)</g)].map((m) => m[1]);
  assert.deepEqual(ns, ['1', '2', '3']);
});

test('steps splits an optional detail on " | "', () => {
  const html = renderSteps('do the thing | in render.js', tag);
  assert.match(html, /class="step-lead"><i>do the thing<\/i>/);
  assert.match(html, /class="step-detail"><i>in render.js<\/i>/);
});

test('steps omits the detail span when there is no pipe', () => {
  const html = renderSteps('lonely step', tag);
  assert.doesNotMatch(html, /step-detail/);
});

test('steps tolerates blank lines and empty input', () => {
  assert.match(renderSteps('', tag), /class="steps"/);
  assert.equal((renderSteps('\n\n', tag).match(/class="step"/g) || []).length, 0);
});

// ── callout ──────────────────────────────────────────────────────────────--
test('callout applies the type class and a label', () => {
  for (const t of ['note', 'info', 'tip', 'safe', 'warn', 'danger']) {
    const html = renderCallout('body', t, tag);
    assert.match(html, new RegExp(`class="callout callout-${t}"`));
    assert.match(html, /class="callout-label">[^<]+</);
  }
});

test('callout falls back to note for unknown or missing type', () => {
  assert.match(renderCallout('b', 'bogus', tag), /callout callout-note/);
  assert.match(renderCallout('b', '', tag), /callout callout-note/);
  assert.match(renderCallout('b', undefined, tag), /callout callout-note/);
});

test('callout routes the body through the inline parser', () => {
  assert.match(renderCallout('hi there', 'warn', tag), /<i>hi there<\/i>/);
});

// ── decision ─────────────────────────────────────────────────────────────--
test('decision builds a header row from the first line', () => {
  const html = renderDecision('Q | Opt A | Opt B\nworks? | yes | no', tag);
  assert.match(html, /<table class="decision">/);
  assert.match(html, /<th[^>]*>Opt A<\/th>/);
  assert.match(html, /<th[^>]*>Opt B<\/th>/);
});

test('decision colors yes/no/checkmark cells semantically', () => {
  const html = renderDecision('Q | A | B | C\nrow | yes | no | maybe', tag);
  assert.match(html, /class="dec-yes"/);
  assert.match(html, /class="dec-no"/);
  assert.match(html, /class="dec-neutral"[^>]*>maybe</);
});

test('decision treats ✓/✗/true/false as yes/no', () => {
  const html = renderDecision('Q | A | B | C | D\nr | ✓ | ✗ | true | false', tag);
  assert.equal((html.match(/dec-yes/g) || []).length, 2);
  assert.equal((html.match(/dec-no/g) || []).length, 2);
});

test('decision pads ragged rows without crashing', () => {
  const html = renderDecision('Q | A | B\nrow with one | yes', tag);
  assert.match(html, /<table class="decision">/);
  // header has 2 option columns => each body row should expose 2 answer cells
  assert.ok((html.match(/<td class="dec-/g) || []).length >= 2);
});

test('decision escapes header and neutral cell text', () => {
  const html = renderDecision('Q | <x> & y\nrow | weird <thing>', tag);
  assert.match(html, /&lt;x&gt; &amp; y/);
  assert.match(html, /weird &lt;thing&gt;/);
});
