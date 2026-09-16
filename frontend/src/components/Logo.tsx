'use client';

import { useId } from 'react';

export default function Logo({
  variant = 'dark',
  showWordmark = true,
  size = 20,
}: {
  variant?: 'dark' | 'light';
  showWordmark?: boolean;
  size?: number;
}) {
  const gradientId = useId();
  const textColor = variant === 'dark' ? 'text-white' : 'text-brand-night';

  return (
    <span className="inline-flex items-center gap-1">
      {showWordmark && (
        <span className={`font-satoshi font-black text-lg tracking-tight ${textColor}`}>
          Sparx
        </span>
      )}
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id={gradientId} x1="12" y1="0" x2="12" y2="24" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#FFDB15" />
            <stop offset="55%" stopColor="#FFDB15" />
            <stop offset="100%" stopColor="#AB2057" />
          </linearGradient>
        </defs>
        <path
          d="M12 0C12.8 6.4 13.6 9.6 16 12C13.6 14.4 12.8 17.6 12 24C11.2 17.6 10.4 14.4 8 12C10.4 9.6 11.2 6.4 12 0Z"
          fill={`url(#${gradientId})`}
        />
      </svg>
    </span>
  );
}
