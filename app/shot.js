'use strict'
/* 开发期自测壳：离屏渲染 + 截图，不弹窗口。打包时用 --ignore 排除，不进包。 */
const { app, BrowserWindow } = require('electron')
const fs = require('fs')
const path = require('path')
const host = require('./engine-host')

const OUT = path.join(__dirname, '..', 'shots')
const VIEWS = ['browse', 'batch', 'settings']
const jsErrors = []
const events = []
const fails = []

function check (name, ok, extra) {
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? ' — ' + extra : ''))
  if (!ok) fails.push(name)
}

async function main () {
  await app.whenReady()
  let win = null
  host.init({
    getWindow: () => win,
    send: (o) => {
      events.push(o)
      if (win && !win.isDestroyed()) win.webContents.send('engine:event', o)
    },
  })
  fs.mkdirSync(OUT, { recursive: true })
  win = new BrowserWindow({
    width: 1240,
    height: 820,
    show: false,
    paintWhenInitiallyHidden: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  })
  win.webContents.on('console-message', (e, level, message) => {
    if (level >= 2) jsErrors.push(message)
  })
  win.webContents.on('preload-error', (e, p, err) => jsErrors.push('preload: ' + err.message))

  const js = (code) => win.webContents.executeJavaScript(code)
  await win.loadFile(path.join(__dirname, 'renderer', 'index.html'))
  console.log('loaded')

  // 等预设列表 + 环境检测落地
  const deadline = Date.now() + 30000
  let rows = 0
  while (Date.now() < deadline) {
    rows = await js('document.querySelectorAll("#presetList .prow").length')
    const badge = await js('document.querySelector("#envBadge span").textContent')
    console.log('poll rows=' + rows + ' badge=' + badge)
    if (rows > 0 && !/检测中/.test(badge)) break
    await new Promise((r) => setTimeout(r, 300))
  }
  check('预设行渲染', rows > 0, rows + ' 行（虚拟列表，只画可见部分）')

  // 首条预览应自动出图
  const pv = await js('JSON.stringify({title:document.getElementById("previewTitle").textContent,' +
    'wires:document.querySelectorAll("#previewCanvas .wire").length,' +
    'pins:document.querySelectorAll("#previewCanvas .pinl").length,' +
    'syms:document.querySelectorAll("#previewCanvas .sym").length,' +
    'nets:document.querySelectorAll("#infoNets .netchip").length,' +
    'bom:document.querySelectorAll("#infoBom tr").length,' +
    'empty:document.getElementById("previewEmpty").hidden,' +
    'vb:(document.getElementById("schSvg")||{getAttribute:()=>""}).getAttribute("viewBox")})')
  console.log('preview ' + pv)
  const P = JSON.parse(pv)
  check('原理图预览自动出图', P.wires > 0 && P.pins > 0 && P.syms > 0, pv)
  check('详情面板有 BOM/网络', P.bom > 0 && P.nets > 0, 'bom=' + P.bom + ' nets=' + P.nets)
  check('空态提示已隐藏', P.empty === true)

  // 分类切换 + 搜索过滤
  const cats = await js('document.querySelectorAll("#catChips .cat").length')
  check('分类 chips', cats >= 2, cats + ' 个')
  const filt = await js(`(() => {
    const b = document.querySelectorAll('#catChips .cat')[1]
    const name = b.textContent
    b.click()
    return JSON.stringify({name, rows: document.querySelectorAll('#presetList .prow').length,
      stats: document.getElementById('libStats').textContent})
  })()`)
  console.log('cat ' + filt)
  // 列表是异步刷的，刚点完 chip 时 DOM 里还是 0 行，只信统计数字
  const before = +(JSON.parse(filt).stats.match(/筛出 (\d+)/) || [0, 1e9])[1]
  await js('document.getElementById("presetSearch").value = "led"')
  await js('document.getElementById("presetSearch").dispatchEvent(new Event("input"))')
  const srch = await js('JSON.stringify({shown:+(document.getElementById("libStats").textContent.match(/筛出 (\\d+)/)||[0,0])[1],' +
    'stats:document.getElementById("libStats").textContent})')
  console.log('search ' + srch)
  // 只要比这个分类的总数窄就行：预设量级一直在涨，写死数字会假报警
  const S = JSON.parse(srch)
  check('搜索过滤生效', S.shown > 0 && S.shown < before, srch + ' 分类内 ' + before + ' 条')
  await js('document.getElementById("presetSearch").value = ""')
  await js('document.getElementById("presetSearch").dispatchEvent(new Event("input"))')
  await js(`document.querySelector("#catChips .cat").click()`)

  // 键盘导航 + 勾选
  await js('document.getElementById("presetList").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowDown",bubbles:true}))')
  await js('document.dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowDown",bubbles:true}))')
  await new Promise((r) => setTimeout(r, 500))
  await js('document.dispatchEvent(new KeyboardEvent("keydown",{key:" ",bubbles:true}))')
  const sel = await js('JSON.stringify({sel:document.getElementById("selInfo").textContent,' +
    'badge:document.getElementById("navSel").textContent,' +
    'badgeHidden:document.getElementById("navSel").hidden,' +
    'cur:document.querySelectorAll("#presetList .prow.is-on").length,' +
    'pick:document.querySelectorAll("#presetList .prow.is-sel").length})')
  console.log('keyboard ' + sel)
  const KS = JSON.parse(sel)
  check('键盘上下选 + 空格勾选', KS.pick > 0 && KS.badgeHidden === false && KS.cur === 1, sel)

  // 引脚悬停高亮：直接触发 readout
  const hot = await js(`(() => {
    const hit = document.querySelector('#previewCanvas .pinhit')
    if (!hit) return JSON.stringify({err:'没有引脚'})
    hit.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:100,clientY:100}))
    const tip = document.getElementById('pinReadout').textContent
    const chips = document.querySelectorAll('#infoNets .netchip')
    const other = Array.from(chips).find((c) =>
      !c.classList.contains('is-hot')) || chips[chips.length - 1]
    other.click()
    const on = {hot:document.querySelectorAll('#previewCanvas .hot').length,
      dim:document.querySelector('#schSvg').classList.contains('has-hot'),
      sel:document.querySelectorAll('#infoNets .netchip.is-hot').length}
    other.click()
    const off = document.querySelectorAll('#previewCanvas .hot').length
    return JSON.stringify({tip, on, off})
  })()`)
  console.log('hot ' + hot)
  const H = JSON.parse(hot)
  check('悬停引脚读网络', !!H.tip && H.tip.length > 4, H.tip)
  check('点网络名单独高亮', H.on.hot > 0 && H.on.dim === true && H.on.sel === 1, hot)
  check('再点一次取消隔离', H.off === 0, 'hot=' + H.off)

  // 缩放
  const zm = await js(`(() => {
    const before = document.getElementById('vp').getAttribute('transform')
    document.getElementById('btnZoomIn').click()
    return JSON.stringify({before, after:document.getElementById('vp').getAttribute('transform')})
  })()`)
  console.log('zoom ' + zm)
  const Z = JSON.parse(zm)
  check('缩放按钮改 viewBox 变换', Z.before !== Z.after, zm)
  await js('document.getElementById("btnZoomFit").click()')

  for (const v of VIEWS) {
    await js('location.hash = "' + v + '"')
    await js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
    await new Promise((r) => setTimeout(r, 500))
    await win.webContents.capturePage()          // 丢掉可能陈旧的帧
    await new Promise((r) => setTimeout(r, 260))
    const img = await win.webContents.capturePage()
    const file = path.join(OUT, v + '.png')
    fs.writeFileSync(file, img.toPNG())
    console.log('shot ' + v + ' ' + img.getSize().width + 'x' + img.getSize().height + ' -> ' + file)
  }

  await js('location.hash = "batch"')
  await js('document.getElementById("outdir").value = "D:/AutoDraw_Test"')
  const t0 = Date.now()
  await js('document.getElementById("btnRun").click()')
  while (Date.now() - t0 < 240000) {
    if (events.some((e) => e.ev === 'exit')) break
    await new Promise((r) => setTimeout(r, 400))
  }
  const countEvents = (ev) => events.filter((e) => e.ev === ev).length
  const res = events.filter((e) => e.ev === 'result').pop()
  console.log('job_stats ' + JSON.stringify({
    logs: countEvents('log'), progress: countEvents('progress'),
    starts: countEvents('start'), results: countEvents('result'),
    errors: countEvents('error'), exit: events.filter((e) => e.ev === 'exit')[0],
    secs: Math.round((Date.now() - t0) / 1000),
  }))
  check('引擎跑完一条', !!res, res ? res.key : '无 result 事件')
  check('任务期间无 error 事件', countEvents('error') === 0)

  const q = await js('JSON.stringify({rows:document.querySelectorAll("#queueList .qrow").length,' +
    'st:[].map.call(document.querySelectorAll("#queueList .qrow"),r=>r.className),' +
    'stats:document.getElementById("queueStats").textContent,' +
    'pct:document.getElementById("progPct").textContent})')
  console.log('queue ' + q)
  const Q = JSON.parse(q)
  check('队列行有状态', Q.rows > 0 && /st-(done|bad)/.test(Q.st.join()), q)

  console.log('result ' + (res
    ? JSON.stringify({ key: res.key, erc: res.erc_ok, trusted: res.erc_trusted, pcb: res.pcb_ok, files: Object.keys(res.files) })
    : 'none'))
  check('ERC 干净', !!(res && res.erc_ok), res ? 'errors=' + res.erc_errors + ' warns=' + res.erc_warns : '')
  check('PCB 生成', !!(res && res.pcb_ok))
  await new Promise((r) => setTimeout(r, 500))
  const chipDeadline = Date.now() + 15000
  while (Date.now() < chipDeadline) {
    const ok = await js('document.querySelector("#resultChips").textContent.includes("嘉立创")')
    if (ok) break
    await new Promise((r) => setTimeout(r, 250))
  }
  await js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
  await new Promise((r) => setTimeout(r, 1200))
  const img2 = await win.webContents.capturePage()
  fs.writeFileSync(path.join(OUT, 'after-run.png'), img2.toPNG())
  console.log('shot after-run')
  console.log('ui_after ' + await js(
    'JSON.stringify({runEnabled:!document.getElementById("btnRun").disabled,' +
    'stopDisabled:document.getElementById("btnStop").disabled,' +
    'pct:document.getElementById("progPct").textContent,' +
    'label:document.getElementById("progLabel").textContent,' +
    'logLines:document.querySelectorAll("#logBox div").length,' +
    'chips:[].map.call(document.querySelectorAll("#resultChips .chip"),c=>c.textContent).join("|"),' +
    'fileBtns:document.querySelectorAll("#resultFiles button").length})'))

  // 日志过滤
  await js('document.getElementById("logFilter").value = "ERC"')
  await js('document.getElementById("logFilter").dispatchEvent(new Event("input"))')
  const lf = await js('JSON.stringify({n:document.querySelectorAll("#logBox div").length,' +
    'c:document.getElementById("logCount").textContent})')
  console.log('logfilter ' + lf)
  check('日志过滤生效', JSON.parse(lf).n > 0 && /命中/.test(JSON.parse(lf).c), lf)

  // 收藏落盘
  await js('location.hash = "browse"')
  await js('document.getElementById("btnFav").click()')
  await new Promise((r) => setTimeout(r, 700))
  const st = host.loadSettings()
  check('收藏写进设置文件', (st.favorites || []).length > 0, JSON.stringify(st.favorites))
  check('严格 ERC 开关有默认值', st.strict_erc === false, String(st.strict_erc))

  console.log('js_errors ' + (jsErrors.length ? JSON.stringify(jsErrors) : 'none'))
  if (jsErrors.length) fails.push('控制台报错')
  console.log('SUMMARY ' + (fails.length ? 'FAIL: ' + fails.join(', ') : 'all green'))
  app.exit(fails.length ? 2 : 0)
}

main().catch((e) => {
  console.log('fatal ' + (e && e.message))
  app.exit(1)
})
