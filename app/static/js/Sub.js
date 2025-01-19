import u from './Util';
import TextConfirm from  './utils/TextConfirm';
import _ from './utils/I18n';
import 'autocompleter/autocomplete.css';
import autocomplete from 'autocompleter';

u.sub('.revoke-mod2inv', 'click', function(e){
  var user=this.getAttribute('data-user');
  var nsub=this.getAttribute('data-sub');
  u.post('/do/revoke_mod2inv/'+nsub+'/'+user, {},
  function(data){
    if (data.status == "ok") {
      document.location.reload();
    }
  });
});

u.sub('.revoke-memberinv', 'click', function(e){
  var user=this.getAttribute('data-user');
  var nsub=this.getAttribute('data-sub');
  u.post('/do/revoke_memberinv/'+nsub+'/'+user, {},
  function(data){
    if (data.status == "ok") {
      document.location.reload();
    }
  });
});

u.sub('#accept-mod2-inv', 'click', function(e){
  var user=this.getAttribute('data-user');
  var nsub=this.getAttribute('data-sub');
  u.post('/do/accept_modinv/'+nsub+'/'+user, {},
  function(data){
    if (data.status == "ok") {
      document.location.reload();
    }
  });
});

u.sub('#refuse-mod2-inv', 'click', function(e){
  var user=this.getAttribute('data-user');
  var nsub=this.getAttribute('data-sub');
  u.post('/do/refuse_mod2inv/'+nsub, {},
  function(data){
    if (data.status == "ok") {
      document.location = '/messages';
    }
  });
});

u.sub('#accept-member-inv', 'click', function(e) {
  var user = this.getAttribute('data-user');
  var nsub = this.getAttribute('data-sub');
  u.post('/do/accept_memberinv/' + nsub + '/' + user, {}, function(data) {
    if (data.status == "ok") {
      if (data.addr) {
        document.location.href = data.addr; // Redirect to the sub's page
      } else {
        document.location.reload(); // Fallback: reload the current page
      }
    } else {
      alert(data.error || "An error occurred while accepting the invite."); // Handle errors
    }
  });
});

u.sub('#refuse-member-inv', 'click', function(e) {
  var user = this.getAttribute('data-user');
  var nsub = this.getAttribute('data-sub');

  u.post('/do/refuse_memberinv/' + nsub + '/' + user, {}, function(data) {
    // Callback function to handle the response data
    if (data.status == "ok") {
      document.location = '/messages'; // Redirect to messages page
    }
  });
});

u.sub('.revoke-mod2', 'click', function(e){
  var user=this.getAttribute('data-user');
  var nsub=this.getAttribute('data-sub');
  TextConfirm(this, function(){
    u.post('/do/remove_mod2/'+nsub+'/'+user, {},
    function(data){
      if (data.status == "ok") {
        if(!data.resign){
          document.location.reload();
        }else{
          document.location = '/s/' + nsub;
        }
      }
    });
  });
});

u.sub('.remove-member', 'click', function(e){
  var user=this.getAttribute('data-user');
  var nsub=this.getAttribute('data-sub');
  TextConfirm(this, function(){
    u.post('/do/remove_member/'+nsub+'/'+user, {},
    function(data){
      if (data.status == "ok") {
        if(!data.resign){
          document.location.reload();
        }else{
          document.location = '/s/' + nsub;
        }
      }
    });
  });
});

u.sub('.revoke-ban', 'click', function(e){
  var user=this.getAttribute('data-user');
  var nsub=this.getAttribute('data-sub');
  u.post('/do/remove_sub_ban/'+nsub+'/'+user, {},
  function(data){
    if (data.status == "ok") {
      document.location.reload();
    }
  });
});

u.sub('button.blk,button.unblk,button.sub,button.unsub', 'click', function(e){
  var sid=this.parentNode.getAttribute('data-sid');
  var act=this.getAttribute('data-ac')
  u.post('/do/' + act + '/' + sid, {},
  function(data){
    if (data.status == "ok") {
      document.location.reload();
    }
  });
});

