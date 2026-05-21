// VPN 订阅 / 节点管理页 —— 框架,具体接口/列表后续填充
import { useEffect, useState } from "react";
import {
  type VpnSub,
  listVpnSubs,
  addVpnSub,
  deleteVpnSub,
  refreshVpnSub,
} from "../api";

export default function VpnPage() {
  const [list, setList] = useState<VpnSub[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [yaml, setYaml] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function reload() {
    try {
      setList(await listVpnSubs());
    } catch (e) {
      setMsg("加载失败:" + (e as Error).message);
    }
  }
  useEffect(() => {
    reload();
  }, []);

  async function doAdd() {
    setBusy(true);
    setMsg("");
    try {
      await addVpnSub({
        name: name.trim() || "未命名订阅",
        url: url.trim() || null,
        yaml: yaml.trim() || null,
      });
      setName("");
      setUrl("");
      setYaml("");
      setAdding(false);
      await reload();
      setMsg("已添加");
    } catch (e) {
      setMsg("添加失败:" + (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="chat-head">
        <h1>VPN 订阅</h1>
        {!adding && (
          <button className="cfg-toggle" onClick={() => setAdding(true)}>
            ＋ 添加订阅
          </button>
        )}
      </div>
      <div className="muted">
        管理 Clash 订阅(链接或 YAML 文件),仅服务于 API 调用,不接管整机网络。
        启用了「走 VPN」的 API 在被调用时按规则启动 mihomo 子代理。
      </div>

      {adding && (
        <div className="cfg-box">
          <label>
            订阅名称
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如 主线机房 / 备用-香港"
            />
          </label>
          <label>
            订阅 URL(任选其一)
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://provider.example/api/sub?token=..."
            />
          </label>
          <label>
            或粘贴 Clash YAML 内容
            <textarea
              className="sys-area"
              value={yaml}
              onChange={(e) => setYaml(e.target.value)}
              placeholder="port: 7890\nproxies:\n  - name: ..."
              style={{ minHeight: 120 }}
            />
          </label>
          <div className="cfg-actions">
            <button disabled={busy} onClick={doAdd}>
              {busy ? "添加中…" : "确认添加"}
            </button>
            <button onClick={() => setAdding(false)}>取消</button>
            <span className="cfg-msg">{msg}</span>
          </div>
        </div>
      )}

      {list === null ? (
        <div className="loading-row">
          <span className="spinner" />
          <span>加载订阅中…</span>
        </div>
      ) : list.length === 0 ? (
        <div className="placeholder">
          还没有 VPN 订阅,点上面「＋ 添加订阅」开始。
        </div>
      ) : (
        <div className="prov-list">
          {list.map((s) => (
            <div key={s.id} className="prov-card">
              <div className="prov-main">
                <div className="prov-name">
                  {s.name}
                  {s.source === "url" ? (
                    <span className="badge">URL</span>
                  ) : (
                    <span className="badge">YAML</span>
                  )}
                </div>
                {s.updated && (
                  <div className="muted">
                    最近更新:{new Date(s.updated * 1000).toLocaleString()}
                  </div>
                )}
                {(s.upload != null || s.download != null) && (
                  <div className="muted">
                    流量:↑ {fmtBytes(s.upload)} / ↓ {fmtBytes(s.download)}
                    {s.total != null && ` / 总额 ${fmtBytes(s.total)}`}
                  </div>
                )}
                {s.expire && (
                  <div className="muted">
                    到期:{new Date(s.expire * 1000).toLocaleDateString()}
                  </div>
                )}
                {s.nodes && s.nodes.length > 0 && (
                  <div className="muted">
                    节点:{s.nodes.length} 个(
                    {s.nodes.slice(0, 3).join(" · ")}
                    {s.nodes.length > 3 ? " · …" : ""})
                  </div>
                )}
              </div>
              <div className="prov-actions">
                {s.url && (
                  <button
                    onClick={async () => {
                      setBusy(true);
                      setMsg("刷新中…");
                      try {
                        await refreshVpnSub(s.id);
                        await reload();
                        setMsg("已刷新");
                      } catch (e) {
                        setMsg("刷新失败:" + (e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    刷新
                  </button>
                )}
                <button
                  className="danger"
                  onClick={async () => {
                    if (!confirm(`删除订阅「${s.name}」?`)) return;
                    await deleteVpnSub(s.id);
                    reload();
                  }}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function fmtBytes(b: number | null | undefined): string {
  if (b == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = b;
  while (v > 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return v.toFixed(v < 10 ? 2 : v < 100 ? 1 : 0) + " " + u[i];
}
