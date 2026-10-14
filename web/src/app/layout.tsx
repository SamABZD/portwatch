import type { Metadata } from "next";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "PortWatch — Maritime data engineering demo", template: "%s | PortWatch" },
  description:
    "A maritime data engineering demo. Trace vessel reports through Python ingestion, validation, PostGIS analytics, Prefect workflows and historical dbt models. Explore Los Angeles and Oakland on the vessel map.",
  openGraph: {
    type: "website", siteName: "PortWatch",
    title: "PortWatch — Maritime data engineering demo",
    description: "Maritime data engineering: received AIS reports become traceable port analytics, historical replay and the vessel map.",
  },
  twitter: {
    card: "summary", title: "PortWatch — Maritime data engineering demo",
    description: "Inspect Python, PostGIS, Prefect and historical dbt pipelines behind the vessel map.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
