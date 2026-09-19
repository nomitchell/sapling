import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sapling — Research workspace",
  description: "An open-ended workspace for research that grows with your ideas.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
