import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { fetchDiscovery, type DiscoveryItem } from '@/api/changes'

export interface ScopedDocument {
  document_id: string
  jurisdiction: string
  sector: string
}

export const SCOPED_DOCUMENTS_QUERY_KEY = ['discovery', 'scoped-documents'] as const

const DISCOVERY_LIMIT = 50

/**
 * Reads /v1/discovery and projects to a deduped list of (document_id,
 * jurisdiction, sector) — the add-watchlist dialog needs document identities,
 * not change events. The endpoint already enforces scope at the API layer,
 * so anything the user can NOT subscribe to is invisible here by construction.
 */
export function useScopedDocuments() {
  const query = useQuery({
    queryKey: SCOPED_DOCUMENTS_QUERY_KEY,
    queryFn: () => fetchDiscovery({ limit: DISCOVERY_LIMIT }),
    staleTime: 30_000,
  })

  const documents = computed<ScopedDocument[]>(() => {
    const items: DiscoveryItem[] = query.data.value?.items ?? []
    const seen = new Set<string>()
    const out: ScopedDocument[] = []
    for (const item of items) {
      if (seen.has(item.document_id)) continue
      seen.add(item.document_id)
      out.push({
        document_id: item.document_id,
        jurisdiction: item.jurisdiction,
        sector: item.sector,
      })
    }
    return out
  })

  return { ...query, documents }
}
