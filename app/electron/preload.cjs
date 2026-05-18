// 最小预加载：告诉前端跑在 Electron 外壳里，并提供原生标题栏主题同步。
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("aihelper", {
  isElectron: true,
  platform: process.platform,
  setNativeTheme: (t) => ipcRenderer.send("set-native-theme", t),
});
