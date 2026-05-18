import { useEffect, useState } from "react";
import {
  deleteProvider,
  getProviders,
  setActive,
  upsertProvider,
  type ProvidersState,
  type WhoAmI,
} from "../api";

interface PresetRow {
  label: string;
  model: string;
  extra: string; // JSON 文本
}
interface Draft {
  id?: string;
  name: string;
  format: string;
  base_url: string;
  api_key: string;
  api_key_set: boolean;
  capability: string;
  presets: PresetRow[];
}

const FORMATS = ["openai_compat", "anthropic", "gemini", "custom"];

function blankDraft(): Draft {
  return {
    name: "",
    format: "openai_compat",
    base_url: "",
    api_key: "",
    api_key_set: false,
    capability: "",
    presets: [{ label: "默认", model: "", extra: "" }],
  };
}

export default function ApiPage({ who }: { who: WhoAmI | null }) {
  const canManage = who ? who.permissions.api_manage !== false : true;
  const [ps, setPs] = useState<ProvidersState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [err, setErr] = useState("");

  async function reload() {
    try {
      setPs(await getProviders());
    } catch {
      /* ignore */
    }
  }
  useEffect(() => {
    reload();
  }, []);

  function editProvider(id: string) {
    const p = ps?.providers.find((x) => x.id === id);
    if (!p) return;
    setErr("");
    setDraft({
      id: p.id,
      name: p.name,
      format: p.format,
      base_url: p.base_url,
      api_key: "",
      api_key_set: p.api_key_set,
      capability: p.capability || "",
      presets: p.presets.length
        ? p.presets.map((pr) => ({
            label: pr.label,
            model: pr.model,
            extra: Object.keys(pr.extra_body || {}).length
              ? JSON.stringify(pr.extra_body)
              : "",
          }))
        : [{ label: "默认", model: "", extra: "" }],
    });
  }

  async function save() {
    if (!draft) return;
    setErr("");
    const presets = [];
    for (const r of draft.presets) {
      if (!r.label.trim() || !r.model.trim()) {
        setErr("每个预设的「标签」和「模型」必填");
        return;
      }
      let extra_body = {};
      if (r.extra.trim()) {
        try {
          extra_body = JSON.parse(r.extra);
        } catch {
          setErr(`预设「${r.label}」的额外参数不是合法 JSON`);
          return;
        }
      }
      presets.push({ label: r.label.trim(), model: r.model.trim(), extra_body });
    }
    try {
      await upsertProvider({
        id: draft.id,
        name: draft.name.trim() || "未命名",
        format: draft.format,
        base_url: draft.base_url.trim(),
        api_key: draft.api_key.trim() || undefined,
        capability: draft.capability.trim(),
        presets,
      });
      setDraft(null);
      reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function del(id: string) {
    await deleteProvider(id);
    reload();
  }

  async function makeActive(pid: string, label: string) {
    await setActive(pid, label);
    reload();
  }

  return (
    <div className="page">
      <div className="chat-head">
        <h1>API 管理</h1>
        {canManage && !draft && (
          <button
            className="cfg-toggle"
            onClick={() => {
              setErr("");
              setDraft(blankDraft());
            }}
          >
            ＋ 新增 API
          </button>
        )}
      </div>

      {!canManage && (
        <div className="cfg-note warn">
          远程访问只读：可切换当前 API，不能新增/编辑/删除（密钥仅本机可见可改）。
        </div>
      )}

      {!draft && (
        <div className="prov-list">
          {ps?.providers.length ? (
            ps.providers.map((p) => {
              const isActive = ps.active.provider_id === p.id;
              return (
                <div
                  key={p.id}
                  className={"prov-card" + (isActive ? " active" : "")}
                >
                  <div className="prov-main">
                    <div className="prov-name">
                      {p.name}
                      {isActive && <span className="badge">当前</span>}
                      {!p.api_key_set && (
                        <span className="badge warn">无 key</span>
                      )}
                    </div>
                    <div className="muted">
                      {p.format} · {p.base_url}
                    </div>
                    {p.capability && (
                      <div className="muted">擅长：{p.capability}</div>
                    )}
                    <div className="prov-presets">
                      {p.presets.map((pr) => (
                        <button
                          key={pr.label}
                          className={
                            "chip" +
                            (isActive && ps.active.preset_label === pr.label
                              ? " on"
                              : "")
                          }
                          onClick={() => makeActive(p.id, pr.label)}
                          title="设为当前并用此预设"
                        >
                          {pr.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  {canManage && (
                    <div className="prov-actions">
                      <button onClick={() => editProvider(p.id)}>编辑</button>
                      <button className="danger" onClick={() => del(p.id)}>
                        删除
                      </button>
                    </div>
                  )}
                </div>
              );
            })
          ) : (
            <div className="empty">还没有 API，点右上「＋ 新增 API」。</div>
          )}
        </div>
      )}

      {draft && (
        <div className="cfg-box">
          <label>
            名称
            <input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="DeepSeek"
            />
          </label>
          <label>
            格式
            <select
              value={draft.format}
              onChange={(e) => setDraft({ ...draft, format: e.target.value })}
            >
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                  {f === "openai_compat" ? "（当前已支持）" : "（后续阶段）"}
                </option>
              ))}
            </select>
          </label>
          <label>
            base_url
            <input
              value={draft.base_url}
              onChange={(e) =>
                setDraft({ ...draft, base_url: e.target.value })
              }
              placeholder="https://api.deepseek.com"
            />
          </label>
          <label>
            api_key
            <input
              type="password"
              value={draft.api_key}
              onChange={(e) =>
                setDraft({ ...draft, api_key: e.target.value })
              }
              placeholder={
                draft.api_key_set ? "已配置（留空=不改）" : "未配置，请填入"
              }
            />
          </label>

          <label>
            擅长描述（给本地模型自动路由参考，如「推理/代码强，中文写作一般」）
            <input
              value={draft.capability}
              onChange={(e) =>
                setDraft({ ...draft, capability: e.target.value })
              }
              placeholder="推理、代码、数学较强，长文本与中文不错"
            />
          </label>

          <div className="set-title">预设（模型 + 模式）</div>
          {draft.presets.map((r, i) => (
            <div key={i} className="preset-row">
              <input
                placeholder="标签 如 V4 Pro·思考"
                value={r.label}
                onChange={(e) => {
                  const ps2 = [...draft.presets];
                  ps2[i] = { ...r, label: e.target.value };
                  setDraft({ ...draft, presets: ps2 });
                }}
              />
              <input
                placeholder="模型 id 如 deepseek-v4-pro"
                value={r.model}
                onChange={(e) => {
                  const ps2 = [...draft.presets];
                  ps2[i] = { ...r, model: e.target.value };
                  setDraft({ ...draft, presets: ps2 });
                }}
              />
              <input
                placeholder='额外参数JSON 如 {"thinking":{"type":"enabled"}}'
                value={r.extra}
                onChange={(e) => {
                  const ps2 = [...draft.presets];
                  ps2[i] = { ...r, extra: e.target.value };
                  setDraft({ ...draft, presets: ps2 });
                }}
              />
              <button
                className="danger"
                onClick={() =>
                  setDraft({
                    ...draft,
                    presets: draft.presets.filter((_, j) => j !== i),
                  })
                }
              >
                ×
              </button>
            </div>
          ))}
          <button
            onClick={() =>
              setDraft({
                ...draft,
                presets: [
                  ...draft.presets,
                  { label: "", model: "", extra: "" },
                ],
              })
            }
          >
            ＋ 加预设
          </button>

          {err && <div className="cfg-note warn">{err}</div>}
          <div className="cfg-actions">
            <button onClick={save}>保存</button>
            <button onClick={() => setDraft(null)}>取消</button>
          </div>
        </div>
      )}
    </div>
  );
}
