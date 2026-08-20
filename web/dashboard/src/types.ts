// Shapes returned by the Go gateway (gateway/main.go, gateway/stream.go).
// Kept hand-written rather than generated -- the gateway is a small, stable
// surface and a codegen step would be more machinery than the API warrants.

export type Kind =
  | "tool_call" | "decision" | "memory_write"
  | "error" | "workflow_start" | "workflow_end" | "observation";

export type Outcome = "success" | "failure" | "partial" | "info";

export interface Trace {
  id: string;
  ts: string;
  agent: string;
  session: string;
  kind: Kind;
  action: string;
  target: string;
  outcome: Outcome;
  duration_ms: number | null;
  payload: string; // JSON-encoded; parse on demand, shape varies per action
}

export type Suggestion = "skill" | "alert" | "config_fix";
export type FindingState = "open" | "applied" | "dismissed";

export interface Finding {
  id: string;
  created_ts: string;
  miner: string;
  confidence: number;
  title: string;
  evidence: string;
  suggestion: Suggestion;
  state: FindingState;
  payload: string; // JSON-encoded; shape is miner-specific, see wallet.ts
}

export interface MinerStat {
  miner: string;
  findings: number;
  last_finding_ts: string | null;
  avg_confidence: number | null;
  by_state?: Partial<Record<FindingState, number>>;
}

export interface ProvenanceStatus {
  valid: boolean;
  anchored: number;
  reason: string;
}

export interface ProvenanceEnvelope {
  index: number;
  trace_id: string;
  ts: string;
  action: string;
  target: string;
  outcome: string;
  payload_sha: string;
  prev_hash: string;
  hash: string;
  sig: string;
}

export interface ProvenanceChain {
  count: number;
  pubkey: string;
  anchored: number;
  chain: ProvenanceEnvelope[];
}

export interface GatewayStatus {
  status: string;
  traces: number;
  findings: number;
  pubkey: string;
  // Ops extensions (gateway/ops.go opsStatusExtras)
  uptime_secs?: number;
  auth_enabled?: boolean;
  storage?: {
    db_bytes?: number;
    wal_bytes?: number;
    oldest_ts?: string;
    newest_ts?: string;
  };
  last_requests?: RequestRecord[];
}

export interface RequestRecord {
  ts: string;
  method: string;
  path: string;
  status: number;
  ms: number;
}

// ------------------------------------------------------------- ops endpoints

export interface AgentStat {
  name: string;
  trace_count: number;
  last_seen: string;
  error_rate: number;
  kinds: Record<string, number>;
}

export interface SkillEntry {
  name: string;
  path: string;
  mtime: string;
  size: number;
}

export interface AlertResult {
  alert: string;
  action: string;
  failures: number;
  total: number;
  rate: number;
  tripped: boolean;
  state: string | null;
}

export interface StatsBucket {
  ts: string;
  traces_by_kind: Record<string, number>;
  traces_total: number;
  findings_by_state: Record<string, number>;
  mine_runs: number;
}

export interface Pick {
  rank: number;
  symbol: string;
  token_addr: string;
  score: number;
  status?: string;
  ts?: string;
  components?: Record<string, unknown>;
  gates?: Record<string, unknown>;
}

export interface PoolHealth {
  gmgn: { keys: number; cooling: number; ip_ban_until: number; ip_banned: boolean };
  proxies: { total: number; cooldown: number; live_estimate: number };
  solscan: { keys: number };
  teamorouter: { keys: number };
  ts?: number;
}

// -------------------------------------------------------- wallet miner payloads

export interface WalletActivityToken {
  token: string;
  symbol: string;
  distinct_wallets: number;
  buys: number;
  volume_usd: number;
  wallets: string[];
  smart_wallets: number;
}

export interface WalletActivityWallet {
  wallet: string;
  distinct_tokens: number;
  buys: number;
  volume_usd: number;
  tags: string[];
  best_price_change: number;
}

export interface WalletActivityPayload {
  tokens: WalletActivityToken[];
  wallets: WalletActivityWallet[];
}

export interface WalletCorrelationPayload {
  wallet_a: string;
  wallet_b: string;
  shared: string[];
}

export interface WalletAnomalyBurstPayload {
  wallet: string;
  tokens: string[];
  window_s: number;
}

export interface WalletAnomalyEverythingPayload {
  wallet: string;
  distinct_tokens: number;
}
