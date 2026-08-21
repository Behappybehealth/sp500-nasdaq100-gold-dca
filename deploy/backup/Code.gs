/**
 * DCA 事实源每日快照（BUG-018）
 *
 * 本文件是唯一事实源。部署方式：绑定在源表格上的 Apps Script 项目（表格 → 扩展 →
 * Apps Script），把本文件全文粘贴进去，再给 dailyBackup 建一个每日时间驱动触发器。
 * 完整步骤与恢复演练指引见 deploy/DEPLOY.md §6——本文件不随 git 自动部署，
 * 改了源码必须重新粘贴才生效，两边漂移以本文件为准。
 *
 * 设计要点（与 docs/BUGLIST.md BUG-018 确认记录一一对应）：
 * - 容器绑定：源表经 SpreadsheetApp.getActiveSpreadsheet() 取得，零 ID 配置、零凭据
 * - 快照内容：四张表（users / transactions / observations / budget_overrides）各一份
 *   CSV + manifest.json（行数 + SHA-256）。users 表含 PIN 哈希与盐——快照与源同在
 *   Google 账号内，不出新落点；这也是恢复后能直接登录的前提
 * - 保留：快照目录按日命名，超 RETENTION_DAYS 自动进回收站；非快照命名的目录不动
 * - 失败告警：dailyBackup 任何异常都会先邮件告警再抛出（面板同步记失败），
 *   每日任务的静默失败在这条路上不存在
 * - 恢复守卫：restoreSnapshot 必须显式给目标表格 ID，且拒绝恢复到源表自身——
 *   演练与灾备都只能在另一张表上进行，生产表不可能被手滑覆写
 */

// ---- 配置 ----
var BACKUP_FOLDER_NAME = 'dca-backups';
var RETENTION_DAYS = 30;
// 四张表名与 storage.py 的工作表名一一对应（tests/test_backup_script.py 有同步断言）
var TABLES = ['users', 'transactions', 'observations', 'budget_overrides'];
var ALERT_EMAIL = ''; // 留空 = 发到脚本所属 Google 账号

/**
 * 触发器入口：每日快照 + 修剪旧份。失败先邮件告警再把异常抛给执行日志。
 */
function dailyBackup() {
  try {
    var result = exportSnapshot_();
    pruneOldSnapshots_();
    console.log('备份完成：%s（%s）', result.folder, JSON.stringify(result.tables));
  } catch (e) {
    var to = ALERT_EMAIL || Session.getEffectiveUser().getEmail();
    if (to) {
      MailApp.sendEmail(
        to,
        '[DCA] 每日备份失败',
        'dailyBackup 执行失败，备份制度处于无保护状态，请尽快查看 Apps Script 执行日志。\n\n' +
          '异常：' + e + '\n时间：' + new Date().toISOString()
      );
    }
    throw e;
  }
}

/**
 * 从快照恢复到【另一张表】。演练与真实灾备共用的唯一入口。
 * @param {string} dateStr 快照日期前缀，如 "2026-08-21"（同一天多份取最新）
 * @param {string} targetSpreadsheetId 目标表格 ID（URL 里 /d/ 与 /edit 之间那段）
 * @return {Object} 每表恢复行数报告
 */
function restoreSnapshot(dateStr, targetSpreadsheetId) {
  if (!dateStr || !targetSpreadsheetId) {
    throw new Error('用法：restoreSnapshot("2026-08-21", "<目标表格ID>")');
  }
  var src = SpreadsheetApp.getActiveSpreadsheet();
  if (src && targetSpreadsheetId === src.getId()) {
    throw new Error('拒绝恢复到源表自身——灾备恢复必须显式指定另一张表');
  }
  var snap = findSnapshot_(dateStr);
  var target = SpreadsheetApp.openById(targetSpreadsheetId);
  var report = {};
  TABLES.forEach(function (t) {
    var files = snap.getFilesByName(t + '.csv');
    if (!files.hasNext()) {
      report[t] = '快照中缺失（源表当时无此工作表），跳过';
      return;
    }
    var rows = parseCsv_(files.next().getBlob().getDataAsString('UTF-8'));
    var sheet = target.getSheetByName(t) || target.insertSheet(t);
    sheet.clearContents();
    if (rows.length) {
      var width = Math.max.apply(null, rows.map(function (r) { return r.length; }));
      rows = rows.map(function (r) {
        while (r.length < width) r.push('');
        return r;
      });
      sheet.getRange(1, 1, rows.length, width).setValues(rows);
    }
    report[t] = Math.max(0, rows.length - 1) + ' 行';
  });
  console.log('恢复完成：%s → %s（%s）', snap.getName(), target.getName(), JSON.stringify(report));
  return report;
}

