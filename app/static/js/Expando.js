import u from './Util'
import icon from './Icon';

// Lite YouTube Embed
// Based on Paul Irish's lite-youtube-embed but simplified for your needs
// https://github.com/paulirish/lite-youtube-embed
class LiteYTEmbed {
  constructor(element) {
    this.videoId = element.getAttribute('data-videoid');
    this.element = element;
    this.t = element.getAttribute('data-t') || ''; // Handle timestamp
    this.createInnerHTML();
    this.setupEventListeners();
  }

  // Create the initial placeholder elements
  createInnerHTML() {
    const playBtnEl = document.createElement('div');
    playBtnEl.classList.add('lty-playbtn');

    // Use a simple play icon
    playBtnEl.innerHTML = '<svg width="68" height="48" viewBox="0 0 68 48"><path class="lty-playbtn-svg" d="M66.52,7.74c-0.78-2.93-2.49-5.41-5.42-6.19C55.79,.13,34,0,34,0S12.21,.13,6.9,1.55 C3.97,2.33,2.27,4.81,1.48,7.74C0.06,13.05,0,24,0,24s0.06,10.95,1.48,16.26c0.78,2.93,2.49,5.41,5.42,6.19 C12.21,47.87,34,48,34,48s21.79-0.13,27.1-1.55c2.93-0.78,4.64-3.26,5.42-6.19C67.94,34.95,68,24,68,24S67.94,13.05,66.52,7.74z" fill="#f00"></path><path d="M 45,24 27,14 27,34" fill="#fff"></path></svg>';

    const container = document.createElement('div');
    container.classList.add('lty-placeholder');

    // Generate thumbnail URL from video ID
    const posterUrl = `https://i.ytimg.com/vi/${this.videoId}/hqdefault.jpg`;
    container.style.backgroundImage = `url('${posterUrl}')`;

    this.element.appendChild(container);
    container.appendChild(playBtnEl);
  }

  // Set up event listeners for the placeholder
  setupEventListeners() {
    this.element.addEventListener('click', () => {
      this.addIframe();
    });
  }

  // When clicked, replace the placeholder with the actual iframe
  addIframe() {
    // Create the iframe element
    const iframeEl = document.createElement('iframe');
    iframeEl.setAttribute('frameborder', '0');
    iframeEl.setAttribute('allowfullscreen', '1');
    iframeEl.setAttribute('allow', 'accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture');

    // Handle timestamp if present
    let params = '?autoplay=1&rel=0';
    if (this.t) {
      params += '&start=' + this.t;
    }

    // Use nocookie domain
    iframeEl.setAttribute('src', `https://www.youtube-nocookie.com/embed/${this.videoId}${params}`);

    // Replace the placeholder with the iframe
    this.element.textContent = '';
    this.element.appendChild(iframeEl);

    // Add loaded class for styling
    this.element.classList.add('lty-activated');
  }
}

// Add CSS for the lite YouTube embed component
function addLiteYTStyles() {
  const style = document.createElement('style');
  style.textContent = `
    .lite-youtube {
        background-color: #000; */
        position: absolute;
        /* display: block; */
        /* top: 0; */
        /* left: 0; */
        /* width: 100% !important; */
        height: 100%;
        max-width: 100%;
        /* contain: content; */
        background-position: center center;
        background-size: cover;
        cursor: pointer;
        width: 640px;
        /* height: 360px; */
    }

    .lite-youtube .lty-placeholder {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
      background-size: cover;
      background-position: center;
      display: flex;
      justify-content: center;
      align-items: center;
    }

    .lite-youtube .lty-playbtn {
      width: 68px;
      height: 48px;
      opacity: 0.8;
      transition: all 0.2s cubic-bezier(0, 0, 0.2, 1);
    }

    .lite-youtube:hover .lty-playbtn {
      opacity: 1;
      transform: scale(1.1);
    }

    .lite-youtube.lty-activated {
      cursor: unset;
    }

    .lite-youtube.lty-activated .lty-playbtn,
    .lite-youtube.lty-activated .lty-placeholder {
      display: none;
    }

    .lite-youtube iframe {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
    }
  `;
  document.head.appendChild(style);
}

