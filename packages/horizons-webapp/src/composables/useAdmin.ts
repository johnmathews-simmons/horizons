import { computed, type Ref } from 'vue'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationReturnType,
} from '@tanstack/vue-query'
import {
  fetchScopedDiscoveryDocuments,
  listAdminAudit,
  listAdminClients,
  listAdminSubscriptions,
  patchAdminSubscription,
  type AdminAuditResponse,
  type AdminClientsListResponse,
  type AdminSubscriptionsListResponse,
  type DiscoveryDocumentSummary,
  type ListAdminClientsParams,
  type ListAuditParams,
  type PatchSubscriptionBody,
  type PatchSubscriptionResponse,
} from '@/api/admin'

export const ADMIN_CLIENTS_QUERY_KEY_BASE = ['admin', 'clients'] as const
export const ADMIN_AUDIT_QUERY_KEY_BASE = ['admin', 'audit'] as const
export const ADMIN_SUBSCRIPTIONS_QUERY_KEY_BASE = ['admin', 'subscriptions'] as const
export const ADMIN_DISCOVERY_QUERY_KEY = ['admin', 'discovery', 'scoped-documents'] as const

export function adminClientsKey(params: ListAdminClientsParams) {
  return [...ADMIN_CLIENTS_QUERY_KEY_BASE, params.limit ?? 25, params.offset ?? 0] as const
}

export function adminAuditKey(params: ListAuditParams) {
  return [
    ...ADMIN_AUDIT_QUERY_KEY_BASE,
    params.since ?? null,
    params.admin_id ?? null,
    params.target_user_id ?? null,
    params.action ?? null,
    params.limit ?? 100,
  ] as const
}

export function adminSubscriptionsKey(userId: string) {
  return [...ADMIN_SUBSCRIPTIONS_QUERY_KEY_BASE, userId] as const
}

export function useAdminClientsQuery(params: Ref<ListAdminClientsParams>) {
  return useQuery({
    queryKey: computed(() => adminClientsKey(params.value)),
    queryFn: () => listAdminClients(params.value),
    staleTime: 30_000,
  })
}

export function useAdminAuditQuery(params: Ref<ListAuditParams>) {
  return useQuery<AdminAuditResponse>({
    queryKey: computed(() => adminAuditKey(params.value)),
    queryFn: () => listAdminAudit(params.value),
    staleTime: 15_000,
  })
}

export function useAdminSubscriptionsQuery(userId: Ref<string>) {
  return useQuery<AdminSubscriptionsListResponse>({
    queryKey: computed(() => adminSubscriptionsKey(userId.value)),
    queryFn: () => listAdminSubscriptions(userId.value),
    enabled: computed(() => userId.value.length > 0),
    staleTime: 15_000,
  })
}

export function useAdminScopedDocumentsQuery() {
  return useQuery<DiscoveryDocumentSummary[]>({
    queryKey: ADMIN_DISCOVERY_QUERY_KEY,
    queryFn: fetchScopedDiscoveryDocuments,
    staleTime: 60_000,
  })
}

export interface PatchSubscriptionVariables {
  subscriptionId: string
  userId: string
  body: PatchSubscriptionBody
}

export function usePatchSubscriptionMutation(): UseMutationReturnType<
  PatchSubscriptionResponse,
  Error,
  PatchSubscriptionVariables,
  unknown
> {
  const queryClient = useQueryClient()
  return useMutation<PatchSubscriptionResponse, Error, PatchSubscriptionVariables, unknown>({
    mutationFn: ({ subscriptionId, body }) => patchAdminSubscription(subscriptionId, body),
    onSettled: (_data, _err, variables) => {
      void queryClient.invalidateQueries({
        queryKey: adminSubscriptionsKey(variables.userId),
      })
    },
  })
}

export type { AdminClientsListResponse, ListAdminClientsParams, ListAuditParams }
