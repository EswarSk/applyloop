import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Orbit', description: 'Turn knowledge into real-world experience' };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
