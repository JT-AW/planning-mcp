// Margin comment card UI

import { comments, removeComment, getNextLocalId } from './state.js';
import { postFeedback, postReply } from './api.js';
import { removeHighlight } from './highlight.js';

function getHighlightTop(markId) {
  const mark = document.querySelector(`mark[data-comment="${CSS.escape(String(markId))}"]`);
  if (!mark) return null;
  return mark.getBoundingClientRect().top + window.scrollY;
}

export function renderCommentCards() {
  const margin = document.getElementById("comment-margin");
  margin.textContent = "";

  const sorted = [...comments].sort((a, b) => {
    const aY = getHighlightTop(a.localId);
    const bY = getHighlightTop(b.localId);
    if (aY === null && bY === null) return 0;
    if (aY === null) return 1;
    if (bY === null) return -1;
    return aY - bY;
  });

  sorted.forEach(c => {
    const card = buildCommentCard(c);
    margin.appendChild(card);
  });

  updatePendingCount();
}

export function buildCommentCard(c) {
  const card = document.createElement("div");
  const markId = c.localId;
  card.className = `comment-card ${c.status}${c.orphaned ? " orphaned" : ""}`;
  card.dataset.localId = c.localId;

  // Header
  const header = document.createElement("div");
  header.className = "comment-header";

  const badge = document.createElement("span");
  badge.className = `comment-type-badge ${c.type}`;
  badge.textContent = c.type === "investigate" ? "Investigate"
    : c.type === "update_opinion" ? "Update" : "Overall";
  header.appendChild(badge);

  if (c.orphaned) {
    const ob = document.createElement("span");
    ob.className = "orphan-badge";
    ob.textContent = "text changed";
    header.appendChild(ob);
  }

  // "Go to text" link
  if (c.type !== "overall" && c.status !== "editing") {
    const goLink = document.createElement("span");
    goLink.className = "go-to-text";
    goLink.textContent = "\u2197";
    goLink.title = "Scroll to highlighted text";
    goLink.addEventListener("click", (e) => {
      e.stopPropagation();
      const marks = document.querySelectorAll(`mark[data-comment="${CSS.escape(String(markId))}"]`);
      if (marks.length > 0) {
        marks[0].scrollIntoView({ behavior: "smooth", block: "center" });
        marks.forEach(m => {
          m.classList.remove("pulsing");
          void m.offsetWidth;
          m.classList.add("pulsing");
        });
        setTimeout(() => marks.forEach(m => m.classList.remove("pulsing")), 2000);
      }
    });
    header.appendChild(goLink);
  }

  card.appendChild(header);

  // Quoted text
  if (c.selectedText && c.type !== "overall") {
    const quote = document.createElement("div");
    quote.className = "comment-quote";
    quote.textContent = c.selectedText;
    card.appendChild(quote);
  }

  if (c.status === "editing") {
    // Editing mode
    const editArea = document.createElement("div");
    editArea.className = "comment-edit-area";

    const ta = document.createElement("textarea");
    ta.placeholder = c.type === "investigate"
      ? "What should Claude verify?"
      : "What do you want changed?";
    ta.addEventListener("input", () => {
      ta.style.height = "auto";
      ta.style.height = ta.scrollHeight + "px";
    });

    const actions = document.createElement("div");
    actions.className = "comment-edit-actions";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn-ghost btn-sm";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", () => {
      removeHighlight(markId);
      removeComment(c.localId);
      renderCommentCards();
    });

    const addBtn = document.createElement("button");
    addBtn.className = "btn-primary btn-sm";
    addBtn.textContent = "Add Comment";
    addBtn.addEventListener("click", async () => {
      const text = ta.value.trim();
      if (!text) { ta.focus(); return; }
      c.userMessage = text;
      c.status = "draft";
      c.timestamp = new Date().toISOString();
      try {
        const data = await postFeedback({
          type: c.type,
          selected_text: c.selectedText,
          anchor_context: c.anchorContext,
          user_message: c.userMessage,
          text_offset: c.textOffset || -1,
        });
        c.serverId = data.id;
      } catch { /* will retry on submit */ }
      renderCommentCards();
    });

    // Cmd+Enter to submit
    ta.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        addBtn.click();
      }
    });

    editArea.appendChild(ta);
    actions.appendChild(cancelBtn);
    actions.appendChild(addBtn);
    editArea.appendChild(actions);
    card.appendChild(editArea);
  } else {
    // Display mode
    const msg = document.createElement("div");
    msg.className = "comment-message";
    msg.textContent = c.userMessage;
    card.appendChild(msg);

    // Replies
    if (c.replies && c.replies.length > 0) {
      const thread = document.createElement("div");
      thread.className = "thread";
      c.replies.forEach(r => {
        const reply = document.createElement("div");
        reply.className = `thread-reply${r.is_pushback ? " pushback" : ""}`;

        if (r.is_pushback && r.pushback_reasoning) {
          const pbLabel = document.createElement("div");
          pbLabel.className = "pushback-label";
          pbLabel.textContent = "Claude disagrees";
          reply.appendChild(pbLabel);
        }

        const author = document.createElement("div");
        author.className = `reply-author ${r.author}`;
        author.textContent = r.author === "claude" ? "Claude" : "You";
        reply.appendChild(author);

        const rmsg = document.createElement("div");
        rmsg.className = "reply-message";
        const replyText = r.is_pushback ? (r.pushback_reasoning || r.message) : r.message;
        if (r.author === "claude") {
          // Safe: sanitized by DOMPurify before DOM insertion (same pattern as render.js)
          rmsg.innerHTML = DOMPurify.sanitize(marked.parse(replyText)); // nosec: sanitized
        } else {
          rmsg.textContent = replyText;
        }
        reply.appendChild(rmsg);

        thread.appendChild(reply);
      });
      card.appendChild(thread);
    }

    // Reply trigger
    if (c.status === "submitted" && c.serverId) {
      const trigger = document.createElement("span");
      trigger.className = "reply-trigger";
      trigger.textContent = "Reply";
      trigger.addEventListener("click", () => showReplyInput(card, c));
      card.appendChild(trigger);
    }

    // Timestamp
    if (c.timestamp) {
      const ts = document.createElement("div");
      ts.className = "comment-time";
      const d = new Date(c.timestamp);
      ts.textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      card.appendChild(ts);
    }
  }

  return card;
}

