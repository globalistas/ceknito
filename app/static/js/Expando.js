import u from './Util'
import icon from './Icon';

// Lite YouTube Embed - Optimized from Paul Irish's version
class LiteYTEmbed {
  constructor(element) {
    this.element = element;
    this.videoId = element.getAttribute('data-videoid');
    this.t = element.getAttribute('data-t') || '';
    this.createInnerHTML();
    this.element.addEventListener('click', () => this.addIframe());
  }

  // Create the initial placeholder elements
  createInnerHTML() {
    const container = document.createElement('div');
    container.classList.add('lty-placeholder');

    // Generate thumbnail URL from video ID - use direct template
    container.style.backgroundImage = `url('https://i.ytimg.com/vi/${this.videoId}/hqdefault.jpg')`;

    const playBtnEl = document.createElement('div');
    playBtnEl.classList.add('lty-playbtn');
    // Use a simple play icon
    playBtnEl.innerHTML = '<svg width="68" height="48" viewBox="0 0 68 48"><path class="lty-playbtn-svg" d="M66.52,7.74c-0.78-2.93-2.49-5.41-5.42-6.19C55.79,.13,34,0,34,0S12.21,.13,6.9,1.55 C3.97,2.33,2.27,4.81,1.48,7.74C0.06,13.05,0,24,0,24s0.06,10.95,1.48,16.26c0.78,2.93,2.49,5.41,5.42,6.19 C12.21,47.87,34,48,34,48s21.79-0.13,27.1-1.55c2.93-0.78,4.64-3.26,5.42-6.19C67.94,34.95,68,24,68,24S67.94,13.05,66.52,7.74z" fill="#f00"></path><path d="M 45,24 27,14 27,34" fill="#fff"></path></svg>';

    container.appendChild(playBtnEl);
    this.element.appendChild(container);
  }

  // When clicked, replace the placeholder with the actual iframe
  addIframe() {
    const iframeEl = document.createElement('iframe');
    iframeEl.setAttribute('frameborder', '0');
    iframeEl.setAttribute('allowfullscreen', '1');
    iframeEl.setAttribute('allow', 'accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture');

    // Handle timestamp if present
    const params = this.t ? `?autoplay=1&rel=0&start=${this.t}` : '?autoplay=1&rel=0';
    iframeEl.setAttribute('src', `https://www.youtube-nocookie.com/embed/${this.videoId}${params}`);

    // Use textContent = '' for faster DOM clearing
    this.element.textContent = '';
    this.element.appendChild(iframeEl);
    this.element.classList.add('lty-activated');
  }
}

