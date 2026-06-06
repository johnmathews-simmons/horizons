import { useQuery } from '@tanstack/vue-query'
import { fetchOverview, type OverviewResponse } from '@/api/overview'

const STALE_MS = 30_000

export function useMeOverview() {
  return useQuery<OverviewResponse>({
    queryKey: ['me', 'overview'],
    queryFn: fetchOverview,
    staleTime: STALE_MS,
  })
}
