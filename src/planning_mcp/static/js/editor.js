// In-browser raw-markdown editor: swaps the plan column for a textarea.

import { planData } from './state.js';
import { putPlan } from './api.js';
import { fetchAndRender } from './render.js';

let editing = false;
let panel = null;
let textarea = null;
// The review columns we hide while editing (restored on exit).
let hiddenEls = [];

// sse.js checks this so a `plan_updated` broadcast (including this save's own
// echo) doesn't blow away the textarea while the user is mid-edit.
export function isEditingPlan() {
  return editing;
}

function buildPanel() {
  panel = document.createElement("div");
  panel.className = "editor-panel";
  panel.style.display = "none";

  textarea = document.createElement("textarea");
  textarea.className = "editor-textarea";
  textarea.spellcheck = false;

  const bar = document.createElement("div");
  bar.className = "editor-bar";
  const cancel = document.createElement("button");
  cancel.className = "editor-cancel";
  cancel.textContent = "Cancel";
  const save = document.createElement("button");
  save.className = "editor-save";
  save.textContent = "Save";
  bar.append(cancel, save);

  panel.append(textarea, bar);
  // Sibling of the plan column inside the flex row — NOT a child of
  // #plan-content, which we hide while editing.
  document.querySelector(".layout").appendChild(panel);

  save.addEventListener("click", onSave);
  cancel.addEventListener("click", exit);
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); exit(); }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); onSave(); }
  });
}

function enter() {
  if (!panel) buildPanel();
  editing = true;
  textarea.value = planData.markdown;
  hiddenEls = [
    document.getElementById("plan-content"),
    document.getElementById("resize-comments"),
    document.getElementById("comment-margin"),
  ].filter(Boolean);
  hiddenEls.forEach(el => { el.style.display = "none"; });
  panel.style.display = "flex";
  document.getElementById("edit-btn").classList.add("active");
  textarea.focus();
}

function exit() {
  editing = false;
  if (panel) panel.style.display = "none";
  hiddenEls.forEach(el => { el.style.display = ""; });
  hiddenEls = [];
  document.getElementById("edit-btn").classList.remove("active");
}

async function onSave() {
  const save = panel.querySelector(".editor-save");
  save.disabled = true;
  save.textContent = "Saving...";
  try {
    await putPlan(textarea.value);
    exit();
    await fetchAndRender();
  } catch {
    save.disabled = false;
    save.textContent = "Retry";
    return;
  }
  save.disabled = false;
  save.textContent = "Save";
}

export function initEditor() {
  document.getElementById("edit-btn").addEventListener("click", () => {
    editing ? exit() : enter();
  });
}
