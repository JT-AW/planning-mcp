// SSE connection and event handling

import { comments } from './state.js';
import { fetchAndRender } from './render.js';
import { renderCommentCards } from './comments.js';

export function connectSSE() {
  const es = new EventSource("/events");
  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "plan_updated") {
      if (msg.comments) applyServerAnchors(msg.comments);
      fetchAndRender();
    } else if (msg.type === "reply_added") {
      handleReplyAdded(msg);
    } else if (msg.type === "feedback_processed") {
      handleFeedbackProcessed(msg);
    } else if (msg.type === "plan_accepted") {
      showAcceptedBanner();
    }
  };
  es.onerror = () => { es.close(); setTimeout(connectSSE, 2000); };
}

function handleFeedbackProcessed(msg) {
  const c = comments.find(c => c.serverId === msg.feedback_id);
  if (!c) return;
  c.status = "processed";
  renderCommentCards();
}

function handleReplyAdded(msg) {
  const c = comments.find(c => c.serverId === msg.feedback_id);
  if (!c) return;
  if (!c.replies) c.replies = [];
  c.replies.push(msg.reply);
  // User reply to processed comment reopens it
  if (msg.unprocessed) {
    c.status = "submitted";
    const statusEl = document.getElementById("footer-status");
    if (statusEl) {
      statusEl.textContent = "Comment reopened \u2014 Claude is revising...";
      statusEl.className = "footer-status sent";
    }
  }
  renderCommentCards();
}

export function applyServerAnchors(serverComments) {
  serverComments.forEach(sc => {
    const c = comments.find(c => c.serverId === sc.id);
    if (c) {
      c.textOffset = sc.text_offset;
      c.orphaned = sc.orphaned;
    }
  });
}

function showAcceptedBanner() {
  const existing = document.getElementById("accepted-banner");
  if (existing) existing.remove();

  const banner = document.createElement("div");
  banner.id = "accepted-banner";
  banner.style.cssText = "position:fixed;top:60px;left:50%;transform:translateX(-50%);background:#16a34a;color:white;padding:8px 20px;border-radius:6px;font-size:13px;font-weight:600;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,0.15);";
  banner.textContent = "Plan accepted and saved";
  document.body.appendChild(banner);
  setTimeout(() => banner.remove(), 4000);
}
