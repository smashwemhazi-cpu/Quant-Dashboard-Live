// D.O.N.N.A Google bridge - runs inside your own Google account, so no password is ever given to DONNA.
// It READS new unread inbox emails, your calendar, and report attachments (.csv / .xlsx / .xls).
// The one thing it writes is a DRAFT reply when you ask for one (!draft 12) - you review and send it yourself.
// It never sends, deletes, labels or marks anything read.
// Set-up (5 minutes, once) is in DONNA.md under "Email and calendar without a password".
// After pasting a newer copy: Deploy > Manage deployments > pencil > Version: New version > Deploy (the link stays the same).

var VERSION = 2;
var REPORT_FILE = /\.(csv|xlsx|xls)$/i;
var REPORT_MAX_BYTES = 8 * 1024 * 1024;

function setup() {
  var p = PropertiesService.getScriptProperties();
  if (!p.getProperty('KEY')) {
    p.setProperty('KEY', Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, ''));
  }
  GmailApp.getInboxUnreadCount();   // asks Google for permission the first time
  CalendarApp.getDefaultCalendar();
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
  } else {
    out = {ok: true, version: VERSION, account: Session.getEffectiveUser().getEmail(),
           unread: GmailApp.getInboxUnreadCount()};
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

function mail_(days, max) {
  var msgs = [];
  GmailApp.search('in:inbox is:unread newer_than:' + days + 'd', 0, max).forEach(function (t) {
    t.getMessages().forEach(function (m) {
      if (!m.isUnread()) return;
      msgs.push({id: m.getHeader('Message-ID') || m.getId(), date: m.getDate().toISOString(), from: m.getFrom(),
                 subject: m.getSubject(), body: m.getPlainBody().slice(0, 8000)});
    });
  });
  return {messages: msgs.slice(-max)};
}

function calendar_(start, days) {
  var s = start ? new Date(start) : new Date();
  var e = new Date(s.getTime() + days * 86400000);
  var tz = Session.getScriptTimeZone();
  return {events: CalendarApp.getDefaultCalendar().getEvents(s, e).map(function (ev) {
    var allDay = ev.isAllDayEvent();
    return {title: ev.getTitle(), all_day: allDay, location: ev.getLocation(),
            day: allDay ? Utilities.formatDate(ev.getAllDayStartDate(), tz, 'yyyy-MM-dd') : '',
            start: ev.getStartTime().toISOString(), end: ev.getEndTime().toISOString()};
  })};
}

// spreadsheet attachments of the last few days: names and sizes only - DONNA asks for the ones it hasn't seen
function reports_(days, max) {
  var out = [];
  var query = 'has:attachment newer_than:' + days + 'd -in:trash -in:spam (filename:csv OR filename:xlsx OR filename:xls)';
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
