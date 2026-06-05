import { apiClient } from './client'

export interface AdminClient {
  user_id: string
  email: string
  role: 'client'
  created_at: string
}

export interface AdminClientsListResponse {
  limit: number
  offset: number
  total: number
  clients: AdminClient[]
}

export interface ListAdminClientsParams {
  limit?: number
  offset?: number
}

export async function listAdminClients(
  params: ListAdminClientsParams = {},
): Promise<AdminClientsListResponse> {
  const response = await apiClient.get<AdminClientsListResponse>('/v1/admin/clients', {
    params,
  })
  return response.data
}

export interface ImpersonateRequest {
  target_user_id: string
  reason?: string | null
}

export interface ImpersonateResponse {
  impersonation_token: string
  target_user_id: string
  target_email: string
  original_admin_id: string
  original_admin_email: string
  expires_in_seconds: number
}

export async function postImpersonate(body: ImpersonateRequest): Promise<ImpersonateResponse> {
  const response = await apiClient.post<ImpersonateResponse>('/v1/admin/impersonate', body)
  return response.data
}

export type AdminAuditMode = 'operator' | 'impersonation'

export interface AdminAuditRow {
  id: string
  admin_id: string
  target_user_id: string | null
  mode: AdminAuditMode
  token_id: string | null
  reason: string | null
  granted_at: string
}

export interface AdminAuditResponse {
  since: string
  limit: number
  count: number
  rows: AdminAuditRow[]
}

export interface ListAuditParams {
  since?: string
  admin_id?: string
  target_user_id?: string
  action?: AdminAuditMode
  limit?: number
}

export async function listAdminAudit(params: ListAuditParams = {}): Promise<AdminAuditResponse> {
  const response = await apiClient.get<AdminAuditResponse>('/v1/admin/audit', { params })
  return response.data
}

export interface AdminScopePair {
  jurisdiction: string
  sector: string
}

export interface AdminScopeOut extends AdminScopePair {
  valid_to: string | null
}

export interface AdminSubscriptionOut {
  id: string
  user_id: string
  valid_from: string
  valid_to: string | null
  created_at: string
  scopes: AdminScopeOut[]
}

export interface AdminSubscriptionsListResponse {
  user_id: string
  subscriptions: AdminSubscriptionOut[]
}

export async function listAdminSubscriptions(
  userId: string,
): Promise<AdminSubscriptionsListResponse> {
  const response = await apiClient.get<AdminSubscriptionsListResponse>(
    '/v1/admin/subscriptions',
    { params: { user_id: userId } },
  )
  return response.data
}

export interface PatchSubscriptionBody {
  add_scopes?: AdminScopePair[]
  remove_scopes?: AdminScopePair[]
}

export interface PatchSubscriptionResponse {
  subscription: AdminSubscriptionOut
  scopes_added: number
  scopes_removed: number
  watchlists_soft_hidden: number
}

export async function patchAdminSubscription(
  subscriptionId: string,
  body: PatchSubscriptionBody,
): Promise<PatchSubscriptionResponse> {
  const response = await apiClient.patch<PatchSubscriptionResponse>(
    `/v1/admin/subscriptions/${subscriptionId}`,
    body,
  )
  return response.data
}

export interface DiscoveryDocumentSummary {
  document_id: string
  jurisdiction: string
  sector: string
}

interface DiscoveryEnvelope {
  items: Array<{
    document_id: string
    jurisdiction: string
    sector: string
  }>
}

export async function fetchScopedDiscoveryDocuments(): Promise<DiscoveryDocumentSummary[]> {
  const response = await apiClient.get<DiscoveryEnvelope>('/v1/discovery', {
    params: { scope: 'corpus', limit: 200 },
  })
  const seen = new Set<string>()
  const out: DiscoveryDocumentSummary[] = []
  for (const item of response.data.items) {
    if (seen.has(item.document_id)) continue
    seen.add(item.document_id)
    out.push({
      document_id: item.document_id,
      jurisdiction: item.jurisdiction,
      sector: item.sector,
    })
  }
  return out
}
