import { useInfiniteQuery } from '@tanstack/vue-query'
import { fetchDiscovery, type DiscoveryPage } from '@/api/changes'

const DEFAULT_LIMIT = 50

export function useChangeEvents() {
  return useInfiniteQuery({
    queryKey: ['changes', 'discovery', 'corpus'],
    queryFn: ({ pageParam }: { pageParam: string | null }) =>
      fetchDiscovery({ cursor: pageParam, limit: DEFAULT_LIMIT }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: DiscoveryPage): string | null =>
      lastPage.has_more && lastPage.next_cursor ? lastPage.next_cursor : null,
  })
}
