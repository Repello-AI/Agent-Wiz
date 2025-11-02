// Drag and Drop Context
import { createContext, useContext, useState, type ReactNode } from 'react';

interface DnDContextType {
  type: string | null;
  setType: (type: string | null) => void;
}

const DnDContext = createContext<DnDContextType | undefined>(undefined);

export function DnDProvider({ children }: { children: ReactNode }) {
  const [type, setType] = useState<string | null>(null);
  
  return (
    <DnDContext.Provider value={{ type, setType }}>
      {children}
    </DnDContext.Provider>
  );
}

export function useDnD(): DnDContextType {
  const context = useContext(DnDContext);
  if (!context) {
    throw new Error('useDnD must be used within DnDProvider');
  }
  return context;
}

