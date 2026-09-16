'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getProspects, approveProspect, rejectProspect, type Prospect } from '@/lib/api';
import { Check, X, Linkedin, ExternalLink } from 'lucide-react';
import Link from 'next/link';

function FitBadge({ score }: { score: number | null }) {
  if (score === null) return <span className="text-gray-300 text-sm">—</span>;
  const pct = Math.round(score * 10);
  const color =
    pct >= 70 ? 'bg-green-100 text-green-700' :
    pct >= 40 ? 'bg-yellow-100 text-yellow-700' :
    'bg-red-100 text-red-600';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${color}`}>
      {pct}
    </span>
  );
}

function ActionBadge({ action }: { action: string | null }) {
  const map: Record<string, string> = {
    prioritize: 'bg-green-50 text-green-700 ring-1 ring-green-200',
    nurture: 'bg-brand-amaranth-50 text-brand-amaranth-600 ring-1 ring-brand-amaranth-100',
    deprioritize: 'bg-gray-100 text-gray-500',
  };
  const cls = action ? map[action] || 'bg-gray-100 text-gray-500' : 'bg-gray-100 text-gray-400';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {action ?? '—'}
    </span>
  );
}

function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  const map: Record<string, string> = {
    Approved: 'bg-green-50 text-green-700',
    Rejected: 'bg-red-50 text-red-600',
    'Flagged for Review': 'bg-yellow-50 text-yellow-700',
    'LinkedIn DM Needed': 'bg-brand-amaranth-50 text-brand-amaranth-700',
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${map[status] || 'bg-gray-100 text-gray-500'}`}>
      {status}
    </span>
  );
}

function MaturityDot({ level }: { level: string }) {
  const map: Record<string, string> = {
    high: 'bg-green-500',
    medium: 'bg-yellow-400',
    low: 'bg-red-400',
  };
  return (
    <span className="flex items-center gap-1.5 text-sm text-gray-600">
      <span className={`w-2 h-2 rounded-full ${map[level] || 'bg-gray-300'}`} />
      {level}
    </span>
  );
}

export default function ProspectsPage() {
  const qc = useQueryClient();

  const { data: prospects = [], isLoading } = useQuery({
    queryKey: ['prospects'],
    queryFn: getProspects,
  });

  const approveMutation = useMutation({
    mutationFn: (key: string) => approveProspect(key),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['prospects'] }),
  });

  const rejectMutation = useMutation({
    mutationFn: (key: string) => rejectProspect(key),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['prospects'] }),
  });

  if (isLoading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="w-5 h-5 border-2 border-brand-night border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const sorted = [...prospects].sort((a, b) => (b.overall_fit_score ?? 0) - (a.overall_fit_score ?? 0));

  return (
    <div className="p-8">
      <div className="mb-6">
        <h1 className="font-satoshi font-bold text-xl text-gray-900">Prospects</h1>
        <p className="text-sm text-gray-500 mt-0.5">{prospects.length} prospects found</p>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead>
            <tr className="bg-gray-50">
              {['Name', 'Company', 'Role', 'Fit', 'AI Maturity', 'Action', 'Status', 'Contact', ''].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-12 text-center text-sm text-gray-400">
                  No prospects yet. Run the pipeline from the Dashboard.
                </td>
              </tr>
            ) : (
              sorted.map((p, i) => {
                const key = p.prospect_key ?? p.source_url_used ?? String(i);
                const isDmOnly = !p.email && !!p.linkedin_dm_draft;
                return (
                  <tr
                    key={i}
                    className={`hover:bg-gray-50 transition-colors ${isDmOnly ? 'bg-brand-amaranth-50/40' : ''}`}
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/prospects/${encodeURIComponent(key)}`}
                        className="text-sm font-medium text-gray-900 hover:text-brand-amaranth-600 transition-colors"
                      >
                        {p.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">{p.company}</td>
                    <td className="px-4 py-3 text-sm text-gray-500 max-w-[180px] truncate">{p.role}</td>
                    <td className="px-4 py-3">
                      <FitBadge score={p.overall_fit_score} />
                    </td>
                    <td className="px-4 py-3">
                      <MaturityDot level={p.ai_maturity} />
                    </td>
                    <td className="px-4 py-3">
                      <ActionBadge action={p.recommended_action} />
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={p.outreach_status} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {p.email ? (
                          <span className="text-xs text-gray-500 font-mono truncate max-w-[140px]">{p.email}</span>
                        ) : isDmOnly ? (
                          <a
                            href={p.linkedin_url ?? p.source_url_used ?? '#'}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-1 text-xs text-brand-amaranth-600 font-medium hover:text-brand-amaranth-700 transition-colors"
                          >
                            <Linkedin size={12} />
                            Send DM
                          </a>
                        ) : (
                          <span className="text-xs text-gray-300">No email</span>
                        )}
                        {p.source_url_used && (
                          <a
                            href={p.source_url_used}
                            target="_blank"
                            rel="noreferrer"
                            className="text-gray-400 hover:text-gray-600"
                          >
                            <ExternalLink size={12} />
                          </a>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => approveMutation.mutate(key)}
                          disabled={approveMutation.isPending}
                          title="Approve"
                          className="p-1.5 rounded hover:bg-green-50 text-gray-400 hover:text-green-600 transition-colors disabled:opacity-50"
                        >
                          <Check size={14} />
                        </button>
                        <button
                          onClick={() => rejectMutation.mutate(key)}
                          disabled={rejectMutation.isPending}
                          title="Reject"
                          className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500 transition-colors disabled:opacity-50"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
