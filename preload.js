const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  sendCommand: (cmd) => ipcRenderer.invoke('send-command', cmd),
  getMap: () => ipcRenderer.invoke('get-map'),
  restartGame: () => ipcRenderer.invoke('restart-game')
});
