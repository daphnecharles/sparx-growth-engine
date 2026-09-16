'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getProspect, approveProspect, rejectProspect } from '@/lib/api';
import { use } from 'react';
import { Check, X, Linkedin, ExternalLink, ArrowLeft } from 'lucide-react';
import Link from 'next/link';

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  if (value === null) return null;
  const pct = Math.round(value * 10);
  const color =
    pct >= 70 ? 'bg-green-500' :
    pct >= 40 ? 'bg-yellow-400' :
    'bg-red-400';
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-500">{label}</span>
        <span className="text-xs font-semibold text-gray-700">{pct}/100</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function ProspectProfilePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const key = decodeURIComponent(id);
  const qc = useQueryClient();

  const { data: prospect, isLoading, isError } = useQuery({
    queryKey: ['prospect', key],
    queryFn: () => getProspect(key),
  });

  const approveMutation = useMutation({
    mutationFn: () => approveProspect(key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prospect', key] });
      qc.invalidateQueries({ queryKey: ['prospects'] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () => rejectProspect(key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prospect', key] });
      qc.invalidateQueries({ queryKey: ['prospects'] });
    },
  });

  if (isLoading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="w-5 h-5 border-2 border-brand-night border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (isError || !prospect) {
    return (
      <div className="p-8">
        <Link href="/prospects" className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6">
          <ArrowLeft size={14} /> Back to Prospects
        </Link>
        <p className="text-gray-400">Prospect not found.</p>
      </div>
    );
  }

  const isDmOnly = !prospect.email && !!prospect.linkedin_dm_draft;

  return (
    <div className="p-8 max-w-5xl">
      {/* Back */}
      <Link href="/prospects" className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6">
        <ArrowLeft size={14} /> Back to Prospects
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="font-satoshi font-bold text-xl text-gray-900">{prospect.name}</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {prospect.role} · {prospect.company}
          </p>
          {prospect.outreach_status && (
            <span className="inline-flex items-center mt-2 px-2 py-0.5 rounded text-xs font-medium bg-brand-amaranth-50 text-brand-amaranth-700">
              {prospect.outreach_status}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => approveMutation.mutate()}
            disabled={approveMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white text-sm font-medium rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors"
          >
            <Check size={14} /> Approve
          </button>
          <button
            onClick={() => rejectMutation.mutate()}
            disabled={rejectMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 text-gray-600 text-sm font-medium rounded-md hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            <X size={14} /> Reject
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Left column — 2/3 width */}
        <div className="col-span-2 space-y-5">
          {/* Pain Points */}
          <div className="bg-white rounded-lg border border-gray-200 p-5">
            <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-3">Pain Points</h2>
            {prospect.top_pain_points.length > 0 ? (
              <ul className="space-y-1.5">
                {prospect.top_pain_points.map((pt, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-gray-600">
                    <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-brand-amaranth-500 flex-shrink-0" />
                    {pt}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-400">None identified.</p>
            )}
          </div>

          {/* Course Modules */}
          {prospect.course_modules && prospect.course_modules.length > 0 && (
            <div className="bg-white rounded-lg border border-gray-200 p-5">
              <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-3">Recommended Course Modules</h2>
              <div className="flex flex-wrap gap-2">
                {prospect.course_modules.map((m, i) => (
                  <span key={i} className="px-2.5 py-1 bg-brand-amaranth-50 text-brand-amaranth-700 text-xs font-medium rounded">
                    {m}
                  </span>
                ))}
              </div>
              {prospect.recommended_course_angle && (
                <p className="mt-3 text-sm text-gray-600 italic">"{prospect.recommended_course_angle}"</p>
              )}
            </div>
          )}

          {/* Personalization Notes */}
          {prospect.outreach_personalization_notes && (
            <div className="bg-white rounded-lg border border-gray-200 p-5">
              <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-3">Personalization Notes</h2>
              <p className="text-sm text-gray-600 leading-relaxed">{prospect.outreach_personalization_notes}</p>
            </div>
          )}

          {/* Summary */}
          {prospect.fit_summary && (
            <div className="bg-white rounded-lg border border-gray-200 p-5">
              <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-3">Fit Summary</h2>
              <p className="text-sm text-gray-600 leading-relaxed">{prospect.fit_summary}</p>
            </div>
          )}

          {/* LinkedIn DM Draft */}
          {isDmOnly && prospect.linkedin_dm_draft && (
            <div className="bg-brand-amaranth-50 rounded-lg border border-brand-amaranth-100 p-5">
              <div className="flex items-center gap-2 mb-3">
                <Linkedin size={15} className="text-brand-amaranth-600" />
                <h2 className="font-satoshi font-bold text-sm text-brand-amaranth-700">LinkedIn DM Draft</h2>
              </div>
              <p className="text-sm text-brand-amaranth-700 leading-relaxed whitespace-pre-line">
                {prospect.linkedin_dm_draft}
              </p>
            </div>
          )}
        </div>

        {/* Right column */}
        <div className="space-y-5">
          {/* Scores */}
          <div className="bg-white rounded-lg border border-gray-200 p-5">
            <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-4">Scores</h2>
            <div className="space-y-3">
              <ScoreBar label="Overall Fit" value={prospect.overall_fit_score} />
              <ScoreBar label="Course Fit" value={prospect.course_fit} />
              <ScoreBar label="AI Opportunity" value={prospect.ai_opportunity} />
              <ScoreBar label="Outreach Priority" value={prospect.outreach_priority} />
            </div>
            {prospect.recommended_action && (
              <div className="mt-4 pt-4 border-t border-gray-100">
                <p className="text-xs text-gray-400 mb-1">Recommended Action</p>
                <span className={`inline-flex items-center px-2.5 py-1 rounded text-xs font-semibold ${
                  prospect.recommended_action === 'prioritize' ? 'bg-green-50 text-green-700' :
                  prospect.recommended_action === 'nurture' ? 'bg-brand-amaranth-50 text-brand-amaranth-600' :
                  'bg-gray-100 text-gray-500'
                }`}>
                  {prospect.recommended_action}
                </span>
              </div>
            )}
          </div>

          {/* Contact Info */}
          <div className="bg-white rounded-lg border border-gray-200 p-5">
            <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-3">Contact</h2>
            <div className="space-y-2">
              {prospect.email ? (
                <div>
                  <p className="text-xs text-gray-400 mb-0.5">Email</p>
                  <p className="text-sm text-gray-700 font-mono break-all">{prospect.email}</p>
                </div>
              ) : isDmOnly ? (
                <div className="flex items-center gap-1.5 text-sm text-brand-amaranth-600">
                  <Linkedin size={14} />
                  <span>LinkedIn DM only</span>
                </div>
              ) : (
                <p className="text-sm text-gray-400">No email found</p>
              )}
              {prospect.source_url_used && (
                <div>
                  <p className="text-xs text-gray-400 mb-0.5">Source</p>
                  <a
                    href={prospect.source_url_used}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1 text-xs text-brand-amaranth-600 hover:underline"
                  >
                    View source <ExternalLink size={10} />
                  </a>
                </div>
              )}
              {prospect.website_url && (
                <div>
                  <p className="text-xs text-gray-400 mb-0.5">Website</p>
                  <a
                    href={prospect.website_url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1 text-xs text-brand-amaranth-600 hover:underline"
                  >
                    {prospect.website_url} <ExternalLink size={10} />
                  </a>
                </div>
              )}
            </div>
          </div>

          {/* Company Info */}
          <div className="bg-white rounded-lg border border-gray-200 p-5">
            <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-3">Company</h2>
            <div className="space-y-2 text-sm">
              <div>
                <p className="text-xs text-gray-400">Industry</p>
                <p className="text-gray-700">{prospect.industry || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-gray-400">Company Size</p>
                <p className="text-gray-700">{prospect.company_size || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-gray-400">AI Maturity</p>
                <p className={`font-medium ${
                  prospect.ai_maturity === 'high' ? 'text-green-600' :
                  prospect.ai_maturity === 'medium' ? 'text-yellow-600' :
                  'text-red-500'
                }`}>
                  {prospect.ai_maturity}
                </p>
              </div>
              <div>
                <p className="text-xs text-gray-400">Budget Signal</p>
                <p className={prospect.budget_signal_found ? 'text-green-600' : 'text-gray-400'}>
                  {prospect.budget_signal_found ? 'Found' : 'Not found'}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
