// import { useState, useRef } from 'react';
// import { Box, AppBar, Toolbar, Typography, Switch, Drawer, Card, List, ListItem, ButtonGroup, Button, Snackbar, TextField, InputAdornment } from '@mui/material';
// import ReactFlow, { MiniMap, Controls, Background, ReactFlowProvider, useReactFlow } from 'react-flow-renderer';
// import { loadGraph } from './graphLoader';
// import CustomNode from './CustomNode';
// import SearchIcon from './assets/search.svg';
// import AgentIcon from './assets/agent.svg';
// import ToolIcon from './assets/tool.svg';
// import OrchestratorIcon from './assets/Planner.svg';
// import StartIcon from './assets/start.svg';
// import TeamIcon from './assets/generic.svg';

// // Node types mapping
// const nodeTypes = { customNode: CustomNode };
// const { nodes: initialNodes, edges: initialEdges, framework } = loadGraph();

// function App() {
//   const [themeDark, setThemeDark] = useState(false);
//   const [drawerOpen, setDrawerOpen] = useState(false);
//   const [selectedDetails, setSelectedDetails] = useState<any>(null);
//   const [snackbarOpen, setSnackbarOpen] = useState(false);
//   const [search, setSearch] = useState('');
//   const [highlighted, setHighlighted] = useState<string | null>(null);
//   const reactFlowWrapper = useRef<HTMLDivElement>(null);

//   const {zoomTo, getZoom} = useReactFlow();


//   // Handlers
//   const handleNodeClick = (_event: any, node: any) => {
//     setSelectedDetails({ type: 'node', ...node.data });
//     setDrawerOpen(true);
//     setHighlighted(node.id);
//   };
//   const handleEdgeClick = (_event: any, edge: any) => {
//     setSelectedDetails({ type: 'edge', ...edge });
//     setDrawerOpen(true);
//   };
//   const handleCloseDrawer = () => {
//     setDrawerOpen(false);
//     setSelectedDetails(null);
//     setHighlighted(null);
//   };
//   const handleThemeToggle = () => setThemeDark((prev) => !prev);
//   const handleSnackbarClose = () => setSnackbarOpen(false);

//   // Search logic
//   const handleSearch = (e: any) => {
//     setSearch(e.target.value);
//     const found = initialNodes.find(n => n.id.toLowerCase().includes(e.target.value.toLowerCase()));
//     setHighlighted(found ? found.id : null);
//     // Zoom to node if found
//     if (found) {
//       zoomTo(found.id);
//     }
//   };

//   // Custom node style for highlight
//   const getNodeStyle = (id: string, color: string) => ({
//     border: highlighted === id ? '3px solid #E57373' : '2px solid #fff',
//     boxShadow: highlighted === id ? '0 0 16px #E57373' : '0 2px 8px rgba(80,80,120,0.08)',
//     background: color,
//     borderRadius: 16,
//   });

//   return (
//     <>
//       <ReactFlowProvider>
//         <Box sx={{ height: '100vh', width: '100vw', display: 'flex', flexDirection: 'column', bgcolor: themeDark ? '#222' : '#fafafa', overflow: 'hidden' }}>
//           {/* Header */}
//           <AppBar position="static" color={themeDark ? 'default' : 'primary'}>
//             <Toolbar>
//               <Typography variant="h6">Agent-Wiz Visualization ({framework})</Typography>
//               <Box sx={{ flexGrow: 1 }} />
//               <Switch checked={themeDark} onChange={handleThemeToggle} />
//             </Toolbar>
//           </AppBar>

//           {/* Search Bar */}
//         <Box sx={{ p: 2, pb: 0, display: 'flex', alignItems: 'center', gap: 2 }}>
//           <TextField
//             variant="outlined"
//             size="small"
//             placeholder="Search node..."
//             value={search}
//             onChange={handleSearch}
//             InputProps={{
//               startAdornment: (
//                 <InputAdornment position="start">
//                   <img src={SearchIcon} alt="Search" style={{ width: 20, opacity: 0.5 }} />
//                 </InputAdornment>
//               ),
//             }}
//             sx={{ width: 260 }}
//           />
//         </Box>

