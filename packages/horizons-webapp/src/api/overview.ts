import { apiClient } from './client'

export interface JurisdictionOverviewItem {
  code: string
  document_count: number
  change_count: number
  subscribed: boolean
}

export interface SectorOverviewItem {
  code: string
  document_count: number
  change_count: number
  subscribed: boolean
}

export interface OverviewTotals {
  documents: number
  jurisdictions: number
  sectors: number
  subscribed_jurisdictions: number
  subscribed_sectors: number
}

export interface OverviewResponse {
  is_admin: boolean
  totals: OverviewTotals
  jurisdictions: JurisdictionOverviewItem[]
  sectors: SectorOverviewItem[]
}

export async function fetchOverview(): Promise<OverviewResponse> {
  const response = await apiClient.get<OverviewResponse>('/v1/me/overview')
  return response.data
}
