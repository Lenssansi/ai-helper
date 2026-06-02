// 浮窗外壳 preload:只暴露置顶切换 / 最小化 / 关闭 三个能力给页面。
// 上下文隔离开启,页面拿不到 node,只能用这几个白名单方法。
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("aihFloat", {
  togglePin: () => ipcRenderer.invoke("aih:toggle-pin"), // 返回切换后的置顶状态(bool)
  getPin: () => ipcRenderer.invoke("aih:get-pin"), // 取当前置顶状态
  minimize: () => ipcRenderer.send("aih:minimize"), // 收进托盘
  close: () => ipcRenderer.send("aih:close"), // 收进托盘
});
