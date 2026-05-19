// 后端基址：开发(Vite 跨端口) 或 打包(file:// 加载) → 绝对地址打后端；
// 被后端同源(http)托管(浏览器/局域网) → 空串走相对路径。
const DEV_BACKEND = "http://127.0.0.1:8756";
export const backendBase =
  import.meta.env.DEV ||
  (typeof location !== "undefined" &&
    !location.protocol.startsWith("http"))
    ? DEV_BACKEND
    : "";

export interface Health {
  status: string;
  version: string;
}

export interface WhoAmI {
  trust: "local" | "remote";
  client_host: string;
  permissions: Record<string, boolean>;
}

export interface Preset {
  label: string;
  model: string;
  extra_body: Record<string, unknown>;
}

export interface ProviderInfo {
  id: string;
  name: string;
  format: string;
  base_url: string;
  api_key_set: boolean;
  capability: string;
  presets: Preset[];
}

export interface ActiveSel {
  provider_id: string;
  preset_label: string;
}

export interface ProvidersState {
  providers: ProviderInfo[];
  active: ActiveSel;
}

export interface RouteMeta {
  mode: "manual" | "cloud" | "local";
  name: string;
  reason?: string;
}

export interface ChatMsg {
  role: "user" | "assistant" | "system";
  content: string;
  reasoning?: string; // 思考过程（思考模型）
  route?: RouteMeta; // 本次由谁回答（路由结果）
  web?: number; // 本次联网搜索了几条
  events?: AgentEvent[]; // 文件模式：本轮工具/结果流
}

export interface BrainCfg {
  auto_route: boolean;
  local_answer: boolean;
  summary: boolean;
  summary_threshold: number;
}
export interface OllamaCfg {
  base_url: string;
  model: string;
}
export interface OllamaStatus {
  reachable: boolean;
  models: string[];
  model: string;
  config: OllamaCfg;
}

export interface ConvSummary {
  id: string;
  title: string;
  updated: number;
}
export interface Conversation extends ConvSummary {
  messages: ChatMsg[];
  web?: boolean;
  file?: boolean;
}

