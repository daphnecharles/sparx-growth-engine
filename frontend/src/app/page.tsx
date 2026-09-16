'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, Users, TrendingUp, CheckCircle, Send } from 'lucide-react';
import { getProspects, runPipeline, getPipelineLogs, type PipelineResult, type Prospect } from '@/lib/api';
import { useEffect, useRef, useState } from 'react';

function StatCard({ label, value, icon: Icon, color, iconColor = 'text-white' }: {
  label: string;
  value: number | string;
  icon: React.ElementType;
  color: string;
  iconColor?: string;
}) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-gray-500">{label}</span>
        <div className={`w-8 h-8 rounded-md flex items-center justify-center ${color}`}>
          <Icon size={15} className={iconColor} />
        </div>
      </div>
      <p className="font-satoshi font-bold text-2xl text-gray-900">{value}</p>
    </div>
  );
}

function ActionBadge({ action }: { action: string | null }) {
  const map: Record<string, string> = {
    prioritize: 'bg-green-50 text-green-700',
    nurture: 'bg-brand-amaranth-50 text-brand-amaranth-600',
    deprioritize: 'bg-gray-100 text-gray-500',
  };
  const cls = action ? map[action] || 'bg-gray-100 text-gray-500' : 'bg-gray-100 text-gray-400';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {action ?? 'unknown'}
    </span>
  );
}

