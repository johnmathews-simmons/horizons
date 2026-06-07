import { apiClient } from './client'

export interface ChangeCounts {
  added: number
  removed: number
  modified: number
  moved: number
}

export interface DocumentItem {
  id: string
  jurisdiction: string
  sector: string
  lawstronaut_document_id: string
  title: string
  created_at: string
  clause_count: number
  change_counts: ChangeCounts
  previous_version_at: string | null
  current_version_at: string | null
}

export interface DocumentPage {
  items: DocumentItem[]
  total: number
  limit: number
  offset: number
}

export interface DocumentVersion {
  id: string
  version_label: string
  publication_date: string | null
  effective_date: string | null
  content_bytes: number
  created_at: string
}

export interface DocumentDetail extends DocumentItem {
  versions: DocumentVersion[]
}

export interface ClauseItem {
  id: string
  clause_uid: string
  clause_path: string
  text_content: string
  heading_text: string | null
  numbering_label: string | null
  ord: number
}

export interface ClauseBundle {
  document_id: string
  version_id: string
  version_label: string
  clauses: ClauseItem[]
}

export interface ListDocumentsParams {
  jurisdiction?: string
  sector?: string
  search?: string
  limit?: number
  offset?: number
}

export async function listDocuments(params: ListDocumentsParams = {}): Promise<DocumentPage> {
  const search: Record<string, string | number> = {}
  if (params.jurisdiction) search.jurisdiction = params.jurisdiction
  if (params.sector) search.sector = params.sector
  if (params.search) search.search = params.search
  if (params.limit !== undefined) search.limit = params.limit
  if (params.offset !== undefined) search.offset = params.offset
  const response = await apiClient.get<DocumentPage>('/v1/documents', { params: search })
  return response.data
}

export async function getDocument(documentId: string): Promise<DocumentDetail> {
  const response = await apiClient.get<DocumentDetail>(`/v1/documents/${documentId}`)
  return response.data
}

export async function getClauses(
  documentId: string,
  versionLabel: string,
): Promise<ClauseBundle> {
  const response = await apiClient.get<ClauseBundle>(
    `/v1/documents/${documentId}/versions/${versionLabel}/clauses`,
  )
  return response.data
}
