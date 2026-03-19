// Column resize handle for comment margin

const MIN_COMMENTS = 200;
const MAX_COMMENTS = 480;

export function initResizeHandles() {
  initHandle('resize-comments', resizeComments);
}

function initHandle(handleId, onDrag) {
  const handle = document.getElementById(handleId);
  if (!handle) return;

  let startX = 0;
  let startSize = 0;

  function onMouseDown(e) {
    e.preventDefault();
    startX = e.clientX;
    const margin = document.getElementById('comment-margin');
    startSize = margin ? margin.offsetWidth : 280;

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

function resizeComments(delta, startSize) {
  const margin = document.getElementById('comment-margin');
  if (!margin) return;
  // Dragging right = shrink comments, dragging left = grow comments
  const newWidth = Math.min(MAX_COMMENTS, Math.max(MIN_COMMENTS, startSize - delta));
  margin.style.width = newWidth + 'px';
}
