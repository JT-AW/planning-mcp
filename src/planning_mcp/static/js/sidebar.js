// Sidebar: project tree, vault links, cycle history

import { fetchProjects, fetchProject, fetchProjectPlans, fetchPlanById, acceptPlan, newCycle } from './api.js';
import { renderPlan, fetchAndRender } from './render.js';
import { state, setCurrentProject, setCurrentPlan, setPlanData, planData } from './state.js';

let projects = [];
let expandedNodes = new Set();
let selectedProjectPlans = [];

export async function fetchAndRenderProjects() {
  try {
    projects = await fetchProjects();
  } catch {
    projects = [];
  }
  // If we have a current project, fetch its plans for cycle history + button state
  if (state.currentProjectId) {
    try {
      selectedProjectPlans = await fetchProjectPlans(state.currentProjectId);
    } catch {
      selectedProjectPlans = [];
    }
  }
  renderSidebar();
  updateHeader();
}

function renderSidebar() {
  const container = document.getElementById('sidebar-content');
  if (!container) return;
  container.textContent = '';

  // Split projects into active and archived
  const activeProjects = projects.filter(p => p.status !== 'archived');
  const archivedProjects = projects.filter(p => p.status === 'archived');

  // Active projects section
  const treeSection = document.createElement('div');
  treeSection.className = 'sidebar-section';

  const treeHeader = document.createElement('div');
  treeHeader.className = 'sidebar-section-header';
  treeHeader.textContent = 'Active Projects';
  treeSection.appendChild(treeHeader);

  if (activeProjects.length > 0) {
    const tree = document.createElement('div');
    tree.className = 'project-tree';
    renderProjectTree(activeProjects, tree, 0);
    treeSection.appendChild(tree);
  } else {
    const empty = document.createElement('div');
    empty.className = 'sidebar-empty';
    empty.textContent = 'No active projects';
    treeSection.appendChild(empty);
  }
  container.appendChild(treeSection);

  // Vault links and cycle history for selected project
  if (state.currentProjectId) {
    const project = findProject(state.currentProjectId);
    if (project) {
      renderVaultLinks(container, project);
      renderCycleHistory(container);
    }
  }

  // Archived projects section (collapsible dropdown)
  if (archivedProjects.length > 0) {
    const archivedSection = document.createElement('div');
    archivedSection.className = 'sidebar-section archived-section';

    const archivedHeader = document.createElement('div');
    archivedHeader.className = 'sidebar-section-header archived-toggle';
    const arrow = document.createElement('span');
    arrow.className = 'archived-arrow';
    arrow.textContent = expandedNodes.has('__archived__') ? '\u25BE' : '\u25B8';
    archivedHeader.appendChild(arrow);
    archivedHeader.appendChild(document.createTextNode(' Archived Projects'));
    archivedHeader.style.cursor = 'pointer';
    archivedHeader.addEventListener('click', () => {
      if (expandedNodes.has('__archived__')) {
        expandedNodes.delete('__archived__');
      } else {
        expandedNodes.add('__archived__');
      }
      renderSidebar();
    });
    archivedSection.appendChild(archivedHeader);

    if (expandedNodes.has('__archived__')) {
      const archivedTree = document.createElement('div');
      archivedTree.className = 'project-tree';
      renderProjectTree(archivedProjects, archivedTree, 0);
      archivedSection.appendChild(archivedTree);
    }

    container.appendChild(archivedSection);
  }
}

function findProject(id) {
  for (const p of projects) {
    if (p.id === id) return p;
    if (p.children) {
      for (const c of p.children) {
        if (c.id === id) return c;
        if (c.children) {
          for (const gc of c.children) {
            if (gc.id === id) return gc;
          }
        }
      }
    }
  }
  return null;
}

function renderProjectTree(items, container, level) {
  if (!items || level >= 3) return;

  items.forEach(project => {
    const row = document.createElement('div');
    row.className = 'tree-item';
    if (project.id === state.currentProjectId) row.classList.add('active');
    if (project.status === 'archived') row.classList.add('archived');
    row.style.paddingLeft = `${12 + level * 16}px`;

    const hasChildren = project.children && project.children.length > 0;

    // Expand/collapse toggle
    if (hasChildren) {
      const toggle = document.createElement('span');
      toggle.className = 'tree-toggle';
      toggle.textContent = expandedNodes.has(project.id) ? '\u25BE' : '\u25B8';
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        if (expandedNodes.has(project.id)) {
          expandedNodes.delete(project.id);
        } else {
          expandedNodes.add(project.id);
        }
        renderSidebar();
      });
      row.appendChild(toggle);
    } else {
      const spacer = document.createElement('span');
      spacer.className = 'tree-toggle-spacer';
      row.appendChild(spacer);
    }

    // Status dot
    const dot = document.createElement('span');
    dot.className = `status-dot status-${project.status || 'active'}`;
    row.appendChild(dot);

    // Project name
    const name = document.createElement('span');
    name.className = 'tree-label';
    name.textContent = project.name || project.title || 'Untitled';
    row.appendChild(name);

    row.addEventListener('click', () => navigateToProject(project.id));
    container.appendChild(row);

    // Children
    if (hasChildren && expandedNodes.has(project.id)) {
      renderProjectTree(project.children, container, level + 1);
    }
  });
}

