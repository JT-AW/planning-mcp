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
    }
  };
  es.onerror = () => { es.close(); setTimeout(connectSSE, 2000); };
}

function handleReplyAdded(msg) {
  const c = comments.find(c => c.serverId === msg.feedback_id);
  if (!c) return;
  if (!c.replies) c.replies = [];
  c.replies.push(msg.reply);
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
