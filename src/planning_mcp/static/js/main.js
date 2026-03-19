// Entry point — init + footer submit logic

import { fetchAndRender, loadExistingComments } from './render.js';
import { connectSSE } from './sse.js';
import { reanchorComments } from './highlight.js';
import { renderCommentCards, updatePendingCount } from './comments.js';
import { initToolbar } from './toolbar.js';
import { submitAllDrafts, postFeedback } from './api.js';
import { comments, addComment, getNextLocalId } from './state.js';
import { initResizeHandles } from './resize.js';

function initFooter() {
  const btn = document.getElementById("submit-btn");
  const statusEl = document.getElementById("footer-status");

  async function submitAllFeedback() {
    const overallText = document.getElementById("overall-feedback").value.trim();
    const drafts = comments.filter(c => c.status === "draft");

    // POST overall as a draft first if present
    if (overallText) {
      try {
        const data = await postFeedback({
          type: "overall",
          selected_text: "",
          anchor_context: "",
          user_message: overallText,
          text_offset: -1,
        });
        addComment({
          localId: "local-" + getNextLocalId(),
          serverId: data.id,
          type: "overall",
          selectedText: "",
          anchorContext: "",
          textOffset: -1,
          userMessage: overallText,
          timestamp: new Date().toISOString(),
          status: "draft",
          replies: [],
          orphaned: false,
        });
      } catch { /* proceed anyway */ }
    }

    if (drafts.length === 0 && !overallText) {
      const reopened = comments.filter(c => c.status === "submitted");
      if (reopened.length > 0) {
        statusEl.textContent = `${reopened.length} reopened comment${reopened.length === 1 ? "" : "s"} already sent \u2014 Claude is revising...`;
        statusEl.className = "footer-status sent";
      } else {
        statusEl.textContent = "No feedback to submit.";
        statusEl.className = "footer-status";
      }
      return;
    }

    btn.disabled = true;
    statusEl.textContent = "Sending...";
    statusEl.className = "footer-status";

    try {
      await submitAllDrafts();

      comments.forEach(c => {
        if (c.status === "draft") c.status = "submitted";
      });

      document.getElementById("overall-feedback").value = "";
      renderCommentCards();
      statusEl.textContent = "Sent \u2014 Claude is revising...";
      statusEl.className = "footer-status sent";
    } catch {
      statusEl.textContent = "Error sending. Try again.";
      statusEl.className = "footer-status";
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", submitAllFeedback);

  document.getElementById("overall-feedback").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submitAllFeedback();
    }
  });
}

// Click highlight → scroll to card
function initHighlightClickHandler() {
  document.querySelector(".main-content").addEventListener("click", (e) => {
    const mark = e.target.closest("mark[data-comment]");
    if (!mark) return;
    const commentId = mark.dataset.comment;
    const card = document.querySelector(`.comment-card[data-local-id="${CSS.escape(commentId)}"]`)
      || document.querySelector(`.comment-card`);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

async function init() {
  await fetchAndRender();
  await loadExistingComments();
  reanchorComments();
  renderCommentCards();
  connectSSE();
  initToolbar();
  initFooter();
  initHighlightClickHandler();
  initResizeHandles();

  // Reposition on resize
  window.addEventListener("resize", () => { renderCommentCards(); });
}

init();
