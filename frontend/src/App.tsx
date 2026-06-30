import Layout from './components/Layout'
import { useUiStore } from './store/uiStore'
import AssistantPage from './pages/AssistantPage'
import ProjectsPage from './pages/ProjectsPage'
import FramesPage from './pages/FramesPage'
import GraphPage from './pages/GraphPage'
import ProjectionsPage from './pages/ProjectionsPage'
import SpacesPage from './pages/SpacesPage'
import SkillsPage from './pages/SkillsPage'
import FeedbackPage from './pages/FeedbackPage'
import SettingsPage from './pages/SettingsPage'

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

  return <Layout>{renderPage()}</Layout>
}

export default App
