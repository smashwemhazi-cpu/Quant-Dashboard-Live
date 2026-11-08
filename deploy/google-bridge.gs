// D.O.N.N.A Google bridge - runs inside your own Google account, so no password is ever given to DONNA.
// It READS new inbox emails (read or unread), your calendar (with guest addresses), which of your sent emails
// are still unanswered (subjects only), and report attachments (.csv / .xlsx / .xls, and from v6 cTrader's zipped .html EOD report).
// It WRITES only: a draft reply when you ask for one (!draft 12), an event in your own calendar when you ask for one
// (!diary add / !diary block - no guests, so no invitations), DONNA's encrypted nightly backup to the
// "DONNA backups" folder in your Drive, and an email TO YOU if DONNA's server stops checking in (uptime alert).
// It never sends email to anyone else, and never deletes, labels or marks anything read.
// Set-up (5 minutes, once) is in DONNA.md under "Email and calendar without a password".
// After pasting a newer copy: Deploy > Manage deployments > pencil > Version: New version > Deploy (the link stays the same).

var VERSION = 6;
var WATCH_MINUTES = 30;      // email me if DONNA hasn't checked in for this long
var BACKUPS_KEPT = 14;
var REPORT_FILE = /\.(csv|xlsx|xls|zip|html|htm)$/i;   // v6: cTrader's zipped HTML EOD report
var REPORT_MAX_BYTES = 8 * 1024 * 1024;

function setup() {
  var p = PropertiesService.getScriptProperties();
  if (!p.getProperty('KEY')) {
    p.setProperty('KEY', Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, ''));
  }
  GmailApp.getInboxUnreadCount();   // asks Google for permission the first time
  CalendarApp.getDefaultCalendar();
  backupFolder_();
  // the uptime watchdog: every 15 minutes, check DONNA's last heartbeat
  var has = ScriptApp.getProjectTriggers().some(function (t) { return t.getHandlerFunction() === 'watchdog'; });
  if (!has) ScriptApp.newTrigger('watchdog').timeBased().everyMinutes(15).create();
  Logger.log('DONNA bridge key - paste it at the "bridge key" question: ' + p.getProperty('KEY'));
}

