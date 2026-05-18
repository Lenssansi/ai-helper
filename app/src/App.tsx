import { useEffect, useState } from "react";
import ChatPage from "./pages/ChatPage";
import AgentPage from "./pages/AgentPage";
import ApiPage from "./pages/ApiPage";
import SettingsPage from "./pages/SettingsPage";
import {
  getHealth,
  getTheme,
  getWhoAmI,
  type ThemeMode,
  type WhoAmI,
} from "./api";

type Page = "chat" | "agent" | "api" | "settings";

const NAV: { key: Page; label: string }[] = [
  { key: "chat", label: "对话" },
  { key: "agent", label: "编程" },
  { key: "api", label: "API 管理" },
  { key: "settings", label: "设置" },
];

declare global {
  interface Window {
    aihelper?: { isElectron?: boolean; setNativeTheme?: (t: string) => void };
  }
}

function effective(theme: ThemeMode): "dark" | "light" {
  if (theme === "system")
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  return theme;
}

export function applyTheme(theme: ThemeMode) {
  document.documentElement.dataset.theme = effective(theme);
  window.aihelper?.setNativeTheme?.(theme);
}

export default function App() {
  const [page, setPage] = useState<Page>("chat");
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [who, setWho] = useState<WhoAmI | null>(null);
  const [version, setVersion] = useState("");
  const [theme, setThemeState] = useState<ThemeMode>("dark");

  useEffect(() => {
    (async () => {
      try {
        const h = await getHealth();
        setBackendOk(true);
        setVersion(h.version);
        setWho(await getWhoAmI());
        const t = (await getTheme()).theme;
        setThemeState(t);
        applyTheme(t);
        // system 主题：原生主题经 IPC 设置有延迟，稍后再算一次防首帧错配
        if (t === "system") setTimeout(() => applyTheme("system"), 80);
      } catch {
        setBackendOk(false);
      }
    })();
  }, []);

  // 跟随系统时，监听系统明暗变化实时套用
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const fn = () => applyTheme("system");
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, [theme]);

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">
          <span className="brand-dot" />
          ai-helper
        </div>
        {NAV.map((n) => (
          <button
            key={n.key}
            className={"nav-item" + (page === n.key ? " active" : "")}
            onClick={() => setPage(n.key)}
          >
            {n.label}
          </button>
        ))}
        <div className="sidebar-foot">
          <StatusLine ok={backendOk} who={who} version={version} />
        </div>
      </nav>
      <main className="content">
        {page === "chat" && <ChatPage />}
        {page === "agent" && <AgentPage who={who} />}
        {page === "api" && <ApiPage who={who} />}
        {page === "settings" && (
          <SettingsPage
            who={who}
            theme={theme}
            onTheme={(t) => {
              setThemeState(t);
              applyTheme(t);
            }}
          />
        )}
      </main>
    </div>
  );
}

function StatusLine({
  ok,
  who,
  version,
}: {
  ok: boolean | null;
  who: WhoAmI | null;
  version: string;
}) {
  if (ok === null) return <span className="status">连接后端中…</span>;
  if (!ok)
    return <span className="status err">后端未连接（先跑 开发启动.bat）</span>;
  return (
    <span className="status ok">
      后端已连接 v{version}
      {who && (
        <>
          <br />
          访问级别：{who.trust === "local" ? "本地（全功能）" : "远程（受限）"}
        </>
      )}
    </span>
  );
}
