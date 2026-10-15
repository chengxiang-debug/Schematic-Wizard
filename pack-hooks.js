'use strict'
/* electron-packager --overwrite 会连 exe 旁边的 autodraw_settings.json 一起删掉，
   打包前把它挪到上层备份，打包后再放回去。npm 的 prepack/postpack 调用本文件。 */
const fs = require('fs')
const path = require('path')

const ROOT = __dirname
const OUTDIR = path.join(ROOT, 'AutoDraw-win32-x64')
const LIVE = path.join(OUTDIR, 'autodraw_settings.json')
const BACKUP = path.join(ROOT, '.autodraw_settings.backup.json')

const cmd = process.argv[2]
try {
  if (cmd === 'stash' && fs.existsSync(LIVE)) {
    fs.copyFileSync(LIVE, BACKUP)
    console.log('[pack-hooks] 已备份设置: ' + LIVE)
  } else if (cmd === 'restore' && fs.existsSync(BACKUP)) {
    fs.mkdirSync(OUTDIR, { recursive: true })
    fs.copyFileSync(BACKUP, LIVE)
    fs.unlinkSync(BACKUP)
    console.log('[pack-hooks] 已还原设置: ' + LIVE)
  }
} catch (e) {
  console.log('[pack-hooks] 失败: ' + (e.message || e))
  process.exitCode = 1
}
