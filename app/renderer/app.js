'use strict'
;(() => {
  const api = window.autodraw
  const $ = (id) => document.getElementById(id)
  const el = (tag, cls, text) => {
    const n = document.createElement(tag)
    if (cls) n.className = cls
    if (text !== undefined) n.textContent = text
    return n
  }
  const esc = (s) => String(s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

  const ROW = 44          // 预设行高（改样式要同步）
  const QROW = 32         // 队列行高
  const MAX_LOG = 2000
  const NET_COLORS = ['#58a6ff', '#3fb950', '#e3b341', '#f85149', '#bc8cff', '#79c0ff',
    '#56d4dd', '#d2a8ff', '#f0883e', '#7ee787', '#ff7b72', '#a5d6ff']

  const FILE_LABELS = [
    ['sch', '原理图', 'kicad'], ['pcb', 'PCB', 'kicad'],
    ['pdf', 'PDF', 'os'], ['svg', 'SVG', 'os'], ['net', '网表', 'os'],
    ['png', '3D 图', 'os'], ['bom', 'BOM', 'os'],
    ['lczip', '嘉立创导入包', 'reveal'], ['gbr', 'Gerber 包', 'reveal'],
    ['cpl', '贴装坐标 CPL', 'os'], ['jbom', '嘉立创 BOM', 'os'],
    ['report', '报告', 'os'], ['erc', 'ERC 报告', 'os'], ['drc', 'DRC 报告', 'os'],
  ]

  const state = {
    presets: [],
    shown: [],
    cats: [],
    settings: {},
    defaults: {},
    current: null,
    cat: '全部',
    favOnly: false,
    sel: new Set(),
    fav: new Set(),
    recent: [],
    detail: {},
    detailSeq: 0,
    running: false,
    jobStart: 0,
    queue: [],
    results: {},
    readme: '',
    logLines: [],
    logFilter: '',
    lineNo: 0,
    hot: null,
    zoom: { k: 1, x: 0, y: 0 },
  }

  /* ================= 小工具 ================= */
  function toast (msg, kind) {
    const t = el('div', 'toast' + (kind ? ' ' + kind : ''), msg)
    $('toasts').appendChild(t)
    setTimeout(() => t.classList.add('is-out'), 2200)
    setTimeout(() => t.remove(), 2600)
  }

  function classify (t) {
    if (/^=+$/.test(t.trim())) return 'l-sep'
    if (/ERC messages:\s*0|Found 0|: OK|通过$|完成 —/.test(t)) return 'l-ok'
    if (/失败|FAIL|异常|错误|not found|Error\b|✗/.test(t)) return 'l-bad'
    if (/告警|警告|未通过|violation|沿用/.test(t)) return 'l-warn'
    return ''
  }

  let logRaf = 0
  function pushLog (text) {
    state.logLines.push({ text: String(text), cls: classify(String(text)) })
    if (state.logLines.length > MAX_LOG) {
      state.logLines.splice(0, state.logLines.length - MAX_LOG)
    }
    state.lineNo++
    if (!logRaf) logRaf = requestAnimationFrame(() => { logRaf = 0; renderLog() })
  }

  function renderLog () {
    const box = $('logBox')
    const q = state.logFilter.toLowerCase()
    const frag = document.createDocumentFragment()
    let i = 0
    for (const line of state.logLines) {
      if (q && line.text.toLowerCase().indexOf(q) < 0) continue
      const n = el('div')
      n.appendChild(el('span', 'n', String(++i).padStart(3, '0')))
      n.appendChild(el('span', line.cls, line.text))
      frag.appendChild(n)
    }
    box.textContent = ''
    box.appendChild(frag)
    $('logCount').textContent = state.logLines.length + ' 行' +
      (q ? ' · 命中 ' + i : '')
    if ($('optFollow').checked) box.scrollTop = box.scrollHeight
  }

  /* ================= 视图路由 ================= */
  const VIEWS = ['browse', 'batch', 'settings']
  function showView (name) {
    const target = VIEWS.includes(name) ? name : 'browse'
    document.querySelectorAll('.view')
      .forEach((v) => v.classList.toggle('is-active', v.id === 'view-' + target))
    document.querySelectorAll('.nav-item')
      .forEach((n) => n.classList.toggle('is-active', n.dataset.view === target))
    document.querySelector('.stage').scrollTop = 0
  }

  /* ================= 过滤 / 分类 ================= */
  function matches (p, q) {
    if (!q) return true
    const hay = [p.key, p.label, p.title, p.cat, p.desc, p.bom,
      (p.params || []).map((x) => x.join(' ')).join(' ')].join(' ').toLowerCase()
    return q.split(/\s+/).every((w) => hay.includes(w))
  }

  function applyFilter () {
    const q = $('presetSearch').value.trim()
    state.shown = state.presets.filter((p) =>
      (state.cat === '全部' || p.cat === state.cat) &&
      (!state.favOnly || state.fav.has(p.key)) &&
      matches(p, q))
    list.set(state.shown)
    $('libStats').textContent =
      state.presets.length + ' 条预设 · ' + state.cats.length + ' 个分类 · 当前筛出 ' +
      state.shown.length + ' 条'
  }

  function renderCats () {
    const host = $('catChips')
    host.textContent = ''
    const counts = {}
    for (const p of state.presets) counts[p.cat] = (counts[p.cat] || 0) + 1
    const add = (name, n) => {
      const b = el('button', 'cat' + (state.cat === name ? ' is-on' : ''))
      b.appendChild(el('span', null, name))
      b.appendChild(el('i', null, String(n)))
      b.onclick = () => { state.cat = name; renderCats(); applyFilter() }
      host.appendChild(b)
    }
    add('全部', state.presets.length)
    for (const c of state.cats) add(c, counts[c] || 0)
  }

  /* ================= 虚拟列表 ================= */
  function makeVirtual (scrollId, hostId, rowH, build) {
    const scroll = $(scrollId)
    const host = $(hostId)
    let items = []
    let raf = 0
    function paint () {
      const top = scroll.scrollTop
      const h = scroll.clientHeight || 320
      const start = Math.max(0, Math.floor(top / rowH) - 3)
      const end = Math.min(items.length, Math.ceil((top + h) / rowH) + 3)
      host.style.height = items.length * rowH + 'px'
      host.textContent = ''
      for (let i = start; i < end; i++) {
        const n = build(items[i], i)
        n.style.position = 'absolute'
        n.style.top = i * rowH + 'px'
        host.appendChild(n)
      }
    }
    scroll.addEventListener('scroll', () => {
      if (raf) return
      raf = requestAnimationFrame(() => { raf = 0; paint() })
    })
    return {
      set (arr) { items = arr; paint() },
      refresh: paint,
      scrollTo (i) {
        const h = scroll.clientHeight || 320
        const y = i * rowH
        if (y < scroll.scrollTop || y + rowH > scroll.scrollTop + h) {
          scroll.scrollTop = Math.max(0, y - h / 2 + rowH)
        }
      },
    }
  }

  /* ---- 预设行 ---- */
  function buildRow (p, i) {
    const on = state.current === p.key
    const r = el('div', 'prow' + (on ? ' is-on' : '') +
      (state.sel.has(p.key) ? ' is-sel' : ''))
    r.dataset.key = p.key
    const chk = el('span', 'pick')
    chk.title = '勾选（空格）'
    chk.onclick = (e) => { e.stopPropagation(); toggleSel(p.key) }
    r.appendChild(chk)
    const body = el('div', 'pmain')
    body.appendChild(el('b', null, p.label))
    const meta = [p.key, p.parts + ' 件', p.wires + ' 线']
    if (p.params && p.params.length) {
      meta.push(p.params.map((x) => x[1]).join(' '))
    }
    body.appendChild(el('span', 'pmeta', meta.join(' · ')))
    r.appendChild(body)
    const star = el('button', 'star' + (state.fav.has(p.key) ? ' is-fav' : ''), '★')
    star.title = '收藏'
    star.onclick = (e) => { e.stopPropagation(); toggleFav(p.key) }
    r.appendChild(star)
    r.onclick = () => select(p.key)
    return r
  }

  const list = makeVirtual('listScroll', 'presetList', ROW, buildRow)
  const queue = makeVirtual('queueScroll', 'queueList', QROW, buildQueueRow)

  function shownIndex (key) {
    return state.shown.findIndex((p) => p.key === key)
  }

  /* ================= 选择 / 收藏 / 最近 ================= */
  function toggleSel (key) {
    if (state.sel.has(key)) state.sel.delete(key)
    else state.sel.add(key)
    syncSel()
    list.refresh()
  }

  function syncSel () {
    const n = state.sel.size
    $('navSel').textContent = n
    $('navSel').hidden = !n
    $('selInfo').textContent = n ? '已勾选 ' + n + ' 条' : '未勾选'
    $('runCount').textContent = n ? '(' + n + ')' : '(当前条)'
    const cur = state.presets.find((p) => p.key === state.current)
    $('btnToggleSel').textContent = state.sel.has(state.current) ? '取消勾选' : '勾选'
    if (cur) $('btnFav').textContent = state.fav.has(cur.key) ? '★ 已收藏' : '☆ 收藏'
  }

  function toggleFav (key) {
    if (state.fav.has(key)) state.fav.delete(key)
    else state.fav.add(key)
    state.settings.favorites = Array.from(state.fav)
    persist({ favorites: state.settings.favorites })
    syncSel()
    list.refresh()
    if (state.favOnly) applyFilter()
  }

  function pushRecent (key) {
    state.recent = [key].concat(state.recent.filter((k) => k !== key)).slice(0, 12)
    persist({ recent: state.recent })
  }

  function select (key, fromKeyboard) {
    state.current = key
    list.refresh()
    syncSel()
    showPreview(key)
    if (fromKeyboard) list.scrollTo(shownIndex(key))
    pushRecent(key)
    persist({ circuit: key })
    const p = state.presets.find((x) => x.key === key)
    if (p) $('resultKey').textContent = p.label
  }

  function step (d) {
    if (!state.shown.length) return
    let i = shownIndex(state.current)
    if (i < 0) i = 0
    select(state.shown[(i + d + state.shown.length) % state.shown.length].key, true)
  }

  function stepCat (d) {
    const names = ['全部'].concat(state.cats)
    const i = (names.indexOf(state.cat) + d + names.length) % names.length
    state.cat = names[i]
    renderCats()
    applyFilter()
    if (state.shown.length) select(state.shown[0].key, true)
  }

  /* ================= 预览 ================= */
  async function showPreview (key) {
    const seq = ++state.detailSeq
    let d = state.detail[key]
    if (!d) {
      $('previewEmpty').textContent = '载入中…'
      $('previewEmpty').hidden = false
      const r = await api.presetDetail(key)
      if (seq !== state.detailSeq) return
      if (!r || !r.ok) {
        $('previewEmpty').textContent = '预览失败：' + ((r && r.error) || '引擎没回应')
        $('previewEmpty').hidden = false
        toast('预览失败', 'bad')
        return
      }
      d = r.payload.data
      state.detail[key] = d
    }
    if (seq !== state.detailSeq) return
    $('previewEmpty').hidden = true
    state.hot = null
    state.zoom = { k: 1, x: 0, y: 0 }
    drawPreview(d)
    renderInfo(d)
  }

  function shapeMarkup (item, cls) {
    const p = item.pts
    if (!p.length) return ''
    if (item.kind === 'rectangle' && p.length >= 2) {
      const x = Math.min(p[0][0], p[1][0])
      const y = Math.min(p[0][1], p[1][1])
      return '<rect class="' + cls + '" x="' + x + '" y="' + y +
        '" width="' + Math.abs(p[1][0] - p[0][0]) +
        '" height="' + Math.abs(p[1][1] - p[0][1]) + '"/>'
    }
    if (item.kind === 'circle' && p.length >= 2) {
      return '<circle class="' + cls + '" cx="' + p[0][0] + '" cy="' + p[0][1] +
        '" r="' + Math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1]) + '"/>'
    }
    if (item.kind === 'arc' && p.length >= 3) {
      const [a, m, b] = p
      const c = circumcenter(a, m, b)
      if (!c) return ''
      const cross = (m[0] - a[0]) * (b[1] - m[1]) - (m[1] - a[1]) * (b[0] - m[0])
      const sw = cross > 0 ? 1 : 0
      const ang = (q) => Math.atan2(q[1] - c.y, q[0] - c.x)
      let span = ang(b) - ang(a)
      while (sw ? span <= 0 : span >= 0) span += (sw ? 2 : -2) * Math.PI
      return '<path class="' + cls + '" d="M ' + a[0] + ' ' + a[1] +
        ' A ' + c.r + ' ' + c.r + ' 0 ' + (Math.abs(span) > Math.PI ? 1 : 0) + ' ' + sw +
        ' ' + b[0] + ' ' + b[1] + '"/>'
    }
    return '<polyline class="' + cls + '" points="' +
      p.map((q) => q[0] + ',' + q[1]).join(' ') + '"/>'
  }

  function drawPreview (d) {
    const [x0, y0, x1, y1] = d.bbox
    const w = Math.max(1, x1 - x0)
    const h = Math.max(1, y1 - y0)
    const pinNet = {}
    for (const net of d.nets) {
      for (const name of net.pins) pinNet[name] = net.id
    }
    const wireNet = {}
    for (const net of d.nets) {
      for (const wi of (net.wires || [])) wireNet[wi] = net.id
    }
    const colors = d.nets.map((n) => NET_COLORS[n.id % NET_COLORS.length])
    const names = {}
    for (const n of d.nets) names[n.id] = n.name

    let g = ''
    // 导线
    d.wires.forEach((ln, i) => {
      const nid = wireNet[i]
      const c = state.settings.net_colors !== false && nid !== undefined
        ? colors[nid % colors.length] : ''
      g += '<line class="wire' + (nid === undefined ? ' loose' : '') +
        '" data-net="' + (nid === undefined ? '' : nid) + '" x1="' + ln[0] +
        '" y1="' + ln[1] + '" x2="' + ln[2] + '" y2="' + ln[3] + '"' +
        (c ? ' style="stroke:' + c + '"' : '') + '/>'
    })
    // 元件本体 + 引脚 + 标号
    d.symbols.forEach((s) => {
      g += '<g class="sym">'
      for (const item of s.body) g += shapeMarkup(item, 'body')
      g += '</g>'
    })
    d.pins.forEach((p) => {
      const id = pinNet[p.ref + '.' + p.num]
      const c = state.settings.net_colors !== false && id !== undefined
        ? colors[id] : ''
      g += '<line class="pinl" data-net="' + (id === undefined ? '' : id) + '"' +
        ' x1="' + p.x + '" y1="' + p.y + '" x2="' + p.tx + '" y2="' + p.ty + '"' +
        (c ? ' stroke="' + c + '"' : '') + '/>'
      g += '<text class="pinnum" data-net="' + (id === undefined ? '' : id) +
        '" x="' + (p.x + (p.tx - p.x) * 0.55) +
        '" y="' + (p.y + (p.ty - p.y) * 0.55 - 0.5) + '">' + esc(p.num) + '</text>'
      g += '<circle class="pinhit" data-net="' + (id === undefined ? '' : id) +
        '" data-ref="' + esc(p.ref) + '" data-num="' + esc(p.num) +
        '" data-name="' + esc(p.name) + '" data-type="' + esc(p.type) +
        '" cx="' + p.x + '" cy="' + p.y + '" r="0.6"/>'
    })
    d.symbols.forEach((s) => {
      // #PWR/#FLG 的位号在 KiCad 里就是不显示的，跟着画只会糊成一团
      if (!s.rh && s.ref.charCodeAt(0) !== 35) {
        g += '<text class="lref" x="' + s.rx + '" y="' + s.ry + '">' + esc(s.ref) + '</text>'
      }
      if (!s.vh) {
        const pwr = s.ref.charCodeAt(0) === 35
        g += '<text class="lval' + (pwr ? ' pwrval' : '') +
          '" x="' + s.vx + '" y="' + s.vy + '">' + esc(s.val) + '</text>'
      }
    })

    const grid = state.settings.grid !== false
    const pad = 4
    const html =
      '<svg class="schsvg" id="schSvg" viewBox="' + (x0 - pad) + ' ' + (y0 - pad) +
      ' ' + (w + pad * 2) + ' ' + (h + pad * 2) + '" preserveAspectRatio="xMidYMid meet">' +
      (grid ? '<defs><pattern id="g127" width="1.27" height="1.27" patternUnits="userSpaceOnUse">' +
        '<circle cx="0" cy="0" r="0.08" fill="rgba(255,255,255,.16)"/></pattern></defs>' +
        '<rect class="gridbg" x="' + (x0 - pad) + '" y="' + (y0 - pad) +
        '" width="' + (w + pad * 2) + '" height="' + (h + pad * 2) + '" fill="url(#g127)"/>' : '') +
      '<g id="vp">' + g + '</g></svg>'
    $('previewCanvas').innerHTML = html
    applyZoom()
    bindCanvas(d)
    $('previewTitle').textContent = d.label
    $('previewCat').textContent = d.cat + ' · ' + d.key
  }

  function circumcenter (a, b, c) {
    const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if (Math.abs(d) < 1e-9) return null
    const ax = a[0] * a[0] + a[1] * a[1]
    const bx = b[0] * b[0] + b[1] * b[1]
    const cx = c[0] * c[0] + c[1] * c[1]
    const ux = (ax * (b[1] - c[1]) + bx * (c[1] - a[1]) + cx * (a[1] - b[1])) / d
    const uy = (ax * (c[0] - b[0]) + bx * (a[0] - c[0]) + cx * (b[0] - a[0])) / d
    return { x: ux, y: uy, r: Math.hypot(a[0] - ux, a[1] - uy) }
  }

  function applyZoom () {
    const svg = $('schSvg')
    if (!svg) return
    $('vp').setAttribute('transform',
      'translate(' + state.zoom.x + ' ' + state.zoom.y + ') scale(' + state.zoom.k + ')')
  }

  function bindCanvas (d) {
    const canvas = $('previewCanvas')
    const svg = $('schSvg')
    const toUser = (ev) => {
      const pt = svg.createSVGPoint()
      pt.x = ev.clientX
      pt.y = ev.clientY
      return pt.matrixTransform(svg.getScreenCTM().inverse())
    }
    canvas.onwheel = (ev) => {
      ev.preventDefault()
      const f = ev.deltaY < 0 ? 1.12 : 1 / 1.12
      const p = toUser(ev)
      const k = Math.max(0.3, Math.min(14, state.zoom.k * f))
      const real = k / state.zoom.k
      state.zoom.x = p.x - (p.x - state.zoom.x) * real
      state.zoom.y = p.y - (p.y - state.zoom.y) * real
      state.zoom.k = k
      applyZoom()
    }
    let drag = null
    canvas.onpointerdown = (ev) => {
      if (ev.target.classList.contains('pinhit')) return
      drag = { x: ev.clientX, y: ev.clientY, ox: state.zoom.x, oy: state.zoom.y }
      canvas.setPointerCapture(ev.pointerId)
    }
    canvas.onpointermove = (ev) => {
      if (drag) {
        const sc = svg.clientWidth / Math.max(1, parseFloat(
          svg.getAttribute('viewBox').split(' ')[2]))
        state.zoom.x = drag.ox + (ev.clientX - drag.x) / sc
        state.zoom.y = drag.oy + (ev.clientY - drag.y) / sc
        applyZoom()
      }
      const t = ev.target
      if (t.classList && t.classList.contains('pinhit')) readout(t, d)
    }
    canvas.onpointerup = () => { drag = null }
    canvas.onpointerleave = () => {
      drag = null
      $('pinReadout').textContent = '悬停引脚看网络，点击网络名可以只亮一条'
    }
  }

  function readout (hit, d) {
    const netId = hit.dataset.net
    const pin = hit.dataset.ref + '.' + hit.dataset.num
    const net = d.nets.find((n) => String(n.id) === netId)
    setHot(netId)
    $('pinReadout').textContent = pin + ' 「' + (hit.dataset.name || '—') + '」 ' +
      hit.dataset.type + '　网络 ' + (net ? net.name + '：' + net.pins.join(' ') : '未连')
  }

  function setHot (netId) {
    const id = (netId === null || netId === undefined || netId === '') ? null : String(netId)
    state.hot = id
    const svg = $('schSvg')
    if (!svg) return
    svg.classList.toggle('has-hot', id !== null)
    svg.querySelectorAll('[data-net]').forEach((n) => {
      n.classList.toggle('hot', id !== null && n.dataset.net === id)
    })
    document.querySelectorAll('#infoNets .netchip').forEach((c) => {
      c.classList.toggle('is-hot', id !== null && c.dataset.net === id)
    })
  }

  /* ================= 详情面板 ================= */
  function renderInfo (d) {
    $('infoDesc').textContent = d.desc || d.title
    const chips = $('infoParams')
    chips.textContent = ''
    const rows = (d.params || []).slice()
    rows.push(['分类', d.cat])
    rows.push(['拓扑', d.tpl === d.key ? '原创' : d.tpl])
    for (const [k, v] of rows) {
      const c = el('span', 'chip', k + ' ' + v)
      chips.appendChild(c)
    }
    $('infoCounts').textContent = d.bom.length + ' 件 · ' + d.wires.length +
      ' 线 · ' + d.nets.length + ' 网络'
    const tb = $('infoBom')
    tb.textContent = ''
    for (const b of d.bom) {
      const tr = document.createElement('tr')
      tr.appendChild(el('td', 'r', b.ref))
      const v = el('td', 'v', b.val)
      if (b.fp) {
        const fp = el('span', 'fp', b.fp)
        fp.title = b.fp
        v.appendChild(fp)
      }
      tr.appendChild(v)
      tb.appendChild(tr)
    }
    const host = $('infoNets')
    host.textContent = ''
    for (const n of d.nets) {
      const c = el('button', 'netchip')
      c.dataset.net = String(n.id)
      c.title = n.name + '：' + (n.pins.join(' ') || '（空）')
      const sw = el('i', 'sw')
      sw.style.background = NET_COLORS[n.id % NET_COLORS.length]
      c.appendChild(sw)
      c.appendChild(el('b', null, n.name))
      c.appendChild(el('span', null, n.pins.join(' · ') || '（空）'))
      c.onclick = () => setHot(state.hot === String(n.id) ? null : n.id)
      host.appendChild(c)
    }
  }

  /* ================= 运行 / 队列 ================= */
  function buildQueueRow (q) {
    const r = el('div', 'qrow st-' + q.st)
    r.appendChild(el('i', 'qdot'))
    r.appendChild(el('b', null, q.label || q.key))
    r.appendChild(el('span', 'qqkey', q.key))
    const meta = []
    if (q.st === 'wait') meta.push('排队')
    if (q.st === 'run') meta.push('生成中…')
    if (q.st === 'done' || q.st === 'bad') {
      meta.push(q.erc_trusted ? 'ERC 沿用'
        : (q.erc_ok ? 'ERC ✓'
          : 'ERC ' + (q.erc_errors || 0) + ' 错 ' + (q.erc_warns || 0) + ' 警'))
      meta.push(q.pcb_ok ? 'PCB ✓' : 'PCB ✗')
      meta.push((q.files ? Object.keys(q.files).length : 0) + ' 产物')
    }
    r.appendChild(el('span', 'qmeta', meta.join(' · ')))
    if (q.files && q.files.sch) {
      const b = el('button', 'ghost tiny', '打开')
      b.onclick = () => api.openKicad(q.files.sch)
      r.appendChild(b)
    }
    return r
  }

  function queueStats () {
    const done = state.queue.filter((q) => q.st === 'done').length
    const bad = state.queue.filter((q) => q.st === 'bad').length
    $('queueStats').textContent = state.queue.length
      ? (done + bad) + ' / ' + state.queue.length + ' 条完成' +
        (bad ? ' · ' + bad + ' 条有问题' : '')
      : '空'
  }

  function refreshQueue () {
    const rows = $('optFailOnly').checked
      ? state.queue.filter((q) => q.st === 'bad' || q.st === 'stop')
      : state.queue
    queue.set(rows)
    queueStats()
  }

  function setProgress (pct, label) {
    $('progFill').style.transform = 'scaleX(' + Math.max(0, Math.min(100, pct)) / 100 + ')'
    $('progPct').textContent = Math.round(pct) + '%'
    if (label !== undefined) $('progLabel').textContent = label
    if (state.running && state.jobStart) {
      const done = state.queue.filter((q) => q.st === 'done' || q.st === 'bad').length
      const left = state.queue.length - done
      $('progEta').textContent = done > 1
        ? '还需约 ' + fmtSec((Date.now() - state.jobStart) / done * left)
        : ''
    }
  }

  const fmtSec = (s) => s < 60 ? Math.round(s) + ' 秒'
    : Math.floor(s / 60) + ' 分 ' + (Math.round(s) % 60) + ' 秒'

  function setRunning (on, label) {
    state.running = on
    $('btnRun').disabled = on
    $('btnRunFav').disabled = on
    $('btnRunCat').disabled = on
    $('btnRunOne').disabled = on
    $('btnStop').disabled = !on
    $('progFill').parentElement.classList.toggle('is-run', on)
    if (!on) $('progEta').textContent = ''
    if (label) $('progLabel').textContent = label
  }

  function rememberOutdir (dir) {
    const hist = [dir].concat((state.settings.outdir_history || [])
      .filter((x) => x !== dir)).slice(0, 6)
    state.settings.outdir_history = hist
    persist({ outdir_history: hist })
    fillHistory()
  }

  function fillHistory () {
    const dl = $('outdirHistory')
    dl.textContent = ''
    for (const d of state.settings.outdir_history || []) {
      dl.appendChild(el('option', null, ''))
      dl.lastChild.value = d
    }
  }

  async function run (keys, note) {
    if (!api) return
    const outdir = $('outdir').value.trim()
    if (!keys.length) { toast('没有要生成的预设', 'warn'); return }
    if (!outdir) { pushLog('请先填写输出目录。'); location.hash = 'batch'; showView('batch'); $('outdir').focus(); return }
    if (!$('optSch').checked && !$('optPcb').checked) {
      toast('原理图和 PCB 至少选一个', 'warn')
      return
    }
    if ($('optJlc').checked && !$('optPcb').checked) {
      pushLog('嘉立创文件要基于 PCB，没勾 PCB 就只出导入说明。')
    }
    if (keys.length > 200) {
      pushLog('这次要跑 ' + keys.length + ' 条，会生成一大堆文件，慢是正常的。')
    }
    if (note) showView('batch')
    state.queue = keys.map((k) => {
      const p = state.presets.find((x) => x.key === k) || {}
      return { key: k, label: p.label || k, st: 'wait' }
    })
    state.jobStart = Date.now()
    refreshQueue()
    setRunning(true, '准备中…')
    setProgress(0, '准备中…')
    rememberOutdir(outdir)
    const req = {
      action: 'generate',
      keys,
      outdir,
      sch: $('optSch').checked,
      pcb: $('optPcb').checked,
      jlc: $('optJlc').checked,
      strict_erc: $('optStrict').checked,
    }
    const res = await api.generate(req)
    if (res && !res.ok) {
      pushLog(res.error || '启动引擎失败')
      setRunning(false, '失败')
      toast('启动引擎失败', 'bad')
    }
  }

  function keysToSend () {
    return state.sel.size
      ? Array.from(state.sel)
      : (state.current ? [state.current] : [])
  }

  function onEvent (e) {
    if (e.ev === 'log') pushLog(e.text)
    else if (e.ev === 'progress') setProgress(e.pct, e.label)
    else if (e.ev === 'start') {
      const q = state.queue[e.index]
      if (q && q.key === e.key) q.st = 'run'
      refreshQueue()
    } else if (e.ev === 'result') {
      state.results[e.key] = e
      const q = state.queue.find((x) => x.key === e.key)
      if (q) {
        Object.assign(q, e)
        q.st = (e.erc_ok && (e.pcb_ok || !$('optPcb').checked)) ? 'done' : 'bad'
      }
      refreshQueue()
      if (e.key === state.current) renderResult(e)
      setProgress((state.queue.filter((x) => x.st !== 'wait' && x.st !== 'run').length /
        Math.max(1, state.queue.length)) * 100)
    } else if (e.ev === 'error') {
      pushLog('! ' + e.text)
      if (e.trace) pushLog(e.trace.trim().split('\n').slice(-3).join(' | '))
    } else if (e.ev === 'done') {
      if (e.readme) state.readme = e.readme
      if (e.stats) {
        $('queueStats').textContent =
          e.stats.ok + '/' + e.stats.total + ' 条全绿' +
          (e.stats.erc_bad ? ' · ERC 有问题 ' + e.stats.erc_bad + ' 条' : '') +
          (e.stats.pcb_bad ? ' · PCB 有问题 ' + e.stats.pcb_bad + ' 条' : '')
        toast('这批跑完：' + e.stats.ok + '/' + e.stats.total + ' 条全绿',
          e.stats.erc_bad || e.stats.pcb_bad ? 'warn' : 'ok')
      } else setProgress(100, '完成 ' + e.total + ' 条')
    } else if (e.ev === 'exit') {
      for (const q of state.queue) if (q.st === 'run' || q.st === 'wait') q.st = 'stop'
      refreshQueue()
      setRunning(false, e.killed ? '已停止' : (e.code === 0 ? '完成' : '引擎退出码 ' + e.code))
    }
  }

  /* ================= 结果区 ================= */
  function chip (text, kind) {
    return el('span', 'chip' + (kind ? ' ' + kind : ''), text)
  }

  function renderResult (r) {
    const chips = $('resultChips')
    const files = $('resultFiles')
    chips.textContent = ''
    files.textContent = ''
    const ne = r.erc_errors || 0
    const nw = r.erc_warns || 0
    chips.appendChild(chip(r.erc_trusted ? 'ERC 沿用同拓扑'
      : (r.erc_ok ? 'ERC 通过' : (ne ? 'ERC ' + ne + ' 错误' : 'ERC ' + nw + ' 告警')),
      r.erc_ok ? (r.erc_trusted ? '' : 'pass') : (ne ? 'fail' : 'warn')))
    chips.appendChild(chip(r.pcb_ok ? 'PCB 已生成' : 'PCB 未生成', r.pcb_ok ? 'pass' : 'fail'))
    const n = Object.keys(r.files || {}).length
    chips.appendChild(chip(n + ' 个产物', ''))
    if (r.files && r.files.lczip) chips.appendChild(chip('嘉立创导入包已出', 'pass'))
    for (const [tag, label, how] of FILE_LABELS) {
      const p = (r.files || {})[tag]
      if (!p) continue
      const b = el('button')
      b.appendChild(el('span', 'k', label))
      b.appendChild(document.createTextNode(p.split('/').pop()))
      b.onclick = async () => {
        let res
        if (how === 'kicad') res = await api.openKicad(p)
        else if (how === 'reveal') res = await api.reveal(p)
        else res = await api.openPath(p)
        if (res && !res.ok) pushLog('打开失败: ' + p + ' — ' + res.error)
      }
      files.appendChild(b)
    }
    $('resultKey').textContent = r.label || r.key
  }

  /* ================= 环境 / 设置 ================= */
  function badge (kind, text) {
    const b = $('envBadge')
    b.className = 'badge' + (kind ? ' ' + kind : '')
    b.querySelector('span').textContent = text
  }

  async function checkEnv () {
    badge('', '检测中…')
    const r = await api.checkEnv()
    if (!r || !r.ok) {
      badge('bad', '引擎无法启动')
      $('envRows').textContent = ''
      $('envNote').textContent = (r && r.error) || '调用引擎失败'
      $('envNote').className = 'note bad'
      return
    }
    const d = r.payload.data
    renderEnvTable(d)
    if (d.ok) badge('ok', 'KiCad ' + (d.version || '?') + ' 就绪')
    else badge('bad', '缺少 ' + d.missing.length + ' 项')
  }

  const ENV_NAMES = {
    kicad_cli: 'kicad-cli',
    kicad_python: 'KiCad Python',
    symbols: '符号库',
    footprints: '封装库',
  }

  function renderEnvTable (d) {
    const host = $('envRows')
    host.textContent = ''
    $('envVersion').textContent = d.version ? 'KiCad ' + d.version : '版本未知'
    for (const [k, p] of Object.entries(d.paths)) {
      const tr = document.createElement('tr')
      const ok = !d.missing.includes(k)
      const c3 = el('td', null, ok ? 'OK' : '缺失')
      c3.style.color = ok ? 'var(--blue)' : 'var(--coral)'
      tr.append(el('td', null, ENV_NAMES[k] || k), el('td', null, p), c3)
      host.appendChild(tr)
    }
    $('envNote').textContent = d.ok
      ? '四项外部依赖齐活，可以直接生成。'
      : '缺 ' + d.missing.join(', ') + '，请确认 KiCad 安装目录填对。'
    $('envNote').className = 'note' + (d.ok ? '' : ' bad')
  }

  let saveTimer = 0
  function persist (patch) {
    Object.assign(state.settings, patch)
    clearTimeout(saveTimer)
    saveTimer = setTimeout(async () => {
      const r = await api.saveSettings(patch)
      if (r && !r.ok) pushLog('设置保存失败: ' + r.error)
    }, 350)
  }

  function fillSettingsUI () {
    const s = state.settings
    $('outdir').value = s.outdir || ''
    $('optSch').checked = s.sch !== false
    $('optPcb').checked = s.pcb !== false
    $('optJlc').checked = s.jlc !== false
    $('optStrict').checked = s.strict_erc === true
    $('optFollow').checked = s.follow_log !== false
    $('optGrid').checked = s.grid !== false
    $('optNets').checked = s.net_colors !== false
    $('setKiCadRoot').value = s.kicad_root || ''
    $('setKicadPy').value = s.kicad_py || ''
    $('setEnginePy').value = s.engine_py || ''
    $('setLcedaExe').value = s.lceda_exe || ''
    state.fav = new Set(s.favorites || [])
    state.recent = (s.recent || []).slice()
  }

  /* ================= 绑定 ================= */
  function bind () {
    window.addEventListener('hashchange', () => showView(location.hash.slice(1)))
    document.querySelectorAll('.nav-item')
      .forEach((n) => n.addEventListener('click', () => { location.hash = n.dataset.view }))

    $('presetSearch').addEventListener('input', applyFilter)
    $('btnFavOnly').onclick = () => {
      state.favOnly = !state.favOnly
      $('btnFavOnly').classList.toggle('is-on', state.favOnly)
      applyFilter()
    }
    $('btnRandom').onclick = () => {
      if (!state.shown.length) return
      select(state.shown[Math.floor(Math.random() * state.shown.length)].key, true)
    }
    $('btnSelectShown').onclick = () => {
      for (const p of state.shown) state.sel.add(p.key)
      syncSel(); list.refresh()
      toast('已勾选筛出的 ' + state.shown.length + ' 条', 'ok')
    }
    $('btnClearSel').onclick = () => {
      state.sel.clear(); syncSel(); list.refresh()
    }
    $('btnSendBatch').onclick = () => {
      if (!state.sel.size) { toast('先在左边勾选预设', 'warn'); return }
      run(Array.from(state.sel), true)
    }
    $('btnRunOne').onclick = () => state.current && run([state.current], true)
    $('btnRun').onclick = () => run(keysToSend())
    $('btnRunFav').onclick = () => {
      const keys = state.presets.filter((p) => state.fav.has(p.key)).map((p) => p.key)
      if (!keys.length) { toast('还没有收藏任何预设', 'warn'); return }
      run(keys)
    }
    $('btnRunCat').onclick = () => run(state.shown.map((p) => p.key))
    $('btnFav').onclick = () => state.current && toggleFav(state.current)
    $('btnToggleSel').onclick = () => state.current && toggleSel(state.current)

    $('btnStop').onclick = () => api.stop()
    $('btnClearLog').onclick = () => {
      state.logLines = []; state.lineNo = 0; renderLog()
    }
    $('btnCopyLog').onclick = async () => {
      const txt = state.logLines.map((l) => l.text).join('\n')
      try { await navigator.clipboard.writeText(txt); toast('日志已复制', 'ok') }
      catch (e) { toast('复制失败：' + e, 'bad') }
    }
    $('logFilter').addEventListener('input', () => {
      state.logFilter = $('logFilter').value.trim()
      renderLog()
    })
    $('optFailOnly').addEventListener('change', refreshQueue)

    $('btnBrowse').onclick = async () => {
      const r = await api.pickDir($('outdir').value.trim())
      if (r && r.ok) { $('outdir').value = r.dir; persist({ outdir: r.dir }) }
    }
    $('btnOpenDir').onclick = () => api.openPath($('outdir').value.trim())
    $('outdir').addEventListener('change', () => persist({ outdir: $('outdir').value.trim() }))

    const dropTargets = [$('outdir'), $('btnBrowse').parentElement]
    for (const t of dropTargets) {
      t.addEventListener('dragover', (e) => { e.preventDefault(); t.classList.add('is-drop') })
      t.addEventListener('dragleave', () => t.classList.remove('is-drop'))
      t.addEventListener('drop', (e) => {
        e.preventDefault()
        t.classList.remove('is-drop')
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
        if (!f) return
        const p = api.filePath ? api.filePath(f) : ''
        if (p) { $('outdir').value = p; persist({ outdir: p }); toast('目录已填入', 'ok') }
      })
    }

    $('optSch').addEventListener('change', () => persist({ sch: $('optSch').checked }))
    $('optPcb').addEventListener('change', () => persist({ pcb: $('optPcb').checked }))
    $('optJlc').addEventListener('change', () => persist({ jlc: $('optJlc').checked }))
    $('optStrict').addEventListener('change', () => persist({ strict_erc: $('optStrict').checked }))
    $('optFollow').addEventListener('change', () => persist({ follow_log: $('optFollow').checked }))
    $('optGrid').addEventListener('change', () => {
      persist({ grid: $('optGrid').checked })
      if (state.current) showPreview(state.current)
    })
    $('optNets').addEventListener('change', () => {
      persist({ net_colors: $('optNets').checked })
      if (state.current) showPreview(state.current)
    })

    $('btnZoomIn').onclick = () => { state.zoom.k = Math.min(14, state.zoom.k * 1.25); applyZoom() }
    $('btnZoomOut').onclick = () => { state.zoom.k = Math.max(0.3, state.zoom.k / 1.25); applyZoom() }
    $('btnZoomFit').onclick = () => { state.zoom = { k: 1, x: 0, y: 0 }; applyZoom() }

    $('btnLceda').onclick = async () => {
      const r = await api.openLceda()
      if (r && !r.ok) pushLog(r.error)
      else pushLog('已启动嘉立创 EDA 专业版 — 菜单: 文件 → 导入 → KiCad，选 <电路名>_lceda.zip')
    }
    $('btnReadme').onclick = async () => {
      const dir = $('outdir').value.trim()
      const p = state.readme || (dir ? dir.replace(/\\+$/, '') + '/嘉立创EDA导入说明.txt' : '')
      if (!p) { pushLog('还没有输出目录，先生成一次。'); return }
      const r = await api.openPath(p)
      if (r && !r.ok) pushLog('还没有导入说明，先跑一次生成（勾上嘉立创）。')
    }

    $('setLcedaExe').addEventListener('change', () => persist({ lceda_exe: $('setLcedaExe').value.trim() }))
    $('setKiCadRoot').addEventListener('change', async () => {
      persist({ kicad_root: $('setKiCadRoot').value.trim() })
      await checkEnv()
    })
    $('setKicadPy').addEventListener('change', () => persist({ kicad_py: $('setKicadPy').value.trim() }))
    $('setEnginePy').addEventListener('change', async () => {
      persist({ engine_py: $('setEnginePy').value.trim() })
      await checkEnv()
    })
    $('btnEnv').onclick = checkEnv
    $('btnDefaults').onclick = async () => {
      await api.saveSettings(state.defaults)
      state.settings = Object.assign({}, state.defaults)
      fillSettingsUI()
      applyFilter()
      await checkEnv()
      toast('已恢复默认设置', 'ok')
    }

    $('btnHelp').onclick = toggleHelp
    $('btnCloseHelp').onclick = toggleHelp
    $('helpOverlay').onclick = (e) => { if (e.target === $('helpOverlay')) toggleHelp() }

    document.addEventListener('keydown', onKey)
  }

  function toggleHelp () {
    $('helpOverlay').hidden = !$('helpOverlay').hidden
  }

  function typing (e) {
    return e.target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)
  }

  function onKey (e) {
    if (e.key === 'Escape') {
      if (!$('helpOverlay').hidden) { toggleHelp(); return }
      if (typing(e)) { e.target.blur(); return }
      if (state.running) { api.stop(); return }
      return
    }
    if (e.key === '?' && !typing(e)) { toggleHelp(); e.preventDefault(); return }
    if (e.ctrlKey && e.key === 'Enter') { if (!state.running) run(keysToSend()); e.preventDefault(); return }
    if (e.ctrlKey && (e.key === 'a' || e.key === 'A') && !typing(e)) {
      for (const p of state.shown) state.sel.add(p.key)
      syncSel(); list.refresh(); e.preventDefault(); return
    }
    if (typing(e)) {
      if (e.key === 'Escape') e.target.blur()
      return
    }
    if (e.key === '/') { $('presetSearch').focus(); e.preventDefault(); return }
    if (e.key === 'ArrowDown') { step(1); e.preventDefault(); return }
    if (e.key === 'ArrowUp') { step(-1); e.preventDefault(); return }
    if (e.key === 'ArrowRight') { stepCat(1); e.preventDefault(); return }
    if (e.key === 'ArrowLeft') { stepCat(-1); e.preventDefault(); return }
    if (e.key === ' ') { if (state.current) toggleSel(state.current); e.preventDefault(); return }
    if (['g', 'b'].includes(e.key)) { location.hash = 'browse'; return }
    if (e.key === 'r') { location.hash = 'batch'; return }
    if (e.key === 's') { location.hash = 'settings'; return }
  }

  /* ================= 启动 ================= */
  async function init () {
    bind()
    renderLog()
    if (!api) {
      badge('bad', '未连接主进程')
      pushLog('这个页面要在 AutoDraw 窗口里打开，主进程接口不可用。')
      return
    }
    api.onEvent(onEvent)
    const boot = await api.boot()
    state.settings = boot.settings
    state.defaults = boot.defaults
    fillSettingsUI()
    fillHistory()
    $('buildInfo').textContent =
      'v' + boot.version + ' · Electron ' + boot.electron +
      '\n配置: ' + boot.settingsFile
    $('buildInfo').style.whiteSpace = 'pre-line'
    pushLog('AutoDraw v' + boot.version + (boot.packaged ? ' (exe)' : ' (dev)'))
    if (!boot.enginePyExists) {
      pushLog('引擎 Python 不存在: ' + boot.settings.engine_py + ' — 去设置里改。')
    }

    const pl = await api.listPresets()
    if (pl && pl.ok) {
      state.presets = pl.payload.items
      state.cats = Array.from(new Set(state.presets.map((p) => p.cat)))
      pushLog('已载入 ' + state.presets.length + ' 条电路预设，' +
        state.cats.length + ' 个分类。')
      renderCats()
      const want = boot.settings.circuit
      const cur = state.presets.some((p) => p.key === want) ? want
        : (state.presets[0] && state.presets[0].key)
      state.current = cur
      applyFilter()
      if (cur) {
        list.scrollTo(shownIndex(cur))
        showPreview(cur)
      }
      syncSel()
    } else {
      pushLog('读取预设失败: ' + ((pl && pl.error) || '未知错误'))
    }
    await checkEnv()
    showView(location.hash.slice(1) || 'browse')
  }

  window.addEventListener('DOMContentLoaded', init)
})()
