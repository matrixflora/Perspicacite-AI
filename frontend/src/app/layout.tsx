import type { Metadata } from "next";
import { Sidebar } from "@/components/Sidebar";
import { CommandPalette } from "@/components/CommandPalette";
import "./globals.css";

export const metadata: Metadata = {
  title: "Perspicacité — AI for scientific literature",
  description:
    "Multi-mode retrieval-augmented research assistant. CNRS / UniCA / 3iA.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">{children}</div>
        </div>
        <CommandPalette />
      </body>
    </html>
  );
}