// Add styles once on page load using a single style element
function addLiteYTStyles() {
  if (document.getElementById('lite-yt-styles')) return;

  const style = document.createElement('style');
  style.id = 'lite-yt-styles';
  style.textContent = `
    .lite-youtube {
        background-color: #000;
        position: absolute;
        height: 100%;
        max-width: 100%;
        background-position: center center;
        background-size: cover;
        cursor: pointer;
        width: 640px;
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

// Cache regex patterns for better performance
const URL_REGEX = {
  YOUTUBE: /^.*(youtu\.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=|hooktube.com\/(watch\?v=)?)([^#\&\?]*).*/,
  VIMEO: /https?:\/\/(?:www\.)?vimeo.com\/(?:channels\/(?:\w+\/)?|groups\/([^\/]*)\/videos\/|album\/(\d+)\/video\/|)(\d+)(?:$|\/|\?)/,
  VINE: /^http(?:s?):\/\/(?:www\.)?vine\.co\/v\/([a-zA-Z0-9]{1,13})$/,
  STREAMABLE: /^http(?:s?):\/\/(?:www\.)?streamable\.com\/([a-zA-Z0-9]{1,13})$/,
  GFYCAT: /^https?:\/\/gfycat\.com\/(?:gifs\/detail\/?)?([a-zA-Z0-9/-]{1,60})$/,
  STREAMJA: /^https?:\/\/streamja\.com\/([a-zA-Z0-9]{1,20})$/,
  IMGUR: /^http(?:s?):\/\/(i\.)?imgur\.com\/(.*?)(?:\/.gifv|$)/,
  BITCHUTE: /^https?:\/\/(?:www\.)?bitchute\.com\/video\/([a-zA-Z0-9]+)/,
  IMAGE: /\.(png|jpg|gif|tiff|svg|bmp|jpeg)$/i,
  VIDEO: /\.(mp4|webm)$/i
};

// Extract IDs from URLs - use cached regex patterns
function extractID(url, type) {
  if (!url) return null;

  let match;
  switch (type) {
    case 'youtube':
      match = url.match(URL_REGEX.YOUTUBE);
      return (match && match[3].length === 11) ? match[3] : null;

    case 'vimeo':
      match = url.match(URL_REGEX.VIMEO);
      return match ? match[3] : null;

    case 'vine':
      match = url.match(URL_REGEX.VINE);
      return match ? match[1] : null;

    case 'streamable':
      match = url.match(URL_REGEX.STREAMABLE);
      return match ? match[1] : null;

    case 'gfycat':
      match = url.match(URL_REGEX.GFYCAT);
      return match ? match[1].split("-", 1)[0] : null;

    case 'streamja':
      match = url.match(URL_REGEX.STREAMJA);
      return match ? match[1] : null;

    case 'imgur':
      match = url.match(URL_REGEX.IMGUR);
      return match ? match[2].replace(/.gifv/,'') : null;

    case 'bitchute':
      match = url.match(URL_REGEX.BITCHUTE);
      return match ? match[1] : null;
  }
  return null;
}

// Get hostname from URL - optimized with URL API
function getHostname(url) {
  if (!url || url.charAt(0) === "/") return null;
  try {
    return new URL(url).hostname;
  } catch (e) {
    // Fallback to regex for malformed URLs
    const matches = url.match(/^https?\:\/\/([^\/?#]+)(?:[\/?#]|$)/i);
    return matches ? matches[1] : null;
  }
}

// Get parameter from URL - use URLSearchParams when possible
function getParameterByName(name, url) {
  try {
    return new URLSearchParams(new URL(url).search).get(name);
  } catch (e) {
    // Fallback to regex
    name = name.replace(/([[\]\\])/g, "\\$&");
    const regex = new RegExp("[?&]" + name + "(=([^&#]*)|&|#|$)");
    const results = regex.exec(url);
    if (!results) return null;
    if (!results[2]) return '';
    return decodeURIComponent(results[2].replace(/\+/g, " "));
  }
}

// Function to replace YouTube embeds - simplified with better variable names
function replaceYoutubeWithLiteYT(link, expandoElement) {
  const youtubeId = extractID(link, 'youtube');
  if (!youtubeId) return false;

  const timestamp = getParameterByName('t', link);

  // Create container with proper structure
  const container = document.createElement('div');
  container.className = 'expando-wrapper';
  container.style.height = 'auto';
  container.style.willChange = 'height';

  // Create lite YouTube element
  const liteYT = document.createElement('div');
  liteYT.className = 'lite-youtube';
  liteYT.setAttribute('data-videoid', youtubeId);
  if (timestamp) liteYT.setAttribute('data-t', timestamp);

  // Create and add resize handle
  const resizeHandle = document.createElement('div');
  resizeHandle.className = 'resize-handle';
  resizeHandle.innerHTML = '<div class="i-icon" data-icon="resizeArrow"></div>';

  // Append elements
  container.appendChild(liteYT);
  container.appendChild(resizeHandle);

  // Add to expando
  const expandoText = expandoElement.querySelector('.expandotxt');
  expandoText.appendChild(container);

  // Initialize the lite YT embed
  new LiteYTEmbed(liteYT);

  // Set up resizer
  resizer(liteYT, resizeHandle, expandoText);

  return true;
}

// Close expando function - refactored for clarity
function closeExpando(pid) {
  const expando = document.querySelector('div.expando-master[pid="' + pid + '"]');
  if (!expando) return;

  expando.remove();

  const expandoBtn = document.querySelector(`div.expando[data-pid="${pid}"] .expando-btn`);
  if (expandoBtn) {
    const iconContainer = expandoBtn.closest('.icon');
    const origIcon = iconContainer.dataset.origIcon || 'expand';
    iconContainer.dataset.icon = origIcon;
    expandoBtn.innerHTML = icon[origIcon];
  }
}

// Video expando function - refactored for better structure
function videoExpando(link, expando) {
  // Create video element
  const vid = document.createElement("video");
  vid.src = link;
  vid.preload = 'auto';
  vid.controls = true;
  vid.style.width = "640px";
  vid.style.height = "360px";

  // Create source element
  const source = document.createElement("source");
  source.src = link;
  vid.appendChild(source);

  // Create resize handle
  const handle = document.createElement('div');
  handle.className = 'resize-handle';
  handle.innerHTML = '<div class="i-icon" data-icon="resizeArrow"></div>';

  // Create wrapper and append elements
  const wrapper = document.createElement('div');
  wrapper.className = 'expando-wrapper';
  wrapper.appendChild(vid);
  wrapper.appendChild(handle);

  // Add to expando
  const expandoText = expando.querySelector('.expandotxt');
  expandoText.appendChild(wrapper);

  // Set up resizer
  resizer(vid, handle, expandoText);
}

// Escape HTML characters - simplified version
const characterEscape = (str) =>
  str.replace(/[&<>'"]/g, char => `&#${char.charCodeAt(0)};`);

// Main click handler for expandos - optimized with early returns and clearer code structure
u.addEventForChild(document, 'click', '.expando', function(e, ematch) {
  const element = ematch;
  const link = element.getAttribute('data-link');
  const pid = element.getAttribute('data-pid');

  // Check if expando is already open
  if (document.querySelector(`div.expando-master[pid="${pid}"]`)) {
    return closeExpando(pid);
  }

  // Create expando container
  const expando = document.createElement('div');
  expando.setAttribute('pid', pid);
  expando.classList.add('expando-master');
  expando.innerHTML = '<div class="expandotxt"></div>';

  // Find the appropriate container
  const postElement = document.querySelector(`div.post[pid="${pid}"]`);
  if (!postElement) return;

  const isMobile = window.innerWidth <= 480;
  const isTextPost = link === 'None';
  const isYtPost = link && link.includes('youtube.com');

  let targetContainer;
  if ((isMobile && isTextPost) || (isMobile && isYtPost)) {
    targetContainer = postElement;
  } else if (postElement.closest('.postbar')) {
    targetContainer = postElement.closest('.postbar');
  } else {
    targetContainer = postElement.querySelector('.pbody');
  }

  // Handle text posts
  if (isTextPost) {
    u.get(`/do/get_txtpost/${pid}`, function(data) {
      if (data.status === 'ok') {
        const contentElement = document.createElement('div');

        if (data.content.length > 500) {
          contentElement.classList.add('fade-out-content');
          contentElement.innerHTML = data.content.substring(0, 1000);
        } else {
          contentElement.innerHTML = data.content;
        }

        expando.querySelector('.expandotxt').appendChild(contentElement);
      }
    });
  } else {
    // Handle media posts
    const domain = getHostname(link);

    if (domain === 'youtube.com' || domain === 'www.youtube.com' || domain === 'youtu.be') {
      if (!replaceYoutubeWithLiteYT(link, expando)) {
        // Fallback YouTube embed if lite version fails
        const expandoText = expando.querySelector('.expandotxt');
        let extra = '?';

        const listParam = getParameterByName('list', link);
        if (listParam) {
          extra += `list=${listParam}&`;
        }

        const timeParam = getParameterByName('t', link);
        if (timeParam) {
          let start;
          const timeRegex = /(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?/;
          const match = timeRegex.exec(timeParam);

          if (match && !match[1] && !match[2] && !match[3] && !match[4]) {
            start = timeParam.replace(/\D/g, '');
          } else if (match) {
            const days = parseInt(match[1] || 0);
            const hours = parseInt(match[2] || 0);
            const minutes = parseInt(match[3] || 0);
            const seconds = parseInt(match[4] || 0);
            start = days * 86400 + hours * 3600 + minutes * 60 + seconds;
          } else {
            start = timeParam.replace(/\D/g, '');
          }

          extra += `start=${start}`;
        }

        const youtubeEmbed = `
          <div class="expando-wrapper">
            <iframe style="height: 360px; width: 640px;"
              src="https://www.youtube-nocookie.com/embed/${extractID(link, 'youtube')}${extra}"
              allowfullscreen=""></iframe>
            <div class="resize-handle"><div class="i-icon" data-icon="resizeArrow"></div></div>
          </div>`;

        expandoText.innerHTML = youtubeEmbed;
        resizer(expandoText.querySelector('iframe'), expandoText.querySelector('.resize-handle'), expandoText);
      }
    } else if (domain === 'gfycat.com') {
      expando.querySelector('.expandotxt').innerHTML = `
        <div class="iframewrapper">
          <iframe width="100%" src="https://gfycat.com/ifr/${extractID(link, 'gfycat')}"></iframe>
        </div>`;
    } else if (domain === 'vimeo.com') {
      expando.querySelector('.expandotxt').innerHTML = `
        <div class="iframewrapper">
          <iframe width="100%" src="https://player.vimeo.com/video/${extractID(link, 'vimeo')}"></iframe>
        </div>`;
    } else if (domain === 'streamja.com') {
      expando.querySelector('.expandotxt').innerHTML = `
        <div class="iframewrapper">
          <iframe width="100%" src="https://streamja.com/embed/${extractID(link, 'streamja')}"></iframe>
        </div>`;
    } else if (domain === 'streamable.com') {
      expando.querySelector('.expandotxt').innerHTML = `
        <div class="iframewrapper">
          <iframe width="100%" src="https://streamable.com/o/${extractID(link, 'streamable')}"></iframe>
        </div>`;
    } else if (domain === 'vine.co') {
      expando.querySelector('.expandotxt').innerHTML = `
        <div class="iframewrapper">
          <iframe width="100%" src="https://vine.co/v/${extractID(link, 'vine')}/embed/simple"></iframe>
        </div>`;
    } else if (domain === 'www.bitchute.com') {
      const bitchuteEmbed = `
        <div class="expando-wrapper" style="height: auto; will-change: height;">
          <iframe style="height: 360px; width: 640px;"
            src="https://www.bitchute.com/embed/${extractID(link, 'bitchute')}"></iframe>
          <div class="resize-handle"><div class="i-icon" data-icon="resizeArrow"></div></div>
        </div>`;

      expando.querySelector('.expandotxt').innerHTML = characterEscape(bitchuteEmbed);
      resizer(expando.querySelector('.expandotxt iframe'), expando.querySelector('.expandotxt .resize-handle'), expando.querySelector('.expandotxt'));
    } else if (URL_REGEX.IMAGE.test(link)) {
      const img = document.createElement("img");
      img.src = link;
      img.draggable = false;
      confResizer(img, expando.querySelector('.expandotxt'));
      expando.querySelector('.expandotxt').appendChild(img);
    } else if (URL_REGEX.VIDEO.test(link)) {
      videoExpando(link, expando);
    } else if (domain === 'i.imgur.com' && /\.gifv$/i.test(link)) {
      videoExpando(`https://i.imgur.com/${extractID(link, 'imgur')}.mp4`, expando);
    }
  }

  // Update button icon
  element.querySelector('.expando-btn').innerHTML = icon.close;

  // Insert the expando
  const fourthChild = targetContainer.children[3];
  if (fourthChild) {
    targetContainer.insertBefore(expando, fourthChild);
  } else {
    targetContainer.appendChild(expando);
  }

  // Handle NSFW blur
  const parentDiv = expando.parentElement;
  const showNSFWBlur = parentDiv.querySelector('.title.nsfw-blur') !== null;
  if (showNSFWBlur) {
    expando.classList.add('nsfw-blur');
  }

  icon.rendericons();
});

// Resizer function - optimized with cleaner variable names and structure
function resizer(element, handle, boundary) {
  if (!handle) return;

  let lastX, lastY, left, top, startWidth, startHeight, startDiag;
  let active = false;

  handle.addEventListener('mousedown', initResize);

  function resize(e) {
    const deltaX = e.clientX - lastX;
    const deltaY = e.clientY - lastY;

    if (!(1 & e.buttons)) return stop();
    if (!deltaX && !deltaY) return;
    if (!active) activate();

    // Calculate new size based on diagonal
    const diag = Math.round(Math.hypot(
      Math.max(1, e.clientX - left),
      Math.max(1, e.clientY - top)
    ));

    const ratio = 1;
    const nWidth = diag / startDiag * startWidth;

    // Don't resize below minimum width
    if (nWidth * ratio < 100) return;

    const currentWidth = element.getBoundingClientRect().width;
    const currentHeight = element.getBoundingClientRect().height;

    // Don't resize beyond parent bounds
    if (nWidth * ratio >= boundary.clientWidth * 0.95 && (nWidth * ratio) > currentWidth) return;

    // Apply new dimensions
    element.style.height = ((currentHeight / currentWidth) * (nWidth * ratio)).toFixed(2) + 'px';
    element.style.width = nWidth * ratio + 'px';

    lastX = e.clientX;
    lastY = e.clientY;
  }

  function activate() {
    active = true;
    const rect = element.getBoundingClientRect();
    left = rect.left;
    top = rect.top;
    startWidth = rect.width;
    startHeight = rect.height;

    startDiag = Math.round(Math.hypot(
      Math.max(1, lastX - left),
      Math.max(1, lastY - top)
    ));
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

// Configure resizer for images
function confResizer(el, pnode) {
  el.addEventListener('mousedown', initResize, false);

  let startx = 0, starty = 0;

  function initResize(e) {
    startx = e.clientX;
    starty = e.clientY;
    window.addEventListener('mousemove', resize, false);
    window.addEventListener('mouseup', stopResize, false);
  }

  function resize(e) {
    // Calculate average movement
    const emvt = ((e.clientX - startx) + (e.clientY - starty)) / 2;
    startx = e.clientX;
    starty = e.clientY;

    // Calculate resize ratio
    const resizeRatio = (el.width + emvt) / el.width;

    // Check boundaries
    if (el.width * resizeRatio >= pnode.clientWidth * 0.95 && resizeRatio > 1) return;

    // Apply new height with aspect ratio preserved
    el.style.height = (el.height * resizeRatio) + 'px';
  }

  function stopResize() {
    window.removeEventListener('mousemove', resize, false);
    window.removeEventListener('mouseup', stopResize, false);
  }
}

// Expando icon toggling function
function updatePostExpandoIcon(iconEl, negate = false) {
  const postEl = iconEl.closest('.post');

  let isExpanded = !!postEl.querySelector('.expando-master');
  if (negate) {
    isExpanded = !isExpanded;
  }

  if (!iconEl.dataset.origIcon) {
    iconEl.dataset.origIcon = iconEl.dataset.icon;
  }

  iconEl.dataset.icon = isExpanded ? 'remove' : iconEl.dataset.origIcon;
}

// Attach event listeners for all expando buttons
document.addEventListener('DOMContentLoaded', function() {
  // Add Lite YouTube styles
  addLiteYTStyles();

  // Set up expando button event listeners
  const expandoBtns = document.querySelectorAll('.icon.expando-btn');
  expandoBtns.forEach(iconEl => {
    iconEl.addEventListener('click', function() {
      updatePostExpandoIcon(this, true);
    });
    updatePostExpandoIcon(iconEl);
  });
});

// Auto expand all expandos except those in announcement or nsfw posts
function expandAllExpandos() {
  document.querySelectorAll('.expando').forEach(expandoElement => {
    const postElement = expandoElement.closest('.post');
    const pbody = postElement.querySelector('.pbody');
    const hasAnnouncementOrNSFW = pbody && (
      pbody.querySelector('.announcementnotfornow') ||
      pbody.querySelector('.nsfw')
    );

    // Only expand if not announcement or nsfw
    if (!hasAnnouncementOrNSFW) {
      const expandoBtn = expandoElement.querySelector('.expando-btn');
      if (expandoBtn) {
        expandoBtn.click();
      }
    }

    // Add blur class to NSFW posts
    const nsfwPost = postElement.querySelector('.nsfw');
    const expandotxt = postElement.querySelector('.expandotxt');
    if (nsfwPost && expandotxt) {
      expandotxt.classList.add('nsfw-blur');
    }
  });
}

// Add page load event listener
window.addEventListener('load', expandAllExpandos);

// Keyboard shortcut for toggling all expandos
window.addEventListener('keydown', function(e) {
  if (!document.getElementsByClassName('alldaposts')[0]) return;

  if (e.shiftKey && e.which === 88) { // Shift + X
    const allExpandos = document.querySelectorAll('.expando-btn');
    let anyExpanded = false;

    // Check if any are expanded
    allExpandos.forEach(btn => {
      if (btn.closest('.post').querySelector('.expando-master')) {
        anyExpanded = true;
      }
    });

    // Toggle all expandos
    allExpandos.forEach(btn => {
      if (anyExpanded) {
        // Collapse
        const pid = btn.closest('.post').getAttribute('pid');
        closeExpando(pid);
      } else {
        // Expand
        btn.click();
      }
    });

    // Force redraw of icons if collapsing
    if (anyExpanded) {
      icon.rendericons();
    }
  }
});
