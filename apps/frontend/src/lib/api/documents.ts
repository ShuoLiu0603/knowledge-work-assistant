import { apiRequest } from "./client";
import type { DocumentItem, DocumentChunk, DocumentUploadResult } from "./types";

export async function listDocuments(token: string, kbId: string): Promise<DocumentItem[]> {
  return apiRequest<DocumentItem[]>(`/knowledge-bases/${kbId}/documents`, { token });
}

export async function uploadDocument(
  token: string,
  kbId: string,
  file: File,
  securityLevel = 1,
): Promise<DocumentUploadResult> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("security_level", String(securityLevel));
  return apiRequest<DocumentUploadResult>(`/knowledge-bases/${kbId}/documents`, {
    method: "POST",
    token,
    body: fd,
  });
}

export async function deleteDocument(token: string, documentId: string): Promise<void> {
  await apiRequest(`/documents/${documentId}`, { method: "DELETE", token });
}

export async function listDocumentChunks(token: string, documentId: string): Promise<DocumentChunk[]> {
  return apiRequest<DocumentChunk[]>(`/documents/${documentId}/chunks`, { token });
}
