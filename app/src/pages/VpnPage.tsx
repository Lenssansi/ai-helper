// VPN 订阅 / 节点管理页 —— 框架,具体接口/列表后续填充
import { useEffect, useState } from "react";
import {
  type VpnPreview,
  type VpnRule,
  type VpnSub,
  listVpnSubs,
  addVpnSub,
  deleteVpnSub,
  previewVpnSub,
  refreshVpnSub,
  setVpnRules,
} from "../api";

export default function VpnPage() {
  const [list, setList] = useState<VpnSub[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [yaml, setYaml] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  // 哪张卡的刷新在跑(用来显示行内 spinner / 禁用按钮)
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [preview, setPreview] = useState<VpnPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

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
      {msg && (
        <div className="cfg-msg" style={{ marginBottom: 10 }}>
          {msg}
        </div>
      )}

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
                <RulesEditor
                  sub={s}
                  onSaved={(updated) => {
                    setList((prev) =>
                      (prev || []).map((x) =>
                        x.id === updated.id ? updated : x,
                      ),
                    );
                  }}
                />
              </div>
              <div className="prov-actions">
                <button
                  disabled={previewing}
                  onClick={async () => {
                    setPreviewing(true);
                    setMsg("");
                    try {
                      setPreview(await previewVpnSub(s.id));
                    } catch (e) {
                      setMsg("预览失败:" + (e as Error).message);
                    } finally {
                      setPreviewing(false);
                    }
                  }}
                >
                  预览节点
                </button>
                {s.url && (
                  <button
                    disabled={refreshingId === s.id}
                    onClick={async () => {
                      setRefreshingId(s.id);
                      setMsg(`刷新「${s.name}」中…`);
                      try {
                        await refreshVpnSub(s.id);
                        await reload();
                        setMsg(`✓ 已刷新「${s.name}」`);
                      } catch (e) {
                        setMsg(
                          `✖ 刷新「${s.name}」失败: ` +
                            (e as Error).message,
                        );
                      } finally {
                        setRefreshingId(null);
                      }
                    }}
                  >
                    {refreshingId === s.id ? (
                      <>
                        <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2, marginRight: 4 }} />
                        刷新中…
                      </>
                    ) : (
                      "刷新"
                    )}
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

      {preview && (
        <div className="log-modal" onClick={() => setPreview(null)}>
          <div
            className="log-modal-body"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="log-modal-head">
              <div className="set-title">
                节点预览 · {preview.name}
                <span className="badge" style={{ marginLeft: 8 }}>
                  {preview.format}
                </span>
                <span className="badge" style={{ marginLeft: 4 }}>
                  {preview.nodes.length} 节点
                </span>
              </div>
              <button onClick={() => setPreview(null)}>关闭</button>
            </div>
            {preview.nodes.length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 4, overflow: "auto" }}>
                {preview.nodes.map((n, i) => (
                  <div key={i} style={{
                    padding: "6px 10px",
                    background: "var(--panel-2)",
                    border: "1px solid var(--border-2)",
                    borderRadius: 6,
                    fontSize: 13,
                    fontFamily: "monospace",
                  }}>
                    {i + 1}. {n}
                  </div>
                ))}
              </div>
            ) : (
              <div className="cfg-note warn" style={{ marginBottom: 8 }}>
                {preview.format === "v2ray-uri" || preview.format === "base64"
                  ? "订阅返回的是 V2Ray/SS/Trojan URI 列表(非 Clash YAML)。已尽力提取节点名,但 mihomo 实际跑不了这种格式 — 多数机场支持在订阅 URL 末尾加 `&flag=clash`(或 `?flag=clash`)就会返 YAML,改完点「刷新」即可。"
                  : preview.format === "unknown"
                  ? "无法识别订阅格式。看下面原始内容头部排查 — 常见原因:URL 错、token 过期、机场返了 HTML 错误页。"
                  : "已用 Clash YAML 解析,但 proxies 字段为空。看下面原始内容确认订阅是不是空了。"}
              </div>
            )}
            <div className="muted" style={{ marginTop: 10 }}>
              原始内容头部(共 {preview.raw_len} 字节,只显示前 2000):
            </div>
            <pre style={{
              maxHeight: 240,
              overflow: "auto",
              background: "var(--panel-2)",
              border: "1px solid var(--border-2)",
              borderRadius: 7,
              padding: 10,
              fontSize: 11,
              marginTop: 4,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}>
              {preview.raw_head || "(空)"}
            </pre>
          </div>
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

function RulesEditor({
  sub,
  onSaved,
}: {
  sub: VpnSub;
  onSaved: (updated: VpnSub) => void;
}) {
  const [rules, setRules] = useState<VpnRule[]>(sub.rules || []);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");
  // 当父级订阅刷新时同步进来
  useEffect(() => {
    setRules(sub.rules || []);
    setDirty(false);
  }, [sub.id, JSON.stringify(sub.rules)]);

  const nodes = sub.nodes || [];
  function patch(i: number, p: Partial<VpnRule>) {
    setRules((rs) => rs.map((r, j) => (i === j ? { ...r, ...p } : r)));
    setDirty(true);
  }
  function add() {
    setRules((rs) => [
      ...rs,
      { pattern: "", node: nodes[0] || "", note: "" },
    ]);
    setDirty(true);
  }
  function del(i: number) {
    setRules((rs) => rs.filter((_, j) => i !== j));
    setDirty(true);
  }
  async function save() {
    setMsg("保存中…");
    try {
      const updated = await setVpnRules(sub.id, rules);
      onSaved(updated);
      setDirty(false);
      setMsg("已保存");
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  return (
    <div className="vpn-rules">
      <div className="set-title" style={{ fontSize: 13, marginBottom: 4 }}>
        规则(可选,定义"什么走哪个节点";暂只存数据,不自动切节点)
      </div>
      {rules.map((r, i) => (
        <div key={i} className="vpn-rule-row">
          <input
            value={r.pattern}
            placeholder="匹配模式(如域名 api.openai.com)"
            onChange={(e) => patch(i, { pattern: e.target.value })}
          />
          <select
            value={r.node}
            onChange={(e) => patch(i, { node: e.target.value })}
          >
            <option value="">— 节点 —</option>
            {nodes.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <input
            value={r.note || ""}
            placeholder="备注(可选)"
            onChange={(e) => patch(i, { note: e.target.value })}
          />
          <button className="danger" onClick={() => del(i)}>
            ×
          </button>
        </div>
      ))}
      <div className="cfg-actions" style={{ marginTop: 6 }}>
        <button onClick={add}>＋ 新增规则</button>
        <button disabled={!dirty} onClick={save}>
          保存规则
        </button>
        <span className="cfg-msg">{msg}</span>
      </div>
    </div>
  );
}