export function showReplyInput(card, comment) {
  if (card.querySelector(".reply-input-area")) return;
  const area = document.createElement("div");
  area.className = "reply-input-area";
  const ta = document.createElement("textarea");
  ta.placeholder = "Reply...";
  ta.addEventListener("input", () => {
    ta.style.height = "auto";
    ta.style.height = ta.scrollHeight + "px";
  });

  const actions = document.createElement("div");
  actions.className = "reply-input-actions";
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "btn-ghost btn-sm";
  cancelBtn.textContent = "Cancel";
  cancelBtn.addEventListener("click", () => area.remove());
  const sendBtn = document.createElement("button");
  sendBtn.className = "btn-primary btn-sm";
  sendBtn.textContent = "Reply";
  sendBtn.addEventListener("click", async () => {
    const text = ta.value.trim();
    if (!text) return;
    await postReply(comment.serverId, { message: text });
    // SSE will handle rendering the reply
  });

  ta.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      sendBtn.click();
    }
  });

  area.appendChild(ta);
  actions.appendChild(cancelBtn);
  actions.appendChild(sendBtn);
  area.appendChild(actions);
  card.appendChild(area);
  ta.focus();
}

export function updatePendingCount() {
  const badge = document.getElementById("pending-count");
  const n = comments.filter(c => c.status === "draft").length;
  if (n === 0) {
    badge.classList.remove("visible");
  } else {
    badge.textContent = `${n} draft${n === 1 ? "" : "s"}`;
    badge.classList.add("visible");
  }
}
