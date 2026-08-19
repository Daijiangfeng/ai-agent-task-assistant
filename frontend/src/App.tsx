import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { NavShell, Spinner } from "./components";

// 路由级懒加载，配合 vite manualChunks 拆分 vendor。
const DashboardPage = lazy(() => import("./features/dashboard/DashboardPage"));
const TasksPage = lazy(() => import("./features/tasks/TasksPage"));
const TaskDetailPage = lazy(() => import("./features/tasks/TaskDetailPage"));
const TemplatesPage = lazy(() => import("./features/templates/TemplatesPage"));
const MonitoringPage = lazy(() => import("./features/monitoring/MonitoringPage"));

export default function App() {
  return (
    <Suspense
      fallback={
        <div style={{ padding: 48 }}>
          <Spinner label="加载界面…" />
        </div>
      }
    >
      <Routes>
        <Route element={<NavShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="tasks/:taskId" element={<TaskDetailPage />} />
          <Route path="templates" element={<TemplatesPage />} />
          <Route path="monitoring" element={<MonitoringPage />} />
          <Route path="monitoring/:taskId" element={<MonitoringPage />} />
          <Route path="*" element={<DashboardPage />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
