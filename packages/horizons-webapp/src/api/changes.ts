import { apiClient } from './client'
import type { ChangeType } from '@/components/ui/change-type-pill'

export interface DiscoveryItem {
  id: number
  document_id: string
  document_version_id: string
  jurisdiction: string
  sector: string
  change_type: ChangeType
  before_clause_uid: string | null
  after_clause_uid: string | null
  before_path: string | null
  after_path: string | null
  alignment_confidence: number
  detected_at: string
  effective_date: string | null
}

export interface DiscoveryPage {
  items: DiscoveryItem[]
  next_cursor: string | null
  has_more: boolean
}

export interface DiscoveryParams {
  cursor?: string | null
  limit?: number
}

export async function fetchDiscovery(params: DiscoveryParams = {}): Promise<DiscoveryPage> {
  const search: Record<string, string | number> = { scope: 'corpus' }
  if (params.limit !== undefined) search.limit = params.limit
  if (params.cursor) search.cursor = params.cursor
  const response = await apiClient.get<DiscoveryPage>('/v1/discovery', { params: search })
  return response.data
}

export interface DifferentialItem extends DiscoveryItem {
  before_text: string | null
  after_text: string | null
}

export async function fetchDifferentialById(eventId: number): Promise<DifferentialItem> {
  const response = await apiClient.get<DifferentialItem>(`/v1/differential/${eventId}`)
  return response.data
}
