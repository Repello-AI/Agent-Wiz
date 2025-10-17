import { useState, useCallback } from 'react';
import { Box, AppBar, Toolbar, Typography, Switch, Drawer, Card, List, ListItem, ButtonGroup, Button, Snackbar, TextField, InputAdornment } from '@mui/material';
import ReactFlow, { MiniMap, Controls, Background, ReactFlowProvider, useReactFlow} from 'reactflow';
import type {Node, Edge} from 'reactflow';
import { loadGraph } from './graphLoader';
import SearchIcon from './assets/search.svg';
import AgentIcon from './assets/agent.svg';
import ToolIcon from './assets/tool.svg';
import OrchestratorIcon from './assets/Planner.svg';
import StartIcon from './assets/start.svg';
import TeamIcon from './assets/generic.svg';
import CustomNode from './customNode';
import 'reactflow/dist/style.css';

const nodeTypes = { customNode: CustomNode };
const { nodes: initialNodes, edges: initialEdges, framework } = loadGraph();

function FlowCanvas({ nodes, edges, highlighted, onNodeClick, onEdgeClick }: {
  nodes: Node<any>[]; edges: Edge<any>[]; highlighted: string | null;
  onNodeClick: (e: any, node: any) => void; onEdgeClick: (e: any, edge: any) => void;
}) {
  const [search, setSearch] = useState('');
  const { setCenter, fitView } = useReactFlow();

  const handleSearch = (e: any) => {
    const q = e.target.value || '';
    setSearch(q);

    if (!q) {
      // show all nodes by fitting view
      fitView({ padding: 0.12 });
      return;
    }

    const found = nodes.find((n: any) => n.data?.label?.toLowerCase().includes(q.toLowerCase()) || n.id.toLowerCase().includes(q.toLowerCase()));
    if (found) {
      // center and zoom to the node
      setCenter(found.position.x + (found.width || 0) / 2, found.position.y + (found.height || 0) / 2, { zoom: 1.3, duration: 400 });
    }
  };

  const getNodeStyle = (id: string, color: string) => ({
    border: highlighted === id ? '3px solid rgba(229,87,87,0.95)' : '2px solid rgba(255,255,255,0.06)',
    boxShadow: highlighted === id ? '0 8px 30px rgba(229,87,87,0.12)' : '0 2px 12px rgba(80,80,120,0.06)',
    background: color || 'linear-gradient(180deg, #fff, #f7f7fb)',
    borderRadius: 12,
    padding: 6,
  });

  return (
    <>
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
                <img src={SearchIcon} alt="Search" style={{ width: 18, opacity: 0.6 }} />
              </InputAdornment>
            ),
          }}
          sx={{ width: 320, maxWidth: '40vw' }}
        />
        <Box sx={{ flex: 1 }} />
      </Box>

      <ReactFlow
        nodes={nodes.map(n => ({ ...n, style: getNodeStyle(n.id, n.data?.color) }))}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        style={{ width: '100%', height: '100%' }}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        minZoom={0.2}
        maxZoom={2.2}
        snapToGrid
        snapGrid={[24, 24]}
      >
        <MiniMap nodeColor={n => (n.data?.color || '#777')} zoomable />
        <Controls />
        <Background color="#f5f5fa" gap={24} />
      </ReactFlow>
    </>
  );
}

