import { Suspense, lazy } from 'react'
import Layout from './components/Layout'
import { useUiStore } from './store/uiStore'

const AssistantPage = lazy(() => import('./pages/AssistantPage'))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'))
const FramesPage = lazy(() => import('./pages/FramesPage'))
const GraphPage = lazy(() => import('./pages/GraphPage'))
const ProjectionsPage = lazy(() => import('./pages/ProjectionsPage'))
const SpacesPage = lazy(() => import('./pages/SpacesPage'))
const SkillsPage = lazy(() => import('./pages/SkillsPage'))
const FeedbackPage = lazy(() => import('./pages/FeedbackPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))

function App() {
  const page = useUiStore(s => s.page)

  const renderPage = () => {
    switch (page) {
      case 'assistant':    return <AssistantPage />
      case 'projects':    return <ProjectsPage />
      case 'frames':      return <FramesPage />
      case 'graph':       return <GraphPage />
      case 'projections': return <ProjectionsPage />
      case 'spaces':      return <SpacesPage />
      case 'skills':      return <SkillsPage />
      case 'feedback':    return <FeedbackPage />
      case 'settings':    return <SettingsPage />
      default:            return <AssistantPage />
    }
  }

  return (
    <Layout>
      <Suspense fallback={<div className="page-loading">Loading...</div>}>
        {renderPage()}
      </Suspense>
    </Layout>
  )
}

export default App
