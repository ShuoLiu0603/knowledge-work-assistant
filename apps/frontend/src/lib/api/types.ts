/* eslint-disable @typescript-eslint/no-explicit-any */

export type User = {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  is_admin: boolean;
  security_level: number;
  department_id: string | null;
  department_name: string | null;
};

export type KnowledgeBaseVisibility = "private" | "department" | "public";
export type SearchScope = "single" | "department" | "public" | "accessible";

export type Department = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};

export type KnowledgeBase = {
  id: string;
  owner_id: string;
  department_id: string | null;
  department_name: string | null;
  name: string;
  description: string | null;
  visibility: KnowledgeBaseVisibility;
  role: "owner" | "editor" | "viewer";
  created_at: string;
  updated_at: string;
};

export type DocumentItem = {
  id: string;
  knowledge_base_id: string;
  uploader_id: string | null;
  file_name: string;
  file_ext: string;
  mime_type: string | null;
  file_size: number;
  status: "uploaded" | "parsing" | "chunking" | "embedding" | "indexed" | "failed";
  error_message: string | null;
  chunk_count: number;
  security_level: number;
  content_hash: string;
  created_at: string;
  updated_at: string;
};

export type DocumentChunk = {
  id: string;
  document_id: string;
  knowledge_base_id: string;
  chunk_index: number;
  content: string;
  qdrant_point_id: string | null;
  token_count: number;
  title_path: string | null;
  page_number: number | null;
  section_name: string | null;
  security_level: number;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type DocumentUploadResult = {
  document_id: string;
  status: string;
  job_id: string;
  security_level: number;
};

export type Citation = {
  chunk_id: string;
  document_id: string;
  knowledge_base_id: string;
  file_name: string;
  chunk_index: number;
  score: number;
  content_preview: string;
  title_path: string | null;
  page_number: number | null;
  section_name: string | null;
  security_level: number;
  rrf_score: number | null;
  retrieval_routes: string[];
};

export type RetrievalLog = {
  id: string;
  user_id: string;
  knowledge_base_id: string | null;
  scope_type: SearchScope;
  searched_knowledge_base_ids: string[];
  conversation_id: string | null;
  message_id: string | null;
  question: string;
  rewritten_query: string;
  sub_questions: unknown[];
  expanded_queries: unknown[];
  retrieval_routes: unknown[];
  candidates: unknown[];
  selected_chunks: unknown[];
  rrf_k: number;
  reranker_enabled: boolean;
  compression_chars_saved: number;
  created_at: string;
};

export type AgentTraceStep = {
  node: string;
  action: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
};

export type AgentRun = {
  id: string;
  user_id: string;
  knowledge_base_id: string | null;
  conversation_id: string | null;
  message_id: string | null;
  retrieval_log_id: string | null;
  input: string;
  intent: "rag" | "memory" | "chat" | "summary" | "writing";
  status: "running" | "completed" | "failed";
  answer: string;
  citations: Citation[];
  trace: AgentTraceStep[];
  state: Record<string, unknown>;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type MemoryKind = "preference" | "profile" | "instruction";

export type UserMemory = {
  id: string;
  user_id: string;
  content: string;
  content_hash: string;
  status: "active" | "pending" | "superseded" | "ignored" | "deleted";
  kind: MemoryKind;
  category: string;
  canonical_key: string;
  memory_layer: string;
  profile_slot: string;
  scope_type: string;
  scope_id: string;
  pinned: boolean;
  revision: number;
  expires_at: string | null;
  source_text: string;
  source_conversation_id: string | null;
  source_message_id: string | null;
  embedding_model: string;
  embedding_dimension: number;
  merge_count: number;
  touched_count: number;
  superseded_by_id: string | null;
  metadata: Record<string, unknown>;
  valid_at: string;
  invalid_at: string | null;
  created_at: string;
  updated_at: string;
  last_touched_at: string;
};

export type UserMemoryUpdateJob = {
  id: string;
  user_id: string;
  conversation_id: string | null;
  message_id: string | null;
  user_message: string;
  assistant_message: string;
  status: "queued" | "processing" | "completed" | "failed";
  attempts: number;
  actions: unknown[];
  error_message: string;
  lease_expires_at: string | null;
  dispatched_at: string | null;
  created_at: string;
  updated_at: string;
};

export type UserMemoryExport = {
  user_id: string;
  exported_at: string;
  memories: UserMemory[];
  events: unknown[];
  recall_logs: unknown[];
  update_jobs: UserMemoryUpdateJob[];
};

export type LlmCallLog = {
  id: string;
  user_id: string | null;
  conversation_id: string | null;
  agent_name: string | null;
  provider: string;
  model_name: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number | null;
  latency_ms: number | null;
  status: string;
  fallback_used: boolean;
  error_message: string | null;
  created_at: string;
};

export type AdminMetrics = {
  generated_at: string;
  scope: string;
  conversation_count: number;
  message_count: number;
  retrieval_log_count: number;
  llm_call_count: number;
  total_tokens: number;
  average_llm_latency_ms: number | null;
  fallback_call_count: number;
  feedback_count: number;
  positive_feedback_count: number;
  negative_feedback_count: number;
  positive_feedback_rate: number | null;
  average_selected_chunks: number | null;
  external_cleanup_job_count: number;
  failed_external_cleanup_job_count: number;
  queued_external_cleanup_job_count: number;
  recent_llm_errors: Record<string, unknown>[];
};

export type AdminUser = {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  is_admin: boolean;
  security_level: number;
  department_id: string | null;
  department_name: string | null;
  created_at: string;
};

export type AuditLog = {
  id: string;
  actor_user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: string;
  security_level: number | null;
  detail: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type ExternalCleanupJob = {
  id: string;
  actor_user_id: string | null;
  resource_type: string;
  resource_id: string;
  action: string;
  status: "queued" | "processing" | "completed" | "failed" | string;
  attempts: number;
  object_keys: string[];
  error_message: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type RetentionRun = {
  generated_at: string;
  dry_run: boolean;
  deleted_counts: Record<string, number>;
  cutoffs: Record<string, string | null>;
};

export type AskKnowledgeBaseResult = {
  question: string;
  answer: string;
  citations: Citation[];
  retrieval_log: RetrievalLog | null;
};

export type Conversation = {
  id: string;
  knowledge_base_id: string | null;
  knowledge_base_name: string | null;
  search_scope: SearchScope;
  search_department_id: string | null;
  target_label: string;
  title: string;
  summary: string | null;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  status: "completed" | "failed";
  memory_enabled: boolean;
  citations: Citation[];
  agent_trace: unknown[];
  token_usage: Record<string, unknown>;
  error_message: string | null;
  created_at: string;
};

export type StreamHandlers = {
  onConversation?: (conversation: Conversation) => void;
  onUserMessage?: (message: Message) => void;
  onTrace?: (trace: { node: string; status: string }) => void;
  onToken?: (token: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onRetrievalLog?: (log: RetrievalLog) => void;
  onAgentRun?: (run: AgentRun) => void;
  onAssistantMessage?: (message: Message) => void;
  onDone?: (payload: { conversation_id: string; message_id: string }) => void;
  onError?: (message: string) => void;
};
