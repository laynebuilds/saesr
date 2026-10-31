import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// No StrictMode: the data source opens real sockets once per page load.
createRoot(document.getElementById('root')!).render(<App />)
