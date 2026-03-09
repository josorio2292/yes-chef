import { BrowserRouter, Route, Routes } from 'react-router-dom'
import SubmitView from './views/SubmitView'
import KitchenView from './views/KitchenView'
import PassView from './views/PassView'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<SubmitView />} />
        <Route path="/kitchen/:quoteId" element={<KitchenView />} />
        <Route path="/pass/:quoteId" element={<PassView />} />
      </Routes>
    </BrowserRouter>
  )
}
