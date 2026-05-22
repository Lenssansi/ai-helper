// 网络组件按需获取 —— 全局挂一个,平时完全不可见。
// 仅当某处真正用到走 VPN 的功能、而内核又缺失时,后端发 core_missing
// 信号,api.ts 的 notifyCoreMissing() 派发 window 事件,这里接住并弹一个
// 低调的提示框,让用户一键从官方源获取。措辞中性,不出现扎眼字眼。
import { useEffect, useState } from "react";
import { installVpnCore } from "../api";

type Phase = "idle" | "ask" | "installing" | "done" | "error";

export default function CoreInstaller() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    const onMissing = () => {
      // 已在安装中/已弹出则不重复
      setPhase((p) => (p === "installing" || p === "ask" ? p : "ask"));
    };
    window.addEventListener("aih-core-missing", onMissing);
    return () => window.removeEventListener("aih-core-missing", onMissing);
  }, []);

  if (phase === "idle") return null;

  async function doInstall() {
    setPhase("installing");
    setMsg("");
    try {
      const r = await installVpnCore();
      if (r.ok) {
        setPhase("done");
        setMsg(
          r.already
            ? "组件已就绪。"
            : "组件已获取并就绪,请重试刚才的操作。",
        );
      } else {
        setPhase("error");
        setMsg(r.error || "获取失败。");
      }
    } catch (e) {
      setPhase("error");
      setMsg((e as Error).message);
    }
  }

  return (
    <div
      className="log-modal"
      onClick={() => {
        if (phase !== "installing") setPhase("idle");
      }}
    >
      <div
        className="log-modal-body"
        onClick={(e) => e.stopPropagation()}
        style={{ height: "auto", maxWidth: 460, padding: 20 }}
      >
        <div className="set-title" style={{ marginBottom: 10 }}>
          需要一个网络组件
        </div>

        {phase === "ask" && (
          <>
            <div className="muted" style={{ marginBottom: 16 }}>
              当前操作需要一个本机网络代理组件才能运行。是否现在自动获取并
              安装?约 16 MB,从官方源下载,仅本机使用。
            </div>
            <div className="cfg-actions">
              <button onClick={doInstall}>获取并安装</button>
              <button onClick={() => setPhase("idle")}>暂不</button>
            </div>
          </>
        )}

        {phase === "installing" && (
          <div
            className="loading-row"
            style={{ justifyContent: "flex-start" }}
          >
            <span className="spinner" />
            <span>正在获取组件…(下载约 16 MB,请稍候)</span>
          </div>
        )}

        {phase === "done" && (
          <>
            <div
              className="cfg-msg"
              style={{ marginBottom: 16, color: "#2f9e44" }}
            >
              ✓ {msg}
            </div>
            <div className="cfg-actions">
              <button onClick={() => setPhase("idle")}>好</button>
            </div>
          </>
        )}

        {phase === "error" && (
          <>
            <div
              className="cfg-note warn"
              style={{ marginBottom: 16 }}
            >
              获取失败:{msg}
            </div>
            <div className="cfg-actions">
              <button onClick={doInstall}>重试</button>
              <button onClick={() => setPhase("idle")}>关闭</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
