// v0.1.x Electron 外壳 —— 单窗口套 AstrBot dashboard。
//
// 启动流程:
//   1. 显示 loading.html(spinner + 提示)
//   2. spawn .venv/Scripts/python.exe bootstrap.py(若 6185 未起)
//   3. poll http://127.0.0.1:6185/ 到返回 200
//   4. window.loadURL("http://127.0.0.1:6185/")
//   5. 退出时 kill「自己拉起的」python 子进程
//
// 安全:
//   - dashboard 绑死 127.0.0.1(bootstrap.py 已注入 DASHBOARD_HOST 环境变量)
//   - contextIsolation: true / nodeIntegration: false
//   - 拒绝任何 navigate 到非 127.0.0.1 的 URL(防 dashboard 被劫持后跳出)
//   - 链接 target=_blank 全部走 shell.openExternal(默认浏览器),不开新 BrowserWindow
"use strict";

const { app, BrowserWindow, shell } = require("electron");
const path = require("path");
const http = require("http");
const fs = require("fs");
const { spawn } = require("child_process");

const PACKED = app.isPackaged;
// dev:   __dirname = D:\ai-helper\v01\electron\
// packed: __dirname = .../resources/app.asar/(electron/?)  → 用 process.resourcesPath 兜底
const ROOT = path.join(__dirname, "..", ".."); // dev: D:\ai-helper
// packed 下,bootstrap.py + venv + data + mihomo + skills 都在 resourcesPath 下
// (extraResources 的目标位置就是 resources/)
const V01_DIR = PACKED ? process.resourcesPath : path.join(ROOT, "v01");
const PY = PACKED
  ? path.join(V01_DIR, "venv", "Scripts", "python.exe")    // extraResources: .venv → venv
  : path.join(V01_DIR, ".venv", "Scripts", "python.exe");
const BOOTSTRAP = path.join(V01_DIR, "bootstrap.py");
const DASHBOARD_URL = "http://127.0.0.1:6185/";
const ICON = PACKED
  ? path.join(process.resourcesPath, "icon.ico")           // 配置时把 icon 也复制一份
  : path.join(ROOT, "assets", "icon.ico");
const LOADING_HTML = path.join(__dirname, "loading.html");

let astrbotProc = null; // 仅记录「本进程拉起的」,共存场景不会乱杀

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
  // 180 * 1s = 3 min:首次冷启需下 9.9 MB dashboard,慢网络给足余量
  for (let i = 0; i < tries; i++) {
    if (await fn()) return true;
    await new Promise((r) => setTimeout(r, gap));
  }
  return false;
}

async function ensureAstrbot() {
  // 已有进程就别再 spawn(开发热重载/Ctrl+R 不会重复起 python)
  if (await httpOk(DASHBOARD_URL)) return true;
  if (!fs.existsSync(PY) || !fs.existsSync(BOOTSTRAP)) {
    console.error("[v01-shell] 找不到 venv python 或 bootstrap.py");
    return false;
  }
  astrbotProc = spawn(PY, [BOOTSTRAP], {
    cwd: V01_DIR,
    stdio: "ignore", // dashboard 自己写日志到 v01/data/,不抢 Electron 终端
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
      AIH_PACKED: PACKED ? "1" : "0",
      // 仅 dev 模式开 AstrBot 插件热重载(改插件代码不用重启)
      ASTRBOT_RELOAD: PACKED ? "0" : "1",
    },
  });
  astrbotProc.on("error", () => (astrbotProc = null));
  astrbotProc.on("exit", () => (astrbotProc = null));
  return true;
}

function killOurs() {
  if (!astrbotProc) return;
  try {
    // Windows 没有真正的 SIGTERM,kill() 实际发的是 TerminateProcess
    // bootstrap.py 已捕获 KeyboardInterrupt,但 TerminateProcess 不走 Python signal
    // → AstrBot 不会优雅退;data_v4.db 是 WAL 模式,断电级 crash 也安全。可接受。
    astrbotProc.kill();
  } catch {
    /* ignore */
  }
  astrbotProc = null;
}

function hardenSecurity(win) {
  // 0) 锁标题为 "ai-helper" —— AstrBot 是内部引擎,对外仍是 ai-helper
  win.on("page-title-updated", (e) => e.preventDefault());

  // 1) 拦截 window.open / target="_blank" —— 一律外部浏览器,不开 sub window
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });

  // 2) 拦截 navigation —— 只许停在本机 127.0.0.1
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

  // 3) 阻止 webview / 新 webContents attach(纵深防御)
  win.webContents.on("will-attach-webview", (e) => e.preventDefault());
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1300,
    height: 860,
    minWidth: 1040,
    minHeight: 720,
    title: "ai-helper",
    icon: fs.existsSync(ICON) ? ICON : undefined,
    backgroundColor: "#0f1115",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.removeMenu();
  hardenSecurity(win);

  // 先把 loading 页加载好再 show,避免白底闪
  win.loadFile(LOADING_HTML);
  win.once("ready-to-show", () => win.show());

  return win;
}

// 单实例锁:防止「bat 启一个 + 桌面快捷方式又启一个」两个 Electron 抢同一个
// 6185 python。没有这个锁时,第二个 Electron 会 piggyback 第一个拉起的 python;
// 一旦关掉第一个窗口(其 before-quit 杀掉共享的 python),第二个窗口的 dashboard
// 就连到死后端 → 表现为「服务未启用 / 所有模型失效」(bug #3)。
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    // 已有实例:把现有窗口提到前台,而不是新开一个
    const wins = BrowserWindow.getAllWindows();
    if (wins.length) {
      const w = wins[0];
      if (w.isMinimized()) w.restore();
      w.focus();
    }
  });
}

app.whenReady().then(async () => {
  if (!gotLock) return; // 第二实例:whenReady 里直接退,交给上面的 app.quit()

  const win = createWindow();

  const started = await ensureAstrbot();
  if (!started) {
    // 显示一个错误 HTML,而不是停在永久 spinner
    win.loadURL(
      "data:text/html;charset=utf-8," +
        encodeURIComponent(
          "<body style='background:#0f1115;color:#e7e9ee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:40px;'>" +
            "<div><h2>启动失败</h2><p>没找到 v01/.venv/Scripts/python.exe 或 v01/bootstrap.py。<br/>检查 v01 目录结构是否完整。</p></div></body>",
        ),
    );
    return;
  }

  const ready = await waitUntil(() => httpOk(DASHBOARD_URL));
  if (!ready) {
    win.loadURL(
      "data:text/html;charset=utf-8," +
        encodeURIComponent(
          "<body style='background:#0f1115;color:#e7e9ee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:40px;'>" +
            "<div><h2>AstrBot 启动超时(>3 分钟)</h2><p>查 v01/data/ 下 .log 文件定位原因,或终端单跑:<br/><code>v01/.venv/Scripts/python.exe v01/bootstrap.py</code></p></div></body>",
        ),
    );
    return;
  }

  win.loadURL(DASHBOARD_URL);
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", killOurs);
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
