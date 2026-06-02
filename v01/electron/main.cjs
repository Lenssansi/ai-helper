// v0.1.x Electron 外壳 —— 主窗口(完整 dashboard) + 浮窗(便捷对话) + 托盘。
//
// 窗口模型:
//   - 主窗口:完整 AstrBot dashboard。点 × 不退出,收进托盘。
//   - 浮窗:竖屏小窗,直达聊天,置顶 + 可拉伸。点 × 收进托盘。
//   - 托盘右键:显示程序主界面 / 显示浮窗 / 退出。只有"退出"才真正结束程序。
//   - 浮窗尺寸:每次"程序启动"重置为默认竖屏尺寸;程序运行期间关了再开
//     (托盘"显示浮窗")保持用户拉伸后的尺寸(窗口只是 hide,不销毁)。
//
// 启动方式:
//   - 普通快捷方式:electron.exe <app>          → 起主窗口(+托盘)
//   - 浮窗快捷方式:electron.exe <app> --float   → 只起浮窗(主界面在托盘里)
//
// 安全:dashboard 绑死 127.0.0.1;contextIsolation;拒绝外站导航;外链走默认浏览器。
"use strict";

const { app, BrowserWindow, Tray, Menu, shell, nativeImage } = require("electron");
const path = require("path");
const http = require("http");
const fs = require("fs");
const { spawn, spawnSync } = require("child_process");

const PACKED = app.isPackaged;
const ROOT = path.join(__dirname, "..", "..");
const V01_DIR = PACKED ? process.resourcesPath : path.join(ROOT, "v01");
const PY = PACKED
  ? path.join(V01_DIR, "venv", "Scripts", "python.exe")
  : path.join(V01_DIR, ".venv", "Scripts", "python.exe");
const BOOTSTRAP = path.join(V01_DIR, "bootstrap.py");
const DASHBOARD_URL = "http://127.0.0.1:6185/";
const ICON = PACKED
  ? path.join(process.resourcesPath, "icon.ico")
  : path.join(ROOT, "assets", "icon.ico");
const LOADING_HTML = path.join(__dirname, "loading.html");

// 浮窗默认竖屏尺寸(每次程序启动重置回这个)
const FLOAT_W = 400;
const FLOAT_H = 720;
// 浮窗启动后客户端跳转到的路由(直达聊天)
const FLOAT_ROUTE = "/chat";

let astrbotProc = null;
let mainWin = null;
let floatWin = null;
let tray = null;
let isQuitting = false; // 仅当托盘"退出"或 before-quit 时为 true
let backendReady = false;

function isFloatLaunch(argv) {
  return (argv || []).some((a) => a === "--float" || a === "/float");
}

function httpOk(url, timeout = 1500) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout }, (res) => {
      res.resume();
      resolve(res.statusCode > 0);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitUntil(fn, tries = 180, gap = 1000) {
  for (let i = 0; i < tries; i++) {
    if (await fn()) return true;
    await new Promise((r) => setTimeout(r, gap));
  }
  return false;
}

async function ensureAstrbot() {
  if (await httpOk(DASHBOARD_URL)) return true;
  if (!fs.existsSync(PY) || !fs.existsSync(BOOTSTRAP)) {
    console.error("[v01-shell] 找不到 venv python 或 bootstrap.py");
    return false;
  }
  astrbotProc = spawn(PY, [BOOTSTRAP], {
    cwd: V01_DIR,
    stdio: "ignore",
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
      AIH_PACKED: PACKED ? "1" : "0",
      ASTRBOT_RELOAD: PACKED ? "0" : "1",
    },
  });
  astrbotProc.on("error", () => (astrbotProc = null));
  astrbotProc.on("exit", () => (astrbotProc = null));
  return true;
}

function killOurs() {
  if (!astrbotProc) return;
  const pid = astrbotProc.pid;
  try {
    if (process.platform === "win32" && pid) {
      // 树杀:python(AstrBot)+ 它拉起的 mihomo 等子进程一起终止,杜绝孤儿。
      spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], {
        stdio: "ignore",
        windowsHide: true,
      });
    } else {
      astrbotProc.kill();
    }
  } catch {
    /* ignore */
  }
  astrbotProc = null;
}

function hardenSecurity(win) {
  win.on("page-title-updated", (e) => e.preventDefault());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    try {
      const u = new URL(url);
      if (u.hostname !== "127.0.0.1" && u.hostname !== "localhost") {
        e.preventDefault();
        shell.openExternal(url);
      }
    } catch {
      e.preventDefault();
    }
  });
  win.webContents.on("will-attach-webview", (e) => e.preventDefault());
}

// 把窗口的 × 改成"收进托盘"(hide),只有 isQuitting 时才真正关闭
function attachCloseToTray(win) {
  win.on("close", (e) => {
    if (!isQuitting) {
      e.preventDefault();
      win.hide();
    }
  });
}

// 后端就绪后,把窗口从 loading 页切到 dashboard;toChat=true 则客户端跳到聊天路由
function loadDashboard(win, toChat) {
  if (!win || win.isDestroyed()) return;
  win.loadURL(DASHBOARD_URL);
  if (toChat) {
    win.webContents.once("did-finish-load", () => {
      win.webContents
        .executeJavaScript(
          `(function(){try{` +
            `if(!/\\/chat/.test(location.pathname)){` +
            `history.pushState({},'',${JSON.stringify(FLOAT_ROUTE)});` +
            `window.dispatchEvent(new PopStateEvent('popstate'));}` +
            `}catch(e){}})();`,
        )
        .catch(() => {});
    });
  }
}

