import { BrowserRouter, Route, Routes, useParams } from 'react-router-dom'
import SubmitView from './views/SubmitView'
import KitchenViewPlaceholder from './views/KitchenViewPlaceholder'
import PassView from './views/PassView'

function PassViewRoute() {
  const { jobId } = useParams<{ jobId: string }>()
  return <PassView jobId={jobId} />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<SubmitView />} />
        <Route path="/kitchen/:jobId" element={<KitchenViewPlaceholder />} />
        <Route path="/quote/:jobId" element={<PassViewRoute />} />
      </Routes>
    </BrowserRouter>
  )
}