// Function to replace YouTube embeds in the expando
function replaceYoutubeWithLiteYT(link, expandoElement) {
  const youtubeId = youtubeID(link);
  const timestamp = getParameterByName('t', link);

  if (youtubeId) {
    const container = document.createElement('div');
    container.classList.add('expando-wrapper');
    container.style.height = 'auto';
    container.style.willChange = 'height';

    const liteYT = document.createElement('div');
    liteYT.classList.add('lite-youtube');
    liteYT.setAttribute('data-videoid', youtubeId);

    if (timestamp) {
      liteYT.setAttribute('data-t', timestamp);
    }

    container.appendChild(liteYT);

    // Add resize handle
    const resizeHandle = document.createElement('div');
    resizeHandle.classList.add('resize-handle');
    resizeHandle.innerHTML = '<div class="i-icon" data-icon="resizeArrow"></div>';
    container.appendChild(resizeHandle);

    expandoElement.querySelector('.expandotxt').appendChild(container);

    // Initialize the lite YT embed
    new LiteYTEmbed(liteYT);

    // Set up resizer
    resizer(liteYT, resizeHandle, expandoElement.querySelector('.expandotxt'));

    return true;
  }

  return false;
}

// Add the styles when the page loads
document.addEventListener('DOMContentLoaded', addLiteYTStyles);


