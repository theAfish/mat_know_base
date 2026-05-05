## 0. Define Scope (Do Not Skip)

* [ ] Audit current Streamlit app

  * [ ] List all UI modules
  * [ ] Categorize:

    * [ ] Graph visualization (core)
    * [ ] Tables / text panels
    * [ ] Controls (filters, buttons, inputs)

* [ ] Define MVP (keep it minimal)

  * [ ] Render graph using React Flow
  * [ ] Node click interaction
  * [ ] Call backend APIs
  * [ ] Basic dynamic graph updates

---

## 1. Initialize Frontend Project

* [ ] Create project

```bash
npm create vite@latest graph-ui -- --template react-ts
cd graph-ui
npm install
```

* [ ] Install dependencies

```bash
npm install reactflow zustand axios
```

* [ ] Optional utilities

```bash
npm install classnames
```

---

## 2. Project Structure

* [ ] Set up directories

```
src/
  components/
  pages/
  store/
  api/
  types/
  utils/
```

---

## 3. Define Core Data Model

* [ ] Normalize graph schema (critical step)

```ts
// src/types/graph.ts

export interface GraphNode {
  id: string;
  type?: string;
  data: {
    label: string;
    [key: string]: any;
  };
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type?: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
```

---

## 4. API Layer (Replace Streamlit Backend Calls)

* [ ] Create API wrapper

```ts
// src/api/graph.ts
import axios from "axios";

export const fetchGraph = async () => {
  const res = await axios.get("/api/graph");
  return res.data;
};
```

---

## 5. State Management (Replace Streamlit Session State)

* [ ] Create Zustand store

```ts
// src/store/graphStore.ts
import { create } from "zustand";
import { GraphData } from "../types/graph";

interface GraphState {
  graph: GraphData | null;
  setGraph: (g: GraphData) => void;
}

export const useGraphStore = create<GraphState>((set) => ({
  graph: null,
  setGraph: (g) => set({ graph: g }),
}));
```

---

## 6. Integrate React Flow

* [ ] Create graph component

```tsx
// src/components/GraphView.tsx
import ReactFlow from "reactflow";
import "reactflow/dist/style.css";
import { useGraphStore } from "../store/graphStore";

export default function GraphView() {
  const graph = useGraphStore((s) => s.graph);

  if (!graph) return <div>Loading...</div>;

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <ReactFlow
        nodes={graph.nodes}
        edges={graph.edges}
        fitView
      />
    </div>
  );
}
```

---

## 7. Page Integration (Replace Streamlit Pages)

* [ ] Create main page

```tsx
// src/pages/MainPage.tsx
import { useEffect } from "react";
import GraphView from "../components/GraphView";
import { fetchGraph } from "../api/graph";
import { useGraphStore } from "../store/graphStore";

export default function MainPage() {
  const setGraph = useGraphStore((s) => s.setGraph);

  useEffect(() => {
    fetchGraph().then(setGraph);
  }, []);

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <GraphView />
    </div>
  );
}
```

---

## 8. Migrate Interactions

### Node Click

```tsx
<ReactFlow
  nodes={graph.nodes}
  edges={graph.edges}
  onNodeClick={(event, node) => {
    console.log("clicked:", node);
  }}
/>
```

---

### Backend Interaction (e.g. expand node)

```ts
export const fetchNeighbors = async (nodeId: string) => {
  const res = await axios.get(`/api/neighbors?id=${nodeId}`);
  return res.data;
};
```

---

### Update Graph State

```ts
onNodeClick={async (_, node) => {
  const subgraph = await fetchNeighbors(node.id);
  setGraph(mergeGraph(graph, subgraph));
}}
```

---

## 9. Layout Refactor (Replace Streamlit Layout)

* [ ] Introduce layout structure

```
[ Sidebar ]   [ Graph Canvas ]
```

* [ ] Sidebar responsibilities:

  * [ ] Search
  * [ ] Filters
  * [ ] Action controls

---

## 10. Styling Strategy

* [ ] Choose one:

  * [ ] CSS Modules (simple)
  * [ ] Tailwind CSS (recommended for scalability)

---

## 11. Backend Integration

* [ ] Ensure backend endpoints exist:

  * [ ] `/api/graph`
  * [ ] `/api/neighbors`
  * [ ] `/api/search`

* [ ] Enable CORS in backend (e.g. FastAPI)

---

## 12. Decommission Streamlit

* [ ] Mark Streamlit UI as deprecated
* [ ] Keep backend logic
* [ ] Fully switch UI to React frontend

---

## 13. Acceptance Criteria

Migration is complete when:

* [ ] Graph renders correctly
* [ ] Node click works
* [ ] API calls succeed
* [ ] Graph updates dynamically
* [ ] No major UI blocking issues (small graphs)

---

## 🚧 Future Work (Out of Scope for Now)

* [ ] Large graph performance (WebGL, virtualization)
* [ ] Advanced layout algorithms
* [ ] Graph caching
* [ ] Undo / redo
* [ ] Multi-user collaboration