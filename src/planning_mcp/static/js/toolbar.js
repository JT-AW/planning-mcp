// Selection toolbar: mouseup/mousedown/keydown listeners, toolbar button click

import { comments, addComment, getNextLocalId } from './state.js';
import { renderCommentCards } from './comments.js';
import { highlightRange, getAnchorContext, getTextOffset } from './highlight.js';
import { postFeedback } from './api.js';

export function initToolbar() {
  const toolbar = document.getElementById("selection-toolbar");
  let savedRange = null;

  document.querySelector(".main-content").addEventListener("mouseup", () => {
    setTimeout(() => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.toString().trim() === "") {
        return;
      }
      const mainEl = document.querySelector(".main-content");
      if (!mainEl.contains(sel.anchorNode) || !mainEl.contains(sel.focusNode)) return;

      savedRange = sel.getRangeAt(0).cloneRange();
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      const toolbarW = toolbar.offsetWidth || 200;
      let left = rect.left + rect.width / 2;
      left = Math.max(toolbarW / 2 + 8, Math.min(left, window.innerWidth - toolbarW / 2 - 8));
      toolbar.style.left = `${left}px`;
      toolbar.style.top = `${Math.max(8, rect.top - 44)}px`;
      toolbar.classList.add("visible");
    }, 10);
  });

  document.addEventListener("mousedown", (e) => {
    if (toolbar.contains(e.target)) return;
    toolbar.classList.remove("visible");
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toolbar.classList.remove("visible");
  });

  toolbar.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-tb-action]");
    if (!btn || !savedRange) return;

    const action = btn.dataset.tbAction;
    const selectedText = savedRange.toString().trim();
    const anchorContext = getAnchorContext(savedRange);
    const textOffset = getTextOffset(savedRange);
    const commentId = "local-" + getNextLocalId();

    highlightRange(savedRange, commentId);

    addComment({
      localId: commentId,
      serverId: null,
      type: action,
      selectedText: selectedText,
      anchorContext: anchorContext,
      textOffset: textOffset,
      userMessage: "",
      timestamp: null,
      status: "editing",
      replies: [],
      orphaned: false,
    });

    toolbar.classList.remove("visible");
    window.getSelection().removeAllRanges();
    renderCommentCards();

    setTimeout(() => {
      const card = document.querySelector(`.comment-card[data-local-id="${commentId}"]`);
      if (card) {
        const ta = card.querySelector("textarea");
        if (ta) ta.focus();
      }
    }, 50);
  });
}
