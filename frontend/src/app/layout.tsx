import type { Metadata } from "next";
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
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
