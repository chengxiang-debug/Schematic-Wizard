'use strict'
const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('autodraw', {
  filePath: (f) => { try { return webUtils.getPathForFile(f) } catch (e) { return '' } },
  boot: () => ipcRenderer.invoke('app:boot'),
  listPresets: () => ipcRenderer.invoke('presets:list'),
  presetDetail: (key) => ipcRenderer.invoke('presets:detail', key),
  checkEnv: () => ipcRenderer.invoke('env:check'),
  saveSettings: (patch) => ipcRenderer.invoke('settings:save', patch),
  generate: (req) => ipcRenderer.invoke('job:start', req),
  stop: () => ipcRenderer.invoke('job:stop'),
  pickDir: (cur) => ipcRenderer.invoke('dialog:pickDir', cur),
  openPath: (p) => ipcRenderer.invoke('fs:open', p),
  openKicad: (p) => ipcRenderer.invoke('fs:openKicad', p),
  reveal: (p) => ipcRenderer.invoke('fs:reveal', p),
  openLceda: () => ipcRenderer.invoke('fs:openLceda'),
  onEvent: (cb) => {
    const handler = (e, obj) => cb(obj)
    ipcRenderer.on('engine:event', handler)
    return () => ipcRenderer.removeListener('engine:event', handler)
  },
})
