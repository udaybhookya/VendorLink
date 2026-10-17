/* Generated from vl_core.schemas by `npm run gen:types`. Do not edit. */

export interface A2AEnvelope {
  trace_id: string;
  /**
   * Unique id; also bound into the request signature
   */
  jti: string;
  skill: string;
  from_agent: string;
  to_agent: string;
  on_behalf_of: string;
  sent_at: string;
  body: {
    [k: string]: unknown;
  };
}

export interface Ack {
  ok?: boolean;
  detail?: string;
}

export interface ActivateRequest {
  preset?: string | null;
  refs?: ConfigRefs | null;
}

export interface ConfigRefs {
  business_profile: ConfigRef;
  retailer_profile: ConfigRef;
  country_pack: ConfigRef;
  retailer_policy: ConfigRef;
  /**
   * None = profile's set
   */
  workflows?: ConfigRef | null;
}

export interface ConfigRef {
  id: string;
  /**
   * None = latest
   */
  version?: number | null;
}

export interface ActiveConfig {
  retailer_id: string;
  refs: ConfigRefs;
  business_profile: BusinessProfile;
  retailer_profile: RetailerProfile;
  country_pack: CountryPack;
  retailer_policy: RetailerPolicy;
  workflows: WorkflowSet;
  specialists: string[];
  config_hash: string;
  activated_at: string;
}
/**
 * Pinned to concrete versions
 */

export interface BusinessProfile {
  id: string;
  version: number;
  name: {
    [k: string]: string;
  };
  attributes?: AttributeSpec[];
  units: string[];
  grading_rules?: GradingRules;
  /**
   * DocSpec roles; the Country Pack names the documents
   */
  required_docs?: string[];
  /**
   * Agent ids to load
   */
  specialists?: string[];
}

export interface AttributeSpec {
  key: string;
  label: {
    [k: string]: string;
  };
  type: "str" | "int" | "decimal" | "bool" | "enum";
  required?: boolean;
  options?: string[];
}

export interface GradingRules {
  scale?: string[];
  params?: {
    [k: string]: unknown;
  };
}

export interface RetailerProfile {
  id: string;
  version: number;
  retailer_type: string;
  ordering_mode: "auto_replenish" | "manual" | "standing_order";
  categories?: Category[];
  quality_thresholds?: QualityThresholds;
  term_templates?: TermTemplate[];
  /**
   * Agent ids to load
   */
  specialists?: string[];
  workflow_set?: string;
  /**
   * Product claims flagged for review, e.g. organic
   */
  claims_requiring_proof?: string[];
}

export interface Category {
  id: string;
  name: {
    [k: string]: string;
  };
  units?: string[];
}

export interface QualityThresholds {
  min_grade?: string | null;
  min_shelf_life_days?: number | null;
  max_reject_fraction?: number | null;
}

export interface TermTemplate {
  id: string;
  payment_days: number;
  description?: {
    [k: string]: string;
  };
}

export interface CountryPack {
  code: string;
  version: number;
  name: {
    [k: string]: string;
  };
  /**
   * @minItems 1
   */
  languages: [string, ...string[]];
  default_language: string;
  currency: string;
  timezone: string;
  local_units?: string[];
  id_docs?: DocSpec[];
  food_safety_regs?: DocSpec[];
  consent_wording?: {
    [k: string]: string;
  };
  /**
   * Label field → words that introduce it on a label; `halal` → halal marks
   */
  label_keywords?: {
    [k: string]: string[];
  };
}
/**
 * An identity document or food-safety registration, with its number format.
 */

export interface DocSpec {
  id: string;
  /**
   * Generic role a Business Profile requires, e.g. 'identity'
   */
  role: string;
  name: {
    [k: string]: string;
  };
  regex: string;
  /**
   * Mock registry key, e.g. 'fssai'
   */
  registry?: string | null;
}

export interface RetailerPolicy {
  id: string;
  version: number;
  /**
   * In the Country Pack currency
   */
  auto_order_max_value: string;
  /**
   * Max relative change in order value a counter may make and still auto-accept
   */
  auto_accept_counter_max_delta: number;
  new_vendor_needs_human?: boolean;
  listing_publish_min_confidence?: number;
}

export interface WorkflowSet {
  id: string;
  version: number;
  workflows: Workflow[];
}

export interface Workflow {
  job_type: string;
  steps: WorkflowStep[];
}

export interface WorkflowStep {
  /**
   * Capability or wildcard, e.g. 'grade.*'
   */
  capability: string;
  /**
   * Skip when no loaded specialist offers it
   */
  optional?: boolean;
}

export interface ActivityItem {
  ts: string;
  action: string;
  text: string;
  outcome: string;
  actor: string;
}

export interface AgentCard {
  agent_id: string;
  name: string;
  kind: "core" | "specialist" | "channel";
  /**
   * Which tenant may host it in-process
   */
  tenant: "vendor" | "retailer" | "any";
  description?: string;
  version?: string;
  /**
   * @minItems 1
   */
  skills: [SkillSpec, ...SkillSpec[]];
  permissions?: string[];
  transport?: "local" | "a2a";
  /**
   * 'module:AgentClass' for local
   */
  entrypoint?: string | null;
  /**
   * Base URL for a2a
   */
  endpoint?: string | null;
}

