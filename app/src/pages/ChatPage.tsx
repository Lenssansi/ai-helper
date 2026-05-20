import { useEffect, useRef, useState } from "react";
import {
  createConversation,
  deleteConversation,
  getConversation,
  getProviders,
  getWorkspace,
  listConversations,
  saveConversation,
  setActive,
  streamSSE,
  type AgentEvent,
  type ChatMsg,
  type ConvSummary,
  type ProvidersState,
} from "../api";
import Markdown from "../components/Markdown";

export default function ChatPage() {
  const [ps, setPs] = useState<ProvidersState | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [convList, setConvList] = useState<ConvSummary[]>([]);
  const [convId, setConvId] = useState<string | null>(null);
  const [initLoading, setInitLoading] = useState(true);
  const [webOn, setWebOn] = useState(false);
  // 文件工具默认始终可用(不再设 toggle);设置工具(改设置)从对话中移除
  const [cfAwait, setCfAwait] = useState<AgentEvent | null>(null);
  const [editArgs, setEditArgs] = useState("");

  const acRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<HTMLDivElement>(null);
  const convIdRef = useRef<string | null>(null);
  const messagesRef = useRef<ChatMsg[]>([]);
  const jumpBottomRef = useRef(false);
  const webOnRef = useRef(false);
  const baseDirRef = useRef("");
  const cfRunRef = useRef("");
  const cfAwaitRef = useRef(false);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);
  useEffect(() => {
    webOnRef.current = webOn;
  }, [webOn]);
  useEffect(() => {
    cfAwaitRef.current = !!cfAwait;
  }, [cfAwait]);
  useEffect(() => {
    getWorkspace()
      .then((w) => (baseDirRef.current = w.cwd || ""))
      .catch(() => void 0);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        getProviders().then(setPs).catch(() => void 0);
        const list = await listConversations();
        setConvList(list);
        if (list.length) await loadConv(list[0].id);
      } catch {
        /* ignore */
      } finally {
        setInitLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    const el = msgsRef.current;
    if (!el) return;
    if (jumpBottomRef.current) {
      el.scrollTop = el.scrollHeight;
      jumpBottomRef.current = false;
      return;
    }
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 140)
      el.scrollTop = el.scrollHeight;
  }, [messages]);

  const activeProv =
    ps?.providers.find((p) => p.id === ps.active.provider_id) || null;

  async function chooseProvider(pid: string) {
    const prov = ps?.providers.find((p) => p.id === pid);
    if (!prov || !ps) return;
    const label = prov.presets[0]?.label ?? "";
    await setActive(pid, label);
    setPs({ ...ps, active: { provider_id: pid, preset_label: label } });
  }

  async function choosePreset(label: string) {
    if (!ps) return;
    await setActive(ps.active.provider_id, label);
    setPs({ ...ps, active: { ...ps.active, preset_label: label } });
  }

  function persistNow(msgs: ChatMsg[]) {
    const id = convIdRef.current;
    if (!id || !msgs.length) return;
    const title =
      msgs.find((m) => m.role === "user")?.content.slice(0, 40) || "新对话";
    saveConversation(id, title, msgs, webOnRef.current, true)
      .then(refreshList)
      .catch(() => void 0);
  }

  function deleteMsg(i: number) {
    if (streaming) return;
    setMessages((prev) => {
      const c = [...prev];
      // 连带删掉紧随其后的那条 AI 回复
      const drop = c[i + 1]?.role === "assistant" ? 2 : 1;
      c.splice(i, drop);
      persistNow(c);
      return c;
    });
  }

  function editMsg(i: number) {
    if (streaming) return;
    setMessages((prev) => {
      const c = prev.slice(0, i); // 截断该消息及其后
      setInput(prev[i].content); // 内容回填输入框，改完重发
      persistNow(c);
      return c;
    });
  }

  async function refreshList() {
    try {
      setConvList(await listConversations());
    } catch {
      /* ignore */
    }
  }

  async function loadConv(id: string) {
    try {
      const c = await getConversation(id);
      convIdRef.current = c.id;
      setConvId(c.id);
      setWebOn(!!c.web); // 该会话各自的联网开关
      // 文件模式现在总是开启,不再从会话恢复
      jumpBottomRef.current = true;
      setMessages(c.messages);
    } catch {
      /* ignore */
    }
  }

  function newChat() {
    if (streaming) return;
    convIdRef.current = null;
    setConvId(null);
    setMessages([]);
    setWebOn(false); // 新会话默认关联网
    // 文件模式始终开启
    setCfAwait(null);
  }

  function _persistFlags(web: boolean, file: boolean) {
    const id = convIdRef.current;
    if (!id || !messagesRef.current.length) return;
    const title =
      messagesRef.current.find((m) => m.role === "user")?.content.slice(
        0,
        40
      ) || "新对话";
    saveConversation(id, title, messagesRef.current, web, file).catch(
      () => void 0
    );
  }

  function toggleWeb() {
    const next = !webOnRef.current;
    setWebOn(next);
    _persistFlags(next, true); // 文件总开,只持久化 web 变化
  }

  async function deleteCurrent() {
    const id = convIdRef.current;
    if (!id) return;
    await deleteConversation(id);
    newChat();
    refreshList();
  }

  async function persist(msgs: ChatMsg[]) {
    if (!msgs.length) return;
    let id = convIdRef.current;
    if (!id) {
      id = (await createConversation()).id;
      convIdRef.current = id;
      setConvId(id);
    }
    const title =
      msgs.find((m) => m.role === "user")?.content.slice(0, 40) || "新对话";
    await saveConversation(id, title, msgs, webOnRef.current, true);
    refreshList();
  }

  function cfOnEvent(e: AgentEvent) {
    if (e.type === "run") {
      cfRunRef.current = e.run_id || "";
      return;
    }
    if (e.type === "confirm") {
      setCfAwait(e);
      setEditArgs(JSON.stringify(e.args ?? {}, null, 2));
      setStreaming(false);
      return;
    }
    if (e.type === "tool" || e.type === "result") {
      setMessages((prev) => {
        const c = [...prev];
        const last = c[c.length - 1];
        c[c.length - 1] = {
          ...last,
          events: [...(last.events || []), e],
        };
        return c;
      });
      return;
    }
    if (e.type === "answer") {
      setMessages((prev) => {
        const c = [...prev];
        c[c.length - 1] = { ...c[c.length - 1], content: e.content || "" };
        return c;
      });
      return;
    }
    if (e.type === "error") {
      setMessages((prev) => {
        const c = [...prev];
        const last = c[c.length - 1];
        c[c.length - 1] = {
          ...last,
          content: last.content + `\n\n> ⚠️ ${e.error}`,
        };
        return c;
      });
    }
  }

  function cfStart(history: ChatMsg[], mode: "file" | "settings") {
    const plain = history.map((m) => ({
      role: m.role,
      content: m.content,
    }));
    acRef.current = streamSSE(
      "/api/chatfs/start",
      { messages: plain, base: baseDirRef.current, mode },
      cfOnEvent,
      () => {
        setStreaming(false);
        if (!cfAwaitRef.current) persist(messagesRef.current);
      }
    );
  }

  function cfRespond(approve: boolean) {
    let ea: unknown;
    if (approve && editArgs.trim()) {
      try {
        ea = JSON.parse(editArgs);
      } catch {
        alert("参数不是合法 JSON");
        return;
      }
    }
    setCfAwait(null);
    setStreaming(true);
    acRef.current = streamSSE(
      "/api/chatfs/respond",
      { run_id: cfRunRef.current, approve, edited_args: ea },
      cfOnEvent,
      () => {
        setStreaming(false);
        if (!cfAwaitRef.current) persist(messagesRef.current);
      }
    );
  }

  function send() {
    const text = input.trim();
    if (!text || streaming || cfAwait) return;
    const history: ChatMsg[] = [...messages, { role: "user", content: text }];
    setMessages([...history, { role: "assistant", content: "", events: [] }]);
    setInput("");
    setStreaming(true);
    // 文件工具永远可用——所有对话走文件模式工具循环
    // (cfStart 内部会按 webOn 决定是否走智能联网)
    cfStart(history, "file");
  }

  function stop() {
    acRef.current?.abort();
    setStreaming(false);
    persist(messagesRef.current);
  }

  return (
    <div className="page chat">
      <div className="chat-head">
        <h1>对话</h1>
        <div className="chat-tools">
          <button onClick={newChat} title="开始新对话">
            ＋ 新对话
          </button>
          <select
            value={convId ?? ""}
            onChange={(e) => e.target.value && loadConv(e.target.value)}
          >
            <option value="">历史对话…</option>
            {convList.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
          {convId && (
            <button className="danger" onClick={deleteCurrent} title="删除当前对话">
              删除
            </button>
          )}
          <button
            className={"cfg-toggle" + (webOn ? " on" : "")}
            onClick={toggleWeb}
            title="开启后云端模型自决何时联网搜索(状态随该会话保留)"
          >
            🌐 联网{webOn ? "·开" : "·关"}
          </button>
          {ps && ps.providers.length > 0 ? (
            <>
              <select
                title="选择 API"
                value={ps.active.provider_id}
                onChange={(e) => chooseProvider(e.target.value)}
              >
                {ps.providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.api_key_set ? "" : "（无 key）"}
                  </option>
                ))}
              </select>
              <select
                title="模型 / 模式"
                value={ps.active.preset_label}
                onChange={(e) => choosePreset(e.target.value)}
              >
                {(activeProv?.presets ?? []).map((pr) => (
                  <option key={pr.label} value={pr.label}>
                    {pr.label}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <span className="cfg-msg">去「API 管理」加一个 API →</span>
          )}
        </div>
      </div>

      <div className="msgs" ref={msgsRef}>
        {initLoading && (
          <div className="loading-row">
            <span className="spinner" />
            <span>加载对话中…</span>
          </div>
        )}
        {messages.length === 0 && (
          <div className="empty">
            发条消息试试。Markdown / 代码高亮 / 图片 / Mermaid 图表 /
            视频音频都能渲染。
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={"msg " + m.role}>
            <div className="role">{m.role === "user" ? "你" : "AI"}</div>
            <div className="bubble">
              {m.role === "assistant" ? (
                <>
                  {m.events?.map((e, k) =>
                    e.type === "tool" ? (
                      <div key={k} className="ev ev-tool">
                        ▶ {e.name}
                        <pre>{JSON.stringify(e.args)}</pre>
                      </div>
                    ) : (
                      <div
                        key={k}
                        className={
                          "ev ev-res" +
                          ((e.result as { error?: string })?.error
                            ? " err"
                            : "")
                        }
                      >
                        {(e.result as { error?: string })?.error
                          ? "✖ "
                          : "✓ "}
                        {e.name}
                        <pre>
                          {JSON.stringify(e.result).slice(0, 2000)}
                        </pre>
                      </div>
                    )
                  )}
                  {m.route && (
                    <div
                      className="route-badge"
                      title={m.route.reason || ""}
                    >
                      {m.route.mode === "local"
                        ? "🧠 "
                        : m.route.mode === "cloud"
                        ? "🧭 "
                        : "▸ "}
                      {m.route.name}
                      {m.route.mode === "cloud" && " (自动路由)"}
                    </div>
                  )}
                  {typeof m.web === "number" && (
                    <div className="route-badge">
                      🌐 已联网搜索 {m.web} 条
                    </div>
                  )}
                  {m.reasoning && (
                    <details
                      className="think"
                      open={
                        streaming &&
                        i === messages.length - 1 &&
                        !m.content
                      }
                    >
                      <summary>💭 思考过程</summary>
                      <div className="think-body">{m.reasoning}</div>
                    </details>
                  )}
                  <Markdown
                    text={m.content || (m.reasoning ? "" : "…")}
                    live={streaming && i === messages.length - 1}
                  />
                </>
              ) : (
                <>
                  <div className="user-text">{m.content}</div>
                  {!streaming && (
                    <div className="msg-actions">
                      <button onClick={() => editMsg(i)}>编辑</button>
                      <button onClick={() => deleteMsg(i)}>删除</button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      {cfAwait && (
        <div className="confirm-box">
          <div className="confirm-title">
            ⚠️ 文件高危操作待确认：<b>{cfAwait.tool}</b>（可改参数 JSON）
          </div>
          <textarea
            className="sys-area"
            value={editArgs}
            onChange={(e) => setEditArgs(e.target.value)}
          />
          <div className="cfg-actions">
            <button onClick={() => cfRespond(true)}>批准并执行</button>
            <button className="danger" onClick={() => cfRespond(false)}>
              拒绝
            </button>
          </div>
        </div>
      )}

      <div className="composer">
        <textarea
          value={input}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        {streaming ? (
          <button className="stop" onClick={stop}>
            停止
          </button>
        ) : (
          <button onClick={send} disabled={!!cfAwait}>
            发送
          </button>
        )}
      </div>
    </div>
  );
}
