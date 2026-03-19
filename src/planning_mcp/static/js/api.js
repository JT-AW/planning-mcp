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