export default function Dashboard() {
  const qc = useQueryClient();
  const [lastResult, setLastResult] = useState<PipelineResult | null>(null);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [maxProspects, setMaxProspects] = useState(10);

  const { data: prospects = [] } = useQuery({
    queryKey: ['prospects'],
    queryFn: getProspects,
  });

  const mutation = useMutation({
    mutationFn: () => runPipeline(forceRefresh, maxProspects),
    onSuccess: (data) => {
      setLastResult(data);
      if (data.profiles.length > 0) {
        qc.setQueryData(['prospects'], (old: Prospect[] = []) => {
          const existingKeys = new Set(old.map(p => p.prospect_key).filter(Boolean));
          const fresh = data.profiles.filter(p => !existingKeys.has(p.prospect_key));
          return [...old, ...fresh];
        });
      }
      qc.invalidateQueries({ queryKey: ['prospects'] });
      // One more fetch to catch the final "PIPELINE COMPLETE" log line, which
      // is written right before the response returns — the interval below
      // stops the instant isPending flips false, so it could just miss it.
      qc.invalidateQueries({ queryKey: ['pipeline-logs'] });
    },
  });

  const { data: logs = [] } = useQuery({
    queryKey: ['pipeline-logs'],
    queryFn: getPipelineLogs,
    refetchInterval: mutation.isPending ? 1500 : false,
  });

  const latestLog = logs[logs.length - 1];

  const logsEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ block: 'nearest' });
  }, [logs.length]);

  const prioritized = prospects.filter(p => p.recommended_action === 'prioritize').length;
  const approved = prospects.filter(p => p.outreach_status === 'Approved').length;
  const enrolled = prospects.filter(p => p.apollo_id).length;

  const recentActivity = [...prospects]
    .sort((a, b) => (a.name > b.name ? 1 : -1))
    .slice(0, 10);

  return (
    <div className="p-8 max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="font-satoshi font-bold text-xl text-gray-900">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-0.5">AI-powered prospect research and outreach</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
            <input
              type="checkbox"
              checked={forceRefresh}
              onChange={e => setForceRefresh(e.target.checked)}
              className="rounded accent-brand-night"
            />
            Force refresh
          </label>
          <div className="flex items-center gap-1.5">
            <label className="text-sm text-gray-600 whitespace-nowrap">Prospects</label>
            <input
              type="number"
              min={1}
              max={50}
              value={maxProspects}
              onChange={e => setMaxProspects(Math.max(1, Math.min(50, parseInt(e.target.value) || 1)))}
              disabled={mutation.isPending}
              className="w-16 px-2 py-1.5 text-sm border border-gray-300 rounded-md text-center focus:outline-none focus:ring-2 focus:ring-brand-yellow-400 disabled:opacity-50"
            />
          </div>
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-brand-night text-white text-sm font-medium rounded-md hover:bg-brand-night-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <Play size={14} className="text-brand-yellow-400" />
            {mutation.isPending ? 'Running...' : 'Run Pipeline'}
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <StatCard label="Prospects Found" value={prospects.length} icon={Users} color="bg-brand-night" />
        <StatCard label="Prioritized" value={prioritized} icon={TrendingUp} color="bg-green-500" />
        <StatCard label="Approved" value={approved} icon={CheckCircle} color="bg-brand-amaranth-500" />
        <StatCard label="Enrolled in Sequence" value={enrolled} icon={Send} color="bg-brand-yellow-400" iconColor="text-brand-night" />
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Pipeline Output */}
        <div className="bg-white rounded-lg border border-gray-200">
          <div className="px-5 py-4 border-b border-gray-200">
            <h2 className="font-satoshi font-bold text-sm text-gray-900">Pipeline Output</h2>
          </div>
          <div className="p-5">
            {mutation.isPending && (
              <div className="flex items-center gap-2 text-sm text-brand-night mb-4">
                <div className="w-4 h-4 border-2 border-brand-night border-t-transparent rounded-full animate-spin flex-shrink-0" />
                <span className="truncate">{latestLog || 'Starting pipeline...'}</span>
              </div>
            )}
            {mutation.isError && (
              <div className="text-sm text-red-600 mb-4">
                Error: {mutation.error instanceof Error ? mutation.error.message : String(mutation.error) || 'Unknown error'}
              </div>
            )}
            {lastResult ? (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div className="bg-gray-50 rounded p-3">
                    <p className="text-gray-500 text-xs">Found</p>
                    <p className="font-semibold text-gray-900 mt-0.5">{lastResult.total_prospects_found}</p>
                  </div>
                  <div className="bg-green-50 rounded p-3">
                    <p className="text-green-600 text-xs">Prioritized</p>
                    <p className="font-semibold text-gray-900 mt-0.5">{lastResult.total_prioritized}</p>
                  </div>
                  <div className="bg-blue-50 rounded p-3">
                    <p className="text-blue-600 text-xs">Researched</p>
                    <p className="font-semibold text-gray-900 mt-0.5">{lastResult.total_researched}</p>
                  </div>
                  <div className="bg-brand-amaranth-50 rounded p-3">
                    <p className="text-brand-amaranth-600 text-xs">Attio Synced</p>
                    <p className="font-semibold text-gray-900 mt-0.5">{lastResult.attio_synced}</p>
                  </div>
                </div>
                <p className="text-xs text-gray-400 mt-2">
                  Completed in {lastResult.run_duration_seconds}s · {new Date(lastResult.run_timestamp).toLocaleString()}
                </p>
              </div>
            ) : (
              <p className="text-sm text-gray-400">No pipeline run yet this session.</p>
            )}
          </div>
          {/* Logs */}
          {logs.length > 0 && (
            <div className="border-t border-gray-200">
              <div className="px-5 py-3">
                <p className="text-xs font-medium text-gray-500 mb-2">Logs</p>
                <div className="bg-gray-950 rounded p-3 max-h-48 overflow-y-auto font-mono">
                  {logs.slice(-30).map((line, i) => (
                    <p key={i} className="text-xs text-gray-300 leading-5">{line}</p>
                  ))}
                  <div ref={logsEndRef} />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Recent Activity */}
        <div className="bg-white rounded-lg border border-gray-200">
          <div className="px-5 py-4 border-b border-gray-200">
            <h2 className="font-satoshi font-bold text-sm text-gray-900">Recent Prospects</h2>
          </div>
          <div className="divide-y divide-gray-100">
            {recentActivity.length === 0 ? (
              <p className="text-sm text-gray-400 px-5 py-8 text-center">
                No prospects yet. Run the pipeline to get started.
              </p>
            ) : (
              recentActivity.map((p, i) => (
                <div key={i} className="flex items-center justify-between px-5 py-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">{p.name}</p>
                    <p className="text-xs text-gray-400 truncate">{p.company}</p>
                  </div>
                  <ActionBadge action={p.recommended_action} />
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
