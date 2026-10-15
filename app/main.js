'use strict'
const { app, BrowserWindow, Menu } = require('electron')
const path = require('path')
const host = require('./engine-host')

let win = null

function createWindow () {
  win = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 980,
    minHeight: 660,
    backgroundColor: '#0d0d12',
    show: false,
    title: 'AutoDraw',
    icon: path.join(__dirname, 'icon.ico'),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  })
  win.once('ready-to-show', () => win.show())
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  win.on('closed', () => { win = null })
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'))
}

host.init({
  getWindow: () => win,
  send: (obj) => {
    if (win && !win.isDestroyed()) win.webContents.send('engine:event', obj)
  },
})

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore()
      win.focus()
    }
  })
  app.whenReady().then(() => {
    Menu.setApplicationMenu(null)
    createWindow()
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })
  app.on('window-all-closed', () => {
    host.stopJob()
    app.quit()
  })
}