function commonWebPrefs() {
  return {
    preload: path.join(__dirname, "preload.cjs"),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
  };
}

function createMainWindow() {
  mainWin = new BrowserWindow({
    width: 1300,
    height: 860,
    minWidth: 1040,
    minHeight: 720,
    title: "ai-helper",
    icon: fs.existsSync(ICON) ? ICON : undefined,
    backgroundColor: "#0f1115",
    show: false,
    webPreferences: commonWebPrefs(),
  });
  mainWin.removeMenu();
  hardenSecurity(mainWin);
  attachCloseToTray(mainWin);
  mainWin.on("closed", () => (mainWin = null));

  if (backendReady) loadDashboard(mainWin, false);
  else mainWin.loadFile(LOADING_HTML);
  mainWin.once("ready-to-show", () => mainWin.show());
  return mainWin;
}

function createFloatWindow() {
  floatWin = new BrowserWindow({
    width: FLOAT_W,
    height: FLOAT_H,
    minWidth: 320,
    minHeight: 420,
    title: "ai-helper",
    icon: fs.existsSync(ICON) ? ICON : undefined,
    backgroundColor: "#0f1115",
    show: false,
    resizable: true, // 可自己拉宽/拉高
    alwaysOnTop: true, // 置顶
    skipTaskbar: true, // 浮窗是小挂件,不占任务栏;由托盘管理
    webPreferences: commonWebPrefs(),
  });
  floatWin.setAlwaysOnTop(true, "floating");
  floatWin.removeMenu();
  hardenSecurity(floatWin);
  attachCloseToTray(floatWin);
  floatWin.on("closed", () => (floatWin = null));

  if (backendReady) loadDashboard(floatWin, true);
  else floatWin.loadFile(LOADING_HTML);
  floatWin.once("ready-to-show", () => floatWin.show());
  return floatWin;
}

function showMain() {
  if (!mainWin || mainWin.isDestroyed()) createMainWindow();
  else {
    if (mainWin.isMinimized()) mainWin.restore();
    mainWin.show();
    mainWin.focus();
  }
}

function showFloat() {
  if (!floatWin || floatWin.isDestroyed()) createFloatWindow();
  else {
    floatWin.show();
    floatWin.setAlwaysOnTop(true, "floating");
    floatWin.focus();
  }
}

function buildTray() {
  if (tray) return;
  const img = fs.existsSync(ICON)
    ? ICON
    : nativeImage.createEmpty();
  try {
    tray = new Tray(img);
  } catch {
    tray = null;
    return;
  }
  tray.setToolTip("ai-helper");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "显示程序主界面", click: () => showMain() },
      { label: "显示浮窗", click: () => showFloat() },
      { type: "separator" },
      {
        label: "退出 ai-helper",
        click: () => {
          isQuitting = true;
          app.quit();
        },
      },
    ]),
  );
  tray.on("double-click", () => showMain());
}

function showFatalHtml(html) {
  const target = mainWin || floatWin;
  if (target && !target.isDestroyed()) {
    target.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
  }
}

// ---- 单实例锁:防多开抢同一个 6185 后端 ----
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", (_event, argv) => {
    // 第二次启动:按它的参数决定开主界面还是浮窗(而不是新开一个进程)
    if (isFloatLaunch(argv)) showFloat();
    else showMain();
  });
}

app.whenReady().then(async () => {
  if (!gotLock) return;

  buildTray();

  // 按启动参数决定先开哪个窗口(另一个留给托盘按需开)
  const floatLaunch = isFloatLaunch(process.argv);
  if (floatLaunch) createFloatWindow();
  else createMainWindow();

  const started = await ensureAstrbot();
  if (!started) {
    showFatalHtml(
      "<body style='background:#0f1115;color:#e7e9ee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:40px;'>" +
        "<div><h2>启动失败</h2><p>没找到 v01/.venv/Scripts/python.exe 或 v01/bootstrap.py。</p></div></body>",
    );
    return;
  }

  const ready = await waitUntil(() => httpOk(DASHBOARD_URL));
  if (!ready) {
    showFatalHtml(
      "<body style='background:#0f1115;color:#e7e9ee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:40px;'>" +
        "<div><h2>AstrBot 启动超时(&gt;3 分钟)</h2><p>查 v01/data/ 日志,或终端单跑 bootstrap.py。</p></div></body>",
    );
    return;
  }

  backendReady = true;
  // 把已经开着的窗口从 loading 切到 dashboard
  if (mainWin && !mainWin.isDestroyed()) loadDashboard(mainWin, false);
  if (floatWin && !floatWin.isDestroyed()) loadDashboard(floatWin, true);

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) showMain();
  });
});

app.on("before-quit", () => {
  isQuitting = true; // 让窗口 close 不再被拦成 hide
  killOurs();
  if (tray) {
    try {
      tray.destroy();
    } catch {
      /* ignore */
    }
    tray = null;
  }
});

// 收进托盘时窗口只是 hide、不 closed,所以这里平时不触发;
// 仅在真正退出流程(isQuitting)时才让进程结束。
app.on("window-all-closed", () => {
  if (isQuitting && process.platform !== "darwin") app.quit();
});
