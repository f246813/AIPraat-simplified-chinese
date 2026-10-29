// 改这里即可切换入口："button" 新按钮；"magnifier" 原放大镜；"both" 两者。
const AIPRAAT_ENTRY = "button";
// 原放大镜的 CSS 选择器；自定义卡组只需修改这一行。
const AIPRAAT_MAGNIFIER = ".Search, .search a, .dict-link, #dict-link";

function aipraatAttachButton() {
  const button = document.getElementById("aipraat-audio-button");
  if (!button) return;
  let press = null;
  let timer = null;
  let suppressClick = false;
  let bounds = button.getBoundingClientRect();

  function position(left, top) {
    left = Math.max(0, Math.min(left, window.innerWidth - bounds.width));
    top = Math.max(0, Math.min(top, window.innerHeight - bounds.height));
    Object.assign(button.style, {left: "0px", top: "0px", transform: "translate3d(" + left + "px," + top + "px,0)"});
    window.aipraatButtonPosition = {left, top};
  }

  function stopPress() {
    clearTimeout(timer);
    timer = null;
    const pointerId = press && press.pointerId;
    press = null;
    button.style.cursor = "grab";
    if (pointerId != null && button.hasPointerCapture(pointerId)) button.releasePointerCapture(pointerId);
  }

  function down(event) {
    if (event.button !== 0 || !event.isPrimary) return;
    stopPress();
    suppressClick = false;
    const rect = button.getBoundingClientRect();
    bounds = rect;
    press = {pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, dx: 0, dy: 0, dragging: false};
    button.setPointerCapture(event.pointerId);
    timer = setTimeout(startDrag, 350);
  }

  function startDrag() {
    clearTimeout(timer);
    timer = null;
    press.dragging = true;
    suppressClick = true;
    button.style.cursor = "grabbing";
    position(press.left + press.dx, press.top + press.dy);
  }

  function move(event) {
    if (!press || event.pointerId !== press.pointerId) return;
    const dx = event.clientX - press.x;
    const dy = event.clientY - press.y;
    press.dx = dx;
    press.dy = dy;
    if (press.dragging) {
      event.preventDefault();
      position(press.left + dx, press.top + dy);
    } else if (Math.hypot(dx, dy) > 6) {
      event.preventDefault();
      startDrag();
    }
  }

  function up(event) {
    if (press && event.pointerId === press.pointerId) stopPress();
  }

  function cancel() {
    if (press) suppressClick = true;
    stopPress();
  }

  function click(event) {
    event.preventDefault();
    event.stopPropagation();
    if (!suppressClick || event.detail === 0) pycmd("aipraat:open");
    suppressClick = false;
  }

  function resize() {
    bounds = button.getBoundingClientRect();
    const saved = window.aipraatButtonPosition;
    if (saved) position(saved.left, saved.top);
  }

  resize();
  button.addEventListener("pointerdown", down);
  button.addEventListener("pointermove", move);
  button.addEventListener("pointerup", up);
  button.addEventListener("pointercancel", cancel);
  button.addEventListener("lostpointercapture", cancel);
  button.addEventListener("click", click);
  window.addEventListener("blur", cancel);
  window.addEventListener("resize", resize);
  window.aipraatDetachButton = function() {
    cancel();
    button.removeEventListener("pointerdown", down);
    button.removeEventListener("pointermove", move);
    button.removeEventListener("pointerup", up);
    button.removeEventListener("pointercancel", cancel);
    button.removeEventListener("lostpointercapture", cancel);
    button.removeEventListener("click", click);
    window.removeEventListener("blur", cancel);
    window.removeEventListener("resize", resize);
    window.aipraatDetachButton = null;
  };
}

// 由插件在卡片显示时调用；保留 Android / iOS 原词典行为。
function aipraatAttachMagnifier(selector) {
  if (!document.querySelector("html.win, body.win") && !/Windows/i.test(navigator.userAgent)) return;
  document.querySelectorAll(selector).forEach(button => {
    button.title = "在 AIPraat 中查看语图";
    button.onclick = null;
    button.href = "#";
  });
  if (window.aipraatMagnifierHandler) document.removeEventListener("click", window.aipraatMagnifierHandler, true);
  window.aipraatMagnifierHandler = function(event) {
    if (!event.target.closest(selector)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    pycmd("aipraat:open");
  };
  document.addEventListener("click", window.aipraatMagnifierHandler, true);
}
