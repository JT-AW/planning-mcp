// Column resize handles

const MIN_SIDEBAR = 160;
const MAX_SIDEBAR = 400;
const MIN_COMMENTS = 200;
const MAX_COMMENTS = 480;

export function initResizeHandles() {
  initHandle('resize-sidebar', 'sidebar', resizeSidebar);
  initHandle('resize-comments', 'comments', resizeComments);
}

function initHandle(handleId, _kind, onDrag) {
  const handle = document.getElementById(handleId);
  if (!handle) return;

  let startX = 0;
  let startSize = 0;

  function onMouseDown(e) {
    e.preventDefault();
    startX = e.clientX;

    if (_kind === 'sidebar') {
      const sidebar = document.getElementById('sidebar');
      startSize = sidebar ? sidebar.offsetWidth : 240;
    } else {
      const margin = document.getElementById('comment-margin');
      startSize = margin ? margin.offsetWidth : 280;
    }

    document.body.classList.add('resizing');
    handle.classList.add('active');
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }

  function onMouseMove(e) {
    onDrag(e.clientX - startX, startSize);
  }

  function onMouseUp() {
    document.body.classList.remove('resizing');
    handle.classList.remove('active');
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
  }

  handle.addEventListener('mousedown', onMouseDown);
}

function resizeSidebar(delta, startSize) {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar || sidebar.classList.contains('collapsed')) return;
  const newWidth = Math.min(MAX_SIDEBAR, Math.max(MIN_SIDEBAR, startSize + delta));
  sidebar.style.width = newWidth + 'px';
}

function resizeComments(delta, startSize) {
  const margin = document.getElementById('comment-margin');
  if (!margin) return;
  // Dragging right = shrink comments, dragging left = grow comments
  const newWidth = Math.min(MAX_COMMENTS, Math.max(MIN_COMMENTS, startSize - delta));
  margin.style.width = newWidth + 'px';
}