function doGet(e) {
  var key = PropertiesService.getScriptProperties().getProperty('KEY');
  var q = (e && e.parameter) || {};
  var out;
  if (!key || q.key !== key) {
    out = {error: 'bad key'};
  } else if (q.action === 'mail') {
    out = mail_(Number(q.days || 2), Number(q.max || 25));
  } else if (q.action === 'calendar') {
    out = calendar_(q.start, Number(q.days || 1));
  } else if (q.action === 'reports') {
    out = reports_(Number(q.days || 3), Number(q.max || 30));
  } else if (q.action === 'attachment') {
    out = attachment_(q.id);
  } else if (q.action === 'message') {
    out = message_(q.id);
  } else if (q.action === 'sent') {
    out = sent_(Number(q.days || 21), Number(q.max || 40));
  } else if (q.action === 'heartbeat') {
    out = heartbeat_(q.release || '');
  } else {
    out = {ok: true, version: VERSION, account: Session.getEffectiveUser().getEmail(),
           unread: GmailApp.getInboxUnreadCount()};
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

// new inbox mail, read or not (mail you already opened on your phone still counts for !workflow); each message says
// whether it is still unread and whether you have already replied in its thread, so DONNA doesn't chase handled mail
function mail_(days, max) {
  var msgs = [];
  var me = Session.getEffectiveUser().getEmail().toLowerCase();
  GmailApp.search('in:inbox newer_than:' + days + 'd', 0, max).forEach(function (t) {
    var all = t.getMessages();
    all.forEach(function (m, i) {
      if (m.isDraft() || m.getFrom().toLowerCase().indexOf(me) >= 0) return;   // your own messages and drafts
      // answered = you SENT something later in the thread (a reply DONNA drafted doesn't count until you send it)
      var answered = all.slice(i + 1).some(function (n) { return !n.isDraft() && n.getFrom().toLowerCase().indexOf(me) >= 0; });
      msgs.push({id: m.getHeader('Message-ID') || m.getId(), date: m.getDate().toISOString(), from: m.getFrom(),
                 subject: m.getSubject(), body: m.getPlainBody().slice(0, 8000), unread: m.isUnread(),
                 answered: answered});
    });
  });
  return {version: VERSION, messages: msgs.slice(-max * 2)};
}

function calendar_(start, days) {
  var s = start ? new Date(start) : new Date();
  var e = new Date(s.getTime() + days * 86400000);
  var tz = Session.getScriptTimeZone();
  var me = Session.getEffectiveUser().getEmail().toLowerCase();
  return {version: VERSION, events: CalendarApp.getDefaultCalendar().getEvents(s, e).map(function (ev) {
    var allDay = ev.isAllDayEvent();
    // who the meeting is with (for meeting prep): the guests and whoever sent the invitation, never you
    var guests = ev.getGuestList().map(function (g) { return g.getEmail(); }).concat(ev.getCreators())
      .filter(function (x, i, a) { return x && x.toLowerCase() !== me && a.indexOf(x) === i; });
    var status = String(ev.getMyStatus() || '');                           // NO = you declined it
    return {title: ev.getTitle(), all_day: allDay, location: ev.getLocation(), guests: guests, my_status: status,
            day: allDay ? Utilities.formatDate(ev.getAllDayStartDate(), tz, 'yyyy-MM-dd') : '',
            start: ev.getStartTime().toISOString(), end: ev.getEndTime().toISOString()};
  })};
}

// mail you sent that nobody has answered yet: threads in Sent whose newest message is yours (for !waiting).
// Subjects, recipients and dates only - no message text.
function sent_(days, max) {
  var me = Session.getEffectiveUser().getEmail().toLowerCase();
  var out = [];
  GmailApp.search('in:sent newer_than:' + days + 'd', 0, max).forEach(function (t) {
    var all = t.getMessages().filter(function (m) { return !m.isDraft(); });
    var last = all[all.length - 1];
    if (!last || last.getFrom().toLowerCase().indexOf(me) < 0) return;    // they have answered
    var others = [last.getTo(), last.getCc()].join(',').split(',').filter(function (a) {
      return a.indexOf('@') >= 0 && a.toLowerCase().indexOf(me) < 0;
    });
    if (!others.length) return;                                         // a note to yourself
    out.push({thread: t.getId(), to: last.getTo(), subject: last.getSubject(), date: last.getDate().toISOString()});
  });
  return {version: VERSION, sent: out};
}

// spreadsheet attachments of the last few days: names and sizes only - DONNA asks for the ones it hasn't seen
function reports_(days, max) {
  var out = [];
  var query = 'has:attachment newer_than:' + days + 'd -in:trash -in:spam (filename:csv OR filename:xlsx OR filename:xls OR filename:zip OR filename:html)';
  GmailApp.search(query, 0, 50).forEach(function (t) {
    t.getMessages().forEach(function (m) {
      m.getAttachments({includeInlineImages: false}).forEach(function (a, i) {
        if (!REPORT_FILE.test(a.getName()) || a.getSize() > REPORT_MAX_BYTES) return;
        out.push({id: m.getId() + ':' + i, name: a.getName(), size: a.getSize(), from: m.getFrom(),
                  subject: m.getSubject(), date: m.getDate().toISOString()});
      });
    });
  });
  return {version: VERSION, attachments: out.slice(0, max)};
}

function attachment_(id) {
  var parts = String(id || '').split(':');
  var m = GmailApp.getMessageById(parts[0]);
  var a = m ? m.getAttachments({includeInlineImages: false})[Number(parts[1])] : null;
  if (!a || !REPORT_FILE.test(a.getName()) || a.getSize() > REPORT_MAX_BYTES) return {error: 'no such report'};
  return {name: a.getName(), data: Utilities.base64Encode(a.getBytes())};
}

function doPost(e) {
  var key = PropertiesService.getScriptProperties().getProperty('KEY');
  var q = (e && e.parameter) || {};
  var body = {};
  try { body = JSON.parse((e && e.postData && e.postData.contents) || '{}'); } catch (err) { body = {}; }
  var out;
  if (!key || q.key !== key) {
    out = {error: 'bad key'};
  } else if (q.action === 'draft') {
    out = draft_(body.id, body.text);
  } else if (q.action === 'backup') {
    out = backup_(body.name, body.data);
  } else if (q.action === 'event') {
    out = event_(body.title, body.start, Number(body.minutes || 30), body.note);
  } else {
    out = {error: 'unknown action'};
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

// the email behind a task, found by its Message-ID header
function find_(id) {
  if (!id) return null;
  var t = GmailApp.search('rfc822msgid:' + id, 0, 1)[0];
  if (!t) return null;
  var ms = t.getMessages();
  return ms.filter(function (m) { return m.getHeader('Message-ID') === id; })[0] || ms[ms.length - 1];
}

function message_(id) {
  var m = find_(id);
  if (!m) return {error: 'email not found'};
  return {from: m.getFrom(), subject: m.getSubject(), date: m.getDate().toISOString(), body: m.getPlainBody().slice(0, 8000)};
}

function draft_(id, text) {
  var m = find_(id);
  if (!m) return {error: 'email not found'};
  if (!text) return {error: 'empty draft'};
  var d = m.createDraftReply(String(text).slice(0, 20000));   // saved in Drafts - never sent
  return {ok: true, draft: d.getId()};
}

// a new event in your own default calendar: no guests (nobody is invited), never edits or deletes existing ones
function event_(title, start, minutes, note) {
  var s = new Date(start);
  if (!title || isNaN(s.getTime())) return {error: 'bad event'};
  minutes = Math.max(5, Math.min(minutes || 30, 480));
  var ev = CalendarApp.getDefaultCalendar().createEvent(String(title).slice(0, 200), s,
    new Date(s.getTime() + minutes * 60000), {description: 'Added by DONNA. ' + String(note || '').slice(0, 500)});
  return {ok: true, id: ev.getId()};
}

// ------------------------------------------------------------------ uptime
function heartbeat_(release) {
  var p = PropertiesService.getScriptProperties();
  var wasDown = p.getProperty('ALERTED') === '1';
  p.setProperties({LAST_BEAT: String(Date.now()), RELEASE: String(release).slice(0, 20), ALERTED: '0'});
  if (wasDown) {
    MailApp.sendEmail(Session.getEffectiveUser().getEmail(), 'DONNA is back online',
                      'DONNA\'s server is checking in again (version ' + release + '). Nothing for you to do.');
  }
  return {ok: true, version: VERSION};
}

function watchdog() {
  var p = PropertiesService.getScriptProperties();
  var last = Number(p.getProperty('LAST_BEAT') || 0);
  if (!last || p.getProperty('ALERTED') === '1') return;    // never checked in yet, or already told
  var minutes = Math.round((Date.now() - last) / 60000);
  if (minutes < WATCH_MINUTES) return;
  p.setProperty('ALERTED', '1');
  MailApp.sendEmail(Session.getEffectiveUser().getEmail(), 'DONNA is offline (' + minutes + ' minutes)',
    'DONNA\'s server has not checked in for ' + minutes + ' minutes. Morning/evening messages, email triage and ' +
    'alerts are paused until it is back.\n\nWhat to do: open the DigitalOcean dashboard and check the droplet is ' +
    'on (power it on or reboot it if not). If it is on, run DONNA-Console.bat or log in and type: vertex1-update --status\n\n' +
    'You will get a "back online" email as soon as it checks in again.');
}

// ------------------------------------------------------------------ off-site backup
function backupFolder_() {
  var it = DriveApp.getFoldersByName('DONNA backups');
  return it.hasNext() ? it.next() : DriveApp.createFolder('DONNA backups');
}

function backup_(name, data) {
  if (!name || !data || !/^donna-backup-[0-9-]+\.tgz\.enc$/.test(name)) return {error: 'bad backup'};
  var folder = backupFolder_();
  folder.createFile(Utilities.newBlob(Utilities.base64Decode(data), 'application/octet-stream', name));
  var files = [];
  var it = folder.getFiles();
  while (it.hasNext()) files.push(it.next());
  files.sort(function (a, b) { return b.getDateCreated() - a.getDateCreated(); });
  files.slice(BACKUPS_KEPT).forEach(function (f) { f.setTrashed(true); });   // keep the newest 14
  return {ok: true, kept: Math.min(files.length, BACKUPS_KEPT)};
}