window.onkeydown = function(e){
  if(!document.getElementsByClassName('alldaposts')[0]){return;}
  if(e.shiftKey == true && e.which == 88){
    console.log('weew')
    window.expandall = true;
    u.each('div.post', function(t, i){
      var q = t.getElementsByClassName('expando-btn')[0]
      if(q && q.getAttribute('data-icon') == "image"){
        q.click()
      }
    });
  }
}

u.sub('#ban_timepick', 'change', function(e){
  document.querySelector('.date-picker-future.input').value = '';
  if(this.value == 'ban_temp'){
    document.querySelector('.date-picker-future.input').style.display = 'inline-block';
  }else{
    document.querySelector('.date-picker-future.input').style.display = 'none';
  }
});

// Changing the visibility of fields in Submit a Post based on the
// selected radio button.
function onPtypeChange(e) {
  const newVal = e.value;
  const linkInput = document.querySelector('.reqlink');
  u.each('.lncont', function (e) {
    e.style.display = newVal == 'link' ? 'block' : 'none';
  });

  u.each('.lnicont', function (e) {
    e.style.display = newVal == 'link' ? 'block' : 'none';
  });

  u.each('.txcont', function (e) {
    e.style.display = newVal == 'text' ? 'block' : 'none';
  });

  u.each('.txicont', function (e) {
    e.style.display = newVal == 'text' ? 'inline-block' : 'none';
  });

  u.each('.ltxcont', function (e) {
    e.style.display = (newVal == 'link' || newVal == 'text') ? 'block' : 'none';
  });

  u.each('.ltxicont', function (e) {
    e.style.display = (newVal == 'link' || newVal == 'text') ? 'inline-block' : 'none';
  });

  u.each('.pocont', function (e) {
    e.style.display = newVal == 'poll' ? 'block' : 'none';
  });

  u.each('.ulcont', function (e) {
    e.style.display = newVal == 'upload' ? 'block' : 'none';
  });

  u.each('.reqpoll', function (e) {
    if(newVal == 'poll'){
      e.setAttribute('required', '1');
    }else{
      e.removeAttribute('required');
    }
  });

  u.each('.reqlink', function (e) {
    if(newVal == 'link'){
      linkInput.setAttribute('required', '1');
    }else{
      linkInput.removeAttribute('required');
      linkInput.value = '';
    }
  });

  u.each('.requpload', function (e) {
    if(newVal == 'upload'){
      e.setAttribute('required', '1');
    }else{
      e.removeAttribute('required');
    }
  });
}

u.sub('input[name="ptype"]', 'change', function (e) {
  onPtypeChange(e.target);
});

// List of suggestions for sub names, fetched from server.
var suggestions = null;

// Map radio button names to sub permission flag names.
var ptypeNames = {
  link: 'allow_link_posts',
  text: 'allow_text_posts',
  upload: 'allow_upload_posts',
  poll: 'allow_polls'
};

// Show or hide the radio buttons in Submit a Post based
// on what's allowed for the selected sub.
function updateSubmitPostForm(ptypes) {
  const radioButtons = document.querySelectorAll('input[name="ptype"]');
  const onlyUploads = document.getElementById('onlyuploads');
  let currentlySet = null;
  let firstVisible = null;
  for (let i = 0; i < radioButtons.length; i++) {
    const e = radioButtons[i];
    if (e.checked) {
      currentlySet = e;
    }
    if (ptypes[e.value]) {
      e.parentNode.style.display = '';
      if (!firstVisible) {
        firstVisible = e;
      }
    } else {
      e.parentNode.style.display = 'none';
    }
  }

  // Make sure a visible radio button is selected.
  if (!firstVisible) {
    // No radio buttons are visible, and that can only happen if the
    // sub is uploads-only and the user can't upload.
    // There's a message for that, so display it.
    onlyUploads.style.display = '';
  } else if (!ptypes[currentlySet.value]) {
    currentlySet.checked = false;
    firstVisible.checked = true;
    onPtypeChange(firstVisible);
    onlyUploads.style.display = 'none';
  } else {
      // Make sure the right fields are shown on initial load of page.
      onPtypeChange(currentlySet);
  }
}

