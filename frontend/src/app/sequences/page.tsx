'use client';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { getSequenceStats, getProspects } from '@/lib/api';
import { Mail, Users, MousePointerClick, Reply, Copy, Check as CheckIcon, Linkedin } from 'lucide-react';

function StatCard({ label, value, icon: Icon, sub }: {
  label: string;
  value: number | string | null | undefined;
  icon: React.ElementType;
  sub?: string;
}) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-gray-500">{label}</span>
        <div className="w-8 h-8 rounded-md bg-brand-amaranth-50 flex items-center justify-center">
          <Icon size={15} className="text-brand-amaranth-600" />
        </div>
      </div>
      <p className="font-satoshi font-bold text-2xl text-gray-900">{value ?? '—'}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={handleCopy}
      title="Copy message"
      className={`p-1.5 rounded transition-colors ${copied ? 'text-green-600 bg-green-50' : 'text-gray-400 hover:text-gray-700 hover:bg-gray-100'}`}
    >
      {copied ? <CheckIcon size={13} /> : <Copy size={13} />}
    </button>
  );
}

export default function SequencesPage() {
  const { data: stats, isLoading: statsLoading, error: statsError } = useQuery({
    queryKey: ['sequence-stats'],
    queryFn: getSequenceStats,
  });

  const { data: prospects = [] } = useQuery({
    queryKey: ['prospects'],
    queryFn: getProspects,
  });

  const enrolledProspects = prospects.filter(p => p.apollo_id || p.outreach_status === 'Approved');
  const dmOnlyProspects = prospects.filter(p => !p.email && !!p.linkedin_dm_draft);

  // Try to extract Apollo sequence stats from response
  const seq = (stats as Record<string, unknown> | undefined);
  const emailerCampaign = seq?.emailer_campaign as Record<string, unknown> | undefined;

  const contactsCount = emailerCampaign?.contacts_count as number | undefined;
  const openRate = emailerCampaign?.open_rate as number | undefined;
  const clickRate = emailerCampaign?.click_rate as number | undefined;
  const replyRate = emailerCampaign?.reply_rate as number | undefined;
  const name = emailerCampaign?.name as string | undefined;

  return (
    <div className="p-8 max-w-5xl">
      <div className="mb-6">
        <h1 className="font-satoshi font-bold text-xl text-gray-900">Sequences</h1>
        <p className="text-sm text-gray-500 mt-0.5">Apollo outreach sequence performance</p>
      </div>

      {/* Sequence name */}
      {name && (
        <div className="mb-6 flex items-center gap-2">
          <span className="px-3 py-1 bg-brand-yellow-50 text-brand-night text-sm font-medium rounded-full">
            {name}
          </span>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <StatCard
          label="Enrolled"
          value={contactsCount ?? enrolledProspects.length}
          icon={Users}
          sub="total contacts in sequence"
        />
        <StatCard
          label="Open Rate"
          value={openRate !== undefined ? `${(openRate * 100).toFixed(1)}%` : null}
          icon={Mail}
        />
        <StatCard
          label="Click Rate"
          value={clickRate !== undefined ? `${(clickRate * 100).toFixed(1)}%` : null}
          icon={MousePointerClick}
        />
        <StatCard
          label="Reply Rate"
          value={replyRate !== undefined ? `${(replyRate * 100).toFixed(1)}%` : null}
          icon={Reply}
        />
      </div>

      {/* Apollo Enrolled Prospects */}
      <div className="bg-white rounded-lg border border-gray-200 p-5 mb-6">
          <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-4">Enrolled Prospects</h2>
          {statsLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <div className="w-4 h-4 border-2 border-brand-night border-t-transparent rounded-full animate-spin" />
              Loading...
            </div>
          ) : statsError ? (
            <div className="text-sm text-red-500">
              Could not load Apollo stats. Check APOLLO_API_KEY in backend .env.
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {enrolledProspects.length === 0 ? (
                <p className="text-sm text-gray-400 py-4">No enrolled prospects yet.</p>
              ) : (
                enrolledProspects.slice(0, 15).map((p, i) => (
                  <div key={i} className="py-2.5 flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-gray-900">{p.name}</p>
                      <p className="text-xs text-gray-400">{p.company}</p>
                    </div>
                    {p.email ? (
                      <span className="text-xs text-gray-500 font-mono">{p.email}</span>
                    ) : (
                      <span className="text-xs text-brand-amaranth-600 font-medium">LinkedIn DM</span>
                    )}
                  </div>
                ))
              )}
            </div>
          )}
        </div>

      {/* LinkedIn DM Queue — full width below the grid */}
      <div className="mt-6 bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-satoshi font-bold text-sm text-gray-900">LinkedIn DM Queue</h2>
          {dmOnlyProspects.length > 0 && (
            <span className="px-2 py-0.5 bg-brand-amaranth-100 text-brand-amaranth-700 text-xs font-medium rounded-full">
              {dmOnlyProspects.length}
            </span>
          )}
        </div>
        {dmOnlyProspects.length === 0 ? (
          <p className="text-sm text-gray-400 py-4">No LinkedIn DM drafts yet.</p>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {dmOnlyProspects.map((p, i) => (
              <div key={i} className="rounded-lg border border-brand-amaranth-100 bg-brand-amaranth-50/30 p-4">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <p className="font-satoshi font-bold text-sm text-gray-900">{p.name}</p>
                    <p className="text-xs text-gray-500">{p.role} · {p.company}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {(p.linkedin_url ?? p.source_url_used) && (
                      <a
                        href={p.linkedin_url ?? p.source_url_used ?? '#'}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1 text-xs text-brand-amaranth-600 font-medium hover:text-brand-amaranth-700 transition-colors px-2 py-1 rounded bg-brand-amaranth-100 hover:bg-brand-amaranth-100/70"
                      >
                        <Linkedin size={12} />
                        Open Profile
                      </a>
                    )}
                    {p.linkedin_dm_draft && <CopyButton text={p.linkedin_dm_draft} />}
                  </div>
                </div>
                {p.linkedin_dm_draft && (
                  <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
                    {p.linkedin_dm_draft}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Raw stats debug */}
      {seq && (
        <div className="mt-6 bg-gray-950 rounded-lg p-4">
          <p className="text-xs text-gray-400 mb-2 font-mono">Apollo API Response</p>
          <pre className="text-xs text-gray-300 font-mono overflow-auto max-h-64">
            {JSON.stringify(seq, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
