import { z } from 'zod'

export const menuItemSchema = z.object({
  name: z.string().min(1, 'Item name is required'),
  description: z.string().min(1, 'Description is required'),
  dietary_notes: z.string().nullable().optional(),
  service_style: z.string().optional(),
})

export const menuSpecSchema = z.object({
  event: z.string().min(1, 'Event name is required'),
  date: z.string().nullable().optional(),
  venue: z.string().nullable().optional(),
  guest_count_estimate: z.number().positive().nullable().optional(),
  notes: z.string().nullable().optional(),
  categories: z.record(z.string(), z.array(z.unknown())).refine(
    (cats) => Object.keys(cats).length > 0,
    'At least one category with items is required',
  ),
})

export const jobSubmitResponseSchema = z.object({
  job_id: z.string().uuid(),
  status: z.string(),
})

export const jobStatusItemSchema = z.object({
  item_name: z.string(),
  step: z.string(),
  status: z.string(),
})

export const jobStatusSchema = z.object({
  job_id: z.string(),
  status: z.string(),
  total_items: z.number(),
  completed_items: z.number(),
  failed_items: z.number(),
  items: z.array(jobStatusItemSchema),
})

export const ingredientSchema = z.object({
  name: z.string(),
  quantity: z.string(),
  unit_cost: z.number().nullable(),
  source: z.enum(['sysco_catalog', 'estimated', 'not_available']),
  source_item_id: z.string().nullable(),
})

export const lineItemSchema = z.object({
  item_name: z.string(),
  category: z.string().optional(),
  ingredients: z.array(ingredientSchema),
  ingredient_cost_per_unit: z.number(),
})

export const quoteSchema = z.object({
  quote_id: z.string(),
  event: z.string(),
  date: z.string().nullable().optional(),
  venue: z.string().nullable().optional(),
  generated_at: z.string(),
  line_items: z.array(lineItemSchema),
})

export type MenuSpec = z.infer<typeof menuSpecSchema>
export type JobSubmitResponse = z.infer<typeof jobSubmitResponseSchema>
export type JobStatus = z.infer<typeof jobStatusSchema>
export type Quote = z.infer<typeof quoteSchema>
export type LineItem = z.infer<typeof lineItemSchema>
export type Ingredient = z.infer<typeof ingredientSchema>
