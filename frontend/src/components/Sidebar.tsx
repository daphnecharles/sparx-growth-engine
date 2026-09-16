'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, Users, BarChart2, Mail } from 'lucide-react';
import Logo from './Logo';

const nav = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/prospects', label: 'Prospects', icon: Users },
  { href: '/analytics', label: 'Analytics', icon: BarChart2 },
  { href: '/sequences', label: 'Sequences', icon: Mail },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      style={{ width: 240, minWidth: 240 }}
      className="fixed left-0 top-0 h-full bg-brand-night flex flex-col z-20"
    >
      {/* Logo */}
      <div className="px-6 py-5 border-b border-white/10 flex items-center justify-between">
        <Logo variant="dark" />
        <span className="text-[10px] font-medium text-white/60 tracking-wide uppercase">Growth</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {nav.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || (href !== '/' && pathname.startsWith(href));
          return (
            <Link
              key={href}
              href={href}
              className={`relative flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                active
                  ? 'bg-brand-night-800 text-white'
                  : 'text-white/85 hover:bg-brand-night-800/60 hover:text-white'
              }`}
            >
              {active && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 h-4 w-0.5 rounded-full bg-brand-yellow-400" />
              )}
              <Icon size={16} className={active ? 'text-brand-yellow-400' : 'text-white/60'} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-white/10">
        <p className="text-xs text-white/50">Powered by Claude + Exa</p>
      </div>
    </aside>
  );
}
