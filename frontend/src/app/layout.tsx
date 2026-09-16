import type { Metadata } from 'next';
import { Rubik } from 'next/font/google';
import './globals.css';
import Sidebar from '@/components/Sidebar';
import Providers from '@/components/Providers';

const rubik = Rubik({ subsets: ['latin'], variable: '--font-rubik' });

export const metadata: Metadata = {
  title: 'Sparx Growth Engine',
  description: 'AI-powered prospect research and outreach',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://api.fontshare.com" />
        <link
          href="https://api.fontshare.com/v2/css?f[]=satoshi@500,700,900&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className={`${rubik.variable} font-rubik antialiased`} style={{ background: '#F5F5F5' }}>
        <Providers>
          <div className="flex min-h-screen">
            <Sidebar />
            <main style={{ marginLeft: 240 }} className="flex-1 min-h-screen">
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
