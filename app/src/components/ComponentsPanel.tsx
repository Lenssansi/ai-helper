// 设置页「组件与更新」面板 —— 统一显示 ai-helper 用到的可更新外部组件。
// mihomo 内核 / 工程 skills 可更新;Git / Ollama 是系统软件只读显示状态。
import { useEffect, useState } from "react";
import {
  type ComponentsStatus,
  getComponents,
  installVpnCore,
  updateSkills,
} from "../api";

export default function ComponentsPanel({
  canSettings,
}: {
  canSettings: boolean;
}) {
  const [comp, setComp] = useState<ComponentsStatus | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [msg, setMsg] = useState<string>("");

  async function reload() {
    try {
      setComp(await getComponents());
    } catch (e) {
      setMsg("加载组件状态失败:" + (e as Error).message);
    }
  }
  useEffect(() => {
    reload();
  }, []);

  async function doUpdateCore(force: boolean) {
    setBusy("mihomo");
    setMsg(force ? "更新内核中…(下载约 16MB)" : "获取内核中…(下载约 16MB)");
    try {
      const r = await installVpnCore(force);
      setMsg(
        r.ok
          ? `✓ 网络组件就绪${r.version ? "(" + r.version + ")" : ""}`
          : "失败:" + (r.error || ""),
      );
    } catch (e) {
      setMsg("失败:" + (e as Error).message);
    } finally {
      setBusy("");
      reload();
    }
  }

  async function doUpdateSkills() {
    setBusy("skills");
    setMsg("更新 skills 中…");
    try {
      await updateSkills();
      setMsg("✓ skills 已更新到最新");
    } catch (e) {
      setMsg("失败:" + (e as Error).message);
    } finally {
      setBusy("");
      reload();
    }
  }

  return (
    <section className="set-block">
      <div className="set-title">组件与更新</div>
      <div className="muted" style={{ marginBottom: 8 }}>
        ai-helper 用到的外部组件。mihomo 内核、工程 skills 可在这里更新;
        Git、Ollama 是系统软件,只显示状态(由你自己管理)。
      </div>
      {!comp ? (
        <div className="loading-row" style={{ justifyContent: "flex-start" }}>
          <span className="spinner" />
          <span>检测组件中…</span>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <CompRow
            name="网络代理组件 (mihomo)"
            desc="走 VPN 调用境外 API 时用;不接管整机网络"
            statusText={
              comp.mihomo.installed
                ? `已就绪 ${comp.mihomo.version || ""}`.trim()
                : "未安装(用到走 VPN 的功能时会自动提示获取)"
            }
            ok={comp.mihomo.installed}
            action={
              !canSettings ? null : !comp.mihomo.installed ? (
                <button
                  disabled={!!busy}
                  onClick={() => doUpdateCore(false)}
                >
                  {busy === "mihomo" ? "获取中…" : "获取内核"}
                </button>
              ) : comp.mihomo.updatable ? (
                <button
                  disabled={!!busy}
                  title={`内置版本 ${comp.mihomo.bundled}`}
                  onClick={() => doUpdateCore(true)}
                >
                  {busy === "mihomo"
                    ? "更新中…"
                    : `更新到 ${comp.mihomo.bundled}`}
                </button>
              ) : (
                <button
                  disabled={!!busy}
                  title="重新下载并校验(修复损坏等)"
                  onClick={() => doUpdateCore(true)}
                >
                  {busy === "mihomo" ? "处理中…" : "重装"}
                </button>
              )
            }
          />
          <CompRow
            name="工程 skills"
            desc="编程 Agent 的工程实践指南(来自 mattpocock/skills)"
            statusText={
              comp.skills.installed
                ? `已克隆 ${comp.skills.count} 个`
                : "未克隆"
            }
            ok={comp.skills.installed}
            action={
              !canSettings ? null : (
                <button disabled={!!busy} onClick={doUpdateSkills}>
                  {busy === "skills"
                    ? "更新中…"
                    : comp.skills.installed
                    ? "更新"
                    : "克隆"}
                </button>
              )
            }
          />
          <CompRow
            name="Git"
            desc="编程 Agent 的检查点/回滚依赖;系统软件"
            statusText={
              comp.git.installed
                ? comp.git.version || "已安装"
                : "未安装"
            }
            ok={comp.git.installed}
            action={null}
          />
          <CompRow
            name="本地模型 Ollama"
            desc="本地小模型(路由/直答/摘要);系统软件"
            statusText={
              comp.ollama.installed
                ? `运行中 · ${comp.ollama.models ?? 0} 个模型`
                : "未运行"
            }
            ok={comp.ollama.installed}
            action={null}
          />
        </div>
      )}
      {msg && (
        <div className="cfg-msg" style={{ marginTop: 8 }}>
          {msg}
        </div>
      )}
    </section>
  );
}

function CompRow({
  name,
  desc,
  statusText,
  ok,
  action,
}: {
  name: string;
  desc: string;
  statusText: string;
  ok: boolean;
  action: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 12px",
        border: "1px solid var(--border-2)",
        borderRadius: 8,
        background: "var(--panel-2)",
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          flexShrink: 0,
          background: ok ? "#2f9e44" : "var(--muted)",
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{name}</div>
        <div className="muted" style={{ fontSize: 12, margin: 0 }}>
          {desc}
        </div>
        <div
          style={{
            fontSize: 12,
            marginTop: 2,
            color: ok ? "var(--text)" : "var(--muted)",
          }}
        >
          {statusText}
        </div>
      </div>
      {action && <div style={{ flexShrink: 0 }}>{action}</div>}
    </div>
  );
}
