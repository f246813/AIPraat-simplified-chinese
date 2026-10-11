// 改这里即可切换入口："button" 新按钮；"magnifier" 原放大镜；"both" 两者。
const AIPRAAT_ENTRY = "button";
// 原放大镜的 CSS 选择器；自定义卡组只需修改这一行。
const AIPRAAT_MAGNIFIER = ".Search, .search a, .dict-link, #dict-link";

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
