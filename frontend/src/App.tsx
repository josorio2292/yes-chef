import { BrowserRouter, Route, Routes, useParams } from 'react-router-dom'
import SubmitView from './views/SubmitView'
import KitchenView from './views/KitchenView'
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
        <Route path="/kitchen/:jobId" element={<KitchenView />} />
        <Route path="/pass/:jobId" element={<PassViewRoute />} />
        <Route path="/quote/:jobId" element={<PassViewRoute />} />
      </Routes>
    </BrowserRouter>
  )
}
