// Pure renderers for the custom plan visual blocks (compare / steps / callout /
// decision). Each returns a static HTML string and is wired into a `marked`
// block extension in render.js.
//
// `inline` is the inline-markdown parser, injected rather than imported: the
// browser passes `marked.parseInline`; tests pass a stub. This keeps these
// functions pure and testable in plain Node with no `marked` dependency. It
// defaults to `esc` so any caller that forgets it still produces escaped output.

export function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const COMPARE_TAG = /^\[(bad|good|neutral)\]\s*(.*)$/;
const COMPARE_DEFAULT_LABEL = { bad: 'Current', good: 'Proposed', neutral: 'Note' };

export function renderCompare(text, inline = esc) {
  const cards = [];
  let cur = null;
  for (const line of text.split('\n')) {
    const m = COMPARE_TAG.exec(line.trim());
    if (m) {
      cur = { kind: m[1], title: m[2].trim(), body: [] };
      cards.push(cur);
    } else {
      if (!cur) {
        cur = { kind: 'neutral', title: '', body: [] };
        cards.push(cur);
      }
      cur.body.push(line);
    }
  }

  const cardsHtml = cards.map((card) => {
    const label = card.title ? esc(card.title) : COMPARE_DEFAULT_LABEL[card.kind];
    const body = card.body
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => inline(l))
      .join('<br>');
    return `<div class="cmp cmp-${card.kind}"><div class="cmp-label">${label}</div><div class="cmp-body">${body}</div></div>`;
  }).join('');

  return `<div class="cmp-grid">${cardsHtml}</div>`;
}

const STEP_MARKER = /^(\d+[.)]|[-*])\s+/;

export function renderSteps(text, inline = esc) {
  const items = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => l.replace(STEP_MARKER, ''));

  const stepsHtml = items.map((item, i) => {
    const sep = item.indexOf(' | ');
    const lead = sep === -1 ? item : item.slice(0, sep);
    const detail = sep === -1 ? '' : item.slice(sep + 3).trim();
    const detailHtml = detail
      ? `<span class="step-detail">${inline(detail)}</span>`
      : '';
    return `<div class="step"><span class="step-n">${i + 1}</span><div class="step-text"><span class="step-lead">${inline(lead.trim())}</span>${detailHtml}</div></div>`;
  }).join('');

  return `<div class="steps">${stepsHtml}</div>`;
}

const CALLOUT_LABEL = {
  note: 'Note',
  info: 'Info',
  tip: 'Tip',
  safe: 'Safe',
  warn: 'Warning',
  danger: 'Danger',
};

export function renderCallout(text, type, inline = esc) {
  const key = (type || '').toLowerCase();
  const t = CALLOUT_LABEL[key] ? key : 'note';
  const body = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => inline(l))
    .join('<br>');
  return `<div class="callout callout-${t}"><div class="callout-label">${CALLOUT_LABEL[t]}</div><div class="callout-body">${body}</div></div>`;
}

const DECISION_YES = new Set(['yes', 'y', '✓', 'true']);
const DECISION_NO = new Set(['no', 'n', '✗', 'false']);

function decisionCell(raw) {
  const v = (raw || '').trim().toLowerCase();
  if (DECISION_YES.has(v)) return '<td class="dec-yes">✓</td>';
  if (DECISION_NO.has(v)) return '<td class="dec-no">✗</td>';
  return `<td class="dec-neutral">${esc(raw || '')}</td>`;
}

export function renderDecision(text, inline = esc) {
  const rows = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((r) => r.split('|').map((c) => c.trim()));
  if (rows.length === 0) return '<table class="decision"></table>';

  const header = rows[0];
  const ncols = header.length;
  const ths = header
    .map((c, i) => (i === 0 ? `<th class="dq">${esc(c)}</th>` : `<th>${esc(c)}</th>`))
    .join('');

  const bodyRows = rows.slice(1).map((cells) => {
    const tds = [`<td class="dq">${inline(cells[0] || '')}</td>`];
    for (let i = 1; i < ncols; i++) tds.push(decisionCell(cells[i]));
    return `<tr>${tds.join('')}</tr>`;
  }).join('');

  return `<table class="decision"><thead><tr>${ths}</tr></thead><tbody>${bodyRows}</tbody></table>`;
}
