import { Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage'
import Stage1Page from './pages/Stage1Page'
import Stage2Page from './pages/Stage2Page'
import ManagerPage from './pages/ManagerPage'
import NotFoundPage from './pages/NotFoundPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/stage1/:sessionId" element={<Stage1Page />} />
      {/* Employee link — carries a single-use invite token, never a session ID */}
      <Route path="/stage2/i/:inviteToken" element={<Stage2Page />} />
      <Route path="/manager/:stage1SessionId" element={<ManagerPage />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
