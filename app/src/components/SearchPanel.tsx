// 联网搜索独立配置 —— 不与 API 管理共享 key,自成一套。
// 仅本机可编辑(key 是凭证)。带测试按钮 + 错误诊断。
import { useEffect, useState } from "react";
import {
  type SearchCfg,
  type SearchTestResult,
  type VpnSub,
  getSearchCfg,
  listVpnSubs,
  notifyCoreMissing,
  saveSearchCfg,
  testSearch,
} from "../api";

export default function SearchPanel({
  canSettings,
}: {
  canSettings: boolean;
}) {
  const [cfg, setCfg] = useState<SearchCfg | null>(null);
  const [draftKey, setDraftKey] = useState("");
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<SearchTestResult | null>(null);
  const [msg, setMsg] = useState("");
  const [subs, setSubs] = useState<VpnSub[]>([]);

  async function reload() {
    try {
      setCfg(await getSearchCfg());
    } catch (e) {
      setMsg("加载失败:" + (e as Error).message);
    }
  }
  useEffect(() => {
    reload();
    listVpnSubs().then(setSubs).catch(() => setSubs([]));
  }, []);

  async function save(patch: Parameters<typeof saveSearchCfg>[0]) {
    try {
      setCfg(await saveSearchCfg(patch));
      setMsg("已保存");
      setDraftKey("");
    } catch (e) {
      setMsg("失败:" + (e as Error).message);
    }
  }

  async function runTest() {
    setTesting(true);
    setResult(null);
    setMsg("");
    try {
      const r = await testSearch("ai-helper 是什么");
      setResult(r);
      if (r.core_missing) notifyCoreMissing();
    } catch (e) {
      setMsg("测试失败:" + (e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  if (!cfg) {
    return (
      <section className="set-block">
        <div className="set-title">联网搜索</div>
        <div className="muted">加载中…</div>
      </section>
    );
  }

  return (
    <section className="set-block">
      <div className="set-title">联网搜索</div>
      <div className="muted" style={{ marginBottom: 8 }}>
        独立配置,**与 API 管理完全独立**(两边互不可见、可分别存同一 key)。
        推荐 <strong>Tavily</strong>:专为 LLM 设计,免费 1000 次/月,无需信用卡。
        国内访问 Tavily 需走 VPN。注册 <a
          href="https://tavily.com"
          target="_blank"
          rel="noreferrer"
        >tavily.com</a> → API Keys → 复制 <code>tvly-...</code> 粘到下方。
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label>
          <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>
            服务商
          </div>
          <select
            value={cfg.provider}
            disabled={!canSettings}
            onChange={(e) => save({ provider: e.target.value })}
            style={{ width: 200 }}
          >
            <option value="tavily">Tavily(推荐)</option>
            <option value="off">关闭联网搜索</option>
          </select>
        </label>
        {cfg.provider !== "off" && (
          <label>
            <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>
              API Key {cfg.api_key_set && <span style={{ color: "#2f9e44" }}>(已设)</span>}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                type="password"
                value={draftKey}
                disabled={!canSettings}
                placeholder={cfg.api_key_set ? "已存,留空=不改" : "tvly-..."}
                onChange={(e) => setDraftKey(e.target.value)}
                style={{ flex: 1, fontFamily: "monospace" }}
              />
              <button
                disabled={!canSettings || !draftKey.trim()}
                onClick={() => save({ api_key: draftKey })}
              >
                保存 key
              </button>
              {cfg.api_key_set && (
                <button
                  className="danger"
                  disabled={!canSettings}
                  onClick={() => save({ api_key: "__clear__" })}
                >
                  清除
                </button>
              )}
            </div>
          </label>
        )}
        <label>
          <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>
            每次结果数(1-10)
          </div>
          <input
            type="number"
            min={1}
            max={10}
            value={cfg.max_results}
            disabled={!canSettings}
            onChange={(e) => save({ max_results: parseInt(e.target.value) })}
            style={{ width: 80 }}
          />
        </label>

        {cfg.provider !== "off" && (
          <div
            style={{
              marginTop: 4,
              padding: 8,
              border: "1px solid var(--border-2)",
              borderRadius: 6,
              background: "var(--bg)",
            }}
          >
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                cursor: canSettings ? "pointer" : "default",
              }}
            >
              <input
                type="checkbox"
                checked={!!cfg.use_vpn}
                disabled={!canSettings}
                onChange={(e) =>
                  save({ use_vpn: e.target.checked })
                }
              />
              <strong style={{ fontSize: 13 }}>走 VPN 调用</strong>
              <span className="muted" style={{ fontSize: 11 }}>
                (复用 ai-helper 内的 VPN —— Tavily 在国内通常需代理)
              </span>
            </label>
            {cfg.use_vpn && (
              <div
                style={{
                  marginTop: 6,
                  display: "flex",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <label
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                    flex: "1 1 180px",
                  }}
                >
                  <span className="muted" style={{ fontSize: 11 }}>
                    订阅
                  </span>
                  <select
                    value={cfg.vpn_sub_id || ""}
                    disabled={!canSettings}
                    onChange={(e) =>
                      save({ vpn_sub_id: e.target.value, vpn_node: "" })
                    }
                  >
                    <option value="">— 选 —</option>
                    {subs.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                    flex: "1 1 180px",
                  }}
                >
                  <span className="muted" style={{ fontSize: 11 }}>
                    节点
                  </span>
                  <select
                    value={cfg.vpn_node || ""}
                    disabled={!canSettings || !cfg.vpn_sub_id}
                    onChange={(e) => save({ vpn_node: e.target.value })}
                  >
                    <option value="">— 选 —</option>
                    {(
                      subs.find((s) => s.id === cfg.vpn_sub_id)?.nodes || []
                    ).map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
                {subs.length === 0 && (
                  <div className="muted" style={{ fontSize: 11 }}>
                    没有可用 VPN 订阅,先去「VPN 订阅」页加一个
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="cfg-actions" style={{ marginTop: 10 }}>
        <button
          onClick={runTest}
          disabled={testing || cfg.provider === "off" || !cfg.api_key_set}
        >
          {testing ? "测试中…" : "测试搜索"}
        </button>
        {msg && <span className="cfg-msg">{msg}</span>}
      </div>

      {result && (
        <div
          style={{
            marginTop: 10,
            padding: 8,
            border: "1px solid var(--border-2)",
            borderRadius: 6,
            background: "var(--bg)",
            fontSize: 12,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>
            {result.ok ? (
              <span style={{ color: "#2f9e44" }}>
                ✓ 通过 · {result.provider} · 返回 {result.count} 条
                {result.via_vpn && (
                  <span className="badge" style={{ marginLeft: 6, fontSize: 10 }}>
                    走 VPN
                  </span>
                )}
              </span>
            ) : (
              <span style={{ color: "#c92a2a" }}>
                ✖ 失败 · {result.error}
              </span>
            )}
          </div>
          {result.results?.slice(0, 3).map((r, i) => (
            <div key={i} style={{ marginBottom: 6 }}>
              <div style={{ fontWeight: 500 }}>{r.title}</div>
              <div className="muted" style={{ fontSize: 11 }}>
                {r.snippet.slice(0, 200)}
              </div>
              <div style={{ fontSize: 11, color: "#1971c2" }}>{r.url}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