export interface SkillSpec {
  /**
   * e.g. grade.produce, guardian.evaluate
   */
  capability: string;
  /**
   * Name of a vl_core.schemas model
   */
  input_schema: string;
  /**
   * Name of a vl_core.schemas model
   */
  output_schema: string;
  description?: string;
  /**
   * Callable in-process only; never exposed by a standalone host or over A2A
   */
  internal?: boolean;
}

export interface AgentCredential {
  agent_id: string;
  party_type: "vendor" | "retailer" | "platform";
  kind?: "party" | "core" | "specialist" | "channel" | "host";
  /**
   * Vendor or retailer id the agent acts for
   */
  on_behalf_of: string;
  display_name?: string;
  /**
   * Deployable running it in-process
   */
  host?: string | null;
  /**
   * Raw Ed25519 public key, base64url (host's key if hosted)
   */
  public_key: string;
  key_fingerprint?: string;
  /**
   * A2A base URL the gateway forwards to
   */
  endpoint?: string | null;
  /**
   * Allowed skills/capabilities; empty = policy defaults
   */
  permissions?: string[];
  rate_limit_per_min?: number;
  status?: "active" | "suspended" | "quarantined" | "revoked";
  status_reason?: string;
  issued_at: string;
  revoked_at?: string | null;
  last_seen?: string | null;
  key_rotation_requested?: boolean;
  /**
   * Tokens issued before this are void (end session)
   */
  sessions_valid_after?: string | null;
}

export interface AgentEntry {
  id: string;
  /**
   * Path to agent.json, relative to the repository root
   */
  card: string;
  enabled?: boolean;
  /**
   * Deployables that run it in-process, e.g. vendor-svc, retailer-svc
   */
  hosts?: string[];
  /**
   * Overrides the card
   */
  transport?: ("local" | "a2a") | null;
  /**
   * Overrides the card; ${ENV} allowed
   */
  endpoint?: string | null;
  /**
   * Agent's own settings
   */
  settings?: {
    [k: string]: unknown;
  };
}

export interface AgentEvent {
  id: string;
  ts: string;
  type:
    | "call"
    | "decision"
    | "login"
    | "permission_check"
    | "screening"
    | "control"
    | "security"
    | "audit"
    | "ask"
    | "answer"
    | "notify";
  agent_id: string;
  on_behalf_of?: string;
  /**
   * Capability, skill, audit action or control verb
   */
  action: string;
  target?: string;
  outcome?: "ok" | "denied" | "error";
  latency_ms?: number | null;
  trace_id?: string;
  span_id?: string;
  /**
   * Person, for control actions
   */
  actor?: string | null;
  attrs?: {
    [k: string]: string | number | boolean;
  };
}

export interface AgentMetrics {
  calls?: number;
  errors?: number;
  denied?: number;
  success_rate?: number;
  p50_ms?: number | null;
  p95_ms?: number | null;
  calls_last_5m?: number;
  needs_review_rate?: number | null;
  decisions?: {
    [k: string]: number;
  };
}

export interface AgentObservation {
  agent: AgentCredential;
  effective_status: "active" | "suspended" | "quarantined" | "revoked";
  live: boolean;
  metrics: AgentMetrics;
  trust_score: number;
  trust_factors: TrustFactor[];
  quality_score?: number | null;
}
/**
 * The gateway's record of one agent: identity, key, permissions and status.
 *
 * Party agents (one per vendor or retailer) hold their own key. Agents hosted in-process by a
 * service (Guardian, Orchestrator, specialists) are registered by that host and act under it.
 */

export interface TrustFactor {
  name: string;
  /**
   * Points added (+) or removed (-) from 100
   */
  impact: number;
  detail?: string;
}

export interface AgentStatus {
  card: AgentCard;
  /**
   * local, a2a, or None when unavailable here
   */
  route: ("local" | "a2a") | null;
  hosts: string[];
  enabled: boolean;
}
/**
 * What an agent is and what it can do. Lives next to the agent as `agent.json`.
 */

export interface AgentStatusEntry {
  agent_id: string;
  status: "active" | "suspended" | "quarantined" | "revoked";
  /**
   * Status after tenant freezes are applied
   */
  effective: "active" | "suspended" | "quarantined" | "revoked";
  permissions: string[];
  rate_limit_per_min: number;
  key_rotation_requested: boolean;
}

export interface AgentStatusSnapshot {
  agents: AgentStatusEntry[];
  frozen: string[];
}
/**
 * What a host's registry needs to gate in-process calls.
 */

export interface AgentsConfig {
  version: number;
  agents: AgentEntry[];
}
/**
 * One agent registered on the platform.
 */

export interface AnswerIn {
  task_id: string;
  text: string;
  /**
   * Who answered: user id, email or vendor id
   */
  by: string;
  via: "inapp" | "slack" | "gmail";
}

