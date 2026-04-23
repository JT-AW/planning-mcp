// Plan rendering and data loading

import { setPlanData, comments, getNextLocalId } from './state.js';
import { fetchPlan, fetchAllFeedback } from './api.js';
import { reanchorComments } from './highlight.js';
import { renderCommentCards } from './comments.js';

export function parseSections(markdown) {
  // Identify character ranges inside fenced code blocks so we don't treat
  // `# comment` lines inside code as section headers.
  const codeRanges = [];
  const fenceRe = /^(```|~~~)[^\n]*\n[\s\S]*?^\1\s*$/gm;
  for (const match of markdown.matchAll(fenceRe)) {
    codeRanges.push([match.index, match.index + match[0].length]);
  }
  const isInCodeBlock = (idx) => {
    for (const [s, e] of codeRanges) {
      if (idx >= s && idx < e) return true;
    }
    return false;
  };

  const headerRe = /^(#{1,4})\s+(.+)$/gm;
  const matches = [...markdown.matchAll(headerRe)].filter(m => !isInCodeBlock(m.index));
  if (matches.length === 0) {
    return [{ level: 0, title: "__root__", headerLine: "", body: markdown }];
  }
  return matches.map((m, i) => {
    const level = m[1].length;
    const title = m[2].trim();
    const headerLine = m[0];
    const bodyStart = m.index + m[0].length + 1;
    let bodyEnd = markdown.length;
    for (let j = i + 1; j < matches.length; j++) {
      if (matches[j][1].length <= level) { bodyEnd = matches[j].index; break; }
    }
    return { level, title, headerLine, body: markdown.slice(bodyStart, bodyEnd) };
  });
}

export function renderPlan(markdown) {
  const container = document.getElementById("plan-content");
  container.textContent = "";

  parseSections(markdown).forEach(sec => {
    const block = document.createElement("div");
    block.className = "section-block";

    const contentDiv = document.createElement("div");
    contentDiv.className = "section-content";
    // Safe: marked output is sanitized through DOMPurify before DOM insertion
    const raw = marked.parse(sec.headerLine + (sec.headerLine ? "\n" : "") + sec.body);
    const sanitized = DOMPurify.sanitize(raw);
    contentDiv.innerHTML = sanitized; // nosec: sanitized by DOMPurify
    block.appendChild(contentDiv);

    container.appendChild(block);
  });
}

export async function fetchAndRender() {
  const data = await fetchPlan();
  setPlanData(data);
  document.getElementById("page-title").textContent = data.title;
  document.getElementById("plan-title").textContent = data.title;
  renderPlan(data.markdown);
  reanchorComments();
  renderCommentCards();
}

export async function loadExistingComments() {
  try {
    const items = await fetchAllFeedback();
    items.forEach(item => {
      if (!comments.find(c => c.serverId === item.id)) {
        comments.push({
          localId: "server-" + getNextLocalId(),
          serverId: item.id,
          type: item.type,
          selectedText: item.selected_text,
          anchorContext: item.anchor_context,
          userMessage: item.user_message,
          timestamp: item.timestamp,
          status: item.status,
          textOffset: item.text_offset,
          replies: item.replies || [],
          orphaned: item.orphaned || false,
        });
      }
    });
  } catch { /* ignore on first load */ }
}
