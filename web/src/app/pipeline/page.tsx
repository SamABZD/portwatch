import type { Metadata } from "next";
import { Suspense } from "react";
import PipelineView from "@/components/pipeline-view";

export const metadata: Metadata = {
  title: "Data engineering pipeline",
  description:
    "Inspect maritime data engineering: Python, PostGIS and Prefect pipelines behind vessel observations, plus the frozen Los Angeles dbt history.",
};

export default function PipelinePage() {
  return (
    <Suspense fallback={
      <main className="workspace pipeline-workspace">
        <section className="pipeline-intro">
          <h1>Maritime data engineering.</h1>
          <p role="status">Loading the PortWatch pipeline view…</p>
        </section>
      </main>
    }>
      <PipelineView />
    </Suspense>
  );
}
