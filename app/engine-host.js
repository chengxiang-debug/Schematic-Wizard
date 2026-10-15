'use strict'
/* 引擎调度层：Python 子进程 + NDJSON 协议 + 设置持久化 + IPC 注册。
   main.js（正式窗口）和 shot.js（离屏自测）共用同一套。 */
const { app, dialog, ipcMain, shell } = require('electron')
const { spawn } = require('child_process')
const fs = require('fs')
const path = require('path')

const ENGINE = path.join(__dirname, 'engine', 'engine.py')

const DEFAULTS = {
  kicad_root: 'D:\\KiCad',
  kicad_py: 'D:\\KiCad\\bin\\python.exe',
  engine_py: 'D:\\KiCad\\bin\\python.exe',
  outdir: 'D:\\AutoDraw_Output',
  circuit: 'led_3v3',
  sch: true,
  pcb: true,
  jlc: true,
  lceda_exe: 'D:\\Users\\guome\\AppData\\Local\\Programs\\lceda-pro\\lceda-pro.exe',
  follow_log: true,
  favorites: [],
  recent: [],
  outdir_history: [],
  grid: true,
  net_colors: true,
  strict_erc: false,
}

const settingsFile = () => app.isPackaged
  ? path.join(path.dirname(process.execPath), 'autodraw_settings.json')
  : path.join(__dirname, 'autodraw_settings.json')

function loadSettings () {
  let disk = {}
  try {
    disk = JSON.parse(fs.readFileSync(settingsFile(), 'utf8'))
  } catch (e) { /* 首次运行或文件损坏，用默认值 */ }
  return Object.assign({}, DEFAULTS, disk)
}

function saveSettings (patch) {
  const clean = {}
  for (const k of Object.keys(DEFAULTS)) {
    if (Object.prototype.hasOwnProperty.call(patch, k)) clean[k] = patch[k]
  }
  const next = Object.assign(loadSettings(), clean)
  try {
    fs.writeFileSync(settingsFile(), JSON.stringify(next, null, 2), 'utf8')
    return { ok: true, settings: next, file: settingsFile() }
  } catch (e) {
    return { ok: false, settings: next, file: settingsFile(), error: String(e.message || e) }
  }
}

function isAbsFile (p) {
  return typeof p === 'string' && path.isAbsolute(p) && fs.existsSync(p)
}

let job = null

function spawnEngine (settings) {
  const py = settings.engine_py || DEFAULTS.engine_py
  if (!fs.existsSync(py)) {
    return {
      error: '找不到运行引擎的 Python: ' + py +
        '  去「设置」里改成实际存在的 python.exe（默认用 KiCad 自带的 ' +
        DEFAULTS.engine_py + '）',
    }
  }
  if (!fs.existsSync(ENGINE)) {
    return { error: '引擎文件缺失: ' + ENGINE }
  }
  const child = spawn(py, [ENGINE], {
    cwd: path.dirname(ENGINE),
    windowsHide: true,
    env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }),
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  return { child }
}

function withPaths (req, settings) {
  return Object.assign({}, req, {
    paths: { kicad_root: settings.kicad_root, kicad_py: settings.kicad_py },
  })
}

function parseLines (buf, onData) {
  let i
  while ((i = buf.s.indexOf('\n')) >= 0) {
    const line = buf.s.slice(0, i).trim()
    buf.s = buf.s.slice(i + 1)
    if (!line) continue
    let obj
    try {
      obj = JSON.parse(line)
    } catch (e) {
      onData({ ev: 'log', text: '引擎非 JSON 输出: ' + line })
      continue
    }
    onData(obj)
  }
}

function oneShot (req) {
  const settings = loadSettings()
  return new Promise((resolve) => {
    const s = spawnEngine(settings)
    if (s.error) return resolve({ ok: false, error: s.error })
    const { child } = s
    const buf = { s: '' }
    let payload = null
    let errText = ''
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (d) => {
      buf.s += d
      parseLines(buf, (obj) => {
        if (obj.ev === 'presets' || obj.ev === 'env' || obj.ev === 'detail') payload = obj
        if (obj.ev === 'error') errText = obj.text + (obj.trace ? '\n' + obj.trace : '')
      })
    })
    child.stderr.on('data', (d) => { errText += d })
    child.on('error', (e) => resolve({ ok: false, error: String(e.message || e) }))
    child.on('close', (code) => {
      if (!payload && !errText && code !== 0) errText = '引擎退出码 ' + code
      resolve({ ok: !!payload && !errText, payload, error: errText })
    })
    child.stdin.write(JSON.stringify(withPaths(req, settings)))
    child.stdin.end()
  })
}

