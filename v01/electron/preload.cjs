// v0.1.x preload —— 品牌化 + 上下文桥接预留。
//
// 主任务:把 dashboard 里的 "AstrBot" 字样替换为 "ai-helper",
// 但保留少数指向 AstrBot 官方资源的 context(插件市场入口、版本号、
// 官方域名、attribution 等)。
//
// 为啥不直接改 dashboard dist:dist 是 Vue 编译产物,文本经过 minify
// 散落各 chunk;运行时注入更可控,且 AstrBot 升级时也无需重打。
"use strict";

const BRAND_PATTERN = /AstrBot/g;
const BRAND_NEW = "ai-helper";

// 含以下子串的文本节点不替换 —— 这些是直接或间接指向 AstrBot 官方资源,
// 改了会破坏功能(插件市场跳错链接 / 版本号读取出错 / GitHub 引用错位)
const KEEP_PATTERNS = [
  /AstrBot\s*插件市场/,         // 跳官方插件市场
  /AstrBot\s+Devs?/i,           // attribution: AstrBot Devs / Team
  /AstrBot\s+Team/i,
  /astrbot\.app/i,              // 官方域名
  /AstrBotDevs/,                // GitHub org
  /AstrBot\s+v\d/i,             // "AstrBot v4.25.2" 版本上下文
  /AstrBot\s*v?[\d.]+/,         // 版本号变体
  /by\s+AstrBot/i,              // "by AstrBot Team" 这种
];

function shouldKeep(text) {
  return KEEP_PATTERNS.some((re) => re.test(text));
}

function rebrand(text) {
  if (!text || !text.includes("AstrBot")) return text;
  if (shouldKeep(text)) return text;
  return text.replace(BRAND_PATTERN, BRAND_NEW);
}

const SKIP_TAGS = new Set([
  "SCRIPT",
  "STYLE",
  "CODE",
  "PRE",
  "TEXTAREA",
  "INPUT",
]);

function walkAndRebrand(root) {
  if (!root) return;
  // 使用 TreeWalker 高效遍历 text node
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (SKIP_TAGS.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
      if (parent.isContentEditable) return NodeFilter.FILTER_REJECT;
      return node.nodeValue && node.nodeValue.includes("AstrBot")
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT;
    },
  });
  const targets = [];
  let n;
  while ((n = walker.nextNode())) targets.push(n);
  for (const node of targets) {
    const next = rebrand(node.nodeValue);
    if (next !== node.nodeValue) node.nodeValue = next;
  }
}

let pending = false;
function scheduleRescan() {
  if (pending) return;
  pending = true;
  // debounce —— SPA 路由切换/快速 mutation 多次,合并成一次扫描
  setTimeout(() => {
    walkAndRebrand(document.body);
    pending = false;
  }, 150);
}

window.addEventListener("DOMContentLoaded", () => {
  walkAndRebrand(document.body);

  // 监听 SPA 动态内容变化(Vue 路由切换、异步组件加载、对话流式追加)
  const obs = new MutationObserver(scheduleRescan);
  obs.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });

  // 注入小段 CSS 兜底替换 logo 显示文本(如果 dashboard 有 .logo-text 之类)
  const style = document.createElement("style");
  style.textContent = `
    /* 如果将来发现具体 logo selector 需要覆盖,在这里加 */
  `;
  document.head.appendChild(style);
});