export type ThemeMode = "dark" | "light" | "system";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(backendBase + path, { credentials: "include" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

async function sendJSON<T>(
  path: string,
  method: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(backendBase + path, {
    method,
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export const getHealth = () => getJSON<Health>("/api/health");
export const getWhoAmI = () => getJSON<WhoAmI>("/api/whoami");

export const getProviders = () => getJSON<ProvidersState>("/api/providers");
export const upsertProvider = (patch: Partial<ProviderInfo> & {
  api_key?: string;
}) => sendJSON<ProviderInfo>("/api/providers", "POST", patch);
export const deleteProvider = (id: string) =>
  sendJSON<{ ok: boolean }>(`/api/providers/${id}`, "DELETE");
export const setActive = (provider_id: string, preset_label: string) =>
  sendJSON<ActiveSel>("/api/active", "POST", { provider_id, preset_label });

export interface UsageRow {
  name: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}
export interface UsageState {
  rows: UsageRow[];
  totals: Omit<UsageRow, "name">;
  updated: string;
}
export const getUsage = () => getJSON<UsageState>("/api/usage");
export const resetUsage = () =>
  sendJSON<UsageState>("/api/usage/reset", "POST");

export interface ProviderTest {
  ok: boolean;
  ms?: number;
  model?: string;
  error?: string;
}
export const testProvider = (provider_id: string, preset_label: string) =>
  sendJSON<ProviderTest>("/api/providers/test", "POST", {
    provider_id,
    preset_label,
  });

export const getTheme = () => getJSON<{ theme: ThemeMode }>("/api/theme");
export const setTheme = (theme: ThemeMode) =>
  sendJSON<{ theme: ThemeMode }>("/api/theme", "POST", { theme });

export type ConfirmLevel = "all" | "risky" | "none";
export const getConfirmLevel = () =>
  getJSON<{ confirm_level: ConfirmLevel }>("/api/confirm_level");
export const setConfirmLevel = (confirm_level: ConfirmLevel) =>
  sendJSON<{ confirm_level: ConfirmLevel }>(
    "/api/confirm_level",
    "POST",
    { confirm_level },
  );

export interface SkillsStatus {
  enabled: boolean;
  cloned: boolean;
  count: number;
  skills: string[];
}
export const getSkills = () => getJSON<SkillsStatus>("/api/skills");
export const setSkills = (enabled: boolean) =>
  sendJSON<SkillsStatus>("/api/skills", "POST", { enabled });
export const updateSkills = () =>
  sendJSON<SkillsStatus>("/api/skills/update", "POST");

export interface GithubStatus {
  has_token: boolean;
  username: string;
  project_root: string;
  dev: boolean; // 打包分发版=false → 前端隐藏 GitHub 整组
}
export interface GithubSuspects {
  temp: string[];
  big: string[];
  too_many: boolean;
  reasons: string[];
}
export interface GithubPreview {
  path: string;
  gitignore_added: string[];
  forced_excludes: string[];
  will_upload: string[];
  will_count: number;
  suspects?: GithubSuspects;
}
export interface GithubUploadResult {
  ok: boolean;
  repo_url?: string;
  created?: boolean;
  needs_confirm?: boolean;
  suspects?: GithubSuspects;
  will_count?: number;
  message?: string;
}
export const getGithub = () => getJSON<GithubStatus>("/api/github");
export const saveGithub = (p: { token?: string; username?: string }) =>
  sendJSON<GithubStatus>("/api/github", "POST", p);
export const githubPreview = (path: string) =>
  sendJSON<GithubPreview>("/api/github/preview", "POST", { path });
export const githubGenDoc = (path: string) =>
  sendJSON<{ doc: string }>("/api/github/gen-doc", "POST", { path });
export const githubSaveDoc = (path: string, content: string) =>
  sendJSON<{ ok: boolean; file: string }>(
    "/api/github/save-doc",
    "POST",
    { path, content }
  );
export const githubUpload = (
  path: string,
  repo: string,
  isPrivate: boolean,
  confirm = false
) =>
  sendJSON<GithubUploadResult>("/api/github/upload", "POST", {
    path,
    repo,
    private: isPrivate,
    confirm,
  });

export const getSystemPrompt = () =>
  getJSON<{ system_prompt: string }>("/api/system_prompt");
export const setSystemPrompt = (system_prompt: string) =>
  sendJSON<{ system_prompt: string }>("/api/system_prompt", "POST", {
    system_prompt,
  });

export interface WorkspaceCfg {
  allowed_roots: string[];
  cwd: string;
  test_cmd: string;
  cwd_is_git?: boolean;
}
export const getWorkspace = () => getJSON<WorkspaceCfg>("/api/workspace");
export const saveWorkspace = (patch: Partial<WorkspaceCfg>) =>
  sendJSON<WorkspaceCfg>("/api/workspace", "POST", patch);
export const gitInitWorkspace = (path: string) =>
  sendJSON<{ initialized?: boolean; already_git?: boolean }>(
    "/api/workspace/git-init",
    "POST",
    { path }
  );
export interface AgentSessionSummary {
  id: string;
  title: string;
  updated: number;
  cwd: string;
}
export interface AgentSessionFull extends AgentSessionSummary {
  checkpoint: string | null;
  transcript: AgentEvent[];
  status: string;
}
export const listAgentSessions = () =>
  getJSON<AgentSessionSummary[]>("/api/agent/sessions");
export const getAgentSession = (id: string) =>
  getJSON<AgentSessionFull>(`/api/agent/sessions/${id}`);
export const deleteAgentSession = (id: string) =>
  sendJSON<{ ok: boolean }>(`/api/agent/sessions/${id}`, "DELETE");

export const agentRollback = (run_id: string) =>
  sendJSON<{ rolled_back_to?: string; error?: string }>(
    "/api/agent/rollback",
    "POST",
    { run_id }
  );

export interface AgentEvent {
  type:
    | "user"
    | "run"
    | "checkpoint"
    | "tool"
    | "result"
    | "confirm"
    | "answer"
    | "done"
    | "error";
  run_id?: string;
  commit?: string;
  name?: string;
  tool?: string;
  args?: Record<string, unknown>;
  call_id?: string;
  result?: unknown;
  content?: string;
  error?: string;
}

// 通用 SSE POST：逐事件回调，返回 AbortController
export function streamSSE(
  path: string,
  body: unknown,
  onEvent: (e: AgentEvent) => void,
  onClose: () => void
): AbortController {
  const ac = new AbortController();
  (async () => {
    try {
      const res = await fetch(backendBase + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) {
        onEvent({ type: "error", error: `请求失败 ${res.status}` });
        onClose();
        return;
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          onEvent(JSON.parse(line.slice(5).trim()) as AgentEvent);
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        onEvent({ type: "error", error: (e as Error).message });
    } finally {
      onClose();
    }
  })();
  return ac;
}

export const getOllamaStatus = () =>
  getJSON<OllamaStatus>("/api/ollama/status");
export const getBrain = () =>
  getJSON<{ brain: BrainCfg; ollama: OllamaCfg }>("/api/brain");
export const saveBrain = (body: {
  brain?: Partial<BrainCfg>;
  ollama?: Partial<OllamaCfg>;
}) => sendJSON<{ brain: BrainCfg; ollama: OllamaCfg }>(
  "/api/brain",
  "POST",
  body
);

export const listConversations = () =>
  getJSON<ConvSummary[]>("/api/conversations");
export const getConversation = (id: string) =>
  getJSON<Conversation>(`/api/conversations/${id}`);
export const createConversation = () =>
  sendJSON<{ id: string }>("/api/conversations", "POST");
export const saveConversation = (
  id: string,
  title: string,
  messages: ChatMsg[],
  web = false,
  file = false
) =>
  sendJSON<Conversation>(`/api/conversations/${id}`, "PUT", {
    title,
    messages,
    web,
    file,
  });
export const deleteConversation = (id: string) =>
  sendJSON<{ ok: boolean }>(`/api/conversations/${id}`, "DELETE");

// 流式对话
export function streamChat(
  messages: ChatMsg[],
  cb: {
    onDelta: (s: string) => void;
    onReasoning: (s: string) => void;
    onRoute: (r: RouteMeta) => void;
    onWeb: (n: number) => void;
    onDone: () => void;
    onError: (msg: string) => void;
  },
  web = false
): AbortController {
  const ac = new AbortController();
  (async () => {
    try {
      const res = await fetch(backendBase + "/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ messages, web }),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) {
        cb.onError(`请求失败 ${res.status}：${await res.text()}`);
        return;
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          const obj = JSON.parse(line.slice(5).trim());
          if (obj.error) {
            cb.onError(obj.error);
            return;
          }
          if (obj.done) {
            cb.onDone();
            return;
          }
          if (obj.route) cb.onRoute(obj.route);
          if (typeof obj.web === "number") cb.onWeb(obj.web);
          if (obj.reasoning) cb.onReasoning(obj.reasoning);
          if (obj.delta) cb.onDelta(obj.delta);
        }
      }
      cb.onDone();
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        cb.onError(`网络错误：${(e as Error).message}`);
    }
  })();
  return ac;
}
