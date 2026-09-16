import axios from 'axios';

const BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const api = axios.create({ baseURL: BASE });

export interface Prospect {
  name: string;
  company: string;
  role: string;
  company_size: string;
  industry: string;
  ai_maturity: 'low' | 'medium' | 'high';
  top_pain_points: string[];
  course_modules: string[] | null;
  recommended_course_angle: string | null;
  outreach_personalization_notes: string;
  budget_signal_found: boolean;
  source_url_used: string | null;
  linkedin_url: string | null;
  website_url: string | null;
  email: string | null;
  instagram_url: string | null;
  twitter_url: string | null;
  facebook_url: string | null;
  contact_enrichment_confidence: string | null;
  apollo_id: string | null;
  linkedin_dm_draft: string | null;
  verified: boolean;
  confidence: 'high' | 'medium' | 'low' | null;
  disqualified: boolean;
  course_fit: number | null;
  ai_opportunity: number | null;
  outreach_priority: number | null;
  overall_fit_score: number | null;
  fit_summary: string | null;
  recommended_action: 'prioritize' | 'nurture' | 'deprioritize' | null;
  cached: boolean;
  outreach_status?: string | null;
  prospect_key?: string;
}

export interface PipelineResult {
  profiles: Prospect[];
  total_prospects_found: number;
  total_researched: number;
  total_prioritized: number;
  total_deprioritized: number;
  total_flagged_for_review: number;
  total_skipped_duplicates: number;
  attio_synced: number;
  run_timestamp: string;
  run_duration_seconds: number;
}

export async function getProspects(): Promise<Prospect[]> {
  const { data } = await api.get('/api/prospects');
  return data;
}

export async function getProspect(key: string): Promise<Prospect> {
  const { data } = await api.get(`/api/prospects/${encodeURIComponent(key)}`);
  return data;
}

export async function runPipeline(forceRefresh = false, maxProspects = 10): Promise<PipelineResult> {
  const { data } = await api.post('/api/pipeline/run', { force_refresh: forceRefresh, max_prospects: maxProspects });
  return data;
}

export async function approveProspect(key: string): Promise<void> {
  await api.patch('/api/prospects/approve', null, { params: { key } });
}

export async function rejectProspect(key: string): Promise<void> {
  await api.patch('/api/prospects/reject', null, { params: { key } });
}

export async function getSequenceStats(): Promise<Record<string, unknown>> {
  const { data } = await api.get('/api/apollo/sequence-stats');
  return data;
}

export async function getPipelineLogs(): Promise<string[]> {
  const { data } = await api.get('/api/pipeline/logs');
  return data;
}