export interface AskRequest {
  /**
   * vendor:… or retailer:…
   */
  party: string;
  request: InfoRequest;
  language?: string;
  /**
   * What it is about: order id, listing id, job id …
   */
  subject_ref: string;
  /**
   * Who continues on the answer, e.g. job:<id>, queue:<id>
   */
  resume: string;
  trace_id?: string;
  deadline_minutes?: number | null;
  /**
   * Idempotency: same id → same task
   */
  task_id?: string | null;
}
/**
 * What an agent needs from a person to continue. Emitted by specialists and jobs.
 */

export interface InfoRequest {
  /**
   * What is missing, e.g. price, qty, doc:aadhaar
   */
  field: string;
  /**
   * Role to ask: vendor, buyer, category_manager, ops
   */
  ask: string;
  kind?: "missing_info" | "approval" | "consent" | "review" | "notice";
  answer_type?: "choice" | "number" | "text" | "date";
  /**
   * i18n key for the question, e.g. hitl.q.price
   */
  question_key: string;
  params?: {
    [k: string]: string | number;
  };
  choices?: Choice[];
}

export interface Choice {
  /**
   * Machine value, e.g. approve
   */
  id: string;
  label: string;
  /**
   * Extra words that mean this choice
   */
  words?: string[];
}

export interface AuditAppend {
  action: string;
  target: string;
  payload_hash: string;
  outcome?: "ok" | "denied" | "error";
  /**
   * Selects the config_hash recorded
   */
  retailer_id?: string | null;
}

export interface AuditRecord {
  seq: number;
  ts: string;
  actor_agent_id: string;
  on_behalf_of: string;
  action: string;
  target: string;
  payload_hash: string;
  config_hash: string;
  outcome: "ok" | "denied" | "error";
  prev_hash: string;
  hash: string;
}

export interface AuditVerifyResult {
  ok: boolean;
  checked: number;
  first_broken_seq?: number | null;
}

export interface AutoAccept {
  max_value: string;
}

export interface BuildListingIn {
  active_config: ActiveConfig;
  step_in: ListingStepIn;
}

export interface ListingStepIn {
  vendor_id: string;
  language: string;
  photo_uri?: string | null;
  /**
   * What the vendor said about the item
   */
  transcript?: string;
  /**
   * Label text if already extracted
   */
  label_text?: string | null;
  /**
   * Vendor's answers so far
   */
  answers?: {
    [k: string]: string;
  };
  /**
   * Draft from the previous step
   */
  listing?: Listing | null;
  business_profile: BusinessProfile;
  retailer_profile: RetailerProfile;
  country_pack: CountryPack;
}

export interface Listing {
  id: string;
  vendor_id: string;
  sku_name: string;
  category: string;
  unit: string;
  price: string;
  currency: string;
  qty: string;
  grade?: string | null;
  shelf_life_days?: number | null;
  attributes?: {
    [k: string]: string | number | boolean;
  };
  photos?: string[];
  flags?: ListingFlag[];
  confidence?: number;
  status?: "draft" | "needs_review" | "published" | "rejected";
}

export interface ListingFlag {
  /**
   * Machine code, e.g. UNSUPPORTED_CLAIM_ORGANIC
   */
  code: string;
  detail?: string;
}

export interface BuildListingOut {
  result: ListingStepOut;
  /**
   * Agent ids, in order
   */
  ran: string[];
}

export interface ListingStepOut {
  listing: Listing | null;
  flags?: ListingFlag[];
  needs_review?: boolean;
  notes?: string;
  /**
   * What a person must tell us before this can continue
   */
  requests?: InfoRequest[];
}

export interface BusinessOption {
  id: string;
  name: {
    [k: string]: string;
  };
  units: string[];
  required_docs: string[];
}

export interface CatalogueDraft {
  sku_name: string;
  category: string;
  unit: string;
  price?: string | null;
  qty?: string | null;
  /**
   * e.g. organic, homemade
   */
  claims?: string[];
  confidence: number;
}

export interface ChannelConfig {
  id: string;
  version: number;
  note?: string;
  /**
   * MCP server name: settings come from MCP_<SERVER>_* env
   */
  server: string;
  operations: {
    [k: string]: McpOperation;
  };
}
/**
 * One channel operation expressed as an MCP tool call. Strings in `args` may use
 * {to} {text} {subject} {thread_id} {token} placeholders.
 */

export interface McpOperation {
  tool: string;
  args?: {
    [k: string]: unknown;
  };
  /**
   * our field → result path
   */
  returns?: {
    [k: string]: string;
  };
  /**
   * Path to the list of messages (read)
   */
  items?: string | null;
  /**
   * our field → item path
   */
  item?: {
    [k: string]: string;
  };
  /**
   * Skip items with this path
   */
  skip_if?: {
    [k: string]: string;
  };
}