//           {/* Main Content */}
//         <Box sx={{ flex: 1, display: 'flex', minHeight: 0 }}>
//           {/* Sidebar */}
//           <Box sx={{ width: 240, p: 2, bgcolor: themeDark ? '#333' : 'background.paper', minHeight: 0 }}>
//             <Card sx={{ mt: 2 }}>
//               <List>
//                 <ListItem><img src={AgentIcon} alt="Agent" style={{width:24,marginRight:8}}/> Agent</ListItem>
//                 <ListItem><img src={ToolIcon} alt="Tool" style={{width:24,marginRight:8}}/> Tool</ListItem>
//                 <ListItem><img src={OrchestratorIcon} alt="Orchestrator" style={{width:24,marginRight:8}}/> Orchestrator</ListItem>
//                 <ListItem><img src={StartIcon} alt="Start" style={{width:24,marginRight:8}}/> Start/End</ListItem>
//                 <ListItem><img src={TeamIcon} alt="Team" style={{width:24,marginRight:8}}/> Team</ListItem>
//               </List>
//             </Card>
//           </Box>

//             {/* React Flow Canvas */}
//           <Box sx={{ flex: 1, position: 'relative', minHeight: 0 }} ref={reactFlowWrapper}>
//             <ReactFlow
//               nodes={initialNodes.map(n => ({
//                 ...n,
//                 style: getNodeStyle(n.id, n.data.color),
//               }))}
//               edges={initialEdges}
//               nodeTypes={nodeTypes}
//               fitView
//               style={{ width: '100%', height: '100%' }}
//               onNodeClick={handleNodeClick}
//               onEdgeClick={handleEdgeClick}
//               minZoom={0.3}
//               maxZoom={2}
//               snapToGrid
//               snapGrid={[24, 24]}
//             >
//               <MiniMap nodeColor={n => n.data.color} />
//               <Controls />
//               <Background color="#f5f5fa" gap={24} />
//             </ReactFlow>
//           </Box>

//             {/* Details Drawer */}
//           <Drawer anchor="right" open={drawerOpen} onClose={handleCloseDrawer}>
//             <Box sx={{ width: 340, p: 2, maxHeight: '100vh', overflowY: 'auto' }}>
//               <Typography variant="h6">Details</Typography>
//               {selectedDetails && selectedDetails.type === 'node' && (
//                 <Box>
//                   <Typography variant="subtitle1">{selectedDetails.label}</Typography>
//                   <List>
//                     <ListItem>Type: {selectedDetails.nodeType}</ListItem>
//                     {selectedDetails.functionName && <ListItem>Function: {selectedDetails.functionName}</ListItem>}
//                     {selectedDetails.docstring && <ListItem>Docstring: {selectedDetails.docstring}</ListItem>}
//                     {selectedDetails.sourceLocation && (
//                       <ListItem>
//                         Source: {selectedDetails.sourceLocation.file} (Line {selectedDetails.sourceLocation.line})
//                       </ListItem>
//                     )}
//                     {selectedDetails.metadata && Object.keys(selectedDetails.metadata).length > 0 && (
//                       <ListItem>
//                         Metadata:
//                         <pre style={{ maxWidth: 300, overflowX: 'auto' }}>{JSON.stringify(selectedDetails.metadata, null, 2)}</pre>
//                       </ListItem>
//                     )}
//                   </List>
//                 </Box>
//               )}
//               {selectedDetails && selectedDetails.type === 'edge' && (
//                 <Box>
//                   <Typography variant="subtitle1">Edge: {selectedDetails.source} → {selectedDetails.target}</Typography>
//                   <List>
//                     <ListItem>Condition: {selectedDetails.label}</ListItem>
//                     {selectedDetails.data && Object.keys(selectedDetails.data).length > 0 && (
//                       <ListItem>
//                         Metadata:
//                         <pre style={{ maxWidth: 300, overflowX: 'auto' }}>{JSON.stringify(selectedDetails.data, null, 2)}</pre>
//                       </ListItem>
//                     )}
//                   </List>
//                 </Box>
//               )}
//               <Button variant="outlined" onClick={handleCloseDrawer}>Close</Button>
//             </Box>
//           </Drawer>
//         </Box>

//           {/* Bottom Controls */}
//           <Box sx={{ p: 2, display: 'flex', alignItems: 'center' }}>
//             <ButtonGroup>
//               <Button onClick={() => setSnackbarOpen(true)}>Reset Layout</Button>
//               <Button>Spread Nodes</Button>
//               <Button>Export</Button>
//             </ButtonGroup>
//             <Snackbar open={snackbarOpen} autoHideDuration={2000} onClose={handleSnackbarClose} message="Layout reset!" />
//           </Box>
//         </Box>
//       </ReactFlowProvider>
//     </>
//   );
// }

