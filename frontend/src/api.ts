import { useMutation, useQuery } from '@tanstack/react-query'
import { quoteStatusSchema } from './schemas'
import { quoteSubmitResponseSchema } from './schemas'
import { quoteSchema } from './schemas'
import type { MenuSpec } from './schemas'

const BASE = '/api'

export function useSubmitQuote() {
  return useMutation({
    mutationFn: async (spec: MenuSpec) => {
      const res = await fetch(`${BASE}/quotes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(spec),
      })
      if (!res.ok) throw new Error(`Submit failed: ${res.status}`)
      return quoteSubmitResponseSchema.parse(await res.json())
    },
  })
}

export function useQuoteStatus(quoteId: string, enabled = true) {
  return useQuery({
    queryKey: ['quoteStatus', quoteId],
    queryFn: async () => {
      const res = await fetch(`${BASE}/quotes/${quoteId}`)
      if (!res.ok) throw new Error(`Status fetch failed: ${res.status}`)
      return quoteStatusSchema.parse(await res.json())
    },
    enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === 'completed' || status === 'completed_with_errors') return false
      return 3000
    },
  })
}

export function useQuoteResult(quoteId: string, enabled = true) {
  return useQuery({
    queryKey: ['quoteResult', quoteId],
    queryFn: async () => {
      const res = await fetch(`${BASE}/quotes/${quoteId}/result`)
      if (!res.ok) throw new Error(`Quote fetch failed: ${res.status}`)
      return quoteSchema.parse(await res.json())
    },
    enabled,
  })
}
