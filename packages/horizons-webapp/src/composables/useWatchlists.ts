import { useMutation, useQuery, useQueryClient, type UseMutationReturnType } from '@tanstack/vue-query'
import {
  type CreateWatchlistBody,
  type Watchlist,
  createWatchlist,
  deleteWatchlist,
  fetchWatchlists,
} from '@/api/watchlists'

/**
 * Cache keys for the watchlists surface. Keep call sites pointing here so
 * mutation-invalidation stays in lockstep with query registration.
 */
export const WATCHLISTS_QUERY_KEY = ['watchlists', 'me'] as const

interface MutationContext {
  previous: Watchlist[] | undefined
}

export function useWatchlistsQuery() {
  return useQuery({
    queryKey: WATCHLISTS_QUERY_KEY,
    queryFn: fetchWatchlists,
    staleTime: 30_000,
  })
}

export function useAddWatchlistMutation(): UseMutationReturnType<
  Watchlist,
  Error,
  CreateWatchlistBody,
  MutationContext
> {
  const queryClient = useQueryClient()
  return useMutation<Watchlist, Error, CreateWatchlistBody, MutationContext>({
    mutationFn: createWatchlist,
    onMutate: async (body) => {
      await queryClient.cancelQueries({ queryKey: WATCHLISTS_QUERY_KEY })
      const previous = queryClient.getQueryData<Watchlist[]>(WATCHLISTS_QUERY_KEY)
      const optimistic: Watchlist = {
        id: `optimistic-${body.document_id}`,
        document_id: body.document_id,
        name: body.name ?? 'Adding…',
        created_at: new Date().toISOString(),
      }
      queryClient.setQueryData<Watchlist[]>(WATCHLISTS_QUERY_KEY, (old) => [
        ...(old ?? []),
        optimistic,
      ])
      return { previous }
    },
    onError: (_err, _body, context) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(WATCHLISTS_QUERY_KEY, context.previous)
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: WATCHLISTS_QUERY_KEY })
    },
  })
}

export function useRemoveWatchlistMutation(): UseMutationReturnType<
  void,
  Error,
  string,
  MutationContext
> {
  const queryClient = useQueryClient()
  return useMutation<void, Error, string, MutationContext>({
    mutationFn: deleteWatchlist,
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: WATCHLISTS_QUERY_KEY })
      const previous = queryClient.getQueryData<Watchlist[]>(WATCHLISTS_QUERY_KEY)
      queryClient.setQueryData<Watchlist[]>(WATCHLISTS_QUERY_KEY, (old) =>
        (old ?? []).filter((w) => w.id !== id),
      )
      return { previous }
    },
    onError: (_err, _id, context) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(WATCHLISTS_QUERY_KEY, context.previous)
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: WATCHLISTS_QUERY_KEY })
    },
  })
}
