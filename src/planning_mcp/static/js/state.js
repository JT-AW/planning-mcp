// Client-side state store

export const state = {
  currentProjectId: null,
  currentPlanId: null,
};

export let planData = { markdown: "", title: "", plan_id: null, project_id: null, status: null };
export let comments = [];
let nextLocalId = 1;

export function setCurrentProject(projectId) {
  state.currentProjectId = projectId;
}

export function setCurrentPlan(planId) {
  state.currentPlanId = planId;
}

export function setPlanData(data) {
  planData = data;
}

export function addComment(comment) {
  comments.push(comment);
}

export function removeComment(localId) {
  const idx = comments.findIndex(c => c.localId === localId);
  if (idx !== -1) comments.splice(idx, 1);
}

export function updateComment(localId, updates) {
  const c = comments.find(c => c.localId === localId);
  if (c) Object.assign(c, updates);
}

export function getNextLocalId() {
  return nextLocalId++;
}
