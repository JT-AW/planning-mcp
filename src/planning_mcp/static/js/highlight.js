// DOM highlighting and re-anchoring

import { comments } from './state.js';

export function highlightRange(range, commentId) {
  const textNodes = getTextNodesInRange(range);
  textNodes.forEach(({ node, startOffset, endOffset }) => {
    const text = node.textContent;
    const before = text.slice(0, startOffset);
    const middle = text.slice(startOffset, endOffset);
    const after = text.slice(endOffset);

    const parent = node.parentNode;
    const frag = document.createDocumentFragment();
    if (before) frag.appendChild(document.createTextNode(before));

    const mark = document.createElement("mark");
    mark.dataset.comment = commentId;
    mark.textContent = middle;
    frag.appendChild(mark);

    if (after) frag.appendChild(document.createTextNode(after));
    parent.replaceChild(frag, node);
  });
}

export function getTextNodesInRange(range) {
  const result = [];
  const walker = document.createTreeWalker(
    range.commonAncestorContainer.nodeType === 1
      ? range.commonAncestorContainer
      : range.commonAncestorContainer.parentElement,
    NodeFilter.SHOW_TEXT
  );

  let node;
  let inRange = false;
  while ((node = walker.nextNode())) {
    if (node === range.startContainer) {
      inRange = true;
      const start = range.startOffset;
      const end = node === range.endContainer ? range.endOffset : node.textContent.length;
      if (start < end) result.push({ node, startOffset: start, endOffset: end });
      if (node === range.endContainer) break;
      continue;
    }
    if (node === range.endContainer) {
      if (range.endOffset > 0) {
        result.push({ node, startOffset: 0, endOffset: range.endOffset });
      }
      break;
    }
    if (inRange) {
      result.push({ node, startOffset: 0, endOffset: node.textContent.length });
    }
  }
  return result;
}

export function highlightTextInDOMWithHint(text, commentId, hintOffset) {
  const mainEl = document.querySelector(".main-content");
  const fullText = mainEl.textContent;

  let targetIdx = -1;
  if (hintOffset >= 0) {
    const windowStart = Math.max(0, hintOffset - 50);
    const windowEnd = Math.min(fullText.length, hintOffset + text.length + 50);
    const win = fullText.slice(windowStart, windowEnd);
    const localIdx = win.indexOf(text);
    if (localIdx !== -1) targetIdx = windowStart + localIdx;
  }
  if (targetIdx === -1) targetIdx = fullText.indexOf(text);
  if (targetIdx === -1) return false;

  const walker = document.createTreeWalker(mainEl, NodeFilter.SHOW_TEXT);
  let walkerNode;
  let charCount = 0;
  while ((walkerNode = walker.nextNode())) {
    const nodeLen = walkerNode.textContent.length;
    if (charCount + nodeLen <= targetIdx) {
      charCount += nodeLen;
      continue;
    }
    if (walkerNode.parentNode.tagName === "MARK" && walkerNode.parentNode.dataset.comment) {
      charCount += nodeLen;
      continue;
    }

    const localStart = targetIdx - charCount;
    const localEnd = Math.min(localStart + text.length, nodeLen);
    const before = walkerNode.textContent.slice(0, localStart);
    const middle = walkerNode.textContent.slice(localStart, localEnd);
    const after = walkerNode.textContent.slice(localEnd);

    const parent = walkerNode.parentNode;
    const frag = document.createDocumentFragment();
    if (before) frag.appendChild(document.createTextNode(before));
    const mark = document.createElement("mark");
    mark.dataset.comment = commentId;
    mark.textContent = middle;
    frag.appendChild(mark);
    if (after) frag.appendChild(document.createTextNode(after));
    parent.replaceChild(frag, walkerNode);
    return true;
  }
  return false;
}

export function removeHighlight(commentId) {
  document.querySelectorAll(`mark[data-comment="${CSS.escape(String(commentId))}"]`).forEach(mark => {
    const parent = mark.parentNode;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
  });
}

export function reanchorComments() {
  // Sort by descending text offset so later-in-document highlights are inserted
  // first — this prevents DOM mutations from shifting character positions of
  // earlier highlights (TreeWalker loses its place after replaceChild splits nodes).
  const toAnchor = comments
    .filter(c => c.type !== "overall" && c.status !== "editing")
    .sort((a, b) => (b.textOffset ?? -1) - (a.textOffset ?? -1));

  toAnchor.forEach(c => {
    const markId = c.localId;

    const found = highlightTextInDOMWithHint(c.selectedText, markId, c.textOffset);
    if (!found) {
      const ctxFound = c.anchorContext
        ? highlightTextInDOMWithHint(c.anchorContext, markId, c.textOffset)
        : false;
      if (!ctxFound) c.orphaned = true;
    } else {
      c.orphaned = false;
    }
  });
}

export function getAnchorContext(range) {
  const container = range.startContainer.parentElement
    ? range.startContainer.parentElement.closest(".section-content")
    : null;
  if (!container) return range.toString();
  const fullText = container.textContent;
  const selectedText = range.toString();
  const idx = fullText.indexOf(selectedText);
  if (idx === -1) return selectedText;
  const start = Math.max(0, idx - 50);
  const end = Math.min(fullText.length, idx + selectedText.length + 50);
  return fullText.slice(start, end);
}

export function getTextOffset(range) {
  const mainEl = document.querySelector(".main-content");
  const fullText = mainEl.textContent;
  const selectedText = range.toString();
  const idx = fullText.indexOf(selectedText);
  return idx !== -1 ? idx : -1;
}
