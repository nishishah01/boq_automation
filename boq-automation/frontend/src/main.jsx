import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import { FeedbackProvider } from "./components/feedback";
import { WorkProvider } from "./context/WorkContext";
import AppLayout from "./layouts/AppLayout";
import DashboardPage from "./pages/DashboardPage";
import ExportPage from "./pages/ExportPage";
import KnowledgePage from "./pages/KnowledgePage";
import MappingPage from "./pages/MappingPage";
import ReviewPage from "./pages/ReviewPage";
import SettingsPage from "./pages/SettingsPage";
import UploadPage from "./pages/UploadPage";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <WorkProvider>
        <FeedbackProvider>
          <Routes>
            <Route element={<AppLayout />}>
              <Route index element={<DashboardPage />} />
              <Route path="upload" element={<UploadPage />} />
              <Route path="mapping" element={<MappingPage />} />
              <Route path="review" element={<ReviewPage />} />
              <Route path="exports" element={<ExportPage />} />
              <Route path="knowledge" element={<KnowledgePage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </FeedbackProvider>
      </WorkProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
