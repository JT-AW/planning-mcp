// Dark/light theme toggle, persisted in localStorage

const STORAGE_KEY = "planning-theme";

export function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function apply(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  btn.textContent = theme === "light" ? "☾" : "☀";
  btn.title = theme === "light" ? "Switch to dark mode" : "Switch to light mode";
}

export function initTheme() {
  apply(currentTheme());
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const next = currentTheme() === "light" ? "dark" : "light";
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* private mode */ }
    apply(next);
  });
}