// ================= 内部实现 =================

function exportSnapshot_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('本脚本必须作为容器绑定脚本运行（从源表格的"扩展 → Apps Script"进入）');
  var tz = ss.getSpreadsheetTimeZone() || 'Asia/Shanghai';
  var root = getOrCreateFolder_();
  var stamp = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd_HHmmss');
  var name = stamp;
  var n = 2;
  while (root.getFoldersByName(name).hasNext()) name = stamp + '_' + n++;
  var dir = root.createFolder(name);
  var manifest = {
    created_at: stamp,
    timezone: tz,
    spreadsheet_id: ss.getId(),
    tables: {}
  };
  TABLES.forEach(function (t) {
    var sheet = ss.getSheetByName(t);
    if (!sheet) {
      // 表未创建（如从未覆盖预算）是合法空表，与 storage.py 语义一致：记 missing 不报错
      manifest.tables[t] = { missing: true };
      return;
    }
    var values = sheet.getDataRange().getValues();
    var csv = toCsv_(values, tz);
    dir.createFile(t + '.csv', csv, MimeType.CSV);
    manifest.tables[t] = { rows: Math.max(0, values.length - 1), sha256: sha256Hex_(csv) };
  });
  dir.createFile('manifest.json', JSON.stringify(manifest, null, 2), MimeType.PLAIN_TEXT);
  return { folder: name, tables: manifest.tables };
}

function pruneOldSnapshots_() {
  var root = getOrCreateFolder_();
  var cutoff = new Date(Date.now() - RETENTION_DAYS * 86400000);
  var it = root.getFolders();
  while (it.hasNext()) {
    var f = it.next();
    var m = /^(\d{4})-(\d{2})-(\d{2})_/.exec(f.getName());
    if (!m) continue; // 非快照命名（用户自己放的东西）不动
    if (new Date(+m[1], +m[2] - 1, +m[3]) < cutoff) f.setTrashed(true);
  }
}

function findSnapshot_(dateStr) {
  var rootIt = DriveApp.getFoldersByName(BACKUP_FOLDER_NAME);
  if (!rootIt.hasNext()) throw new Error('找不到备份根目录 ' + BACKUP_FOLDER_NAME);
  var it = rootIt.next().getFolders();
  var best = null;
  while (it.hasNext()) {
    var f = it.next();
    if (f.getName().indexOf(dateStr) === 0 && (!best || f.getName() > best.getName())) best = f;
  }
  if (!best) throw new Error('找不到日期前缀为 ' + dateStr + ' 的快照');
  return best;
}

function getOrCreateFolder_() {
  var it = DriveApp.getFoldersByName(BACKUP_FOLDER_NAME);
  return it.hasNext() ? it.next() : DriveApp.createFolder(BACKUP_FOLDER_NAME);
}

/** 全字段加引号的 CSV 写出（引号内双写转义），与 parseCsv_ 互为往返。 */
function toCsv_(values, tz) {
  return values.map(function (row) {
    return row.map(function (cell) {
      var s = (cell instanceof Date)
        ? Utilities.formatDate(cell, tz, "yyyy-MM-dd'T'HH:mm:ss")
        : String(cell);
      return '"' + s.replace(/"/g, '""') + '"';
    }).join(',');
  }).join('\r\n');
}

/** 与 toCsv_ 配对的解析器：状态机处理引号、双写转义与字段内换行。 */
function parseCsv_(text) {
  var rows = [];
  var row = [];
  var field = '';
  var inQ = false;
  for (var i = 0; i < text.length; i++) {
    var c = text.charAt(i);
    if (inQ) {
      if (c === '"') {
        if (text.charAt(i + 1) === '"') { field += '"'; i++; }
        else inQ = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQ = true;
    } else if (c === ',') {
      row.push(field); field = '';
    } else if (c === '\r') {
      // 跳过，\n 才算行尾
    } else if (c === '\n') {
      row.push(field); rows.push(row); row = []; field = '';
    } else {
      field += c;
    }
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows;
}

function sha256Hex_(s) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s, Utilities.Charset.UTF_8)
    .map(function (b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); })
    .join('');
}
