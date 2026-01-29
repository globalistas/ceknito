import io from 'socket.io-client';
import icon from './Icon'
import u from './Util';
import anchorme from "anchorme";
import _ from './utils/I18n';
import Tinycon from 'tinycon'
RegExp.escape = function (s) {
  return s.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
};

function setupTinycon() {
  const valueElem = document.getElementById("pagefoot-notifications-icon");
  const onIcon = !valueElem || (valueElem.getAttribute("data-value") == "True");

  Tinycon.setOptions({
    height: 11,
    font: '10px arial',
    color: '#ffffff',
    background: '#000000',
    fallback: onIcon ? true : "force"
  });
}

const socket = io('///snt', { transports: ['websocket'], upgrade: false });

function updateNotifications(count) {
  const mailCount = document.getElementById('mailcount');
  if (mailCount) {
    if (count == 0) {
      mailCount.innerHTML = '';
      mailCount.style.display = 'none';
    } else {
      mailCount.innerHTML = count;
      mailCount.style.display = 'inline-block';
    }
  }
}

function updateModNotifications(notifications) {
  const modElem = document.getElementById('modcount');
  if (modElem) {
    let sum = 0;
    for (let m in notifications) {
      for (let s in notifications[m]) {
        sum += notifications[m][s];
      }
    }
    if (sum == 0) {
      modElem.innerHTML = '';
      modElem.style.display = 'none';
    } else {
      modElem.innerHTML = sum.toString();
      modElem.style.display = 'inline-block';
    }
  }
}

function updateTitleNotifications() {
  let count = 0;
  const mailcount = document.getElementById('mailcount');
  if (mailcount) {
    count += Number(mailcount.innerHTML);
  }
  const modcount = document.getElementById('modcount');
  if (modcount) {
    count += Number(modcount.innerHTML);
  }

  setupTinycon();
  Tinycon.setBubble(count)
}


var modData = [];

// Get the mod notifications if present, and update the title bar with
// the total number of notifications (for both mods and non-mods).
u.ready(function () {
  var modElem = document.getElementById('modcount');
  if (modElem) {
    modData = JSON.parse(modElem.getAttribute('data-mod'));
    updateModNotifications(modData);
  }
  updateTitleNotifications();
})


// Thumbnails, lazy and deferred loading.  If a thumbnail exists,
// wait until the page is loaded to put it into the src attribute
// of the image element so the browser can start rendering
// the page.  If a thumbnail is still being calculated, listen
// for the socketio message announcing its completion and insert
// it where it belongs.

function loadLazy() {
  var lazy = document.querySelectorAll('.lazy');;
  for (var i = 0; i < lazy.length; i++) {
    var data_src = lazy[i].getAttribute('data-src');
    if (data_src) {
      lazy[i].src = data_src;
      lazy[i].removeAttribute('data-src');
    }
    lazy[i].classList.remove('lazy');
  }
}

socket.loadLazy = loadLazy;


function subscribeDeferred() {
  var deferred = document.querySelectorAll('.deferred');

  // Set up the callback for a thumbnail event.
  socket.on('thumbnail', function (data) {
    for (var i = 0; i < deferred.length; i++) {
      if (deferred[i].getAttribute('data-deferred') == data.target &&
        data.thumbnail != '') {
        var elem = deferred[i];
        if (elem.tagName == 'IMG') {
          elem.src = data.thumbnail;
          elem.classList.remove('deferred');
          elem.removeAttribute('data-deferred');
        } else {
          var img = document.createElement('img');
          if (elem.classList.contains('nsfw-blur')) {
            img.classList.add('nsfw-blur');
          }
          img.src = data.thumbnail;
          elem.parentNode.replaceChild(img, elem);
        }
      }
    }
  });

  // Subscribe to the thumbnail ready event.
  for (var i = 0; i < deferred.length; i++) {
    var data_deferred = deferred[i].getAttribute('data-deferred');
    if (data_deferred) {
      socket.emit('deferred', { target: data_deferred });
    }
  }
}


u.ready(function () {
  loadLazy();
  subscribeDeferred();
})

socket.on('notification', function (d) {
  updateNotifications(d.count.messages + d.count.notifications);
  for (let sub in d.count.modmail) {
    modData["messages"][sub] = d.count.modmail[sub];
  }
  updateModNotifications(modData);
  updateTitleNotifications();
});

socket.on('mod-notification', function (d) {
  const sub = d.update[0];
  modData["reports"][sub] = d.update[1];
  modData["comments"][sub] = d.update[2];
  updateModNotifications(modData);
  updateTitleNotifications();
})

