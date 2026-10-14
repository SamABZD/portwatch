import Dashboard from "@/components/dashboard";
import { Suspense } from "react";

export default function Home() {
  return <Suspense fallback={<main className="workspace"><h1>PortWatch — maritime data engineering demo</h1><p role="status">Reading the selected port…</p></main>}><Dashboard /></Suspense>;
}