// Change the selected flair, and update the form input.
function onSubmitFlairClick(elem) {
  const fid = this.getAttribute('data-id');
  const flairInput = document.getElementById('flair');
  const flairElems = document.querySelectorAll('.submitflair');
  const removeFlair = document.querySelector('.removeflair');
  for (let i=0; i < flairElems.length; i++) {
    let elem = flairElems[i];
    if (elem.getAttribute('data-id') == fid) {
      flairInput.value = elem.classList.contains('selected') ? '' : fid;
      elem.classList.toggle('selected');
    } else {
      elem.classList.remove('selected');
    }
  }
}

// Show the list of flairs in Submit a Post, if there are any.
function updateSubmitPostFormFlairs(flairs) {
  const flairControl = document.getElementById('flair-control');
  const flairInput = document.getElementById('flair');
  const flairContainer = document.getElementById('flair-container');
  if (flairs.length == 0) {
    flairInput.value = '';
    flairContainer.innerHTML = '';
    flairControl.classList.add('hide');
  } else {
    let found = false;
    flairContainer.innerHTML = '';
    for (let i=0; i < flairs.length; i++) {
      let span = document.createElement('span');
      span.setAttribute('data-id', flairs[i].id);
      span.style.color = flairs[i].text_color;
      span.style.backgroundColor = flairs[i].bg_color;
      span.style.borderColor = flairs[i].border_color;
      span.classList.add('submitflair');
      if (flairs[i].id == flairInput.value) {
        found = true;
        span.classList.add('selected');
      }
      span.innerHTML = flairs[i].text;
      span.addEventListener('click', onSubmitFlairClick);
      flairContainer.appendChild(span);
    }
    if (!found) {
      flairInput.value = '';
    }
    flairControl.classList.remove('hide');
  }
}

// When the sub name in Submit a Post is changed, get the types of
// posts permitted and update which radio buttons are visible
// accordingly.
function onSubmitPostSubChange(e) {
  if (e.classList.contains('sub_submitpost')) {
    const name = e.value.toLowerCase();
    let ptypes = null;
    const defaults = {
      link: true,
      upload: true,
      text: true,
      poll: false
    }

    if (e.value == '') {
      // No sub given, so show the defaults.
      updateSubmitPostForm(defaults);
      updateSubmitPostFormFlairs([], false, false);
    } else {
      u.get('/api/v3/sub/' + name, function(data) {
        updateSubmitPostForm(data.postTypes);
        updateSubmitPostFormFlairs(data.flairs);
      });
    }
  }
}

u.ready(function () {
  var sub = document.getElementById('sub');
  if (sub) {
    onSubmitPostSubChange(sub);
  }
})

u.sub('#sub', 'change', function(e) {
  onSubmitPostSubChange(e.target)});

// SUB autocomplete
const sa = document.querySelector('.sub_autocomplete');
if(sa){
  autocomplete({
    minLength: 3,
    debounceWaitMs: 200,
    input: sa,
    fetch: function(text, update) {
        text = text.toLowerCase();
        u.get('/api/v3/sub?query=' + text, function(data){
          suggestions = data.results;
          update(suggestions);
        })
    },
    onSelect: function(item) {
        sa.value = item.name;
        if (sa.id == 'sub') {
          onSubmitPostSubChange(sa);
        }
    },
    render: function(item, currentValue) {
      var div = document.createElement("div");
      div.textContent = item.name;
      return div;
    },
    emptyMsg: _('No subs found')
  });
}

u.sub('#change_user_flair', 'click', function(e) {
  e.preventDefault()
  document.getElementById('userflair').style.display = 'block';
})

u.sub('#userflair .selflair', 'click', function () {
    const flair = this.getAttribute('data-flair');
    const nsub = this.getAttribute('data-sub'), tg = this;
    u.post('/do/user_flair/' + nsub + '/' + flair, {},
        function (data) {
            if (data.status != "ok") {
                tg.parentNode.innerHTML = _('Error: %1', data.error);
            } else {
                tg.parentNode.innerHTML = _('Done!');
                document.location.reload();
            }
        }
    );
});