function startJob (req, send) {
  if (job) return { ok: false, error: '已经有任务在跑，先点「停止」。' }
  const settings = loadSettings()
  const s = spawnEngine(settings)
  if (s.error) return { ok: false, error: s.error }
  const child = s.child
  job = child
  child.__killed = false
  const buf = { s: '' }
  let stderr = ''
  child.stdout.setEncoding('utf8')
  child.stderr.setEncoding('utf8')
  child.stdout.on('data', (d) => { buf.s += d; parseLines(buf, send) })
  child.stderr.on('data', (d) => { stderr += d })
  child.on('error', (e) => send({ ev: 'error', text: String(e.message || e) }))
  child.on('close', (code) => {
    if (stderr.trim()) send({ ev: 'log', text: '[引擎 stderr] ' + stderr.trim() })
    send({ ev: 'exit', code, killed: child.__killed === true })
    job = null
  })
  child.stdin.write(JSON.stringify(withPaths(req, settings)))
  child.stdin.end()
  return { ok: true }
}

function stopJob () {
  if (!job) return { ok: false }
  job.__killed = true
  try { job.kill() } catch (e) { /* 已退出 */ }
  return { ok: true }
}

function init (ctx) {
  const send = (obj) => ctx.send(obj)
  const win = () => ctx.getWindow()

  ipcMain.handle('app:boot', () => {
    const settings = loadSettings()
    return {
      settings,
      defaults: DEFAULTS,
      packaged: app.isPackaged,
      version: require('./package.json').version,
      settingsFile: settingsFile(),
      engineFile: ENGINE,
      enginePyExists: fs.existsSync(settings.engine_py || DEFAULTS.engine_py),
      electron: process.versions.electron,
    }
  })

  ipcMain.handle('presets:list', () => oneShot({ action: 'list' }))
  ipcMain.handle('presets:detail', (e, key) => oneShot({ action: 'detail', key }))
  ipcMain.handle('env:check', () => oneShot({ action: 'env' }))
  ipcMain.handle('settings:save', (e, patch) => saveSettings(patch && typeof patch === 'object' ? patch : {}))
  ipcMain.handle('job:start', (e, req) => startJob(req && typeof req === 'object' ? req : {}, send))
  ipcMain.handle('job:stop', () => stopJob())

  ipcMain.handle('dialog:pickDir', async (e, current) => {
    const settings = loadSettings()
    const r = await dialog.showOpenDialog(win(), {
      title: '选择输出目录',
      defaultPath: isAbsFile(current) ? current : settings.outdir,
      properties: ['openDirectory', 'createDirectory'],
    })
    return r.canceled ? { ok: false } : { ok: true, dir: r.filePaths[0] }
  })

  ipcMain.handle('fs:open', (e, p) => {
    if (!isAbsFile(p)) return { ok: false, error: '文件还不存在: ' + p }
    return shell.openPath(p).then((err) => (err ? { ok: false, error: err } : { ok: true }))
  })

  ipcMain.handle('fs:openKicad', (e, p) => {
    if (!isAbsFile(p)) return { ok: false, error: '文件还不存在: ' + p }
    const settings = loadSettings()
    const kicadExe = path.join(settings.kicad_root, 'bin', 'kicad.exe')
    if (!fs.existsSync(kicadExe)) return { ok: false, error: '找不到 KiCad 主程序: ' + kicadExe }
    const child = spawn(kicadExe, [p], { detached: true, stdio: 'ignore', windowsHide: false })
    child.unref()
    return { ok: true }
  })

  ipcMain.handle('fs:reveal', (e, p) => {
    if (!isAbsFile(p)) return { ok: false, error: '文件还不存在: ' + p }
    shell.showItemInFolder(p)
    return { ok: true }
  })

  ipcMain.handle('fs:openLceda', () => {
    const settings = loadSettings()
    const exe = settings.lceda_exe
    if (!isAbsFile(exe)) {
      return { ok: false, error: '找不到嘉立创 EDA 程序: ' + exe + '  去「设置」里改路径' }
    }
    const child = spawn(exe, [], { detached: true, stdio: 'ignore', windowsHide: false })
    child.unref()
    return { ok: true }
  })
}

module.exports = {
  init,
  loadSettings,
  saveSettings,
  settingsFile,
  oneShot,
  startJob,
  stopJob,
  ENGINE,
  DEFAULTS,
}
