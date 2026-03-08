import { useMutation, useQuery } from '@tanstack/react-query'
import { jobStatusSchema } from './schemas'
import { jobSubmitResponseSchema } from './schemas'
import { quoteSchema } from './schemas'
import type { MenuSpec } from './schemas'

const BASE = '/api'

export function useSubmitJob() {
  return useMutation({
    mutationFn: async (spec: MenuSpec) => {
      const res = await fetch(`${BASE}/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(spec),
      })
      if (!res.ok) throw new Error(`Submit failed: ${res.status}`)
      return jobSubmitResponseSchema.parse(await res.json())
    },
  })
}

export function useJobStatus(jobId: string, enabled = true) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: async () => {
      const res = await fetch(`${BASE}/jobs/${jobId}`)
      if (!res.ok) throw new Error(`Status fetch failed: ${res.status}`)
      return jobStatusSchema.parse(await res.json())
    },
    enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === 'completed' || status === 'completed_with_errors') return false
      return 3000
    },
  })
}

export function useQuote(jobId: string, enabled = true) {
  return useQuery({
    queryKey: ['quote', jobId],
    queryFn: async () => {
      const res = await fetch(`${BASE}/jobs/${jobId}/quote`)
      if (!res.ok) throw new Error(`Quote fetch failed: ${res.status}`)
      return quoteSchema.parse(await res.json())
    },
    enabled,
  })
}
