// All HTTP fetch calls

export async function fetchPlan() {
  const res = await fetch("/plan");
  return res.json();
}

export async function fetchAllFeedback() {
  const res = await fetch("/feedback/all");
  return res.json();
}

export async function postFeedback(item) {
  const res = await fetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item),
  });
  return res.json();
}

export async function submitAllDrafts() {
  const res = await fetch("/feedback/submit-all", { method: "POST" });
  return res.json();
}

export async function postReply(feedbackId, body) {
  const res = await fetch(`/feedback/${feedbackId}/reply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ── Project/Plan endpoints ──────────────────────

export async function fetchProjects() {
  const res = await fetch("/projects");
  return res.json();
}

export async function fetchProject(id) {
  const res = await fetch(`/projects/${id}`);
  return res.json();
}

export async function fetchProjectPlans(projectId) {
  const res = await fetch(`/projects/${projectId}/plans`);
  return res.json();
}

export async function fetchPlanById(planId) {
  const res = await fetch(`/plans/${planId}`);
  return res.json();
}

export async function acceptPlan(planId, vaultDomain, vaultFilename) {
  const res = await fetch(`/plans/${planId}/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      vault_domain: vaultDomain || "",
      vault_filename: vaultFilename || "",
    }),
  });
  return res.json();
}

export async function newCycle(projectId) {
  const res = await fetch(`/projects/${projectId}/plans/new-cycle`, {
    method: "POST",
  });
  return res.json();
}
