'use client';

import { useQuery } from '@tanstack/react-query';
import { getProspects, type Prospect } from '@/lib/api';
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from 'recharts';

const COLORS = {
  prioritize: '#16a34a',
  nurture: '#AB2057',
  deprioritize: '#9ca3af',
  high: '#16a34a',
  medium: '#eab308',
  low: '#ef4444',
};

function SectionHeader({ title }: { title: string }) {
  return <h2 className="font-satoshi font-bold text-sm text-gray-900 mb-4">{title}</h2>;
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <SectionHeader title={title} />
      {children}
    </div>
  );
}

function StatSummary({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <p className="text-sm text-gray-500">{label}</p>
      <p className="font-satoshi font-bold text-3xl text-gray-900 mt-1">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function AnalyticsPage() {
  const { data: prospects = [], isLoading } = useQuery({
    queryKey: ['prospects'],
    queryFn: getProspects,
  });

  if (isLoading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="w-5 h-5 border-2 border-brand-night border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // --- Data computations ---

  // Action distribution
  const actionCounts: Record<string, number> = { prioritize: 0, nurture: 0, deprioritize: 0 };
  prospects.forEach(p => {
    const k = p.recommended_action ?? 'deprioritize';
    actionCounts[k] = (actionCounts[k] || 0) + 1;
  });
  const actionData = Object.entries(actionCounts).map(([name, value]) => ({ name, value }));

  // Industry breakdown (top 10)
  const industryCounts: Record<string, number> = {};
  prospects.forEach(p => {
    if (p.industry) industryCounts[p.industry] = (industryCounts[p.industry] || 0) + 1;
  });
  const industryData = Object.entries(industryCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([name, value]) => ({ name, value }));

  // AI Maturity distribution
  const maturityCounts: Record<string, number> = { high: 0, medium: 0, low: 0 };
  prospects.forEach(p => {
    maturityCounts[p.ai_maturity] = (maturityCounts[p.ai_maturity] || 0) + 1;
  });
  const maturityData = Object.entries(maturityCounts).map(([name, value]) => ({ name, value }));

  // Outreach status breakdown
  const statusCounts: Record<string, number> = {};
  prospects.forEach(p => {
    const s = p.outreach_status ?? 'Pending';
    statusCounts[s] = (statusCounts[s] || 0) + 1;
  });
  const statusData = Object.entries(statusCounts).map(([name, value]) => ({ name, value }));

  const STATUS_COLORS = ['#0A0A0A', '#16a34a', '#ef4444', '#FFDB15', '#AB2057'];

  // Score averages
  const avg = (arr: (number | null)[]): string => {
    const vals = arr.filter((v): v is number => v !== null);
    if (!vals.length) return '—';
    return (vals.reduce((a, b) => a + b, 0) / vals.length * 10).toFixed(0);
  };
  const avgFit = avg(prospects.map(p => p.overall_fit_score));
  const avgCourse = avg(prospects.map(p => p.course_fit));
  const avgAI = avg(prospects.map(p => p.ai_opportunity));

  const emailFound = prospects.filter(p => p.email).length;
  const dmOnly = prospects.filter(p => !p.email && p.linkedin_dm_draft).length;

  return (
    <div className="p-8 max-w-6xl">
      <div className="mb-6">
        <h1 className="font-satoshi font-bold text-xl text-gray-900">Analytics</h1>
        <p className="text-sm text-gray-500 mt-0.5">Pipeline performance overview</p>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <StatSummary label="Total Prospects" value={prospects.length} />
        <StatSummary label="Avg. Fit Score" value={avgFit} sub="overall fit / 100" />
        <StatSummary label="Email Found" value={emailFound} sub={`${dmOnly} LinkedIn DM only`} />
        <StatSummary label="Avg. AI Opportunity" value={avgAI} sub="/ 100" />
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Recommended Action — Pie */}
        <ChartCard title="Recommended Action">
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie
                data={actionData}
                cx="50%"
                cy="50%"
                innerRadius={55}
                outerRadius={85}
                paddingAngle={3}
                dataKey="value"
                label={({ name, value }) => `${name}: ${value}`}
                labelLine={false}
              >
                {actionData.map((entry, i) => (
                  <Cell key={i} fill={COLORS[entry.name as keyof typeof COLORS] || '#e5e7eb'} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
          <div className="flex justify-center gap-4 mt-2">
            {actionData.map(d => (
              <div key={d.name} className="flex items-center gap-1.5 text-xs text-gray-600">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: COLORS[d.name as keyof typeof COLORS] || '#e5e7eb' }} />
                {d.name}
              </div>
            ))}
          </div>
        </ChartCard>

        {/* AI Maturity — Bar */}
        <ChartCard title="AI Maturity Distribution">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={maturityData} barSize={40}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {maturityData.map((entry, i) => (
                  <Cell key={i} fill={COLORS[entry.name as keyof typeof COLORS] || '#e5e7eb'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* Industry — Horizontal Bar */}
        <ChartCard title="Top Industries">
          {industryData.length === 0 ? (
            <p className="text-sm text-gray-400">No industry data.</p>
          ) : (
            <ResponsiveContainer width="100%" height={Math.max(200, industryData.length * 30)}>
              <BarChart data={industryData} layout="vertical" barSize={16}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
                <YAxis type="category" dataKey="name" width={140} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="value" fill="#AB2057" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        {/* Outreach Status — Pie */}
        <ChartCard title="Outreach Status">
          {statusData.length === 0 ? (
            <p className="text-sm text-gray-400">No status data yet.</p>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie
                    data={statusData}
                    cx="50%"
                    cy="50%"
                    outerRadius={85}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {statusData.map((_, i) => (
                      <Cell key={i} fill={STATUS_COLORS[i % STATUS_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex flex-wrap justify-center gap-3 mt-2">
                {statusData.map((d, i) => (
                  <div key={d.name} className="flex items-center gap-1.5 text-xs text-gray-600">
                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: STATUS_COLORS[i % STATUS_COLORS.length] }} />
                    {d.name}: {d.value}
                  </div>
                ))}
              </div>
            </>
          )}
        </ChartCard>
      </div>
    </div>
  );
}
