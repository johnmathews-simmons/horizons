import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { listDocuments, type DocumentItem } from '@/api/documents'

export interface ScopedDocument {
  document_id: string
  jurisdiction: string
  sector: string
  title: string
}

export const SCOPED_DOCUMENTS_QUERY_KEY = ['documents', 'scoped'] as const

const SCOPED_DOCUMENTS_LIMIT = 200

/**
 * Lists documents inside the caller's subscription scope for the add-watchlist
 * dialog. Reads /v1/documents (subscription-scoped, carries titles) rather
 * than /v1/discovery — the modal needs document identities + titles, not
 * change events, and /v1/discovery only surfaces documents that have at
 * least one change event seeded (a subset of the subscription scope).
 */
export function useScopedDocuments() {
  const query = useQuery({
    queryKey: SCOPED_DOCUMENTS_QUERY_KEY,
    queryFn: () => listDocuments({ limit: SCOPED_DOCUMENTS_LIMIT }),
    staleTime: 30_000,
  })

  const documents = computed<ScopedDocument[]>(() => {
    const items: DocumentItem[] = query.data.value?.items ?? []
    return items.map((doc) => ({
      document_id: doc.id,
      jurisdiction: doc.jurisdiction,
      sector: doc.sector,
      title: doc.title,
    }))
  })

  return { ...query, documents }
}
