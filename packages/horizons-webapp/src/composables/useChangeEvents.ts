import { computed, type MaybeRefOrGetter, toValue } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { fetchDiscovery, type DiscoveryPage } from '@/api/changes'

const DEFAULT_LIMIT = 50

export interface ChangeEventFilters {
  jurisdiction?: string | null
  sector?: string | null
}

export function useChangeEvents(filters?: MaybeRefOrGetter<ChangeEventFilters>) {
  const resolved = computed(() => toValue(filters) ?? {})
  return useInfiniteQuery({
    queryKey: computed(() => [
      'changes',
      'discovery',
      'corpus',
      resolved.value.jurisdiction ?? null,
      resolved.value.sector ?? null,
    ]),
    queryFn: ({ pageParam }: { pageParam: string | null }) =>
      fetchDiscovery({
        cursor: pageParam,
        limit: DEFAULT_LIMIT,
        jurisdiction: resolved.value.jurisdiction ?? null,
        sector: resolved.value.sector ?? null,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: DiscoveryPage): string | null =>
      lastPage.has_more && lastPage.next_cursor ? lastPage.next_cursor : null,
  })
}