function renderVaultLinks(container, project) {
  let vaultPaths = project.vault_paths || project.vault_links || [];
  // vault_links may be a JSON string from SQLite
  if (typeof vaultPaths === 'string') {
    try { vaultPaths = JSON.parse(vaultPaths); } catch { vaultPaths = []; }
  }
  if (!Array.isArray(vaultPaths) || vaultPaths.length === 0) return;

  const section = document.createElement('div');
  section.className = 'sidebar-section';

  const header = document.createElement('div');
  header.className = 'sidebar-section-header';
  header.textContent = 'Vault';
  section.appendChild(header);

  vaultPaths.forEach(vp => {
    const link = document.createElement('a');
    link.className = 'vault-link';
    const path = typeof vp === 'string' ? vp : vp.path;
    const filename = path.split('/').pop();
    link.textContent = filename;
    link.title = path;
    link.href = `obsidian://open?vault=vault&file=${encodeURIComponent(path)}`;
    section.appendChild(link);
  });

  container.appendChild(section);
}

function renderCycleHistory(container) {
  if (selectedProjectPlans.length === 0) return;

  const section = document.createElement('div');
  section.className = 'sidebar-section';

  const header = document.createElement('div');
  header.className = 'sidebar-section-header';
  header.textContent = 'Cycles';
  section.appendChild(header);

  selectedProjectPlans.forEach(plan => {
    const row = document.createElement('div');
    row.className = 'cycle-item';
    if (plan.id === state.currentPlanId) row.classList.add('active');

    const icon = document.createElement('span');
    icon.className = 'cycle-icon';
    if (plan.status === 'accepted') {
      icon.textContent = '\u2713';
      icon.classList.add('accepted');
    } else if (plan.status === 'rejected') {
      icon.textContent = '\u2717';
      icon.classList.add('rejected');
    } else {
      icon.textContent = '\u25CF';
      icon.classList.add('reviewing');
    }
    row.appendChild(icon);

    const label = document.createElement('span');
    label.className = 'cycle-label';
    label.textContent = `Cycle ${plan.cycle_number || plan.cycle || '?'}`;
    row.appendChild(label);

    const badge = document.createElement('span');
    badge.className = `cycle-status-badge ${plan.status}`;
    badge.textContent = plan.status;
    row.appendChild(badge);

    row.addEventListener('click', () => loadPlanCycle(plan));
    section.appendChild(row);
  });

  container.appendChild(section);
}

async function navigateToProject(projectId) {
  setCurrentProject(projectId);

  // Fetch plans for this project
  try {
    selectedProjectPlans = await fetchProjectPlans(projectId);
  } catch {
    selectedProjectPlans = [];
  }

  // Load the latest/current plan
  const currentPlan = selectedProjectPlans.find(p => p.status === 'reviewing' || p.status === 'draft')
    || selectedProjectPlans[selectedProjectPlans.length - 1];

  if (currentPlan) {
    setCurrentPlan(currentPlan.id);
    await loadPlanContent(currentPlan.id);
  } else {
    setCurrentPlan(null);
    // Show empty state
    const container = document.getElementById("plan-content");
    if (container) {
      container.innerHTML = '';
      const msg = document.createElement('div');
      msg.className = 'empty-plan-message';
      msg.textContent = 'No plans exist for this project.';
      container.appendChild(msg);
    }
    document.getElementById("page-title").textContent = 'No Plans';
  }

  renderSidebar();
  updateHeader();
  updateArchivedState();
}

async function loadPlanContent(planId) {
  try {
    const plan = await fetchPlanById(planId);
    if (plan && plan.markdown) {
      setPlanData({ markdown: plan.markdown, title: plan.title });
      document.getElementById("page-title").textContent = plan.title;
      renderPlan(plan.markdown);
    }
  } catch {
    // Plan not in DB (in-memory only), leave current content
  }
}

