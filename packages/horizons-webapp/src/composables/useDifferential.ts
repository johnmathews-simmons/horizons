import { useQuery } from '@tanstack/vue-query'
import { type MaybeRef, toValue } from 'vue'
import { fetchDifferentialById } from '@/api/changes'

export function useDifferential(eventId: MaybeRef<number>) {
  return useQuery({
    queryKey: ['differential', eventId],
    queryFn: () => fetchDifferentialById(toValue(eventId)),
  })
}
