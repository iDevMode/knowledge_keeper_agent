import { Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage'
import Stage1Page from './pages/Stage1Page'
import Stage2Page from './pages/Stage2Page'
import NotFoundPage from './pages/NotFoundPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/stage1/:sessionId" element={<Stage1Page />} />
      <Route path="/stage2/:stage1SessionId" element={<Stage2Page />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
