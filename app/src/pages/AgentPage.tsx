import { useEffect, useRef, useState } from "react";
import {
  agentRollback,
  deleteAgentSession,
  getAgentSession,
  getProviders,
  getWorkspace,
  gitInitWorkspace,
  listAgentSessions,
  saveWorkspace,
  setActive,
  streamSSE,
  type AgentEvent,
  type AgentSessionSummary,
  type ProvidersState,
  type WhoAmI,
  type WorkspaceCfg,
} from "../api";
import Markdown from "../components/Markdown";

type Status = "idle" | "running" | "awaiting" | "done" | "error";

export default function AgentPage({ who }: { who: WhoAmI | null }) {
  const canAgent = who ? who.permissions.agent !== false : true;
  const [ws, setWs] = useState<WorkspaceCfg | null>(null);
  const [ps, setPs] = useState<ProvidersState | null>(null);
  const [sessions, setSessions] = useState<AgentSessionSummary[]>([]);
  const [task, setTask] = useState("");
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [runId, setRunId] = useState("");
  const [editArgs, setEditArgs] = useState("");
  const acRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const runIdRef = useRef("");

  function refreshSessions() {
    listAgentSessions().then(setSessions).catch(() => void 0);
  }
  useEffect(() => {
    (async () => {
      getProviders().then(setPs).catch(() => void 0);
      let cwd = "";
      try {
        const w = await getWorkspace();
        setWs(w);
        cwd = w.cwd || "";
      } catch {
        /* ignore */
      }
      try {
        const list = await listAgentSessions();
        setSessions(list);
        // 返回编程页时自动载回上次会话（优先当前工作目录的最近一个）
        if (!runIdRef.current && list.length) {
          const mine = list.filter((s) => s.cwd === cwd);
          const pick = (mine.length ? mine : list)[0];
          if (pick) loadSession(pick.id);
        }
      } catch {
        /* ignore */
      }
    })();
  }, []);

  async function loadSession(id: string) {
    try {
      const s = await getAgentSession(id);
      runIdRef.current = s.id;
      setRunId(s.id);
      setEvents(s.transcript || []);
      setStatus(s.status === "awaiting" ? "awaiting" : "done");
      const last = (s.transcript || [])[s.transcript.length - 1];
      if (s.status === "awaiting" && last?.args)
        setEditArgs(JSON.stringify(last.args, null, 2));
    } catch {
      /* ignore */
    }
  }

  async function deleteCurrent() {
    if (!runIdRef.current) return;
    if (!confirm("删除当前编程会话记录？(不影响已落盘的代码改动)")) return;
    await deleteAgentSession(runIdRef.current);
    newSession();
    refreshSessions();
  }

  const activeProv =
    ps?.providers.find((p) => p.id === ps.active.provider_id) || null;

  async function pickProvider(pid: string) {
    if (!ps) return;
    const prov = ps.providers.find((p) => p.id === pid);
    const label = prov?.presets[0]?.label ?? "";
    await setActive(pid, label);
    setPs({ ...ps, active: { provider_id: pid, preset_label: label } });
  }
  async function pickPreset(label: string) {
    if (!ps) return;
    await setActive(ps.active.provider_id, label);
    setPs({ ...ps, active: { ...ps.active, preset_label: label } });
  }
  async function pickCwd(dir: string) {
    setWs(await saveWorkspace({ cwd: dir }));
    // 该目录有历史会话→跳到最近一个；没有→开新会话
    const list = await listAgentSessions();
    setSessions(list);
    const mine = list.filter((s) => s.cwd === dir); // 已按 updated 倒序
    if (mine.length) loadSession(mine[0].id);
    else newSession();
  }
  async function initGit() {
    if (!ws?.cwd) return;
    try {
      await gitInitWorkspace(ws.cwd);
      setWs(await getWorkspace());
    } catch (e) {
      alert((e as Error).message);
    }
  }
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  function onEvent(e: AgentEvent) {
    if (e.type === "run" && e.run_id) {
      runIdRef.current = e.run_id;
      setRunId(e.run_id);
    }
    if (e.type === "confirm") {
      setStatus("awaiting");
      setEditArgs(JSON.stringify(e.args ?? {}, null, 2));
    }
    if (e.type === "done") setStatus("done");
    if (e.type === "error") setStatus("error");
    setEvents((p) => [...p, e]);
  }

  function submit() {
    const t = task.trim();
    if (!t || status === "running" || status === "awaiting") return;
    setTask("");
    const hasRun = !!runIdRef.current;
    // 不本地插 user 事件——后端 start/continue 都会推送并写入 transcript
    setStatus("running");
    const path = hasRun ? "/api/agent/continue" : "/api/agent/start";
    const body = hasRun
      ? { run_id: runIdRef.current, task: t }
      : { task: t };
    acRef.current = streamSSE(path, body, onEvent, () => {
      setStatus((s) => (s === "running" ? "idle" : s));
      refreshSessions();
    });
  }

  function newSession() {
    acRef.current?.abort();
    runIdRef.current = "";
    setRunId("");
    setEvents([]);
    setStatus("idle");
  }

  function respond(approve: boolean) {
    let ea: unknown = undefined;
    if (approve && editArgs.trim()) {
      try {
        ea = JSON.parse(editArgs);
      } catch {
        alert("编辑后的参数不是合法 JSON");
        return;
      }
    }
    setStatus("running");
    acRef.current = streamSSE(
      "/api/agent/respond",
      { run_id: runIdRef.current, approve, edited_args: ea },
      onEvent,
      () => {
        setStatus((s) => (s === "running" ? "idle" : s));
        refreshSessions();
      }
    );
  }

  async function doRollback() {
    if (!runIdRef.current) return;
    if (
      !confirm(
        "确认回滚？将 git reset --hard 到本次任务开始的检查点，" +
          "工作目录未提交的改动会丢失。"
      )
    )
      return;
    const r = await agentRollback(runIdRef.current);
    alert(r.error ? "回滚失败：" + r.error : "已回滚到检查点");
  }

  const noWs =
    !ws || !ws.allowed_roots.length || !ws.cwd;

  return (
    <div className="page chat">
      <div className="chat-head">
        <h1>编程 Agent</h1>
        <div className="chat-tools">
          <select
            title="工作目录（白名单内）"
            value={ws?.cwd ?? ""}
            onChange={(e) => pickCwd(e.target.value)}
          >
            {(ws?.allowed_roots ?? []).map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          {ws && !ws.cwd_is_git && (
            <button className="danger" onClick={initGit} title="该目录不是 git 仓库">
              初始化 git
            </button>
          )}
          {ps && (
            <>
              <select
                title="API"
                value={ps.active.provider_id}
                onChange={(e) => pickProvider(e.target.value)}
              >
                {ps.providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <select
                title="模型/预设"
                value={ps.active.preset_label}
                onChange={(e) => pickPreset(e.target.value)}
              >
                {(activeProv?.presets ?? []).map((pr) => (
                  <option key={pr.label} value={pr.label}>
                    {pr.label}
                  </option>
                ))}
              </select>
            </>
          )}
          <select
            title="历史编程会话"
            value={runId || ""}
            onChange={(e) => e.target.value && loadSession(e.target.value)}
          >
            <option value="">历史会话…</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
          <button onClick={newSession} title="清空并开新会话">
            ＋ 新会话
          </button>
          {runId && (
            <>
              <button className="danger" onClick={deleteCurrent}>
                删除会话
              </button>
              <button className="danger" onClick={doRollback}>
                回滚检查点
              </button>
            </>
          )}
        </div>
      </div>

      {!canAgent && (
        <div className="cfg-note warn">远程访问下 Agent 不可用。</div>
      )}
      {noWs && (
        <div className="placeholder warn">
          还没配置工作区。去「设置」页：添加授权根目录 + 选当前工作目录
          （须是 git 仓库）+ 可选填测试命令。
        </div>
      )}

      <div className="msgs">
        {events.map((e, i) => (
          <EventRow key={i} e={e} />
        ))}
        {status === "awaiting" && (
          <div className="confirm-box">
            <div className="confirm-title">
              ⚠️ 高危操作待确认：<b>{events[events.length - 1]?.tool}</b>
            </div>
            <textarea
              className="sys-area"
              value={editArgs}
              onChange={(e) => setEditArgs(e.target.value)}
            />
            <div className="cfg-actions">
              <button onClick={() => respond(true)}>批准并执行</button>
              <button className="danger" onClick={() => respond(false)}>
                拒绝
              </button>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="composer">
        <textarea
          value={task}
          disabled={!canAgent || noWs || status === "running"}
          placeholder="用自然语言描述编程任务，如：给 utils.py 的 parse() 加上空输入处理并跑测试"
          onChange={(e) => setTask(e.target.value)}
        />
        <button
          onClick={submit}
          disabled={
            !canAgent ||
            noWs ||
            status === "running" ||
            status === "awaiting"
          }
        >
          {status === "running"
            ? "运行中…"
            : runId
            ? "继续"
            : "开始"}
        </button>
      </div>
    </div>
  );
}

function EventRow({ e }: { e: AgentEvent }) {
  if (e.type === "user")
    return (
      <div className="ev ev-user">
        <span className="role">你</span>
        <span style={{ whiteSpace: "pre-wrap" }}>{e.content}</span>
      </div>
    );
  if (e.type === "checkpoint")
    return (
      <div className="ev ev-cp">
        🛟 已打 git 检查点 {String(e.commit).slice(0, 8)}（可随时回滚）
      </div>
    );
  if (e.type === "tool")
    return (
      <div className="ev ev-tool">
        ▶ 调用 <b>{e.name}</b>
        <pre>{JSON.stringify(e.args, null, 2)}</pre>
      </div>
    );
  if (e.type === "result") {
    const r = e.result as Record<string, unknown>;
    const err = r && (r as { error?: string }).error;
    return (
      <div className={"ev ev-res" + (err ? " err" : "")}>
        {err ? "✖ " : "✓ "}
        {e.name}
        <pre>{JSON.stringify(e.result, null, 2).slice(0, 4000)}</pre>
      </div>
    );
  }
  if (e.type === "answer")
    return (
      <div className="ev ev-ans">
        <Markdown text={e.content || ""} />
      </div>
    );
  if (e.type === "error")
    return <div className="ev ev-err">出错：{e.error}</div>;
  if (e.type === "done") return <div className="ev ev-done">— 任务结束 —</div>;
  return null;
}