async function loadPlanCycle(plan) {
  setCurrentPlan(plan.id);
  await loadPlanContent(plan.id);
  renderSidebar();
  updateHeader();
}

export function updateHeader() {
  const project = state.currentProjectId ? findProject(state.currentProjectId) : null;
  const plan = state.currentPlanId
    ? selectedProjectPlans.find(p => p.id === state.currentPlanId)
    : null;

  const titleEl = document.getElementById('plan-title');
  if (!titleEl) return;

  if (project && plan) {
    const projectName = project.name || project.title || 'Project';
    const cycleNum = plan.cycle_number || plan.cycle || '?';
    titleEl.textContent = '';

    const projSpan = document.createElement('span');
    projSpan.className = 'breadcrumb-project';
    projSpan.textContent = projectName;
    titleEl.appendChild(projSpan);

    const sep = document.createElement('span');
    sep.className = 'breadcrumb-sep';
    sep.textContent = ' \u203A ';
    titleEl.appendChild(sep);

    const cycleSpan = document.createElement('span');
    cycleSpan.className = 'breadcrumb-cycle';
    cycleSpan.textContent = `Cycle ${cycleNum}`;
    titleEl.appendChild(cycleSpan);
  }

  // Update header buttons visibility
  updateHeaderButtons(project, plan);
}

function updateHeaderButtons(project, plan) {
  const acceptBtn = document.getElementById('btn-accept');
  const newCycleBtn = document.getElementById('btn-new-cycle');
  if (!acceptBtn || !newCycleBtn) return;

  const isArchived = project && project.status === 'archived';
  // Use plan from selectedProjectPlans if available, otherwise fall back to planData.status
  const planStatus = plan ? plan.status : planData.status;
  const hasPlan = state.currentPlanId && planStatus;

  if (hasPlan && (planStatus === 'reviewing' || planStatus === 'draft') && !isArchived) {
    acceptBtn.style.display = 'inline-block';
    newCycleBtn.style.display = 'inline-block';
  } else {
    acceptBtn.style.display = 'none';
    newCycleBtn.style.display = 'none';
  }
}

function updateArchivedState() {
  const project = state.currentProjectId ? findProject(state.currentProjectId) : null;
  const isArchived = project && project.status === 'archived';
  const body = document.body;

  if (isArchived) {
    body.classList.add('archived-project');
  } else {
    body.classList.remove('archived-project');
  }

  // Update header badge
  const headerBadge = document.querySelector('.header-badge');
  if (headerBadge) {
    if (isArchived) {
      headerBadge.textContent = 'ARCHIVED';
      headerBadge.classList.add('archived');
    } else {
      headerBadge.textContent = 'Plan Review';
      headerBadge.classList.remove('archived');
    }
  }
}

export function initHeaderActions() {
  const acceptBtn = document.getElementById('btn-accept');
  const newCycleBtn = document.getElementById('btn-new-cycle');

  if (acceptBtn) {
    acceptBtn.addEventListener('click', async () => {
      if (!state.currentPlanId) return;
      acceptBtn.disabled = true;
      acceptBtn.textContent = 'Accepting...';
      try {
        await acceptPlan(state.currentPlanId);
        // Refresh plans list
        if (state.currentProjectId) {
          selectedProjectPlans = await fetchProjectPlans(state.currentProjectId);
        }
        renderSidebar();
        updateHeader();
      } catch {
        acceptBtn.textContent = 'Error';
        setTimeout(() => { acceptBtn.textContent = 'Accept'; }, 2000);
      } finally {
        acceptBtn.disabled = false;
        acceptBtn.textContent = 'Accept';
      }
    });
  }

  if (newCycleBtn) {
    newCycleBtn.addEventListener('click', async () => {
      if (!state.currentProjectId) return;
      newCycleBtn.disabled = true;
      try {
        await newCycle(state.currentProjectId);
        // Refresh plans list
        selectedProjectPlans = await fetchProjectPlans(state.currentProjectId);
        renderSidebar();
        updateHeader();
      } finally {
        newCycleBtn.disabled = false;
      }
    });
  }
}

export function initSidebarToggle() {
  const toggle = document.getElementById('sidebar-toggle');
  const sidebar = document.getElementById('sidebar');
  if (!toggle || !sidebar) return;

  toggle.addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
    toggle.classList.toggle('collapsed');
  });
}

// Called from SSE on project_changed/project_updated
export function handleProjectChanged() {
  fetchAndRenderProjects();
  fetchAndRender();
}

export function handleProjectUpdated() {
  fetchAndRenderProjects();
}
