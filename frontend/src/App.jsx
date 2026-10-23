import { Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage'
import VerifyPage from './pages/VerifyPage'
import DashboardPage from './pages/DashboardPage'
import Stage1Page from './pages/Stage1Page'
import Stage2Page from './pages/Stage2Page'
import NotFoundPage from './pages/NotFoundPage'

export default function App() {
  return (
    <Routes>
      {/* Sign-in; redirects to the dashboard when already signed in. */}
      <Route path="/" element={<HomePage />} />
      <Route path="/auth/verify" element={<VerifyPage />} />
      <Route path="/dashboard" element={<DashboardPage />} />
      <Route path="/stage1/:sessionId" element={<Stage1Page />} />
      {/* The employee's own session id — it used to be the manager's, which
          made the shared link a credential for the manager's interview. */}
      <Route path="/stage2/:sessionId" element={<Stage2Page />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
