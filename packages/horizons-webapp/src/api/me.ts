import { apiClient } from './client'

export interface SubscriptionSummary {
  active_pairs: Array<{ jurisdiction: string; sector: string }>
  is_admin_bypass: boolean
}

export interface MeResponse {
  user_id: string
  email: string
  role: 'admin' | 'client'
  created_at: string
  subscription: SubscriptionSummary
}

export async function fetchMe(): Promise<MeResponse> {
  const response = await apiClient.get<MeResponse>('/v1/me')
  return response.data
}
