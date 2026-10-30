import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Spin } from 'antd'
import { AppLayout, RequireAuth, RequireHr } from './AppLayout'
import LoginPage from './pages/LoginPage'
import './styles/global.css'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ChatPage = lazy(() => import('./pages/ChatPage'))
const CandidatesPage = lazy(() => import('./pages/CandidatesPage'))
const DocumentKnowledgePage = lazy(() => import('./pages/DocumentKnowledgePage'))
const RetrievalTestPage = lazy(() => import('./pages/RetrievalTestPage'))
const StaffingProjectsPage = lazy(() => import('./pages/StaffingProjectsPage'))
const StaffingProjectPage = lazy(() => import('./pages/StaffingProjectPage'))
const StaffingEmployeesPage = lazy(() => import('./pages/StaffingEmployeesPage'))
const StaffingSystemPage = lazy(() => import('./pages/StaffingSystemPage'))

export default function App() {
  return <BrowserRouter><Suspense fallback={<div className="loading-screen"><Spin /></div>}><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<RequireAuth />}><Route element={<AppLayout />}>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/chat" element={<ChatPage />} />
      <Route path="/staffing" element={<StaffingProjectsPage />} />
      <Route path="/staffing/projects/:projectId" element={<StaffingProjectPage />} />
      <Route path="/staffing/employees" element={<StaffingEmployeesPage />} />
      <Route path="/staffing/system" element={<StaffingSystemPage />} />
      <Route element={<RequireHr />}>
        <Route path="/candidates" element={<CandidatesPage />} />
        <Route path="/candidates/:id" element={<DocumentKnowledgePage />} />
        <Route path="/knowledge/retrieval-test" element={<RetrievalTestPage />} />
      </Route>
    </Route></Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense></BrowserRouter>
}
