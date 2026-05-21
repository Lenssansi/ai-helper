// VPN 订阅 / 节点管理页 —— 添加/编辑 + 文件导入 + 转换提示
import { useEffect, useRef, useState } from "react";
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
  testVpnSubAll,
  updateVpnSub,
} from "../api";

type FormMode = null | "add" | "edit";

export default function VpnPage() {
  const [list, setList] = useState<VpnSub[] | null>(null);
  const [formMode, setFormMode] = useState<FormMode>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [yaml, setYaml] = useState("");
  const [yamlFileName, setYamlFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  // 哪张卡的刷新在跑(用来显示行内 spinner / 禁用按钮)
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [preview, setPreview] = useState<VpnPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  // 订阅级 TCP 测延迟结果:subId → (nodeName → ms|null)
  const [latency, setLatency] = useState<
    Record<string, Record<string, number | null>>
  >({});
  // 哪个订阅正在测延迟(行内 spinner)
  const [testingId, setTestingId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

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

  function resetForm() {
    setName("");
    setUrl("");
    setYaml("");
    setYamlFileName("");
    setEditingId(null);
    setBusy(false);
    if (fileRef.current) fileRef.current.value = "";
  }

  function openAdd() {
    resetForm();
    setMsg("");
    setFormMode("add");
  }

  function openEdit(s: VpnSub) {
    resetForm();
    setMsg("");
    setEditingId(s.id);
    setName(s.name);
    setUrl(s.url || "");
    setFormMode("edit");
  }

  function closeForm() {
    resetForm();
    setFormMode(null);
  }

  async function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) {
      setYaml("");
      setYamlFileName("");
      return;
    }
    try {
      const text = await f.text();
      setYaml(text);
      setYamlFileName(f.name);
    } catch (err) {
      setMsg("读取文件失败:" + (err as Error).message);
    }
  }

  async function doSubmit() {
    setBusy(true);
    setMsg("");
    try {
      if (formMode === "add") {
        await addVpnSub({
          name: name.trim() || "未命名订阅",
          url: url.trim() || null,
          yaml: yaml.trim() || null,
        });
        await reload();
        setMsg("已添加");
        closeForm();
      } else if (formMode === "edit" && editingId) {
        await updateVpnSub(editingId, {
          name: name.trim() || null,
          // 空字符串 = 不动 URL;有值 = 切到 URL 源并重新拉取
          url: url.trim() ? url.trim() : null,
          yaml: yaml.trim() || null,
        });
        await reload();
        setMsg("已保存");
        closeForm();
      }
    } catch (e) {
      setMsg("保存失败:" + (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="chat-head">
        <h1>VPN 订阅</h1>
        {!formMode && (
          <button className="cfg-toggle" onClick={openAdd}>
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

      {formMode && (
        <div className="cfg-box">
          <div className="set-title" style={{ marginBottom: 6 }}>
            {formMode === "add" ? "添加新订阅" : "编辑订阅"}
          </div>
          <label>
            订阅名称
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如 主线机房 / 备用-香港"
            />
          </label>
          <label>
            订阅 URL{formMode === "edit" ? "(留空保持不变;改了会重新拉取)" : "(任选其一)"}
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://provider.example/api/sub?token=..."
            />
          </label>
          <label>
            或导入 Clash YAML 文件
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                ref={fileRef}
                type="file"
                accept=".yaml,.yml,.txt,text/yaml,application/yaml"
                onChange={onPickFile}
                style={{ flex: 1 }}
              />
              {yamlFileName && (
                <span className="muted" style={{ fontSize: 12 }}>
                  已选:{yamlFileName}({yaml.length} 字节)
                </span>
              )}
              {yaml && (
                <button
                  onClick={() => {
                    setYaml("");
                    setYamlFileName("");
                    if (fileRef.current) fileRef.current.value = "";
                  }}
                  style={{ padding: "4px 10px" }}
                >
                  清空
                </button>
              )}
            </div>
          </label>
          <div className="cfg-note" style={{ fontSize: 12, marginTop: 4 }}>
            支持 Clash YAML / V2Ray 订阅(base64 URI 列表会自动转 Clash YAML)。
          </div>
          <div className="cfg-actions">
            <button disabled={busy} onClick={doSubmit}>
              {busy
                ? "保存中…"
                : formMode === "add"
                ? "确认添加"
                : "保存修改"}
            </button>
            <button disabled={busy} onClick={closeForm}>
              取消
            </button>
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
                  {s.converted_from_uri && (
                    <span
                      className="badge"
                      style={{ background: "#e7f5ff", color: "#1971c2" }}
                      title="原订阅是 V2Ray URI/base64,已自动转为 Clash YAML 以供 mihomo 使用"
                    >
                      已转换
                    </span>
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
                  <NodeListWithLatency
                    nodes={s.nodes}
                    latency={latency[s.id] || {}}
                  />
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
                <button onClick={() => openEdit(s)}>编辑</button>
                {s.nodes && s.nodes.length > 0 && (
                  <button
                    disabled={testingId === s.id}
                    title="并发 TCP 直连测全部节点(不依赖 mihomo)"
                    onClick={async () => {
                      setTestingId(s.id);
                      setMsg(`测「${s.name}」${s.nodes!.length} 个节点延迟中…`);
                      try {
                        const r = await testVpnSubAll(s.id);
                        const map: Record<string, number | null> = {};
                        for (const item of r.results) {
                          if (item.node)
                            map[item.node] = item.ok ? item.ms ?? null : null;
                        }
                        setLatency((prev) => ({ ...prev, [s.id]: map }));
                        setMsg(
                          `✓ 「${s.name}」${r.alive}/${r.count} 节点可连`,
                        );
                      } catch (e) {
                        setMsg("测延迟失败:" + (e as Error).message);
                      } finally {
                        setTestingId(null);
                      }
                    }}
                  >
                    {testingId === s.id ? (
                      <>
                        <span
                          className="spinner"
                          style={{
                            width: 12,
                            height: 12,
                            borderWidth: 2,
                            marginRight: 4,
                          }}
                        />
                        测延迟中…
                      </>
                    ) : (
                      "测全部延迟"
                    )}
                  </button>
                )}
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
                        <span
                          className="spinner"
                          style={{
                            width: 12,
                            height: 12,
                            borderWidth: 2,
                            marginRight: 4,
                          }}
                        />
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
                    try {
                      await deleteVpnSub(s.id);
                      // 删除时若正在编辑这条 → 关闭表单防卡死
                      if (editingId === s.id) closeForm();
                      // 预览 / 刷新中状态都重置
                      if (preview && preview.id === s.id) setPreview(null);
                      if (refreshingId === s.id) setRefreshingId(null);
                      await reload();
                      setMsg(`已删除「${s.name}」`);
                    } catch (e) {
                      setMsg("删除失败:" + (e as Error).message);
                    }
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
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  overflow: "auto",
                }}
              >
                {preview.nodes.map((n, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "6px 10px",
                      background: "var(--panel-2)",
                      border: "1px solid var(--border-2)",
                      borderRadius: 6,
                      fontSize: 13,
                      fontFamily: "monospace",
                    }}
                  >
                    {i + 1}. {n}
                  </div>
                ))}
              </div>
            ) : (
              <div className="cfg-note warn" style={{ marginBottom: 8 }}>
                {preview.format === "v2ray-uri" || preview.format === "base64"
                  ? "订阅是 V2Ray/SS/Trojan URI 列表。系统已尝试自动转 Clash YAML,如果转换失败可能是协议不支持(hysteria2 / tuic / snell 等待支持)。"
                  : preview.format === "surge"
                  ? "订阅是 Surge 格式,mihomo 不直接支持。多数机场链接末尾加 &flag=clash 就能返 Clash YAML;改完点「刷新」即可。"
                  : preview.format === "unknown"
                  ? "无法识别订阅格式。看下面原始内容头部排查 — 常见原因:URL 错、token 过期、机场返了 HTML 错误页。"
                  : "已用 Clash YAML 解析,但 proxies 字段为空。如果你在 Clash Verge 里能用,大概率是机场对 UA 做了 gating —— 本程序会自动尝试 clash.meta/mihomo/clash-verge 多个 UA。"}
              </div>
            )}
            <div className="muted" style={{ marginTop: 10 }}>
              原始内容头部(共 {preview.raw_len} 字节,只显示前 2000):
            </div>
            <pre
              style={{
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
              }}
            >
              {preview.raw_head || "(空)"}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function fmtLatency(ms: number | null | undefined): {
  text: string;
  color: string;
} {
  if (ms === undefined) return { text: "—", color: "var(--muted)" };
  if (ms === null) return { text: "失败", color: "#c92a2a" };
  if (ms < 200) return { text: `${ms}ms`, color: "#2f9e44" };
  if (ms < 500) return { text: `${ms}ms`, color: "#f59f00" };
  return { text: `${ms}ms`, color: "#e8590c" };
}

function NodeListWithLatency({
  nodes,
  latency,
}: {
  nodes: string[];
  latency: Record<string, number | null | undefined>;
}) {
  // 按延迟排序:有结果的优先,小的在前,失败/未测的靠后
  const ordered = [...nodes].sort((a, b) => {
    const la = latency[a];
    const lb = latency[b];
    const va =
      la === undefined ? 99998 : la === null ? 99999 : la;
    const vb =
      lb === undefined ? 99998 : lb === null ? 99999 : lb;
    return va - vb;
  });
  const tested = Object.keys(latency).length;
  return (
    <div style={{ marginTop: 6 }}>
      <div className="muted" style={{ marginBottom: 4 }}>
        节点:{nodes.length} 个{tested > 0 ? `(已测 ${tested})` : ""}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
          gap: 4,
          maxHeight: 200,
          overflow: "auto",
          padding: "4px 6px",
          background: "var(--panel-2)",
          border: "1px solid var(--border-2)",
          borderRadius: 6,
        }}
      >
        {ordered.map((n) => {
          const f = fmtLatency(latency[n]);
          return (
            <div
              key={n}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 6,
                padding: "2px 4px",
                fontSize: 12,
              }}
            >
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  fontFamily: "monospace",
                }}
                title={n}
              >
                {n}
              </span>
              <span
                style={{
                  color: f.color,
                  fontWeight: 600,
                  minWidth: 50,
                  textAlign: "right",
                }}
              >
                {f.text}
              </span>
            </div>
          );
        })}
      </div>
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
