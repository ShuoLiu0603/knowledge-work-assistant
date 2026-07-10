// re-exports for backward-compatible imports from a single entry
export { ApiError, apiRequest, streamConversationMessage } from "./client";
export type { RequestOptions } from "./client";

export * from "./types";

// auth
export { register, login, fetchMe, refreshAccessToken, logout } from "./auth";

// conversations
export { listConversations, createConversation, listMessages, deleteConversation } from "./conversations";

// knowledge bases
export { listKnowledgeBases, createKnowledgeBase, getKnowledgeBase, updateKnowledgeBase, deleteKnowledgeBase } from "./knowledgeBases";

// documents
export { listDocuments, uploadDocument, deleteDocument, listDocumentChunks } from "./documents";

// memories
export {
  listUserMemories,
  createUserMemory,
  updateUserMemory,
  deleteUserMemory,
  approveUserMemory,
  rejectUserMemory,
  restoreUserMemory,
  purgeUserMemory,
  exportUserMemoryData,
  listUserMemoryUpdateJobs,
  retryUserMemoryUpdateJob,
} from "./memories";

// admin
export {
  fetchAdminMetrics,
  listAdminUsers,
  listAuditLogs,
  listExternalCleanupJobs,
  retryExternalCleanupJob,
  runOperationalRetention,
  updateAdminUser,
} from "./admin";

// departments
export { listDepartments, createDepartment } from "./departments";

// agents / logs / qa
export { listRetrievalLogs, listAgentRuns, createAgentRun, listLlmCallLogs, askKnowledgeBase } from "./agents";
