import { useEffect } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { source } from './data'
import { Shell } from './components/Shell'
import { Overview } from './pages/Overview'
import { Camera, Cameras } from './pages/Camera'
import { Incidents } from './pages/Incidents'
import { Incident } from './pages/Incident'
import { History } from './pages/History'
import { Agents } from './pages/Agents'
import { Analyze } from './pages/Analyze'
import { ComplianceLog } from './pages/ComplianceLog'

export default function App() {
  useEffect(() => {
    source.start()
    return () => source.stop()
  }, [])
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '') || undefined}>
      <Shell>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/cameras" element={<Cameras />} />
          <Route path="/cameras/:id" element={<Camera />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/incidents/:id" element={<Incident />} />
          <Route path="/review" element={<Analyze />} />
          <Route path="/log" element={<ComplianceLog />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/history" element={<History />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  )
}