// export default App;

import { useState, useRef } from 'react';
import { Box, AppBar, Toolbar, Typography, Switch, Drawer, Card, List, ListItem, ButtonGroup, Button, Snackbar, TextField, InputAdornment } from '@mui/material';
import ReactFlow, { MiniMap, Controls, Background, ReactFlowProvider } from 'react-flow-renderer';
import { loadGraph } from './graphLoader';
import CustomNode from './CustomNode';
import SearchIcon from './assets/search.svg';
import AgentIcon from './assets/agent.svg';
import ToolIcon from './assets/tool.svg';
import OrchestratorIcon from './assets/Planner.svg';
import StartIcon from './assets/start.svg';
import TeamIcon from './assets/generic.svg';
import { useReactFlow } from 'reactflow';
import type { Node, Edge } from 'react-flow-renderer';

type FlowCanvasProps = {
  nodes: Node<any>[];
  edges: Edge<any>[];
  highlighted: string | null;
  setHighlighted: React.Dispatch<React.SetStateAction<string | null>>;
  handleNodeClick: (event: React.MouseEvent, node: Node<any>) => void;
  handleEdgeClick: (event: React.MouseEvent, edge: Edge<any>) => void;
};

const nodeTypes = { customNode: CustomNode };
const { nodes: initialNodes, edges: initialEdges, framework } = loadGraph();

function FlowCanvas({
  nodes,
  edges,
  highlighted,
  setHighlighted,
  handleNodeClick,
  handleEdgeClick,
}: FlowCanvasProps) {
  const { zoomTo } = useReactFlow();

  // Search logic inside context
  const [search, setSearch] = useState('');
  const handleSearch = (e: any) => {
    setSearch(e.target.value);
    const found = nodes.find((n: any) => n.id.toLowerCase().includes(e.target.value.toLowerCase()));
    setHighlighted(found ? found.id : null);
    if (found) zoomTo(Number(found.id));
  };

  const getNodeStyle = (id: string, color: string) => ({
    border: highlighted === id ? '3px solid #E57373' : '2px solid #fff',
    boxShadow: highlighted === id ? '0 0 16px #E57373' : '0 2px 8px rgba(80,80,120,0.08)',
    background: color,
    borderRadius: 16,
  });

  return (
    <>
      {/* Search Bar */}
      <Box sx={{ p: 2, pb: 0, display: 'flex', alignItems: 'center', gap: 2 }}>
        <TextField
          variant="outlined"
          size="small"
          placeholder="Search node..."
          value={search}
          onChange={handleSearch}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <img src={SearchIcon} alt="Search" style={{ width: 20, opacity: 0.5 }} />
              </InputAdornment>
            ),
          }}
          sx={{ width: 260 }}
        />
      </Box>
      <ReactFlow
        nodes={nodes.map((n: any) => ({
          ...n,
          style: getNodeStyle(n.id, n.data.color),
        }))}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        style={{ width: '100%', height: '100%' }}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        minZoom={0.3}
        maxZoom={2}
        snapToGrid
        snapGrid={[24, 24]}
      >
        <MiniMap nodeColor={n => n.data.color} />
        <Controls />
        <Background color="#f5f5fa" gap={24} />
      </ReactFlow>
    </>
  );
}