export interface ChannelMessage {
  channel: "inapp" | "slack" | "gmail";
  /**
   * Slack channel/user id, email address, or party id for in-app
   */
  to: string;
  subject?: string;
  text: string;
  /**
   * Reply in this thread (Slack ts, mail thread)
   */
  thread_id?: string;
  /**
   * Task token, embedded so replies can be matched
   */
  token?: string;
  link?: string;
}

export interface ChannelReceipt {
  channel: "inapp" | "slack" | "gmail";
  message_id: string;
  thread_id?: string;
  simulated?: boolean;
}

export interface ChannelReplies {
  replies?: ChannelReply[];
}

export interface ChannelReply {
  message_id: string;
  text: string;
  by: string;
  at?: string | null;
}

export interface ChatReply {
  reply: string;
  language: string;
  events?: UiEvent[];
  mode: "llm" | "offline";
}

export interface UiEvent {
  type: "deal" | "rules" | "listing" | "consent_needed" | "document" | "status" | "notice";
  data?: {
    [k: string]: unknown;
  };
}

export interface ChatRequest {
  text: string;
  language?: string | null;
}

export interface ConciergeChatIn {
  vendor_id: string;
  text: string;
  language?: string | null;
}

export interface ConciergePhotoIn {
  vendor_id: string;
  data_b64: string;
  content_type?: string;
}

export interface ConfigPreset {
  id: string;
  label: string;
  refs: ConfigRefs;
}

export interface ConsentRecord {
  id: string;
  vendor_id: string;
  /**
   * sha256(canonical_json(subject)) of what was agreed
   */
  subject_hash: string;
  audio_uri?: string | null;
  transcript: string;
  language: string;
  captured_at: string;
}

export interface ConsentRequest {
  subject_hash: string;
  transcript: string;
  language: string;
  audio_b64?: string | null;
  audio_mime?: string;
}

export interface Contact {
  role: string;
  name?: string;
  slack?: string | null;
  email?: string | null;
  inapp?: boolean;
}

export interface ContactBook {
  party: string;
  note?: string;
  /**
   * Language this party's staff are written to in
   */
  language?: string;
  contacts: Contact[];
}
/**
 * Where one role at one party can be reached. Addresses may use ${ENV} placeholders.
 */

export interface ControlRequest {
  /**
   * The person taking the action
   */
  actor: string;
  reason: string;
  permissions?: string[] | null;
  rate_limit_per_min?: number | null;
}

export interface ControlResult {
  agent?: AgentCredential | null;
  frozen?: string[];
  detail?: string;
}
/**
 * The gateway's record of one agent: identity, key, permissions and status.
 *
 * Party agents (one per vendor or retailer) hold their own key. Agents hosted in-process by a
 * service (Guardian, Orchestrator, specialists) are registered by that host and act under it.
 */