function get_hostname(url) {
  if(!url || url.charAt(0) == "/"){return;}
  var matches = url.match(/^https?\:\/\/([^\/?#]+)(?:[\/?#]|$)/i);
  return matches[1];
}

function vimeoID(url) {
  var match = url.match(/https?:\/\/(?:www\.)?vimeo.com\/(?:channels\/(?:\w+\/)?|groups\/([^\/]*)\/videos\/|album\/(\d+)\/video\/|)(\d+)(?:$|\/|\?)/);
  if (match){
    return match[3];
	}
}
function vineID(url) {
  var match = url.match(/^http(?:s?):\/\/(?:www\.)?vine\.co\/v\/([a-zA-Z0-9]{1,13})$/);
  if (match){
    return match[1];
	}
}
function streamableID(url) {
  var match = url.match(/^http(?:s?):\/\/(?:www\.)?streamable\.com\/([a-zA-Z0-9]{1,13})$/);
  if (match){
    return match[1];
	}
}
function gfycatID(url) {
  var match = url.match(/^https?:\/\/gfycat\.com\/(?:gifs\/detail\/?)?([a-zA-Z0-9/-]{1,60})$/);
  if (match){
    var gfy = match[1].split("-", 1);
    return gfy;
	}
}
function streamjaID(url) {
  var match = url.match(/^https?:\/\/streamja\.com\/([a-zA-Z0-9]{1,20})$/);
  if (match){
    return match[1];
	}
}
function youtubeID(url) {
  var match =  url.match(/^.*(youtu\.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=|hooktube.com\/(watch\?v=)?)([^#\&\?]*).*/);
  if (match && match[3].length == 11) {
    return match[3];
  }
}

function getParameterByName(name, url) {
  name = name.replace(/[\[\]]/g, "\\$&");
  var regex = new RegExp("[?&]" + name + "(=([^&#]*)|&|#|$)"),
      results = regex.exec(url);
  if (!results) return null;
  if (!results[2]) return '';
  return decodeURIComponent(results[2].replace(/\+/g, " "));
}


function imgurID(url) {
  var match = url.match(/^http(?:s?):\/\/(i\.)?imgur\.com\/(.*?)(?:\/.gifv|$)/);
  if (match){
    return match[2].replace(/.gifv/,'');
	}
}

function bitchuteID(url) {
  var match = url.match(/^https?:\/\/(?:www\.)?bitchute\.com\/video\/([a-zA-Z0-9]+)/);
  if (match){
    return match[1];
  }
}

function close_expando(pid) {
  var k = document.querySelector('div.expando-master[pid="'+pid+'"]');
  if (k) {
    k.parentNode.removeChild(k);
    var expandoBtn = document.querySelector('div.expando[data-pid="'+pid+'"] .expando-btn');
    if (expandoBtn) {
      // Get the original icon from the data attribute
      const origIcon = expandoBtn.closest('.icon').dataset.origIcon || 'expand';
      // Set the data-icon back to the original icon
      expandoBtn.closest('.icon').dataset.icon = origIcon;
      // Update the HTML content
      expandoBtn.innerHTML = icon[origIcon];
    }
  }
}

function video_expando(link, expando) {
  const vid = document.createElement( "video" );
  vid.src = link;
  vid.preload = 'auto';
  vid.autoplay = false;
  vid.loop = false;
  vid.controls = true;
  vid.innerHTML = document.createElement("source").src = link;
  vid.style.width = "640px";
  vid.style.height = "360px";

  const handle = document.createElement('div');
  handle.className = 'resize-handle';
  handle.innerHTML = '<div class="i-icon" data-icon="resizeArrow"</div>';

  const wrapper = document.createElement('div');
  wrapper.className = 'expando-wrapper';
  wrapper.appendChild(vid)
  wrapper.appendChild(handle)
  //wrapper.innerHTML = vid.outerHTML + handle.outerHTML;
  expando.querySelector('.expandotxt').appendChild(wrapper);

  resizer(expando.querySelector('.expandotxt video'), expando.querySelector('.expandotxt .resize-handle'), expando.querySelector('.expandotxt'))
}

const characterEscape = (character) => `&#${character.charCodeAt(0)};`;

u.addEventForChild(document, 'click', '.expando', function(e, ematch){
    var th=ematch;
    var link=th.getAttribute('data-link');
    var pid=th.getAttribute('data-pid');
    if(document.querySelector('div.expando-master[pid="'+pid+'"]')){
      return close_expando(pid);
    }
    var expando = document.createElement('div');
    expando.setAttribute('pid', pid);
    expando.classList.add('expando-master');
    expando.innerHTML = '<div class="expandotxt"></div>';

    // New code to handle both cases - find the appropriate container
    var postElement = document.querySelector('div.post[pid="'+pid+'"]');
    var targetContainer;

    // Check if this is a text or youtube post on mobile device
    var isMobile = window.innerWidth <= 480;
    var isTextPost = link == 'None';
    var isYtPost = link.includes('youtube.com');

    if ((isMobile && isTextPost) || (isMobile && isYtPost)) {
      // Special case: for text posts on mobile, append to the post container directly
      targetContainer = postElement;
    } else if (postElement.closest('.postbar')) {
      // Check if we're in a postbar context
      targetContainer = postElement.closest('.postbar');
    } else {
      // Otherwise use the pbody container
      targetContainer = postElement.querySelector('.pbody');
    }

    if(isTextPost){
    u.get('/do/get_txtpost/' + pid, function(data){
      if(data.status == 'ok'){
        // Create content element
        const contentElement = document.createElement('div');

        // Only add fade-out-content class if truncation is needed
        if(data.content.length > 500) {
          contentElement.classList.add('fade-out-content');
          contentElement.innerHTML = data.content.substring(0, 1000);
        } else {
          contentElement.innerHTML = data.content;
        }

        expando.querySelector('.expandotxt').appendChild(contentElement);
      }
    })
    }else{
      var domain = get_hostname(link);
      if((domain == 'youtube.com') || (domain == 'www.youtube.com') || (domain == 'youtu.be')){

      if (replaceYoutubeWithLiteYT(link, expando)) {
        // Successfully replaced
      } else {

        var extra = '?';
        if(getParameterByName('list', link)){
          extra += 'list=' + getParameterByName('list', link) + '&';
        }
        if(getParameterByName('t', link)){
          var time_regex = /(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?/;
          var start = getParameterByName('t', link);
          var m = null;
          if ((m = time_regex.exec(start)) !== null) {
            if (!m[1] && !m[2] && !m[3] && !m[4]) {
              start = start.replace(/\D/g,'');
              extra += 'start=' + start;
            }else{
              var i;
              for (i = 0; i < m.length; i++) {
                m[i] = (m[i] === undefined) ? 0 : m[i];
              }
              var time = parseInt(m[1]) * 86400 + parseInt(m[2]) * 3600 + parseInt(m[3]) * 60 + parseInt(m[4]);
              extra += 'start=' + time;
            }
          }else{ // not d h m s letters in t
            start = start.replace(/\D/g,'');
            extra += 'start=' + start;
          }
        }

        expando.querySelector('.expandotxt').innerHTML = '<div class="expando-wrapper"><iframe style="height: 360px; width: 640px;" src="https://www.youtube-nocookie.com/embed/' + youtubeID(link) + extra + '" allowfullscreen=""></iframe><div class="resize-handle"><div class="i-icon" data-icon="resizeArrow"</div></div>';
        resizer(expando.querySelector('.expandotxt iframe'), expando.querySelector('.expandotxt .resize-handle'), expando.querySelector('.expandotxt'))
      }}else if(domain == 'gfycat.com'){
        expando.querySelector('.expandotxt').innerHTML = '<div class="iframewrapper"><iframe width="100%" src="https://gfycat.com/ifr/' + gfycatID(link) +'"></iframe></div>';
      }else if(domain == 'vimeo.com'){
        expando.querySelector('.expandotxt').innerHTML = '<div class="iframewrapper"><iframe width="100%" src="https://player.vimeo.com/video/' + vimeoID(link) +'"></iframe></div>';
      }else if(domain == 'streamja.com'){
        expando.querySelector('.expandotxt').innerHTML = '<div class="iframewrapper"><iframe width="100%" src="https://streamja.com/embed/' + streamjaID(link) +'"></iframe></div>';
      }else if(domain == 'streamable.com'){
        expando.querySelector('.expandotxt').innerHTML = '<div class="iframewrapper"><iframe width="100%" src="https://streamable.com/o/' + streamableID(link) +'"></iframe></div>';
      }else if(domain == 'vine.co'){
        expando.querySelector('.expandotxt').innerHTML = '<div class="iframewrapper"><iframe width="100%" src="https://vine.co/v/' + vineID(link) +'/embed/simple"></iframe></div>';
      }else if(domain == 'www.bitchute.com'){
        expando.querySelector('.expandotxt').innerHTML = characterEscape('<div class="expando-wrapper" style="height: auto; will-change: height;"><iframe style="height: 360px; width: 640px;" src="https://www.bitchute.com/embed/' + bitchuteID(link) +'"></iframe><div class="resize-handle"><div class="i-icon" data-icon="resizeArrow"</div></div>');
        resizer(expando.querySelector('.expandotxt iframe'), expando.querySelector('.expandotxt .resize-handle'), expando.querySelector('.expandotxt'))
      }else if(/\.(png|jpg|gif|tiff|svg|bmp|jpeg)$/i.test(link)) {
        const img = document.createElement("img");
        img.src = link;
        img.draggable = false;
        //img.onclick = function(){close_expando(pid);};
        confResizer(img, expando.querySelector('.expandotxt'));
        expando.querySelector('.expandotxt').appendChild(img);
      }else if (/\.(mp4|webm)$/i.test(link)) {
        video_expando(link, expando)
      }else if(domain == 'i.imgur.com' && /\.gifv$/i.test(link)){
        video_expando('https://i.imgur.com/' + imgurID(link) + '.mp4', expando)
      }
    }
    th.querySelector('.expando-btn').innerHTML = icon.close;
    const fourthChild = targetContainer.children[3];

    if (fourthChild) {
      targetContainer.insertBefore(expando, fourthChild);
    } else {
      // If there aren't enough children, append it to the end
      targetContainer.appendChild(expando);
    }

    const parentDiv = expando.parentElement;
    const showNSFWBlur = parentDiv.querySelector('.title.nsfw-blur') !== null;
       if (showNSFWBlur) {
          expando.classList.add('nsfw-blur');
        }
    icon.rendericons();
})

function resizer(element, handle, boundary) {
  if(!handle) element = handle;

  let lastX, lastY, left, top, startWidth, startHeight, startDiag;
  let active = false;

  handle.addEventListener('mousedown', initResize);

  function resize(e) {
    const deltaX = e.clientX - lastX;
    const deltaY = e.clientY - lastY;

    if(1 & ~e.buttons) return stop();
    if(!deltaX || !deltaY) return;
    if(!active) activate();

    // It has decided to move!
    const ratio = 1;

    const diag = Math.round(Math.hypot(Math.max(1, e.clientX - left), Math.max(1, e.clientY - top)))

    const nWidth = diag / startDiag * startWidth;

    if(nWidth * ratio < 100) return;

    const width = element.getBoundingClientRect().width;
    const height = element.getBoundingClientRect().height;

    if(nWidth * ratio >= boundary.clientWidth * 0.95 && (nWidth * ratio) > width) return;

    element.style.height = ((height / width) * (nWidth * ratio)).toFixed(2) + 'px';
    element.style.width = nWidth * ratio + 'px';

    lastX = e.clientX;
    lastY = e.clientY;
    //resize(element, nWidth * ratio);

  }

  function activate() {
    active = true;
    left = element.getBoundingClientRect().left;
    top = element.getBoundingClientRect().top;
    startWidth = element.getBoundingClientRect().width;
    startHeight = element.getBoundingClientRect().height;

    startDiag = Math.round(Math.hypot(Math.max(1, lastX - left), Math.max(1, lastY - top)))
  }

  function stop() {
    document.removeEventListener('mouseup', stop);
    document.removeEventListener('mousemove', resize);
    element.style.pointerEvents = 'all';
  }

  function initResize(e) {
    if (e.button !== 0) return;
    lastX = e.clientX;
    lastY = e.clientY;
    active = false;

    document.addEventListener('mouseup', stop);
    document.addEventListener('mousemove', resize);
    element.style.pointerEvents = 'none';

    e.preventDefault();
  }

}


function confResizer(el, pnode, corner) {
  if(corner) {
    const resizer = document.createElement('div');
    resizer.style.width = '10px';
    resizer.style.height = '10px';
    resizer.style.background = 'red';
    resizer.style.position = 'absolute';
    resizer.style.right = 0;
    resizer.style.bottom = 0;
    resizer.style.cursor = 'se-resize';
    //Append Child to Element
    element.appendChild(resizer);
    //box function onmousemove
    resizer.addEventListener('mousedown', initResize, false);
  }else{
    el.addEventListener('mousedown', initResize, false);
  }

  //element.appendChild(resizer);
  //box function onmousemove
  let startx = 0, starty = 0;
  //Window funtion mousemove & mouseup
  function initResize(e) {
    startx = e.clientX;
    starty = e.clientY;
    window.addEventListener('mousemove', resize, false);
    window.addEventListener('mouseup', stopResize, false);
  }

  function resize(e) {
    // Average x and y mvmt
    let emvt = (e.clientX - startx) + (e.clientY - starty);
    emvt = emvt / 2
    startx = e.clientX
    starty = e.clientY

    // Get ratio of resize so we can keep the aspect ratio
    const resizeRatio = (el.width + emvt) / el.width

    // Check if we're going out of bounds (so far the only limit is width)
    if(el.width * resizeRatio >= pnode.clientWidth * 0.95 && resizeRatio > 1) return;

    //el.style.width = (el.width * resizeRatio) + 'px';
    el.style.height = (el.height * resizeRatio) + 'px';
  }

  function stopResize(e) {
    window.removeEventListener('mousemove', resize, false);
    window.removeEventListener('mouseup', stopResize, false);
  }
}

// toggle expando icon between origin/remove states
function updatePostExpandoIcon(iconEl, negate = false) {
    const postEl = iconEl.closest('.post')

    let isExpanded = !!postEl.querySelector('.expando-master')
    if (negate) {
        isExpanded = !isExpanded
    }

    if (!iconEl.dataset.origIcon) {
        iconEl.dataset.origIcon = iconEl.dataset.icon
    }

    iconEl.dataset.icon = isExpanded ? 'remove' : iconEl.dataset.origIcon
}

for (const iconEl of document.querySelectorAll('.icon.expando-btn')) {
    iconEl.addEventListener('click', function() {
        updatePostExpandoIcon(this, true)
    })
    updatePostExpandoIcon(iconEl)
}

// Auto expand all expandos except those in announcement or nsfw posts
function expandAllExpandos() {
  // Loop through all elements with the class 'expando'
  document.querySelectorAll('.expando').forEach(expandoElement => {
    // Get the associated post
    const postElement = expandoElement.closest('.post');

    // Check if this post contains either an announcement or nsfw
    const pbody = postElement.querySelector('.pbody');
    const hasAnnouncementOrNSFW = pbody && (pbody.querySelector('.announcementnotfornow') || pbody.querySelector('.nsfw'));

    // Only expand if it's not an announcement or nsfw post
    if (!hasAnnouncementOrNSFW) {
      const expandoBtn = expandoElement.querySelector('.expando-btn');
      if (expandoBtn) {
        // Manually trigger the click event on the expando button to open it
        expandoBtn.click();
      }
    }

    // Check if the post is NSFW and add the blur class to expandotxt
    const nsfwPost = postElement.querySelector('.nsfw');
    const expandotxt = postElement.querySelector('.expandotxt');
    if (nsfwPost && expandotxt) {
      expandotxt.classList.add('nsfw-blur');
    }
  });
}
window.addEventListener('load', expandAllExpandos);



// Shift-X toggles expand/collapse all expandos
window.onkeydown = function (e) {
  if (!document.getElementsByClassName('alldaposts')[0]) {
    return;
  }

  if (e.shiftKey && e.which === 88) {
    console.log('wheee');

    const allExpandos = document.querySelectorAll('.expando-btn');
    let anyExpanded = false;

    // Check if any are expanded
    allExpandos.forEach((btn) => {
      if (btn.closest('.post').querySelector('.expando-master')) {
        anyExpanded = true;
      }
    });

    // If any are expanded, collapse them; otherwise, expand all
    allExpandos.forEach((btn) => {
      if (anyExpanded) {
        // Collapse
        const pid = btn.closest('.post').getAttribute('pid');
        close_expando(pid);
        // Force redraw of icons
        icon.rendericons();
      } else {
        // Expand
        btn.click();
      }
    });
  }
};
