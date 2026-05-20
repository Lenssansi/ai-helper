import { useEffect, useState } from "react";
import {
  deleteProvider,
  discoverProviderModels,
  getProviders,
  getUsage,
  resetUsage,
  setActive,
  testProvider,
  upsertProvider,
  type ProvidersState,
  type ProviderTest,
  type UsageState,
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
  const [usage, setUsage] = useState<UsageState | null>(null);
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [tested, setTested] = useState<Record<string, ProviderTest>>({});

  async function reload() {
    try {
      setPs(await getProviders());
    } catch {
      /* ignore */
    }
    try {
      setUsage(await getUsage());
    } catch {
      /* ignore */
    }
  }
  useEffect(() => {
    reload();
  }, []);

  async function runTest(p: { id: string; presets: { label: string }[] }) {
    const label =
      ps && ps.active.provider_id === p.id
        ? ps.active.preset_label
        : p.presets[0]?.label || "";
    setTesting((m) => ({ ...m, [p.id]: true }));
    setTested((m) => {
      const n = { ...m };
      delete n[p.id];
      return n;
    });
    try {
      const r = await testProvider(p.id, label);
      setTested((m) => ({ ...m, [p.id]: r }));
    } catch (e) {
      setTested((m) => ({
        ...m,
        [p.id]: { ok: false, error: (e as Error).message },
      }));
    } finally {
      setTesting((m) => ({ ...m, [p.id]: false }));
    }
  }

  async function clearUsage() {
    try {
      setUsage(await resetUsage());
    } catch {
      /* ignore */
    }
  }

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

      {!draft && !ps && (
        <div className="loading-row">
          <span className="spinner" />
          <span>加载 API 列表中…</span>
        </div>
      )}

      {!draft && ps && (
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
                    <div className="prov-test">
                      <button
                        disabled={!!testing[p.id]}
                        onClick={() => runTest(p)}
                        title="用最小请求实测该 API 能否连通（默认参数）"
                      >
                        {testing[p.id] ? "测试中…" : "测试连通"}
                      </button>
                      {tested[p.id] &&
                        (tested[p.id].ok ? (
                          <span className="badge ok">
                            通 {tested[p.id].ms}ms · {tested[p.id].model}
                          </span>
                        ) : (
                          <span
                            className="badge warn"
                            title={tested[p.id].error}
                          >
                            失败：{tested[p.id].error}
                          </span>
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
          <div className="cfg-actions" style={{ marginBottom: 6 }}>
            <button
              type="button"
              onClick={async () => {
                const base = draft.base_url.trim();
                if (!base) {
                  setErr("先填 base_url 再发现模型");
                  return;
                }
                setErr("发现中…");
                try {
                  const r = await discoverProviderModels(base);
                  if (!r.models.length) {
                    setErr(
                      "未发现模型(地址不可达?或不是 Ollama / OpenAI 兼容接口)"
                    );
                    return;
                  }
                  // 合并到预设(去重),label = model = 发现到的名字
                  const exist = new Set(draft.presets.map((p) => p.model));
                  const add = r.models
                    .filter((m) => !exist.has(m))
                    .map((m) => ({ label: m, model: m, extra: "" }));
                  setDraft({
                    ...draft,
                    presets: [
                      ...draft.presets.filter((p) => p.label || p.model),
                      ...add,
                    ],
                  });
                  setErr(`已新增 ${add.length} 个预设(去重)`);
                } catch (e) {
                  setErr("发现失败：" + (e as Error).message);
                }
              }}
              title="从当前 base_url 自动发现模型列表(Ollama /api/tags 或 OpenAI /v1/models)"
            >
              自动发现模型
            </button>
          </div>
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

      {!draft && (
        <div className="set-block" style={{ marginTop: 18 }}>
          <div className="chat-head">
            <div className="set-title">Token 用量统计</div>
            {usage && usage.totals.calls > 0 && (
              <button className="cfg-toggle" onClick={clearUsage}>
                清零
              </button>
            )}
          </div>
          {usage && usage.rows.length ? (
            <>
              <table className="usage-tbl">
                <thead>
                  <tr>
                    <th>API</th>
                    <th>调用次数</th>
                    <th>输入 tokens</th>
                    <th>输出 tokens</th>
                    <th>合计 tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.rows.map((r) => (
                    <tr key={r.name}>
                      <td>{r.name}</td>
                      <td>{r.calls}</td>
                      <td>{r.prompt_tokens.toLocaleString()}</td>
                      <td>{r.completion_tokens.toLocaleString()}</td>
                      <td>{r.total_tokens.toLocaleString()}</td>
                    </tr>
                  ))}
                  <tr className="usage-total">
                    <td>合计</td>
                    <td>{usage.totals.calls}</td>
                    <td>{usage.totals.prompt_tokens.toLocaleString()}</td>
                    <td>
                      {usage.totals.completion_tokens.toLocaleString()}
                    </td>
                    <td>{usage.totals.total_tokens.toLocaleString()}</td>
                  </tr>
                </tbody>
              </table>
              <div className="muted" style={{ marginTop: 8 }}>
                按各 API 返回的 usage 累计；本地 Ollama 多数不返 usage 故不计。
                {usage.updated && `（更新于 ${usage.updated}）`}
              </div>
            </>
          ) : (
            <div className="muted">
              暂无用量记录。发起对话后，支持 usage 的云端 API 会自动累计。
            </div>
          )}
        </div>
      )}
    </div>
  );
}