export interface CounterResponse {
  order_id: string;
  accept: boolean;
  proposal: OrderProposal;
  note?: string;
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface OrderProposal {
  order_id: string;
  version: number;
  retailer_id: string;
  vendor_id: string;
  store_id: string;
  /**
   * @minItems 1
   */
  lines: [OrderLine, ...OrderLine[]];
  pickup_at: string;
  payment_days: number;
  currency: string;
  terms_version: string;
}

export interface OrderLine {
  sku: string;
  qty: string;
  unit: string;
  /**
   * Per unit
   */
  price: string;
}

export interface CounterReviewIn {
  active_config: ActiveConfig;
  original: OrderProposal;
  counter: OrderProposal;
}

export interface CounterReviewOut {
  accept: boolean;
  /**
   * Relative change in order value
   */
  delta: number;
  reason?: string;
}

export interface CountryOption {
  code: string;
  name: {
    [k: string]: string;
  };
  languages: string[];
  default_language: string;
  currency: string;
}

export interface CredentialRequest {
  agent_id: string;
  party_type: "vendor" | "retailer" | "platform";
  on_behalf_of: string;
  public_key: string;
  endpoint?: string | null;
  display_name?: string;
  kind?: "party" | "host";
}

export type DecisionStatus = "green" | "amber" | "red" | "decline";

export type ReasonCode =
  | "QTY_OVER_DAILY_MAX"
  | "PICKUP_BEFORE_WINDOW"
  | "PRICE_BELOW_FLOOR"
  | "BUYER_NOT_APPROVED"
  | "PAYMENT_TOO_LATE"
  | "VALUE_OVER_AUTO_ACCEPT"
  | "PICKUP_AFTER_WINDOW"
  | "SKU_NOT_IN_MANDATE"
  | "CURRENCY_MISMATCH"
  | "NO_STANDING_MANDATE"
  | "DAILY_MAX_REACHED";

/**
 * Vendor-side view of one order thread.
 */

export interface Deal {
  order_id: string;
  retailer_id: string;
  vendor_id: string;
  store_id: string;
  versions: OrderProposal[];
  decisions?: Decision[];
  status: "open" | "countered" | "awaiting_consent" | "signed" | "declined" | "rejected";
  vendor_sig?: string | null;
  consent_ref?: string | null;
  updated_at: string;
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface Decision {
  order_id: string;
  version: number;
  status: DecisionStatus;
  counter?: OrderProposal | null;
  reasons?: Reason[];
  /**
   * Present when the Guardian signed
   */
  vendor_sig?: string | null;
  consent_ref?: string | null;
}

export interface Reason {
  code: ReasonCode;
  params?: {
    [k: string]: string | number;
  };
}

export interface DealView {
  deal: Deal;
  /**
   * Hash of the latest version; echo it back to consent
   */
  subject_hash: string;
  readback: string;
}
/**
 * Vendor-side view of one order thread.
 */

export interface DecideIn {
  proposal: OrderProposal;
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface Delivery {
  channel: "inapp" | "slack" | "gmail";
  to: string;
  message_id?: string;
  thread_id?: string;
  sent_at: string;
  reminder?: number;
}

export interface DemandLine {
  sku: string;
  qty: number;
  unit: string;
  pickup_local: string;
}

export interface DemandResponse {
  store_id: string;
  for_date: string;
  lines: DemandLine[];
  simulated?: true;
}

export interface DirectoryEntry {
  party_id: string;
  party_type: "vendor" | "retailer";
  display_name: string;
  agent_id: string;
  status: string;
}

export interface Dispute {
  order_id: string;
  received_qty: string;
  rejected_qty: string;
  reasons?: string[];
  photos?: string[];
  timeline?: DisputeEvent[];
}

export interface DisputeEvent {
  ts: string;
  actor: string;
  note: string;
}

export interface DocCheck {
  doc_type: string;
  status: "pass" | "fail" | "needs_review";
  /**
   * Machine code, e.g. NAME_MISMATCH, LICENCE_EXPIRED
   */
  code: string;
  detail?: string;
}

export interface DocumentUpload {
  /**
   * DocSpec id from the Country Pack
   */
  doc_type: string;
  number?: string | null;
  data_b64?: string | null;
  content_type?: string;
  filename?: string;
}

export interface EvaluateIn {
  proposal: OrderProposal;
  mandate: StandingMandate | null;
  /**
   * sku → qty already signed
   */
  committed?: {
    [k: string]: string;
  };
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface StandingMandate {
  vendor_id: string;
  version: number;
  currency: string;
  timezone: string;
  /**
   * @minItems 1
   */
  skus: [SkuRule, ...SkuRule[]];
  /**
   * @minItems 1
   */
  pickup_windows: [TimeWindow, ...TimeWindow[]];
  payment_days_max: number;
  approved_buyers?: string[];
  auto_accept: AutoAccept;
  signature?: string | null;
  consent_ref?: string | null;
  signed_at?: string | null;
}

export interface SkuRule {
  sku: string;
  unit: string;
  /**
   * Minimum price per unit
   */
  price_floor: string;
  max_qty_per_day: string;
}
/**
 * Local wall-clock window in the mandate's timezone.
 */

export interface TimeWindow {
  start: string;
  end: string;
}

export interface EventBatch {
  events: AgentEvent[];
}
/**
 * Every operational fact in one shape: calls, decisions, logins, permission checks,
 * screening and control actions. The audit log stays the formal, tamper-evident record.
 */

export interface GatewayErrorBody {
  /**
   * Machine code, e.g. bad_signature, out_of_scope
   */
  error: string;
  detail?: string;
}

export interface GradeResult {
  grade: string;
  freshness: "fresh" | "ok" | "wilting" | "spoiled";
  shelf_life_days: number;
  confidence: number;
}

export interface HostedAgent {
  agent_id: string;
  kind: "core" | "specialist" | "channel";
  tenant: "vendor" | "retailer";
  on_behalf_of: string;
  /**
   * Its skills
   */
  permissions?: string[];
  display_name?: string;
}

export interface HostedAgentsRegistration {
  host: string;
  agents: HostedAgent[];
}

export interface HumanLoopSummary {
  open: number;
  answered: number;
  expired: number;
  median_minutes_to_answer: number | null;
  by_channel: {
    [k: string]: number;
  };
  by_role: {
    [k: string]: number;
  };
  recent: AgentEvent[];
}
/**
 * Every operational fact in one shape: calls, decisions, logins, permission checks,
 * screening and control actions. The audit log stays the formal, tamper-evident record.
 */

export interface HumanTask {
  id: string;
  /**
   * Short code that ties a reply to this task, e.g. VL-7KQ2
   */
  token: string;
  party: string;
  role: string;
  kind: "missing_info" | "approval" | "consent" | "review" | "notice";
  request: InfoRequest;
  /**
   * Rendered in the recipient's language
   */
  question: string;
  language: string;
  subject_ref: string;
  resume: string;
  status?: "open" | "answered" | "expired" | "cancelled";
  deliveries?: Delivery[];
  created_at: string;
  deadline?: string | null;
  escalation_level?: number;
  answer?: string | null;
  answer_value?: {
    [k: string]: unknown;
  };
  answered_by?: string | null;
  answered_via?: ("inapp" | "slack" | "gmail") | null;
  answered_at?: string | null;
  trace_id?: string;
  seen_replies?: string[];
}
/**
 * What an agent needs from a person to continue. Emitted by specialists and jobs.
 */

export interface InfoRequestMessage {
  request_id: string;
  retailer_id: string;
  vendor_id: string;
  request: InfoRequest;
  subject_ref: string;
}
/**
 * What an agent needs from a person to continue. Emitted by specialists and jobs.
 */

export interface InfoResponseMessage {
  request_id: string;
  retailer_id: string;
  vendor_id: string;
  answer: string;
  answer_value?: {
    [k: string]: unknown;
  };
  status?: "answered" | "expired" | "cancelled";
}

export interface JobRun {
  id: string;
  kind: string;
  retailer_id: string;
  status: "running" | "waiting" | "done" | "failed";
  started_at: string;
  updated_at: string;
  /**
   * Open human task ids
   */
  waiting_on?: string[];
  summary?: {
    [k: string]: unknown;
  };
  error?: string;
}

export interface KeyRotation {
  public_key: string;
}

export interface LabelFields {
  product_name?: string | null;
  ingredients?: string | null;
  allergens?: string | null;
  expiry_date?: string | null;
  net_weight?: string | null;
  /**
   * Label wording → number, e.g. {'P-IRT': '...'}
   */
  registration_numbers?: {
    [k: string]: string;
  };
  halal_logo?: boolean;
  confidence: number;
}

export interface ListingDraftView {
  result: ListingStepOut;
  readback: string;
}

export interface ListingReviewIn {
  active_config: ActiveConfig;
  listing: Listing;
  vendor_approved: boolean;
}

export interface ListingReviewOut {
  publish: boolean;
  reasons?: string[];
}

export interface ListingSubmission {
  vendor_id: string;
  language: string;
  transcript?: string;
  /**
   * JPEG/PNG, downscaled to ≤1024 px
   */
  photo_b64?: string | null;
  photo_mime?: string;
  label_text?: string | null;
  /**
   * Answers the vendor gave to earlier questions (price …)
   */
  answers?: {
    [k: string]: string;
  };
}

export interface MandateOut {
  mandate: StandingMandate | null;
}
/**
 * The vendor's rules (AP2 'intent' analogue). Signed by the Guardian with consent.
 */

export interface MandateQuery {
  vendor_id: string;
}

export type VendorStatus = "draft" | "verifying" | "pending_approval" | "active";

export interface OnboardIn {
  active_config: ActiveConfig;
  check_in: VendorCheckIn;
}

export interface VendorCheckIn {
  vendor: Vendor;
  legal_name: string;
  /**
   * DocSpec id → number
   */
  doc_numbers: {
    [k: string]: string;
  };
  business_profile: BusinessProfile;
  country_pack: CountryPack;
  listings?: Listing[];
}

export interface Vendor {
  id: string;
  display_name: string;
  legal_name?: string | null;
  business_profile: string;
  country: string;
  language: string;
  status?: VendorStatus;
  docs?: VendorDoc[];
  agent_id?: string | null;
  /**
   * Retailers she works with
   */
  retailers?: string[];
  /**
   * Optional: for nudges back to the app
   */
  email?: string | null;
  synthetic?: boolean;
}

export interface VendorDoc {
  /**
   * A DocSpec id from the Country Pack
   */
  doc_type: string;
  number?: string | null;
  uri: string;
  status?: "submitted" | "verified" | "failed";
  failure_reason?: string | null;
}

export interface OnboardOut {
  result: VendorCheckOut;
  ran: string[];
  status: "approved" | "pending_approval" | "checks_failed";
}

export interface VendorCheckOut {
  checks?: DocCheck[];
  flags?: ListingFlag[];
  passed: boolean;
  requests?: InfoRequest[];
}

export interface OrderMandate {
  order_id: string;
  final_version: number;
  terms_hash: string;
  vendor_sig: string;
  retailer_sig: string;
  consent_ref?: string | null;
  created_at: string;
}

export interface OrderMandateRequest {
  proposal: OrderProposal;
  mandate: OrderMandate;
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface OrderPlanIn {
  retailer_id: string;
  vendor_id: string;
  store_id: string;
  for_date: string;
  listings: Listing[];
  country_pack: CountryPack;
  terms_version: string;
  payment_days: number;
  demand_override?: DemandLine[] | null;
}

export interface OrderPlanOut {
  proposals: OrderProposal[];
  notes?: string;
  /**
   * Demand the vendor has no published listing for
   */
  unmatched?: DemandLine[];
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface OutboxMessage {
  id: string;
  message: ChannelMessage;
  sent_at: string;
  replies?: ChannelReply[];
}

export interface PartyConfig {
  retailer_id: string;
  config_hash: string;
  preset?: string | null;
}

export interface PlaceOrderRequest {
  vendor_id: string;
  store_id: string;
  for_date: string;
}

export interface PlanOrdersIn {
  active_config: ActiveConfig;
  plan_in: OrderPlanIn;
}

export interface PlanOrdersOut {
  plan: OrderPlanOut;
  gates: PolicyGate[];
  ran: string[];
}

export interface PolicyGate {
  order_id: string;
  auto: boolean;
  reason?: string;
}

export interface PollRequest {
  channel: "inapp" | "slack" | "gmail";
  to: string;
  thread_id?: string;
  token?: string;
  since?: string | null;
}

export interface ProfileUpdate {
  display_name?: string | null;
  legal_name?: string | null;
  /**
   * Her preferred language: the app, read-backs and questions follow it
   */
  language?: string | null;
}

export interface QualityRow {
  agent_id: string;
  calls: number;
  needs_review_rate: number | null;
  error_rate: number;
  top_flags: {
    [k: string]: number;
  };
  quality_score: number | null;
}

export interface QuestionAnswer {
  text: string;
  by?: string;
}

export interface QueueItem {
  id: string;
  retailer_id: string;
  kind: "place_order" | "handle_counter" | "listing" | "vendor";
  ref_id: string;
  reason: string;
  status?: "pending" | "approved" | "rejected";
  /**
   * The human task asking someone
   */
  task_id?: string | null;
  created_at: string;
}

export interface ReceivingReport {
  order_id: string;
  received_qty: string;
  rejected_qty: string;
  reasons?: string[];
  photos?: string[];
  received_at: string;
}

export interface RegistryCheckRequest {
  /**
   * DocSpec id, e.g. aadhaar, fssai, pirt
   */
  doc_type: string;
  number: string;
  /**
   * Name the vendor gave, to match against the registry
   */
  name: string;
}

export interface RegistryCheckResult {
  doc_type: string;
  status: "pass" | "fail";
  /**
   * OK, NOT_FOUND, NAME_MISMATCH, EXPIRED, ...
   */
  code: string;
  detail?: string;
  registered_name?: string | null;
  valid_until?: string | null;
  simulated?: true;
}

export interface ReplenishmentRunIn {
  /**
   * ISO date, or 'today' / 'tomorrow' (retailer's timezone)
   */
  for_date: string;
  stores?: string[] | null;
  vendor_id: string;
}

export interface RequiredDocs {
  roles: {
    [k: string]: DocSpec[];
  };
}
/**
 * An identity document or food-safety registration, with its number format.
 */

export interface Retailer {
  id: string;
  display_name: string;
  stores?: Store[];
  synthetic?: boolean;
}

export interface Store {
  id: string;
  name: string;
}

export interface RetailerOrder {
  order_id: string;
  retailer_id: string;
  vendor_id: string;
  store_id: string;
  versions: OrderProposal[];
  decisions?: Decision[];
  status: "queued" | "proposed" | "countered" | "awaiting_vendor" | "confirmed" | "declined" | "rejected";
  queue_reason?: string | null;
  mandate_terms_hash?: string | null;
  updated_at: string;
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface RetailerVendor {
  vendor_id: string;
  display_name: string;
  country: string;
  business_profile: string;
  status: "applied" | "checks_failed" | "pending_approval" | "approved" | "rejected";
  checks?: DocCheck[];
  updated_at: string;
}

export interface RevokeRequest {
  reason: string;
}

export interface RoutingConfig {
  id: string;
  version: number;
  rules: RoutingRule[];
}

export interface RoutingRule {
  kind: "missing_info" | "approval" | "consent" | "review" | "notice";
  role: string;
  /**
   * First step is the initial delivery
   *
   * @minItems 1
   */
  steps: [Step, ...Step[]];
  expire_after_minutes?: number | null;
  on_expire?: "cancel" | "reject" | "keep";
}

export interface Step {
  after_minutes: number;
  channels: ("inapp" | "slack" | "gmail")[];
  /**
   * Escalate to another role
   */
  role?: string | null;
}

export interface RulesDraft {
  /**
   * @minItems 1
   */
  skus: [SkuRuleDraft, ...SkuRuleDraft[]];
  /**
   * @minItems 1
   */
  pickup_windows: [TimeWindow, ...TimeWindow[]];
  payment_days_max: number;
  approved_buyers?: string[];
  auto_accept_max_value: string;
}

export interface SkuRuleDraft {
  sku: string;
  unit: string;
  price_floor: string;
  max_qty_per_day: string;
}
/**
 * Local wall-clock window in the mandate's timezone.
 */

export interface RulesView {
  mandate?: StandingMandate | null;
  draft?: StandingMandate | null;
  readback?: string;
  /**
   * Of the draft awaiting consent
   */
  subject_hash?: string | null;
}
/**
 * The vendor's rules (AP2 'intent' analogue). Signed by the Guardian with consent.
 */

export interface SafetySummary {
  security_events: {
    [k: string]: number;
  };
  screening_blocked: number;
  quarantined_subjects: string[];
  frozen: string[];
  agents_by_status: {
    [k: string]: number;
  };
}

export interface ScreenRequest {
  filename: string;
  content_type: string;
  data_b64: string;
  /**
   * e.g. vendor_document, listing_photo
   */
  purpose: string;
  /**
   * Vendor id the upload belongs to
   */
  subject: string;
}

export interface ScreenResult {
  allowed: boolean;
  sha256: string;
  findings?: string[];
  extracted_text_chars?: number;
}

export interface SecurityEvent {
  id: string;
  ts: string;
  type: "bad_token" | "bad_signature" | "out_of_scope" | "injection" | "rate";
  agent_id: string;
  session_id?: string | null;
  /**
   * Summary or hash only; never raw documents
   */
  evidence: string;
  action_taken: string;
}

export interface SignMandateIn {
  mandate: StandingMandate;
  consent: ConsentRecord;
}
/**
 * The vendor's rules (AP2 'intent' analogue). Signed by the Guardian with consent.
 */

export interface SignOrderIn {
  proposal: OrderProposal;
  consent: ConsentRecord;
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface SignOrderOut {
  vendor_sig: string;
  decision: Decision;
}

export interface SignedOrder {
  proposal: OrderProposal;
  vendor_sig: string;
  consent_ref?: string | null;
}
/**
 * One immutable version of an order. A counter is a new version (see `next_version`).
 */

export interface TaskList {
  tasks: HumanTask[];
}

export interface TaskQuery {
  party?: string | null;
  status?: ("open" | "answered" | "expired" | "cancelled") | null;
  subject_ref?: string | null;
}

export interface TaskRef {
  task_id: string;
  reason?: string;
  outcome?: "cancelled" | "answered";
}

export interface TenantFreeze {
  /**
   * vendor, retailer, or one party id
   */
  tenant: string;
  frozen: boolean;
  actor: string;
  reason: string;
  ts: string;
}

export interface TermsIn {
  retailer_id: string;
  vendor_id: string;
  retailer_profile: RetailerProfile;
  template_id?: string | null;
}

export interface TermsJobIn {
  active_config: ActiveConfig;
  retailer_id: string;
  vendor_id: string;
  template_id?: string | null;
}

export interface TermsOut {
  terms: TermsProposal;
}

export interface TermsProposal {
  terms_id: string;
  retailer_id: string;
  vendor_id: string;
  template_id: string;
  payment_days: number;
  description?: {
    [k: string]: string;
  };
}

export interface TermsResponse {
  terms_id: string;
  retailer_id: string;
  vendor_id: string;
  accepted: boolean;
  reason?: string;
  answered_at: string;
}

export interface TickIn {
  party?: string | null;
}

export interface TickOut {
  answered?: string[];
  reminded?: string[];
  escalated?: string[];
  expired?: string[];
}

export interface TokenRequest {
  /**
   * Exactly one skill, e.g. order.propose
   */
  scope: string;
  /**
   * Target agent id
   */
  aud: string;
}

export interface TokenResponse {
  token: string;
  jti: string;
  expires_at: string;
}

export interface TraceSummary {
  trace_id: string;
  started: string;
  duration_ms: number;
  agents: string[];
  actions: string[];
  outcome: "ok" | "denied" | "error";
  events: number;
}

export interface TraceView {
  summary: TraceSummary;
  events: AgentEvent[];
}

export interface TrustScoring {
  note?: string;
  /**
   * Points off while suspended
   */
  suspended?: number;
  /**
   * Points off while quarantined
   */
  quarantined?: number;
  /**
   * Per verified violation by the agent
   */
  per_violation?: number;
  max_violations?: number;
  /**
   * Per blocked call in its name
   */
  per_impersonation?: number;
  max_impersonation?: number;
  /**
   * × share of calls that failed
   */
  error_rate?: number;
  /**
   * × share of calls denied
   */
  denial_rate?: number;
  /**
   * × needs-review rate
   */
  needs_review_rate?: number;
}

export interface VendorApplication {
  vendor: Vendor;
  legal_name: string;
  /**
   * DocSpec id → number
   */
  doc_numbers: {
    [k: string]: string;
  };
}

export interface VendorDeclined {
  order_id: string;
  version: number;
  vendor_id: string;
  retailer_id: string;
  reason?: string;
}

export interface VendorInvite {
  invite_id: string;
  retailer_id: string;
  vendor_id: string;
  retailer_name?: string;
  message?: string;
  sent_at: string;
}

export interface VendorOptions {
  countries: CountryOption[];
  business_profiles: BusinessOption[];
  retailers: DirectoryEntry[];
}

export interface VendorRegistration {
  display_name: string;
  legal_name: string;
  business_profile: string;
  country: string;
  language: string;
  retailers?: string[];
  email?: string | null;
}

export interface VendorStatusUpdate {
  vendor_id: string;
  status: VendorStatus;
  checks?: DocCheck[];
  note?: string;
}

export interface VendorTerms {
  terms: TermsProposal;
  /**
   * WITHIN_RULES, PAYMENT_TOO_LATE or NO_STANDING_MANDATE
   */
  verdict: string;
  status?: "proposed" | "accepted" | "declined";
  received_at: string;
  answered_at?: string | null;
}