export default function App() {
  const [themeDark, setThemeDark] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedDetails, setSelectedDetails] = useState<any>(null);
  const [snackbarOpen, setSnackbarOpen] = useState(false);
  const [highlighted, setHighlighted] = useState<string | null>(null);

  const handleNodeClick = useCallback((_e: any, node: any) => {
    setSelectedDetails({ type: 'node', ...node.data, id: node.id, position: node.position });
    setDrawerOpen(true);
    setHighlighted(node.id);
  }, []);

  const handleEdgeClick = useCallback((_e: any, edge: any) => {
    setSelectedDetails({ type: 'edge', ...edge });
    setDrawerOpen(true);
  }, []);

  const handleCloseDrawer = () => { setDrawerOpen(false); setSelectedDetails(null); setHighlighted(null); };
  const handleThemeToggle = () => setThemeDark((p) => !p);
  const handleSnackbarClose = () => setSnackbarOpen(false);

  return (
    <ReactFlowProvider>
      <Box sx={{ height: '100vh', width: '100vw', display: 'flex', flexDirection: 'column', bgcolor: themeDark ? '#0f1720' : '#fbfcff', overflow: 'hidden' }}>
        <AppBar position="static" color={themeDark ? 'default' : 'primary'}>
          <Toolbar>
            <Typography variant="h6">Agent-Workflow ({framework})</Typography>
            <Box sx={{ flex: 1 }} />
            <Typography variant="body2" sx={{ mr: 1 }}>{themeDark ? 'Dark' : 'Light'}</Typography>
            <Switch checked={themeDark} onChange={handleThemeToggle} />
          </Toolbar>
        </AppBar>

        <Box sx={{ flex: 1, display: 'flex', minHeight: 0 }}>
          <Box sx={{ width: { xs: 64, sm: 220 }, p: 2, bgcolor: themeDark ? '#071025' : 'background.paper', minHeight: 0, borderRight: '1px solid rgba(0,0,0,0.06)' }}>
            <Card sx={{ p: 1, mb: 2, display: 'flex', gap: 1, alignItems: 'center', justifyContent: 'center' }}>
              <img src={AgentIcon} alt="Agent" style={{ width: 28 }} />
              <Typography variant="body2">Agent</Typography>
            </Card>
            <Card sx={{ p: 1, mb: 2 }}>
              <List>
                <ListItem><img src={ToolIcon} alt="Tool" style={{ width: 22, marginRight: 8 }} /> Tool</ListItem>
                <ListItem><img src={OrchestratorIcon} alt="Orchestrator" style={{ width: 22, marginRight: 8 }} /> Orchestrator</ListItem>
                <ListItem><img src={StartIcon} alt="Start" style={{ width: 22, marginRight: 8 }} /> Start/End</ListItem>
                <ListItem><img src={TeamIcon} alt="Team" style={{ width: 22, marginRight: 8 }} /> Team</ListItem>
              </List>
            </Card>

            <Box sx={{ mt: 2 }}>
              <ButtonGroup orientation="vertical" variant="outlined" size="small" fullWidth>
                <Button onClick={() => setSnackbarOpen(true)}>Reset Layout</Button>
                <Button onClick={() => window.print()}>Export</Button>
              </ButtonGroup>
            </Box>
          </Box>

          <Box sx={{ flex: 1, position: 'relative', minHeight: 0 }}>
            <FlowCanvas
              nodes={initialNodes}
              edges={initialEdges}
              highlighted={highlighted}
              onNodeClick={handleNodeClick}
              onEdgeClick={handleEdgeClick}
            />
          </Box>

          <Drawer anchor="right" open={drawerOpen} onClose={handleCloseDrawer} PaperProps={{ sx: { width: { xs: '100%', sm: 380 } } }}>
            <Box sx={{ width: 380, p: 2, maxHeight: '100vh', overflowY: 'auto' }}>
              <Typography variant="h6">Details</Typography>
              {selectedDetails && selectedDetails.type === 'node' && (
                <Box>
                  <Typography variant="subtitle1">{selectedDetails.label || selectedDetails.id}</Typography>
                  <List>
                    <ListItem>Type: {selectedDetails.nodeType}</ListItem>
                    {selectedDetails.functionName && <ListItem>Function: {selectedDetails.functionName}</ListItem>}
                    {selectedDetails.docstring && <ListItem>Docstring: {selectedDetails.docstring}</ListItem>}
                    {selectedDetails.sourceLocation && (
                      <ListItem>Source: {selectedDetails.sourceLocation.file} (Line {selectedDetails.sourceLocation.line})</ListItem>
                    )}
                    {selectedDetails.metadata && Object.keys(selectedDetails.metadata || {}).length > 0 && (
                      <ListItem>
                        Metadata:
                        <pre style={{ maxWidth: 320, overflowX: 'auto' }}>{JSON.stringify(selectedDetails.metadata, null, 2)}</pre>
                      </ListItem>
                    )}
                  </List>
                </Box>
              )}

              {selectedDetails && selectedDetails.type === 'edge' && (
                <Box>
                  <Typography variant="subtitle1">Edge: {selectedDetails.source} → {selectedDetails.target}</Typography>
                  <List>
                    <ListItem>Condition: {selectedDetails.condition?.type}</ListItem>
                    {selectedDetails.metadata && Object.keys(selectedDetails.metadata || {}).length > 0 && (
                      <ListItem>
                        Metadata:
                        <pre style={{ maxWidth: 320, overflowX: 'auto' }}>{JSON.stringify(selectedDetails.metadata, null, 2)}</pre>
                      </ListItem>
                    )}
                  </List>
                </Box>
              )}

              <Button variant="outlined" onClick={handleCloseDrawer}>Close</Button>
            </Box>
          </Drawer>
        </Box>

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