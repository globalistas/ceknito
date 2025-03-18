// making the getCookie function from main.js available here
function getCookie(cname) {
  var name = cname + "=";
  var ca = document.cookie.split(';');
  for (var i = 0; i < ca.length; i++) {
    var c = ca[i];
    while (c.charAt(0) == ' ') {
      c = c.substring(1);
    }
    if (c.indexOf(name) === 0) {
      return c.substring(name.length, c.length);
    }
  }
  return "";
}

// making the setCookie function from main.js available here
function setCookie(cname, cvalue, exdays) {
  var d = new Date();
  d.setTime(d.getTime() + (exdays * 24 * 60 * 60 * 1000));
  var expires = "expires=" + d.toGMTString();
  document.cookie = cname + "=" + cvalue + "; " + expires + "; path=/";
}

function setcolor(color) {
        document.documentElement.style.setProperty('--primary-color', color);
    }

// if cookie exists, its value is primary color. if not, choose a random one from the array and set it in cookie
const primaryColors = ['#46586e', '#48506c', '#800000', "#513863", "#5a8295", "#4a384e"];
let primaryColor = getCookie("primaryColor");

if (!primaryColor) {
  primaryColor = primaryColors[Math.floor(Math.random() * primaryColors.length)];
  setCookie("primaryColor", primaryColor, 365);
}

// setcolor(primaryColor);
