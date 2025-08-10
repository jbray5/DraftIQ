// hooks/useDragResize.js
import { useState, useRef, useEffect } from 'react';
import { HEIGHT_KEY } from '../constants';

export default function useDragResize() {
  const [boardHeight, setBoardHeight] = useState(() => parseInt(localStorage.getItem(HEIGHT_KEY), 10) || 300);
  const isDraggingRef = useRef(false);

  const startDrag = () => { isDraggingRef.current = true; };
  const stopDrag = () => { isDraggingRef.current = false; };

  const onDrag = e => {
    if (!isDraggingRef.current) return;
    setBoardHeight(prev => {
      const newH = Math.max(150, prev + e.movementY);
      localStorage.setItem(HEIGHT_KEY, newH);
      return newH;
    });
  };

  useEffect(() => {
    window.addEventListener('mousemove', onDrag);
    window.addEventListener('mouseup', stopDrag);
    return () => {
      window.removeEventListener('mousemove', onDrag);
      window.removeEventListener('mouseup', stopDrag);
    };
  }, []);

  return { boardHeight, startDrag };
}
