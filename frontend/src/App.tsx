import { BrowserRouter, Route, Routes } from 'react-router-dom'
import SubmitView from './views/SubmitView'
import KitchenView from './views/KitchenView'
import PassView from './views/PassView'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<SubmitView />} />
        <Route path="/kitchen/:jobId" element={<KitchenView />} />
        <Route path="/pass/:jobId" element={<PassView />} />
        <Route path="/quote/:jobId" element={<PassView />} />
      </Routes>
    </BrowserRouter>
  )
}