function App() {
  const [themeDark, setThemeDark] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedDetails, setSelectedDetails] = useState<any>(null);
  const [snackbarOpen, setSnackbarOpen] = useState(false);
  const [highlighted, setHighlighted] = useState<string | null>(null);

  // Handlers
  const handleNodeClick = (_event: any, node: any) => {
    setSelectedDetails({ type: 'node', ...node.data });
    setDrawerOpen(true);
    setHighlighted(node.id);
  };
  const handleEdgeClick = (_event: any, edge: any) => {
    setSelectedDetails({ type: 'edge', ...edge });
    setDrawerOpen(true);
  };
  const handleCloseDrawer = () => {
    setDrawerOpen(false);
    setSelectedDetails(null);
    setHighlighted(null);
  };
  const handleThemeToggle = () => setThemeDark((prev) => !prev);
  const handleSnackbarClose = () => setSnackbarOpen(false);

  return (
    <ReactFlowProvider>
      <Box sx={{ height: '100vh', width: '100vw', display: 'flex', flexDirection: 'column', bgcolor: themeDark ? '#222' : '#fafafa', overflow: 'hidden' }}>
        {/* Header */}
        <AppBar position="static" color={themeDark ? 'default' : 'primary'}>
          <Toolbar>
            <Typography variant="h6">Agent-Wiz Visualization ({framework})</Typography>
            <Box sx={{ flexGrow: 1 }} />
            <Switch checked={themeDark} onChange={handleThemeToggle} />
          </Toolbar>
        </AppBar>

        {/* Main Content */}
        <Box sx={{ flex: 1, display: 'flex', minHeight: 0 }}>
          {/* Sidebar */}
          <Box sx={{ width: 240, p: 2, bgcolor: themeDark ? '#333' : 'background.paper', minHeight: 0 }}>
            <Card sx={{ mt: 2 }}>
              <List>
                <ListItem><img src={AgentIcon} alt="Agent" style={{width:24,marginRight:8}}/> Agent</ListItem>
                <ListItem><img src={ToolIcon} alt="Tool" style={{width:24,marginRight:8}}/> Tool</ListItem>
                <ListItem><img src={OrchestratorIcon} alt="Orchestrator" style={{width:24,marginRight:8}}/> Orchestrator</ListItem>
                <ListItem><img src={StartIcon} alt="Start" style={{width:24,marginRight:8}}/> Start/End</ListItem>
                <ListItem><img src={TeamIcon} alt="Team" style={{width:24,marginRight:8}}/> Team</ListItem>
              </List>
            </Card>
          </Box>

          {/* React Flow Canvas & Search */}
          <Box sx={{ flex: 1, position: 'relative', minHeight: 0 }}>
            <FlowCanvas
              nodes={initialNodes}
              edges={initialEdges}
              highlighted={highlighted}
              setHighlighted={setHighlighted}
              handleNodeClick={handleNodeClick}
              handleEdgeClick={handleEdgeClick}
            />
          </Box>

          {/* Details Drawer */}
          <Drawer anchor="right" open={drawerOpen} onClose={handleCloseDrawer}>
            <Box sx={{ width: 340, p: 2, maxHeight: '100vh', overflowY: 'auto' }}>
              <Typography variant="h6">Details</Typography>
              {selectedDetails && selectedDetails.type === 'node' && (
                <Box>
                  <Typography variant="subtitle1">{selectedDetails.label}</Typography>
                  <List>
                    <ListItem>Type: {selectedDetails.nodeType}</ListItem>
                    {selectedDetails.functionName && <ListItem>Function: {selectedDetails.functionName}</ListItem>}
                    {selectedDetails.docstring && <ListItem>Docstring: {selectedDetails.docstring}</ListItem>}
                    {selectedDetails.sourceLocation && (
                      <ListItem>
                        Source: {selectedDetails.sourceLocation.file} (Line {selectedDetails.sourceLocation.line})
                      </ListItem>
                    )}
                    {selectedDetails.metadata && Object.keys(selectedDetails.metadata).length > 0 && (
                      <ListItem>
                        Metadata:
                        <pre style={{ maxWidth: 300, overflowX: 'auto' }}>{JSON.stringify(selectedDetails.metadata, null, 2)}</pre>
                      </ListItem>
                    )}
                  </List>
                </Box>
              )}
              {selectedDetails && selectedDetails.type === 'edge' && (
                <Box>
                  <Typography variant="subtitle1">Edge: {selectedDetails.source} → {selectedDetails.target}</Typography>
                  <List>
                    <ListItem>Condition: {selectedDetails.label}</ListItem>
                    {selectedDetails.data && Object.keys(selectedDetails.data).length > 0 && (
                      <ListItem>
                        Metadata:
                        <pre style={{ maxWidth: 300, overflowX: 'auto' }}>{JSON.stringify(selectedDetails.data, null, 2)}</pre>
                      </ListItem>
                    )}
                  </List>
                </Box>
              )}
              <Button variant="outlined" onClick={handleCloseDrawer}>Close</Button>
            </Box>
          </Drawer>
        </Box>

        {/* Bottom Controls */}
        <Box sx={{ p: 2, display: 'flex', alignItems: 'center' }}>
          <ButtonGroup>
            <Button onClick={() => setSnackbarOpen(true)}>Reset Layout</Button>
            <Button>Spread Nodes</Button>
            <Button>Export</Button>
          </ButtonGroup>
          <Snackbar open={snackbarOpen} autoHideDuration={2000} onClose={handleSnackbarClose} message="Layout reset!" />
        </Box>
      </Box>
    </ReactFlowProvider>
  );
}

export default App;