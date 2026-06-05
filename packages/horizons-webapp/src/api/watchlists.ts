import { apiClient } from './client'

export interface Watchlist {
  id: string
  document_id: string
  name: string
  created_at: string
}

export interface CreateWatchlistBody {
  document_id: string
  name?: string
}

export async function fetchWatchlists(): Promise<Watchlist[]> {
  const response = await apiClient.get<Watchlist[]>('/v1/me/watchlists')
  return response.data
}

export async function createWatchlist(body: CreateWatchlistBody): Promise<Watchlist> {
  const response = await apiClient.post<Watchlist>('/v1/me/watchlists', body)
  return response.data
}

export async function deleteWatchlist(watchlistId: string): Promise<void> {
  await apiClient.delete(`/v1/me/watchlists/${watchlistId}`)
}