// Using a thousands separator on current user's phuks taken
function numberWithCommas(x) {
    return x.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

socket.on('uinfo', function (d) {
  updateNotifications(d.ntf);
  modData = d.mod_ntf;
  updateModNotifications(modData);
  updateTitleNotifications();
  const postScore = document.getElementById('postscore');
  if (postScore) {
    postScore.innerHTML = numberWithCommas(d.taken);
  }
});

socket.on('uscore', function (d) {
  document.getElementById('postscore').innerHTML = numberWithCommas(d.score);
})

// Track removed RA items in a queue to maintain order
let removedItems = [];

// Recent activity post deletions
socket.on('deletion', function (data) {
  const item = document.querySelector(`li[data-pid="${data.pid}"]`);
  if (item) {
    item.remove();

    // Ensure we have 5 items by replacing the deleted one (if there are removed items stored)
    const recentActivity = document.getElementById('activity_list_sidebar');
    if (removedItems.length > 0) {
      const replacement = removedItems.shift(); // Get the oldest removed item
      recentActivity.appendChild(replacement); // Add it to the bottom of the list
    }
  }
});

// Recent activity comment deletions
socket.on('comment-deletion', function (data) {
  const item = document.querySelector(`li[data-cid="${data.comment_url}"]`);
  if (item) {
    item.remove();

    // Ensure we have 5 items by replacing the deleted one (if there are removed items stored)
    const recentActivity = document.getElementById('activity_list_sidebar');
    if (removedItems.length > 0) {
      const replacement = removedItems.shift(); // Get the oldest removed item
      recentActivity.appendChild(replacement); // Add it to the bottom of the list
    }
  }
});

// Recent activity comment updates
socket.on('comment', function (data) {
  const recentActivity = document.getElementById('activity_list_sidebar');
  if (recentActivity && data.show_sidebar) {
    const showNSFW = document.getElementById('pagefoot-nsfw').getAttribute('data-value') == 'True';
    if (data.nsfw && !showNSFW) {
      return;
    }
    const showNSFWBlur = document.getElementById('pagefoot-nsfw-blur').getAttribute('data-value') == 'True';
    const nsfwClass = (data.nsfw && showNSFWBlur) ? 'nsfw-blur' : '';
    const nsfwElem = data.nsfw ? ('<span class="nsfw smaller" title="' + _('Not safe for work') + '">' + _('NSFW') + '</span>') : '';
    // Handle image-only comments
    let content;
    if (data.content === '<image>' || !data.content || data.content.trim() === '') {
      content = '&lt;image&gt;';
    } else {
      content = data.content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    const elem = document.createElement('li');
    elem.setAttribute('data-cid', data.comment_url);
    elem.innerHTML = _('%1:<br>%2 %3 in %4',
      '<a href="/u/' + data.user + '">' + data.user + '</a> <span class="postedspan">' + _('commented') + '</span>',
      '<a class="' + nsfwClass + '" href="' + data.comment_url + '">' + content + '</a>' + nsfwElem,
      '<div class="sidelocale"><time-ago datetime="' + new Date().toISOString() + '"></time-ago>',
      '<a href="' + data.sub_url + '">' + decodeURIComponent(data.sub_url) + '</a></div>');
    elem.classList.add('new-item');

    // Check if there are already 5 items
    if (recentActivity.children.length >= 5) {
      // Move the last element to the removedItems queue
      removedItems.push(recentActivity.lastElementChild);
      recentActivity.removeChild(recentActivity.lastElementChild); // Remove the last item
    }

    const header = recentActivity.querySelector('h4');
    if (header) {
      header.after(elem); // Inserts the new element after the <h4>
    } else {
      recentActivity.prepend(elem); // Fallback if no <h4> exists
    }

    // Remove 'new-item' class after animation
    setTimeout(() => elem.classList.remove('new-item'), 2500);
  }
});

// Recent activity post updates
socket.on('thread', function (data) {
  if (window.blocked) {
    if (window.blocked.indexOf(data.sid) >= 0) { return; }
  }
  const showNSFW = document.getElementById('pagefoot-nsfw').getAttribute('data-value') == 'True';
  if (data.nsfw && !showNSFW) {
    return;
  }
  const showNSFWBlur = document.getElementById('pagefoot-nsfw-blur').getAttribute('data-value') == 'True';
  const nsfwClass = (data.nsfw && showNSFWBlur) ? 'nsfw-blur' : '';
  const nsfwElem = data.nsfw ? ('<span class="nsfw smaller" title="' + _('Not safe for work') + '">' + _('NSFW') + '</span>') : '';
  const recentActivity = document.getElementById('activity_list_sidebar');
  const title = data.title.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  if (recentActivity && data.show_sidebar) {
    const elem = document.createElement('li');
    elem.setAttribute('data-pid', data.pid);
    elem.innerHTML = _('%1:<br>%2 %3 in %4',
      '<a href="/u/' + data.user + '">' + data.user + '</a> <span class="postedspan">' + _('posted') + '</span>',
      '<a class="' + nsfwClass + '" href="' + data.post_url + '">' + title + '</a>' + nsfwElem,
      '<div class="sidelocale"><time-ago datetime="' + new Date().toISOString() + '"></time-ago>',
      '<a href="' + data.sub_url + '">' + decodeURIComponent(data.sub_url) + '</a></div>');
    elem.classList.add('new-item');

    // Check if there are already 5 items
    if (recentActivity.children.length >= 5) {
      // Move the last element to the removedItems queue
      removedItems.push(recentActivity.lastElementChild);
      recentActivity.removeChild(recentActivity.lastElementChild); // Remove the last item
    }

    const header = recentActivity.querySelector('h4');
    if (header) {
      header.after(elem); // Inserts the new element after the <h4>
    } else {
      recentActivity.prepend(elem); // Fallback if no <h4> exists
    }

    // Remove 'new-item' class after animation
    setTimeout(() => elem.classList.remove('new-item'), 2500);
  }
//  who knows what this does?
//
//  const recentActivitySidebar = document.getElementById('activity_list_sidebar');
//  if (recentActivitySidebar && data.show_sidebar) {
//    const elem = document.createElement('li');
//    let html = _('%1 posted: %2',
//      '<a href="/u/' + data.user + '">' + data.user + '</a>',
//      '<a class="title ' + nsfwClass + '" href="' + data.post_url + '">' + title + '</a>' + nsfwElem);
//    html += '<div class="sidelocale">' +
//      _("%1 in %2", '<time-ago datetime="' + new Date().toISOString() + '"></time-ago>', '<a href="' + data.sub_url + '">' + data.sub_url + '</a>') +
//      '</div>';
//    elem.innerHTML = html;
//    recentActivitySidebar.prepend(elem);
//    recentActivitySidebar.removeChild(recentActivitySidebar.lastChild);
//  }
//  socket.emit('subscribe', { target: data.pid })
//  const ndata = document.createElement("div");
//  ndata.innerHTML = data.html;
//  const x = document.getElementsByClassName('alldaposts')[0];
//
//  while (ndata.firstChild) {
//    const k = x.insertBefore(ndata.firstChild, x.children[0]);
//    if (window.expandall && k.getElementsByClassName) {
//      const q = k.getElementsByClassName('expando-btn')[0];
//      if (q && q.getAttribute('data-icon') == "image") {
//        q.click()
//      }
//    }
//
//  }
//
//  const blurredElems = document.querySelectorAll('.placeholder-nsfw-blur');
//  for (var i = 0; i < blurredElems.length; i++) {
//    blurredElems[i].classList.remove('placeholder-nsfw-blur');
//    if (showNSFWBlur) {
//      blurredElems[i].classList.add('nsfw-blur');
//    }
//  }
//
//  loadLazy();
//  subscribeDeferred();
//  icon.rendericons();
})

socket.on('threadscore', function (data) {
  console.log('article#' + data.pid + ' .count')
  document.querySelector('div[pid="' + data.pid + '"] .score').innerHTML = data.score;
})

/* socket.on('threadcomments', function(data){
  console.log('article#' + data.pid + ' .ccount')
  document.querySelector('div[pid="' + data.pid + '"] .comments').innerHTML = _('comments (%1)', data.comments);
})
*/

socket.on('threadtitle', function (data) {
  document.querySelector('div[pid="' + data.pid + '"] .title').innerHTML = data.title;
});

socket.on('yourvote', function (data) {
  var th = document.querySelector('div.post[pid="' + data.pid + '"] .votebuttons')
  if (th) {
    if (data.status == -1) {
      th.querySelector('.upvote').classList.remove('upvoted');
      th.querySelector('.downvote').classList.add('downvoted');
    } else if (data.status == 1) {
      th.querySelector('.upvote').classList.add('upvoted');
      th.querySelector('.downvote').classList.remove('downvoted');
    } else {
      th.querySelector('.upvote').classList.remove('upvoted');
      th.querySelector('.downvote').classList.remove('downvoted');
    }
    th.querySelector('.score').innerHTML = data.score;
  }
})

u.ready(function () {
  socket.on('connect', function () {
    if (document.getElementById('chpop') && window.chat == true) {
      socket.emit('subscribe', { target: 'chat' });
    }
  });
  socket.on('connect', function () {
    window.sio = true;
    if (window.nposts) {
      socket.emit('subscribe', { target: window.nposts });
    }
    if (document.getElementById('activity_list')) {
      socket.emit('subscribe', { target: '/all/new' });
    }
    u.each('div.post', function (t) {
      socket.emit('subscribe', { target: parseInt(t.getAttribute('pid')) });
    })
  });
})

u.sub('#chsend', 'keydown', function (e) {
  if (document.getElementById('matrix-chat')) return
  if (e.keyCode == 13) {
    socket.emit('msg', { msg: this.value })
    this.value = '';
    var x = document.getElementById('chcont');
    x.scrollTop = x.scrollHeight
  }
})

var ircStylize = require("irc-style-parser");

socket.on('rmannouncement', function () {
  if (window.oindex) {
    document.getElementById('announcement-post').outerHTML = '';
  }
})

// Track the timestamp of when the socket connection was established
let connectionTimestamp = Date.now() / 1000;

socket.on('msg', function (data) {
  if (document.getElementById('matrix-chat')) return
  var cont = document.getElementById('chcont')
  if (!cont) { return; }

  var uname = document.getElementById('unameb').innerHTML.toLowerCase();
  var testString = data.msg;
  var reg = /(^|\s)(@|\/u\/)([a-zA-Z0-9_-]{3,})(\s|\'|\.|,|$)/g;
  var matches = [];
  var match;

  while ((match = reg.exec(testString)) !== null) {
    matches.push(match[3]);
  }

  var reg2 = /\u0001ACTION (.+)\u0001/
  var m = data.msg.match(reg);
  var m2 = data.msg.match(reg2);
  var xc = "";

  // Only play sound if:
  // 1. Message timestamp is newer than when we connected (it's a new message)
  // 2. The message mentions the current user
  // 3. The message isn't from the current user
  if (data.time >= connectionTimestamp &&
      matches.length > 0 &&
      matches.some(username => username.toLowerCase() === uname) &&
      data.user.toLowerCase() !== uname) {
    xc = "msg-hl";
    let audio = new Audio('/static/ceknipop.mp3');
    audio.volume = 0.7;
    audio.play().catch(err => {});
  } else if (matches.length > 0 && matches.some(username => username.toLowerCase() === uname)) {
    xc = "msg-hl";
  }
  if (m2) {
    data.msg = data.user + ' ' + m2[1];
    data.user = "*";
    xc = xc + " msg-ac";
  }

  function addZero(i) {
    if (i < 10) { i = "0" + i }
    return i;
  }
  var d = new Date(data.time * 1000);
  var year = d.getFullYear();
  var month = String(d.getMonth() + 1).padStart(2, '0');
  var day = String(d.getDate()).padStart(2, '0');
  var hours = String(d.getHours()).padStart(2, '0');
  var minutes = String(d.getMinutes()).padStart(2, '0');
  var seconds = String(d.getSeconds()).padStart(2, '0');
  cont.innerHTML = cont.innerHTML + '<div class="msg ' + xc + '"><span class="msgtime">(' + day + '/' + month + '/' + year + '&nbsp;' + hours + ':' + minutes + ':' + seconds + ') </span><span class="msguser">' + data.user + '&gt;</span><span class="damsg">' + anchorme(ircStylize(data.msg.replace(/  /g, '&#32;&nbsp;')), { emails: false, files: false, attributes: [{ name: "target", value: "blank" }] }).replace(reg, "$1<a href='/u/$3'>$2$3</a>$4") + '</span></div>';
  var k = document.getElementsByClassName('msg')
  if (k.length > 3) {
    if (u.isScrolledIntoView(k[k.length - 2])) {
      k[k.length - 2].scrollIntoView();
    }
  }
})

socket.on('announcement', function (data) {
  if (window.oindex) {
    var elm = document.createElement('div');
    elm.id = "announcement-post";
    elm.innerHTML = data.cont;
    document.getElementById('container').insertAdjacentElement('afterbegin', elm);
    icon.rendericons();
  }
})

export default socket;
