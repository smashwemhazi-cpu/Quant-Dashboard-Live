// D.O.N.N.A Google bridge - runs inside your own Google account, so no password is ever given to DONNA.
// It only READS: new unread inbox emails and your calendar. It never sends, deletes, labels or marks anything read.
// Set-up (5 minutes, once) is in DONNA.md under "Email and calendar without a password".

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
  } else {
    out = {ok: true, account: Session.getEffectiveUser().getEmail(), unread: GmailApp.getInboxUnreadCount()};
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
