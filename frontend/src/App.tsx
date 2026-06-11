import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AppShell } from "./layouts/AppShell";
import { HomePage } from "./pages/HomePage";
import { CodeDocPage } from "./pages/CodeDocPage";
import { DocsPage } from "./pages/DocsPage";
import { ProjectHomePage } from "./pages/ProjectHomePage";
import { TraceabilityPage } from "./pages/TraceabilityPage";
import { SrePage } from "./pages/SrePage";
import { MdDashboardPage } from "./pages/MdDashboardPage";
import { DevPage } from "./pages/DevPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "code-doc", element: <CodeDocPage /> },
      { path: "projects/:projectId", element: <ProjectHomePage /> },
      { path: "docs", element: <DocsPage /> },
      { path: "docs/:projectId", element: <DocsPage /> },
      { path: "docs/:projectId/trace", element: <TraceabilityPage /> },
      { path: "docs/:projectId/:docId", element: <DocsPage /> },
      { path: "sre", element: <SrePage /> },
      { path: "md", element: <MdDashboardPage /> },
      { path: "dev", element: <DevPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
