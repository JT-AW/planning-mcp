// DOM highlighting and re-anchoring

import { comments } from './state.js';

export function highlightRange(range, commentId, commentType) {
  // Compute precise character offset of range start within .main-content
  const mainEl = document.querySelector(".main-content");
  const preRange = document.createRange();
  preRange.setStart(mainEl, 0);
  preRange.setEnd(range.startContainer, range.startOffset);
  const offset = preRange.toString().length;
  const selectedText = range.toString();
  if (!selectedText) return;
  highlightTextInDOMWithHint(selectedText, commentId, offset, commentType);
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

export function highlightTextInDOMWithHint(text, commentId, hintOffset, commentType) {
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

  // Collect text nodes that overlap with the target range
  const walker = document.createTreeWalker(mainEl, NodeFilter.SHOW_TEXT);
  let walkerNode;
  let charCount = 0;
  const targetEnd = targetIdx + text.length;
  const nodesToWrap = [];

  while ((walkerNode = walker.nextNode())) {
    const nodeLen = walkerNode.textContent.length;
    const nodeStart = charCount;
    const nodeEnd = charCount + nodeLen;

    if (nodeEnd <= targetIdx) { charCount += nodeLen; continue; }
    if (nodeStart >= targetEnd) break;
    if (walkerNode.parentNode.tagName === "MARK" && walkerNode.parentNode.dataset.comment) {
      charCount += nodeLen; continue;
    }

    const sliceStart = Math.max(0, targetIdx - nodeStart);
    const sliceEnd = Math.min(nodeLen, targetEnd - nodeStart);
    nodesToWrap.push({ node: walkerNode, sliceStart, sliceEnd });
    charCount += nodeLen;
  }

  if (nodesToWrap.length === 0) return false;

  // Wrap in reverse order to avoid invalidating earlier nodes
  for (let i = nodesToWrap.length - 1; i >= 0; i--) {
    const { node, sliceStart, sliceEnd } = nodesToWrap[i];
    const txt = node.textContent;
    const before = txt.slice(0, sliceStart);
    const middle = txt.slice(sliceStart, sliceEnd);
    const after = txt.slice(sliceEnd);

    const parent = node.parentNode;
    const frag = document.createDocumentFragment();
    if (before) frag.appendChild(document.createTextNode(before));
    const mark = document.createElement("mark");
    mark.dataset.comment = commentId;
    if (commentType) mark.dataset.type = commentType;
    mark.textContent = middle;
    frag.appendChild(mark);
    if (after) frag.appendChild(document.createTextNode(after));
    parent.replaceChild(frag, node);
  }
  return true;
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

    const found = highlightTextInDOMWithHint(c.selectedText, markId, c.textOffset, c.type);
    if (!found) {
      // Use anchorContext to locate the region, then highlight selectedText within it.
      // This avoids highlighting the full 100-char context as a superset of the selection.
      let ctxFound = false;
      if (c.anchorContext && c.selectedText) {
        const mainEl = document.querySelector(".main-content");
        const fullText = mainEl.textContent;
        const ctxIdx = fullText.indexOf(c.anchorContext);
        if (ctxIdx !== -1) {
          // Search for selectedText within the context region
          const regionEnd = ctxIdx + c.anchorContext.length;
          const region = fullText.slice(ctxIdx, regionEnd);
          const localIdx = region.indexOf(c.selectedText);
          if (localIdx !== -1) {
            ctxFound = highlightTextInDOMWithHint(c.selectedText, markId, ctxIdx + localIdx, c.type);
          }
        }
      }
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
